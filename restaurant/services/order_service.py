from decimal import Decimal
import datetime
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from ..models import MenuItem, Order, OrderItem
from .menu_service import validate_menu_item_availability


def validate_order_item_selection(menu_item_id, quantity=1):
    """
    Section 15: Server-side validation of item existence, active state, and real-time availability.
    """
    try:
        item = MenuItem.objects.get(pk=menu_item_id)
    except MenuItem.DoesNotExist:
        raise ValidationError(f"Menu item with ID {menu_item_id} does not exist.")

    is_valid, error_msg = validate_menu_item_availability(item)
    if not is_valid:
        raise ValidationError(error_msg)

    if quantity <= 0:
        raise ValidationError("Quantity must be at least 1.")

    return item


def validate_cart_items(cart_items):
    """
    Section 16: Validates a collection of cart items: [{'item_id': ..., 'quantity': ...}, ...]
    Returns: (is_valid: bool, errors: list[str])
    """
    errors = []
    for entry in cart_items:
        item_id = entry.get("item_id")
        try:
            item = MenuItem.objects.get(pk=item_id)
            is_valid, msg = validate_menu_item_availability(item)
            if not is_valid:
                errors.append(msg)
        except MenuItem.DoesNotExist:
            errors.append(f"Item #{item_id} does not exist.")

    return (len(errors) == 0, errors)


def create_order_for_session(session, items_data, placed_by=None, source="CUSTOMER", instructions=""):
    """
    Sections 13, 14, 15:
    Atomically creates an Order and freezes unit_price from the database for each OrderItem.
    """
    if not items_data:
        raise ValidationError("Cannot place an empty order. Select at least one item.")

    if session.status != "ACTIVE":
        raise ValidationError("Cannot place orders for an inactive or closed table session.")

    with transaction.atomic():
        # 1. Validate all items before persisting any database changes
        validated_items = []
        for entry in items_data:
            item_id = entry.get("item_id")
            qty = int(entry.get("quantity", 1))
            notes = entry.get("notes", "").strip()
            menu_item = validate_order_item_selection(item_id, quantity=qty)
            validated_items.append((menu_item, qty, notes))

        # 2. Create the parent Order
        order = Order.objects.create(
            session=session,
            placed_by=placed_by,
            source=source,
            status="ACCEPTED",
            special_instructions=instructions.strip(),
        )

        # 3. Create OrderItems with frozen unit_price from DB and initial PENDING status
        for menu_item, qty, notes in validated_items:
            prep_est = menu_item.preparation_time or 15
            OrderItem.objects.create(
                order=order,
                menu_item=menu_item,
                quantity=qty,
                unit_price=menu_item.price,
                special_instructions=notes,
                status="PENDING",
                started_at=None,
                estimated_minutes=prep_est,
                estimated_ready_at=None,
            )

        return order


def create_customer_order(session, items_data, customer_user=None, instructions=""):
    """Creates an order originated from customer table QR scan."""
    return create_order_for_session(
        session=session,
        items_data=items_data,
        placed_by=customer_user,
        source="CUSTOMER",
        instructions=instructions,
    )


def create_waiter_order(session, items_data, waiter_user=None, instructions=""):
    """Creates an order placed by a floor waiter for the active session."""
    return create_order_for_session(
        session=session,
        items_data=items_data,
        placed_by=waiter_user,
        source="WAITER",
        instructions=instructions,
    )


def update_order_item_status(order_item_id, new_status, updated_by=None, estimated_minutes=None):
    """
    Transitions OrderItem status (PENDING -> PREPARING -> READY -> SERVED).
    Also records chef's estimated cooking time and computes estimated_ready_at.
    """
    with transaction.atomic():
        try:
            item = OrderItem.objects.select_related("order").get(pk=order_item_id)
        except OrderItem.DoesNotExist:
            raise ValidationError(f"OrderItem #{order_item_id} not found.")

        valid_transitions = {
            "PENDING": ["PREPARING", "READY", "CANCELLED"],
            "PREPARING": ["READY", "CANCELLED"],
            "READY": ["SERVED", "CANCELLED"],
            "SERVED": [],
            "CANCELLED": [],
        }

        if new_status not in valid_transitions.get(item.status, []):
            raise ValidationError(f"Cannot transition status from {item.status} to {new_status}.")

        item.status = new_status
        now = timezone.now()

        if estimated_minutes is not None and int(estimated_minutes) > 0:
            mins = int(estimated_minutes)
            item.estimated_minutes = mins
            item.estimated_ready_at = now + datetime.timedelta(minutes=mins)

        if new_status == "PREPARING":
            if not item.started_at:
                item.started_at = now
            if not item.estimated_ready_at:
                est = item.estimated_minutes or 15
                item.estimated_ready_at = now + datetime.timedelta(minutes=est)
        elif new_status == "READY":
            item.ready_at = now
        elif new_status == "SERVED":
            item.served_at = now

        item.save()

        # Update parent order status based on remaining items
        parent_order = item.order
        all_items = list(parent_order.items.all())
        if all(i.status == "CANCELLED" for i in all_items):
            parent_order.status = "CANCELLED"
            parent_order.save(update_fields=["status"])
        elif all(i.status in ["SERVED", "CANCELLED"] for i in all_items) and any(i.status == "SERVED" for i in all_items):
            parent_order.status = "SERVED"
            parent_order.save(update_fields=["status"])
        elif any(i.status == "READY" for i in all_items):
            parent_order.status = "READY"
            parent_order.save(update_fields=["status"])
        elif any(i.status == "PREPARING" for i in all_items):
            parent_order.status = "PREPARING"
            parent_order.save(update_fields=["status"])
        elif any(i.status == "PENDING" for i in all_items):
            parent_order.status = "ACCEPTED"
            parent_order.save(update_fields=["status"])

        return item


def update_order_status(order_id, new_status):
    """Updates overall order status."""
    with transaction.atomic():
        order = Order.objects.get(pk=order_id)
        order.status = new_status
        order.save(update_fields=["status"])
        return order


def get_kitchen_active_orders():
    """
    Section 17: Retrieves all live kitchen orders placed by CUSTOMER or WAITER.
    Only displays orders for active or bill-pending dining sessions.
    """
    return (
        Order.objects.filter(
            source__in=["CUSTOMER", "WAITER"],
            status__in=["PENDING", "ACCEPTED", "PREPARING", "READY"],
            session__status__in=["ACTIVE", "BILL_PENDING"],
        )
        .select_related("session__table", "session", "placed_by")
        .prefetch_related("items__menu_item")
        .order_by("created_at")
    )


def get_pending_serves_for_waiter(waiter=None):
    """
    Retrieves OrderItem items that are READY in kitchen but not yet SERVED to guests.
    """
    qs = OrderItem.objects.filter(status="READY").select_related(
        "order__session__table", "menu_item", "order__placed_by"
    )
    if waiter:
        qs = qs.filter(order__session__waiter=waiter)
    return qs.order_by("ready_at")
