from decimal import Decimal
import uuid
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    phone_number = models.CharField(max_length=20, blank=True)
    is_email_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.email} Profile"


class RestaurantTable(models.Model):
    STATUS_CHOICES = [
        ("AVAILABLE", "Available"),
        ("RESERVED", "Reserved"),
        ("OCCUPIED", "Occupied"),
        ("BILL_PENDING", "Bill Pending"),
        ("OUT_OF_SERVICE", "Out of Service"),
    ]

    AREA_CHOICES = [
        ("INDOOR", "Main Dining Hall"),
        ("TERRACE", "Outdoor Terrace"),
        ("WINDOW", "Window View"),
        ("BOOTH", "Private Booth"),
    ]

    table_number = models.CharField(max_length=20, unique=True)
    capacity = models.PositiveIntegerField(default=4)
    floor_area = models.CharField(max_length=20, choices=AREA_CHOICES, default="INDOOR")
    qr_token = models.CharField(max_length=64, unique=True, default=uuid.uuid4)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="AVAILABLE")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["table_number"]

    def __str__(self):
        return f"Table {self.table_number} ({self.capacity} seats, {self.get_floor_area_display()}) - {self.status}"

    @property
    def current_session(self):
        return self.sessions.filter(status="ACTIVE").first()


class Reservation(models.Model):
    STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("CONFIRMED", "Confirmed"),
        ("ARRIVED", "Arrived"),
        ("CANCELLED", "Cancelled"),
        ("COMPLETED", "Completed"),
        ("NO_SHOW", "No Show"),
    ]

    SEATING_CHOICES = [
        ("No preference", "No preference"),
        ("Indoor", "Indoor"),
        ("Outdoor", "Outdoor"),
        ("Window", "Window"),
        ("Private Booth", "Private Booth"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="reservations")
    table = models.ForeignKey(RestaurantTable, on_delete=models.SET_NULL, null=True, blank=True, related_name="reservations")
    name = models.CharField(max_length=150)
    email = models.EmailField()
    phone_number = models.CharField(max_length=20)
    date = models.DateField()
    time = models.CharField(max_length=20)
    guests = models.PositiveIntegerField(default=2)
    seating_preference = models.CharField(max_length=50, choices=SEATING_CHOICES, default="No preference")
    special_requests = models.TextField(blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="CONFIRMED")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "-created_at"]

    def __str__(self):
        return f"Booking #{self.id} - {self.name} on {self.date} at {self.time}"

    @property
    def is_past(self):
        return self.date < timezone.localdate()

    @property
    def can_cancel(self):
        return self.status in ["PENDING", "CONFIRMED"] and not self.is_past


# ==============================================================================
# MENU MODELS
# ==============================================================================

class MenuCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Menu Categories"

    def __str__(self):
        return self.name

    @property
    def slug_identifier(self):
        return self.name.lower().replace("&", "and").replace(" ", "-")


class MenuItem(models.Model):
    category = models.ForeignKey(MenuCategory, on_delete=models.PROTECT, related_name="items")
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    image = models.ImageField(upload_to="menu_images/", blank=True, null=True)
    is_active = models.BooleanField(default=True, help_text="True if item exists on the restaurant menu; False if archived.")
    is_available = models.BooleanField(default=True, help_text="True if item can currently be ordered from the kitchen.")
    availability_reason = models.CharField(max_length=255, blank=True, help_text="Reason when marked unavailable (e.g. out of stock).")
    preparation_time = models.PositiveIntegerField(default=15, help_text="Estimated preparation time in minutes.")
    availability_updated_at = models.DateTimeField(null=True, blank=True)
    availability_updated_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="availability_updates"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["category__name", "name"]

    def __str__(self):
        return f"{self.name} ({self.category.name}) - ₹{self.price}"

    @property
    def can_be_ordered(self):
        return self.is_active and self.is_available

    @property
    def has_historical_orders(self):
        return self.order_items.exists()


# ==============================================================================
# RESTAURANT TABLES, SESSIONS, ORDERS, BILLING
# ==============================================================================

class TableSession(models.Model):
    STATUS_CHOICES = [
        ("ACTIVE", "Active"),
        ("BILL_PENDING", "Bill Pending"),
        ("CLOSED", "Closed"),
        ("COMPLETED", "Completed"),
        ("CANCELLED", "Cancelled"),
    ]

    table = models.ForeignKey(RestaurantTable, on_delete=models.CASCADE, related_name="sessions")
    reservation = models.ForeignKey(Reservation, on_delete=models.SET_NULL, null=True, blank=True, related_name="sessions")
    customer = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="table_sessions")
    waiter = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_sessions")
    guest_name = models.CharField(max_length=150, blank=True)
    guest_count = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="ACTIVE")
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-opened_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["table"],
                condition=models.Q(status="ACTIVE"),
                name="one_active_session_per_table",
            )
        ]

    def __str__(self):
        return f"Session #{self.id} on Table {self.table.table_number} ({self.status})"

    @property
    def running_subtotal(self):
        subtotal = Decimal("0.00")
        for order in self.orders.exclude(status="CANCELLED"):
            for item in order.items.exclude(status="CANCELLED"):
                subtotal += item.subtotal
        return subtotal

    @property
    def total_items_count(self):
        count = 0
        for order in self.orders.exclude(status="CANCELLED"):
            count += order.items.exclude(status="CANCELLED").count()
        return count


class Order(models.Model):
    SOURCE_CHOICES = [
        ("CUSTOMER", "Customer"),
        ("WAITER", "Waiter"),
        ("CASHIER", "Cashier"),
    ]

    STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("ACCEPTED", "Accepted"),
        ("PREPARING", "Preparing"),
        ("READY", "Ready"),
        ("SERVED", "Served"),
        ("CANCELLED", "Cancelled"),
    ]

    session = models.ForeignKey(TableSession, on_delete=models.CASCADE, null=True, blank=True, related_name="orders")
    bill = models.ForeignKey("Bill", on_delete=models.CASCADE, null=True, blank=True, related_name="orders")
    placed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="placed_orders")
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default="CUSTOMER")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="PENDING")
    special_instructions = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        target = f"Session #{self.session_id}" if self.session_id else f"Bill #{self.bill_id}"
        return f"Order #{self.id} for {target} [{self.source}] - {self.status}"

    @property
    def table_session(self):
        return self.session

    @table_session.setter
    def table_session(self, val):
        self.session = val

    @property
    def created_by(self):
        return self.placed_by

    @created_by.setter
    def created_by(self, val):
        self.placed_by = val

    @property
    def order_total(self):
        return sum(item.subtotal for item in self.items.exclude(status="CANCELLED"))


class OrderItem(models.Model):
    STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("PREPARING", "Preparing"),
        ("READY", "Ready"),
        ("SERVED", "Served"),
        ("CANCELLED", "Cancelled"),
    ]

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    # Section 8 & 32: Use on_delete=models.PROTECT so historical menu items cannot be cascade deleted
    menu_item = models.ForeignKey(MenuItem, on_delete=models.PROTECT, related_name="order_items")
    quantity = models.PositiveIntegerField(default=1)
    # Section 14: unit_price must be stored in OrderItem to freeze historical prices
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    special_instructions = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="PENDING")
    estimated_minutes = models.PositiveIntegerField(null=True, blank=True)
    estimated_ready_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    served_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.quantity}x {self.menu_item.name} in Order #{self.order_id}"

    @property
    def subtotal(self):
        return self.quantity * self.unit_price

    @property
    def total_price(self):
        return self.subtotal


class Bill(models.Model):
    ORDER_TYPE_CHOICES = [
        ("DINE_IN", "Dine In"),
        ("COUNTER", "Counter / Direct"),
        ("TAKEAWAY", "Takeaway / Parcel"),
    ]

    STATUS_CHOICES = [
        ("OPEN", "Open"),
        ("REQUESTED", "Requested"),
        ("UNPAID", "Unpaid"),
        ("PAID", "Paid"),
        ("CANCELLED", "Cancelled"),
        ("VOID", "Void"),
    ]

    session = models.OneToOneField(TableSession, on_delete=models.CASCADE, null=True, blank=True, related_name="bill")
    order_type = models.CharField(max_length=20, choices=ORDER_TYPE_CHOICES, default="DINE_IN")
    customer_name = models.CharField(max_length=150, blank=True)
    customer_phone = models.CharField(max_length=20, blank=True)
    cashier = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="generated_bills")
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    tax = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    tip = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    total = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="OPEN")
    requested_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        if self.session and hasattr(self.session, "table"):
            return f"Bill #{self.id} for Table {self.session.table.table_number} - ₹{self.total} ({self.status})"
        return f"Bill #{self.id} ({self.display_title}) - ₹{self.total} ({self.status})"

    @property
    def table_session(self):
        return self.session

    @table_session.setter
    def table_session(self, val):
        self.session = val

    @property
    def display_title(self):
        if self.session and hasattr(self.session, "table"):
            return f"Table {self.session.table.table_number}"
        return self.get_order_type_display() if hasattr(self, "get_order_type_display") else "Counter / Direct"

    @property
    def display_customer(self):
        if self.session and self.session.guest_name:
            return self.session.guest_name
        return self.customer_name or "Counter Customer"

    @property
    def all_orders(self):
        if self.session:
            return self.session.orders.exclude(status="CANCELLED")
        return self.orders.exclude(status="CANCELLED")

    @property
    def total_items_count(self):
        if self.session:
            return self.session.total_items_count
        return sum(order.items.exclude(status="CANCELLED").count() for order in self.orders.all())


class Payment(models.Model):
    METHOD_CHOICES = [
        ("CASH", "Cash"),
        ("UPI", "UPI"),
        ("CARD", "Card"),
    ]

    STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("SUCCESS", "Success"),
        ("FAILED", "Failed"),
        ("REFUNDED", "Refunded"),
    ]

    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name="payments")
    processed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="processed_payments")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=METHOD_CHOICES, default="UPI")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="SUCCESS")
    transaction_id = models.CharField(max_length=100, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Payment #{self.id} of ₹{self.amount} via {self.payment_method} ({self.status})"

    @property
    def method(self):
        return self.payment_method

    @method.setter
    def method(self, val):
        self.payment_method = val

    @property
    def received_by(self):
        return self.processed_by

    @received_by.setter
    def received_by(self, val):
        self.processed_by = val
