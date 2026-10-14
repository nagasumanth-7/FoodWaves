from django.core.exceptions import ValidationError
from django.db import transaction
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


def open_table_session(table, guest_name, guest_count, waiter_user=None, reservation=None):
    """
    Convenience wrapper for floor waiters seating guests.
    """
    return get_or_create_active_session(
        table=table,
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

        table = RestaurantTable.objects.select_for_update().get(pk=locked_session.table_id)
        table.status = "AVAILABLE"
        table.save(update_fields=["status"])


def set_table_bill_pending(table_id):
    """Marks a table status as BILL_PENDING when guests request their check."""
    with transaction.atomic():
        table = RestaurantTable.objects.select_for_update().get(pk=table_id)
        table.status = "BILL_PENDING"
        table.save(update_fields=["status"])
        return table
