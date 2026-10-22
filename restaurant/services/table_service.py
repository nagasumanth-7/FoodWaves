from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ..models import RestaurantTable, TableSession, Reservation, Bill


def get_all_tables(status_filter=None, area_filter=None):
    """Returns active restaurant tables, optionally filtered by status or floor area."""
    qs = RestaurantTable.objects.filter(is_active=True)
    if status_filter and status_filter != "ALL":
        qs = qs.filter(status=status_filter)
    if area_filter and area_filter != "ALL":
        qs = qs.filter(floor_area=area_filter)
    return qs.order_by("table_number")


def get_table_by_id(table_id):
    """Retrieves a single RestaurantTable by ID or raises 404."""
    return get_object_or_404(RestaurantTable, pk=table_id, is_active=True)


def get_table_by_qr_token(qr_token):
    """
    Section 10: Secure table lookup via cryptographically unique QR token.
    Never trusts manual URL query parameter table IDs.
    """
    return get_object_or_404(RestaurantTable, qr_token=qr_token, is_active=True)


def get_active_session_for_table(table_id):
    """Returns the currently active TableSession for a table, if one exists."""
    return TableSession.objects.filter(table_id=table_id, status="ACTIVE").first()


def get_or_create_active_session(table, customer=None, guest_name=None, guest_count=None, waiter_user=None, reservation=None):
    """
    Section 4 & 39: Thread-safe, atomic retrieval or creation of a TableSession.
    Uses select_for_update() on RestaurantTable to eliminate race conditions
    when multiple customers scan the same table QR code simultaneously.
    Prevents opening new sessions if the previous session has an unpaid bill.
    """
    with transaction.atomic():
        # Lock table record during session check/creation
        locked_table = RestaurantTable.objects.select_for_update().get(pk=table.pk)

        # 1. Check if an active session already exists for this table
        existing_session = TableSession.objects.filter(table=locked_table, status="ACTIVE").first()
        if existing_session:
            # If user is logged in and session customer wasn't set, link customer
            if customer and customer.is_authenticated and not existing_session.customer:
                existing_session.customer = customer
                existing_session.save(update_fields=["customer"])
            return existing_session

        # 2. Prevent opening a new session while the previous session still has unpaid bills or pending settlement
        unsettled_session = TableSession.objects.filter(
            table=locked_table,
            status="BILL_PENDING",
        ).first()
        if not unsettled_session:
            unsettled_bill = Bill.objects.filter(
                session__table=locked_table,
                status__in=["OPEN", "REQUESTED", "UNPAID"],
            ).select_related("session").first()
            if unsettled_bill:
                unsettled_session = unsettled_bill.session

        if unsettled_session:
            raise ValidationError(
                f"Cannot open a new session for Table {locked_table.table_number}. "
                f"Previous session #{unsettled_session.id} has an unsettled bill."
            )

        # 3. If table is OUT_OF_SERVICE or BILL_PENDING, reject session creation
        if locked_table.status == "OUT_OF_SERVICE":
            raise ValidationError(f"Table {locked_table.table_number} is currently out of service.")
        if locked_table.status == "BILL_PENDING":
            raise ValidationError(f"Table {locked_table.table_number} is currently awaiting bill settlement.")

        # 3. Create a new active session
        name = (guest_name or "").strip()
        if not name:
            if customer and customer.is_authenticated:
                name = customer.first_name or customer.username
            else:
                name = f"Table {locked_table.table_number} Guests"

        count = int(guest_count) if guest_count else 2

        new_session = TableSession.objects.create(
            table=locked_table,
            customer=customer if (customer and customer.is_authenticated) else None,
            guest_name=name,
            guest_count=count,
            waiter=waiter_user,
            reservation=reservation,
            status="ACTIVE",
        )

        locked_table.status = "OCCUPIED"
        locked_table.save(update_fields=["status"])

        return new_session


def open_table_session(table, guest_name, guest_count, waiter_user=None, reservation=None, customer=None):
    """
    Convenience wrapper for floor waiters seating guests.
    """
    if reservation and not customer:
        customer = getattr(reservation, "user", None)

    return get_or_create_active_session(
        table=table,
        customer=customer,
        guest_name=guest_name,
        guest_count=guest_count,
        waiter_user=waiter_user,
        reservation=reservation,
    )


def close_table_session(session):
    """
    Closes an active TableSession and frees the table back to AVAILABLE.
    Uses transaction.atomic() to guarantee synchronized state changes.
    """
    with transaction.atomic():
        locked_session = TableSession.objects.select_for_update().get(pk=session.pk)
        locked_session.status = "CLOSED"
        locked_session.closed_at = timezone.now()
        locked_session.save(update_fields=["status", "closed_at"])

        if locked_session.reservation:
            res = locked_session.reservation
            if res.status == "ARRIVED":
                res.status = "COMPLETED"
                res.save(update_fields=["status"])

        table = RestaurantTable.objects.select_for_update().get(pk=locked_session.table_id)
        # Check if another confirmed reservation exists for this table today
        today = timezone.localdate()
        has_future_res = Reservation.objects.filter(
            table=table,
            date=today,
            status__in=["CONFIRMED", "PENDING"],
        ).exists()
        table.status = "RESERVED" if has_future_res else "AVAILABLE"
        table.save(update_fields=["status"])


def set_table_bill_pending(table_id):
    """Marks a table status as BILL_PENDING when guests request their check."""
    with transaction.atomic():
        table = RestaurantTable.objects.select_for_update().get(pk=table_id)
        table.status = "BILL_PENDING"
        table.save(update_fields=["status"])
        return table


def get_reservations_for_waiter(date=None, status=None, search_query=None, assignment=None):
    """
    Retrieves reservations for the waiter portal with multi-criteria filtering.
    """
    qs = Reservation.objects.select_related("user", "table").all()

    today = timezone.localdate()
    if date:
        date_str = str(date).strip().upper()
        if date_str == "TODAY":
            qs = qs.filter(date=today)
        elif date_str == "UPCOMING":
            qs = qs.filter(date__gte=today)
        elif date_str == "PAST":
            qs = qs.filter(date__lt=today)
        elif date_str != "ALL":
            qs = qs.filter(date=date)

    if status and status != "ALL":
        qs = qs.filter(status=status)

    if assignment:
        assignment_str = str(assignment).strip().upper()
        if assignment_str == "UNASSIGNED":
            qs = qs.filter(table__isnull=True)
        elif assignment_str == "ASSIGNED":
            qs = qs.filter(table__isnull=False)

    if search_query:
        sq = search_query.strip()
        qs = qs.filter(
            Q(name__icontains=sq)
            | Q(phone_number__icontains=sq)
            | Q(email__icontains=sq)
            | Q(user__username__icontains=sq)
            | Q(user__email__icontains=sq)
            | Q(id__iexact=sq)
        )

    return qs.order_by("date", "time", "-created_at")


def assign_table_to_reservation(reservation, table, waiter_user=None):
    """
    Assigns or reassigns a restaurant table to a reservation.
    Transitions table to OCCUPIED and opens an active TableSession so the table
    is open and immediately ready to take orders.
    """
    with transaction.atomic():
        locked_res = Reservation.objects.select_for_update().get(pk=reservation.pk)
        old_table = locked_res.table

        locked_table = RestaurantTable.objects.select_for_update().get(pk=table.pk)
        if not locked_table.is_active:
            raise ValidationError(f"Table {locked_table.table_number} is inactive.")
        if locked_table.status == "OUT_OF_SERVICE":
            raise ValidationError(f"Table {locked_table.table_number} is currently out of service.")

        # If reassigning away from a previous table, clean up old table session
        if old_table and old_table.pk != locked_table.pk:
            old_session = TableSession.objects.filter(table=old_table, reservation=locked_res, status="ACTIVE").first()
            if old_session:
                if not old_session.orders.exists():
                    old_session.status = "CANCELLED"
                    old_session.closed_at = timezone.now()
                    old_session.save(update_fields=["status", "closed_at"])
                else:
                    old_session.table = locked_table
                    old_session.save(update_fields=["table"])

            other_res_count = Reservation.objects.filter(
                table=old_table,
                date=locked_res.date,
                status__in=["CONFIRMED", "PENDING", "ARRIVED"],
            ).exclude(pk=locked_res.pk).count()
            has_other_session = TableSession.objects.filter(table=old_table, status="ACTIVE").exists()
            if other_res_count == 0 and not has_other_session and old_table.status in ["RESERVED", "OCCUPIED"]:
                old_table.status = "AVAILABLE"
                old_table.save(update_fields=["status"])

        locked_res.table = locked_table
        locked_res.status = "ARRIVED"
        locked_res.save(update_fields=["table", "status"])

        # Automatically open table session so table is open and ready to take orders
        session = TableSession.objects.filter(table=locked_table, status="ACTIVE").first()
        if not session:
            session = open_table_session(
                table=locked_table,
                guest_name=locked_res.name,
                guest_count=locked_res.guests,
                waiter_user=waiter_user or getattr(locked_res, "user", None),
                reservation=locked_res,
                customer=getattr(locked_res, "user", None),
            )
        else:
            if not session.reservation:
                session.reservation = locked_res
                session.guest_name = locked_res.name
                session.guest_count = locked_res.guests
                session.save(update_fields=["reservation", "guest_name", "guest_count"])

        locked_table.status = "OCCUPIED"
        locked_table.save(update_fields=["status"])

        return locked_res


def unassign_table_from_reservation(reservation):
    """
    Unassigns a table from a reservation.
    Cancels empty active sessions and reverts the table status back to AVAILABLE.
    """
    with transaction.atomic():
        locked_res = Reservation.objects.select_for_update().get(pk=reservation.pk)
        old_table = locked_res.table
        if old_table:
            active_sess = TableSession.objects.filter(table=old_table, reservation=locked_res, status="ACTIVE").first()
            if active_sess:
                if not active_sess.orders.exists():
                    active_sess.status = "CANCELLED"
                    active_sess.closed_at = timezone.now()
                    active_sess.save(update_fields=["status", "closed_at"])
                else:
                    active_sess.reservation = None
                    active_sess.save(update_fields=["reservation"])

            other_res_count = Reservation.objects.filter(
                table=old_table,
                date=locked_res.date,
                status__in=["CONFIRMED", "PENDING", "ARRIVED"],
            ).exclude(pk=locked_res.pk).count()
            has_other_session = TableSession.objects.filter(table=old_table, status="ACTIVE").exists()
            if other_res_count == 0 and not has_other_session:
                old_table.status = "AVAILABLE"
                old_table.save(update_fields=["status"])

            locked_res.table = None
            if locked_res.status == "ARRIVED":
                locked_res.status = "CONFIRMED"
            locked_res.save(update_fields=["table", "status"])

        return locked_res


def seat_reservation(reservation, table=None, waiter_user=None):
    """
    Seats a customer from their reservation:
    - Verifies/assigns the target table
    - Sets reservation status to ARRIVED
    - Opens an active TableSession
    - Sets table status to OCCUPIED
    """
    with transaction.atomic():
        locked_res = Reservation.objects.select_for_update().get(pk=reservation.pk)
        target_table = table or locked_res.table
        if not target_table:
            raise ValidationError("Please assign or select a table before seating guests.")

        locked_table = RestaurantTable.objects.select_for_update().get(pk=target_table.pk)
        if not locked_table.is_active:
            raise ValidationError(f"Table {locked_table.table_number} is inactive.")
        if locked_table.status == "OUT_OF_SERVICE":
            raise ValidationError(f"Table {locked_table.table_number} is out of service.")
        if locked_table.status == "OCCUPIED" and not TableSession.objects.filter(table=locked_table, status="ACTIVE", reservation=locked_res).exists():
            raise ValidationError(f"Table {locked_table.table_number} is already occupied by another party.")

        # Update reservation
        locked_res.table = locked_table
        locked_res.status = "ARRIVED"
        locked_res.save(update_fields=["table", "status"])

        # Open active session
        session = open_table_session(
            table=locked_table,
            guest_name=locked_res.name,
            guest_count=locked_res.guests,
            waiter_user=waiter_user,
            reservation=locked_res,
        )
        return session

