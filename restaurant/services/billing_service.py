from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum, Count
from django.utils import timezone
from ..models import Bill, Payment, TableSession, RestaurantTable


GST_RATE = Decimal("0.05")  # 5% Restaurant GST (2.5% CGST + 2.5% SGST)


def calculate_and_generate_bill(session, cashier=None, discount_pct=Decimal("0.00"), tip_amount=Decimal("0.00"), bill_status=None):
    """
    Sections 19 & 38:
    Combines Customer QR orders and Waiter orders into ONE consolidated bill.
    Computes subtotal from all session orders, applies 5% GST, calculates discounts,
    adds tip, and generates/updates an active Bill inside a database transaction.
    """
    if session.status not in ["ACTIVE", "BILL_PENDING", "COMPLETED"]:
        raise ValidationError(f"Cannot generate bill for session with status {session.status}.")

    with transaction.atomic():
        # Lock session to prevent race conditions during bill generation
        locked_session = TableSession.objects.select_for_update().get(pk=session.pk)

        subtotal = locked_session.running_subtotal
        if subtotal <= Decimal("0.00"):
            raise ValidationError("Cannot generate a bill with 0 total. No served or active items found.")

        discount_amount = (subtotal * (Decimal(str(discount_pct)) / Decimal("100.00"))).quantize(Decimal("0.01"))
        taxable_amount = max(Decimal("0.00"), subtotal - discount_amount)
        tax_amount = (taxable_amount * GST_RATE).quantize(Decimal("0.01"))
        tip_dec = Decimal(str(tip_amount)).quantize(Decimal("0.01"))
        total_amount = (taxable_amount + tax_amount + tip_dec).quantize(Decimal("0.01"))

        valid_bill_statuses = {"OPEN", "REQUESTED", "UNPAID", "PAID", "CANCELLED", "VOID"}
        if bill_status and bill_status in valid_bill_statuses:
            target_status = bill_status
        elif bill_status == "BILL_PENDING":
            # Map legacy/erroneous BILL_PENDING argument to REQUESTED for Bill
            target_status = "REQUESTED"
        else:
            target_status = "OPEN"

        bill, created = Bill.objects.select_for_update().get_or_create(
            session=locked_session,
            defaults={
                "subtotal": subtotal,
                "tax": tax_amount,
                "discount": discount_amount,
                "tip": tip_dec,
                "total": total_amount,
                "status": target_status,
                "cashier": cashier,
            },
        )

        if not created and bill.status != "PAID":
            bill.subtotal = subtotal
            bill.tax = tax_amount
            bill.discount = discount_amount
            bill.tip = tip_dec
            bill.total = total_amount
            if target_status and target_status != "OPEN":
                bill.status = target_status
            if cashier and not bill.cashier:
                bill.cashier = cashier
            bill.save()

        # Update table and session status to BILL_PENDING only when bill is actually requested
        table = locked_session.table
        if target_status in ["REQUESTED", "UNPAID"]:
            if table.status != "BILL_PENDING":
                table.status = "BILL_PENDING"
                table.save(update_fields=["status"])
            if locked_session.status == "ACTIVE":
                locked_session.status = "BILL_PENDING"
                locked_session.save(update_fields=["status"])

        return bill


def request_bill_from_customer(session):
    """
    Sections 18 & 43:
    Triggered when customer presses "REQUEST BILL" on the bill page.
    Changes Bill -> REQUESTED and Table -> BILL_PENDING.
    """
    with transaction.atomic():
        bill = calculate_and_generate_bill(session, bill_status="REQUESTED")
        bill.requested_at = timezone.now()
        bill.save(update_fields=["status", "requested_at"])

        table = RestaurantTable.objects.select_for_update().get(pk=session.table_id)
        table.status = "BILL_PENDING"
        table.save(update_fields=["status"])

        session.status = "BILL_PENDING"
        session.save(update_fields=["status"])

        return bill


def process_bill_payment(bill, payment_method, amount_paid, transaction_id="", processed_by=None):
    """
    Sections 20, 21, 22, 39:
    Thread-safe payment processor preventing race conditions and duplicate payments.
    Marks Payment -> SUCCESS, Bill -> PAID, TableSession -> CLOSED, Table -> AVAILABLE.
    """
    with transaction.atomic():
        locked_bill = Bill.objects.select_for_update().get(pk=bill.pk)

        # 1. Prevent duplicate payment
        if locked_bill.status == "PAID":
            raise ValidationError("This bill has already been paid in full.")

        amount_dec = Decimal(str(amount_paid))
        if amount_dec < locked_bill.total:
            raise ValidationError(f"Amount paid (₹{amount_dec}) is less than total bill amount (₹{locked_bill.total}).")

        # 2. Record Payment record
        now = timezone.now()
        payment = Payment.objects.create(
            bill=locked_bill,
            amount=amount_dec,
            payment_method=payment_method,
            status="SUCCESS",
            transaction_id=transaction_id.strip(),
            processed_by=processed_by,
            paid_at=now,
        )

        # 3. Mark Bill as PAID
        locked_bill.status = "PAID"
        locked_bill.paid_at = now
        if processed_by and not locked_bill.cashier:
            locked_bill.cashier = processed_by
        locked_bill.save(update_fields=["status", "paid_at", "cashier"])

        # 4. Close the TableSession if table-based (Section 22)
        if locked_bill.session_id:
            session = TableSession.objects.select_for_update().get(pk=locked_bill.session_id)
            session.status = "CLOSED"
            session.closed_at = now
            session.save(update_fields=["status", "closed_at"])

            # 5. Free Table back to AVAILABLE
            table = RestaurantTable.objects.select_for_update().get(pk=session.table_id)
            table.status = "AVAILABLE"
            table.save(update_fields=["status"])

        return payment


def create_direct_counter_bill(
    items_data,
    cashier_user,
    customer_name="Counter Guest",
    customer_phone="",
    order_type="COUNTER",
    discount_pct=Decimal("0.00"),
    tip_amount=Decimal("0.00"),
    instructions="",
    settle_immediately=False,
    payment_method="CASH",
    transaction_id="",
):
    """
    Cashier Direct Non-Table Billing:
    Allows cashier to select items, enter quantities, and immediately generate a bill
    without requiring a dining table or table session.
    Calculates item prices from database, 5% GST, discounts, and optional tip.
    """
    if not items_data:
        raise ValidationError("Cannot create a bill with no items selected.")

    from ..models import MenuItem, Order, OrderItem

    with transaction.atomic():
        # 1. Server-side validation of items & prices
        validated_items = []
        subtotal = Decimal("0.00")
        for entry in items_data:
            item_id = entry.get("item_id")
            qty = int(entry.get("quantity", 1))
            notes = entry.get("notes", "").strip()
            if qty <= 0:
                continue
            try:
                item = MenuItem.objects.get(pk=item_id)
            except MenuItem.DoesNotExist:
                raise ValidationError(f"Menu item #{item_id} does not exist.")

            # Cashier direct billing allows active items even if kitchen marked is_available=False
            # (e.g. cashier billing counter walk-ins or already-served items)
            if not item.is_active:
                raise ValidationError(f"'{item.name}' has been archived and is no longer on the menu.")

            line_subtotal = item.price * qty
            subtotal += line_subtotal
            validated_items.append((item, qty, item.price, notes))

        if not validated_items:
            raise ValidationError("Please select at least one valid item with quantity greater than 0.")

        # 2. Financial computations
        disc_pct_dec = Decimal(str(discount_pct or 0))
        discount_amount = (subtotal * (disc_pct_dec / Decimal("100.00"))).quantize(Decimal("0.01"))
        taxable_amount = max(Decimal("0.00"), subtotal - discount_amount)
        tax_amount = (taxable_amount * GST_RATE).quantize(Decimal("0.01"))
        tip_dec = Decimal(str(tip_amount or 0)).quantize(Decimal("0.01"))
        total_amount = (taxable_amount + tax_amount + tip_dec).quantize(Decimal("0.01"))

        # 3. Create Bill record
        bill = Bill.objects.create(
            session=None,
            order_type=order_type,
            customer_name=customer_name.strip() or "Counter Guest",
            customer_phone=customer_phone.strip(),
            cashier=cashier_user,
            subtotal=subtotal,
            tax=tax_amount,
            discount=discount_amount,
            tip=tip_dec,
            total=total_amount,
            status="UNPAID",
        )

        # 4. Create Order record linked directly to Bill
        order = Order.objects.create(
            session=None,
            bill=bill,
            placed_by=cashier_user,
            source="CASHIER",
            status="SERVED",
            special_instructions=instructions.strip(),
        )

        now = timezone.now()
        for menu_item, qty, unit_price, notes in validated_items:
            OrderItem.objects.create(
                order=order,
                menu_item=menu_item,
                quantity=qty,
                unit_price=unit_price,
                special_instructions=notes,
                status="SERVED",
                started_at=now,
                ready_at=now,
                served_at=now,
            )

        # 5. If immediate settlement requested (e.g. counter walk-in pays right away)
        if settle_immediately:
            process_bill_payment(
                bill=bill,
                payment_method=payment_method,
                amount_paid=total_amount,
                transaction_id=transaction_id,
                processed_by=cashier_user,
            )
            bill.refresh_from_db()

        return bill


def record_payment_failure(bill, payment_method, amount, reason="", processed_by=None):
    """
    Records a failed payment attempt while keeping the bill, session, and table open.
    """
    now = timezone.now()
    return Payment.objects.create(
        bill=bill,
        amount=Decimal(str(amount)),
        payment_method=payment_method,
        status="FAILED",
        transaction_id=reason.strip(),
        processed_by=processed_by,
        paid_at=now,
    )


def get_unpaid_bills():
    """Retrieves all unpaid or requested bills for the cashier dashboard."""
    return (
        Bill.objects.filter(status__in=["OPEN", "REQUESTED", "UNPAID"])
        .select_related("session__table", "session__waiter", "cashier")
        .order_by("-created_at")
    )


def get_sales_report_summary(start_date=None, end_date=None, period="today"):
    """
    Financial Sales Report calculation supporting flexible periods and date constraints:
    Periods supported:
      - 'today': metrics for today
      - 'yesterday': metrics for yesterday
      - 'week': last 7 days (today - 6 days through today)
      - 'month': last 30 days (today - 29 days through today)
      - 'custom': custom range bounded by start_date and end_date
    Validates date constraints (start_date <= end_date, safe fallbacks).
    Aggregates:
      - Total revenue collected
      - Payment methods breakdown (Cash, UPI, Card)
      - Food subtotal, taxes (GST), discounts, tips
      - Transaction count
      - Itemized payment ledger
    """
    today = timezone.localdate()

    period = (period or "today").lower().strip()
    if period == "today":
        start_date = today
        end_date = today
        period_label = f"Today ({today.strftime('%d %b %Y')})"
    elif period == "yesterday":
        yesterday = today - timedelta(days=1)
        start_date = yesterday
        end_date = yesterday
        period_label = f"Yesterday ({yesterday.strftime('%d %b %Y')})"
    elif period == "week":
        start_date = today - timedelta(days=6)
        end_date = today
        period_label = f"Last 7 Days ({start_date.strftime('%d %b')} – {end_date.strftime('%d %b %Y')})"
    elif period == "month":
        start_date = today - timedelta(days=29)
        end_date = today
        period_label = f"Last 30 Days ({start_date.strftime('%d %b')} – {end_date.strftime('%d %b %Y')})"
    elif period == "custom":
        if start_date is None:
            start_date = today
        if end_date is None:
            end_date = start_date or today
        # Date constraint enforcement: start_date cannot exceed end_date
        if start_date > end_date:
            start_date, end_date = end_date, start_date
        if start_date == end_date:
            period_label = f"Single Date ({start_date.strftime('%d %b %Y')})"
        else:
            period_label = f"Custom Range ({start_date.strftime('%d %b %Y')} – {end_date.strftime('%d %b %Y')})"
    else:
        period = "today"
        start_date = today
        end_date = today
        period_label = f"Today ({today.strftime('%d %b %Y')})"

    payments_qs = Payment.objects.filter(
        status="SUCCESS",
        created_at__date__gte=start_date,
        created_at__date__lte=end_date,
    )

    total_revenue = payments_qs.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    total_transactions = payments_qs.count()

    # Method breakdowns
    cash_total = payments_qs.filter(payment_method="CASH").aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    upi_total = payments_qs.filter(payment_method="UPI").aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    card_total = payments_qs.filter(payment_method="CARD").aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

    # Linked settled bills aggregates (subtotal, tax, discount, tip)
    paid_bills_qs = Bill.objects.filter(
        pk__in=payments_qs.values_list("bill_id", flat=True)
    )
    subtotal_sum = paid_bills_qs.aggregate(total=Sum("subtotal"))["total"] or Decimal("0.00")
    discount_sum = paid_bills_qs.aggregate(total=Sum("discount"))["total"] or Decimal("0.00")
    tax_sum = paid_bills_qs.aggregate(total=Sum("tax"))["total"] or Decimal("0.00")
    tip_sum = paid_bills_qs.aggregate(total=Sum("tip"))["total"] or Decimal("0.00")

    payments_list = (
        payments_qs.select_related("bill__session__table", "bill", "processed_by")
        .order_by("-created_at")[:100]
    )

    return {
        "date": start_date,  # backwards compatibility for single-day consumers
        "start_date": start_date,
        "end_date": end_date,
        "period": period,
        "period_label": period_label,
        "is_single_day": (start_date == end_date),
        "total_revenue": total_revenue,
        "total_transactions": total_transactions,
        "cash_total": cash_total,
        "upi_total": upi_total,
        "card_total": card_total,
        "subtotal_sum": subtotal_sum,
        "discount_sum": discount_sum,
        "tax_sum": tax_sum,
        "tip_sum": tip_sum,
        "payments": payments_list,
    }


def get_daily_cashier_summary(target_date=None):
    """
    Backwards-compatible wrapper around get_sales_report_summary for a single target date.
    """
    if target_date is None:
        target_date = timezone.localdate()
    return get_sales_report_summary(start_date=target_date, end_date=target_date, period="custom" if target_date != timezone.localdate() else "today")

