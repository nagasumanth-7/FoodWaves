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

        target_status = bill_status if bill_status else "OPEN"

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
            if bill_status:
                bill.status = bill_status
            if cashier and not bill.cashier:
                bill.cashier = cashier
            bill.save()

        # Update table status to BILL_PENDING if requested or unbilled
        table = locked_session.table
        if bill_status in ["REQUESTED", "BILL_PENDING"] or table.status != "BILL_PENDING":
            table.status = "BILL_PENDING"
            table.save(update_fields=["status"])

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

        # 4. Close the TableSession (Section 22)
        session = TableSession.objects.select_for_update().get(pk=locked_bill.session_id)
        session.status = "CLOSED"
        session.closed_at = now
        session.save(update_fields=["status", "closed_at"])

        # 5. Free Table back to AVAILABLE
        table = RestaurantTable.objects.select_for_update().get(pk=session.table_id)
        table.status = "AVAILABLE"
        table.save(update_fields=["status"])

        return payment


def get_unpaid_bills():
    """Retrieves all unpaid or requested bills for the cashier dashboard."""
    return (
        Bill.objects.filter(status__in=["OPEN", "REQUESTED", "UNPAID"])
        .select_related("session__table", "session__waiter", "cashier")
        .order_by("-created_at")
    )


def get_daily_cashier_summary(target_date=None):
    """
    Aggregates financial statistics for a given day (defaults to today):
    total revenue, paid bills count, breakdown by payment method.
    """
    if target_date is None:
        target_date = timezone.localdate()

    payments_today = Payment.objects.filter(
        status="SUCCESS",
        created_at__date=target_date,
    )

    total_revenue = payments_today.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    total_transactions = payments_today.count()

    # Method breakdowns
    cash_total = payments_today.filter(payment_method="CASH").aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    upi_total = payments_today.filter(payment_method="UPI").aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    card_total = payments_today.filter(payment_method="CARD").aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

    return {
        "date": target_date,
        "total_revenue": total_revenue,
        "total_transactions": total_transactions,
        "cash_total": cash_total,
        "upi_total": upi_total,
        "card_total": card_total,
        "payments": payments_today.select_related("bill__session__table", "processed_by").order_by("-created_at")[:20],
    }
