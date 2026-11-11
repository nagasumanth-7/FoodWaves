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
        indexes = [
            models.Index(fields=["is_active", "status"]),
        ]

    def __str__(self):
        return f"Table {self.table_number} ({self.capacity} seats, {self.get_floor_area_display()}) - {self.status}"

    @property
    def current_session(self):
        if hasattr(self, "_prefetched_active_sessions"):
            return self._prefetched_active_sessions[0] if self._prefetched_active_sessions else None
        return self.sessions.filter(status__in=["ACTIVE", "BILL_PENDING"]).first()


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
        indexes = [
            models.Index(fields=["date", "status"]),
            models.Index(fields=["user", "date"]),
        ]

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


CURATED_DISH_PHOTOS = {
    "pesarattu": "https://images.unsplash.com/photo-1589301760014-d929f3979dbc?auto=format&fit=crop&w=600&q=80",
    "andhra chicken 65": "https://images.unsplash.com/photo-1610057099443-fde8c4d50f91?auto=format&fit=crop&w=600&q=80",
    "chicken 65": "https://images.unsplash.com/photo-1610057099443-fde8c4d50f91?auto=format&fit=crop&w=600&q=80",
    "paneer tikka": "https://images.unsplash.com/photo-1599488615731-7e5c2823ff28?auto=format&fit=crop&w=600&q=80",
    "gobi manchurian": "https://images.unsplash.com/photo-1625398407796-82650a8c135f?auto=format&fit=crop&w=600&q=80",
    "gobi manchuria": "https://images.unsplash.com/photo-1625398407796-82650a8c135f?auto=format&fit=crop&w=600&q=80",
    "plain dosa": "https://images.unsplash.com/photo-1668236543090-82eba5ee5976?auto=format&fit=crop&w=600&q=80",
    "masala dosa": "https://images.unsplash.com/photo-1589301760014-d929f3979dbc?auto=format&fit=crop&w=600&q=80",
    "dosa": "https://images.unsplash.com/photo-1668236543090-82eba5ee5976?auto=format&fit=crop&w=600&q=80",
    "idly": "https://images.unsplash.com/photo-1589301760014-d929f3979dbc?auto=format&fit=crop&w=600&q=80",
    "idli": "https://images.unsplash.com/photo-1589301760014-d929f3979dbc?auto=format&fit=crop&w=600&q=80",
    "vada": "https://images.unsplash.com/photo-1601050690597-df0568f70950?auto=format&fit=crop&w=600&q=80",
    "medu vada": "https://images.unsplash.com/photo-1601050690597-df0568f70950?auto=format&fit=crop&w=600&q=80",
    "butter chicken": "https://images.unsplash.com/photo-1603894584373-5ac82b2ae398?auto=format&fit=crop&w=600&q=80",
    "paneer butter masala": "https://images.unsplash.com/photo-1631452180519-c014fe946bc7?auto=format&fit=crop&w=600&q=80",
    "andhra chicken curry": "https://images.unsplash.com/photo-1565557623262-b51c2513a641?auto=format&fit=crop&w=600&q=80",
    "chicken curry": "https://images.unsplash.com/photo-1565557623262-b51c2513a641?auto=format&fit=crop&w=600&q=80",
    "dal tadka": "https://images.unsplash.com/photo-1546833999-b9f581a1996d?auto=format&fit=crop&w=600&q=80",
    "chicken biryani": "https://images.unsplash.com/photo-1563379091339-03b21ab4a4f8?auto=format&fit=crop&w=600&q=80",
    "mutton biryani": "https://images.unsplash.com/photo-1589302168068-964664d93dc0?auto=format&fit=crop&w=600&q=80",
    "veg biryani": "https://images.unsplash.com/photo-1642821373181-696a54913e9a?auto=format&fit=crop&w=600&q=80",
    "biryani": "https://images.unsplash.com/photo-1563379091339-03b21ab4a4f8?auto=format&fit=crop&w=600&q=80",
    "fresh lime soda": "https://images.unsplash.com/photo-1513558161293-cdaf765ed2fd?auto=format&fit=crop&w=600&q=80",
    "mango lassi": "https://images.unsplash.com/photo-1528498033373-3c6c08e93d51?auto=format&fit=crop&w=600&q=80",
    "lassi": "https://images.unsplash.com/photo-1528498033373-3c6c08e93d51?auto=format&fit=crop&w=600&q=80",
    "cold coffee": "https://images.unsplash.com/photo-1517701604599-bb29b565090c?auto=format&fit=crop&w=600&q=80",
    "filter coffee": "https://images.unsplash.com/photo-1517701604599-bb29b565090c?auto=format&fit=crop&w=600&q=80",
    "mineral water": "https://images.unsplash.com/photo-1548839140-29a749e1bc4e?auto=format&fit=crop&w=600&q=80",
    "gulab jamun": "https://images.unsplash.com/photo-1601050690597-df0568f70950?auto=format&fit=crop&w=600&q=80",
}

CATEGORY_DEFAULT_PHOTOS = {
    "starters": "https://images.unsplash.com/photo-1599488615731-7e5c2823ff28?auto=format&fit=crop&w=600&q=80",
    "tiffins": "https://images.unsplash.com/photo-1589301760014-d929f3979dbc?auto=format&fit=crop&w=600&q=80",
    "curries": "https://images.unsplash.com/photo-1565557623262-b51c2513a641?auto=format&fit=crop&w=600&q=80",
    "rice & biryani": "https://images.unsplash.com/photo-1563379091339-03b21ab4a4f8?auto=format&fit=crop&w=600&q=80",
    "rice and biryani": "https://images.unsplash.com/photo-1563379091339-03b21ab4a4f8?auto=format&fit=crop&w=600&q=80",
    "beverages": "https://images.unsplash.com/photo-1513558161293-cdaf765ed2fd?auto=format&fit=crop&w=600&q=80",
    "desserts": "https://images.unsplash.com/photo-1571877227200-a0d98ea607e9?auto=format&fit=crop&w=600&q=80",
}


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
        indexes = [
            models.Index(fields=["is_active", "is_available"]),
        ]

    def __str__(self):
        cat_label = self.category.name if self.category_id else "Uncategorized"
        return f"{self.name} ({cat_label}) - ₹{self.price}"

    @property
    def can_be_ordered(self):
        return self.is_active and self.is_available

    @property
    def has_historical_orders(self):
        return self.order_items.exists()

    @property
    def photo_url(self):
        """Returns the uploaded dish image URL or an authentic high-res curated photo."""
        if self.image:
            try:
                name_str = str(self.image)
                if name_str.startswith("http://") or name_str.startswith("https://"):
                    return name_str
                return self.image.url
            except Exception:
                pass
        name_key = (self.name or "").lower().strip()
        if name_key in CURATED_DISH_PHOTOS:
            return CURATED_DISH_PHOTOS[name_key]
        for key, url in CURATED_DISH_PHOTOS.items():
            if key in name_key or name_key in key:
                return url
        cat_key = self.category.name.lower().strip() if self.category_id else ""
        return CATEGORY_DEFAULT_PHOTOS.get(
            cat_key,
            "https://images.unsplash.com/photo-1546833999-b9f581a1996d?auto=format&fit=crop&w=600&q=80",
        )


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
        indexes = [
            models.Index(fields=["table", "status"]),
            models.Index(fields=["status"]),
        ]
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
        if hasattr(self, "_prefetched_subtotal") and self._prefetched_subtotal is not None:
            return Decimal(self._prefetched_subtotal).quantize(Decimal("0.01"))
        total = (
            OrderItem.objects.filter(order__session=self)
            .exclude(order__status="CANCELLED")
            .exclude(status="CANCELLED")
            .aggregate(
                total=models.Sum(
                    models.F("quantity") * models.F("unit_price"),
                    output_field=models.DecimalField(max_digits=12, decimal_places=2),
                )
            )["total"]
        )
        return (total or Decimal("0.00")).quantize(Decimal("0.01"))

    @property
    def total_items_count(self):
        if hasattr(self, "_prefetched_items_count") and self._prefetched_items_count is not None:
            return self._prefetched_items_count
        return (
            OrderItem.objects.filter(order__session=self)
            .exclude(order__status="CANCELLED")
            .exclude(status="CANCELLED")
            .count()
        )


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
        indexes = [
            models.Index(fields=["source", "status"]),
            models.Index(fields=["session", "status"]),
        ]

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
        if "items" in getattr(self, "_prefetched_objects_cache", {}):
            return sum((item.subtotal for item in self.items.all() if item.status != "CANCELLED"), Decimal("0.00"))
        total = self.items.exclude(status="CANCELLED").aggregate(
            total=models.Sum(
                models.F("quantity") * models.F("unit_price"),
                output_field=models.DecimalField(max_digits=12, decimal_places=2),
            )
        )["total"]
        return (total or Decimal("0.00")).quantize(Decimal("0.01"))

    @property
    def table_display(self):
        if self.session and hasattr(self.session, "table") and self.session.table:
            return f"Table {self.session.table.table_number}"
        elif self.bill:
            return self.bill.display_title
        return "Direct Order"

    @property
    def source_display(self):
        if self.source == "WAITER":
            waiter_name = self.placed_by.get_full_name() or self.placed_by.username if self.placed_by else "Waiter"
            return f"Waiter ({waiter_name})"
        elif self.source == "CUSTOMER":
            guest = self.session.guest_name if self.session and self.session.guest_name else "Customer"
            return f"Customer ({guest})"
        return self.get_source_display()


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
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["order", "status"]),
        ]

    def __str__(self):
        return f"{self.quantity}x {self.menu_item.name} in Order #{self.order_id}"

    @property
    def subtotal(self):
        return self.quantity * self.unit_price

    @property
    def total_price(self):
        return self.subtotal

    @property
    def table_display(self):
        return self.order.table_display if self.order else "Table Unknown"

    @property
    def source_display(self):
        return self.order.source_display if self.order else "Unknown"


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

    class Meta:
        indexes = [
            models.Index(fields=["status", "created_at"]),
        ]

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
        if self.session_id:
            return self.session.orders.exclude(status="CANCELLED").prefetch_related("items__menu_item")
        return self.orders.exclude(status="CANCELLED").prefetch_related("items__menu_item")

    @property
    def total_items_count(self):
        if hasattr(self, "_prefetched_items_count") and self._prefetched_items_count is not None:
            return self._prefetched_items_count
        if self.session_id:
            return self.session.total_items_count
        return (
            OrderItem.objects.filter(order__bill=self)
            .exclude(order__status="CANCELLED")
            .exclude(status="CANCELLED")
            .count()
        )


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

    class Meta:
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["status", "payment_method", "created_at"]),
        ]

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


class StoredMedia(models.Model):
    """
    Database-backed media storage for uploaded dish photos so uploads persist
    across serverless / read-only filesystem deployments (e.g. Vercel).
    """
    name = models.CharField(max_length=255, unique=True, db_index=True)
    content_type = models.CharField(max_length=100, default="application/octet-stream")
    data = models.BinaryField()
    size = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Stored Media Files"

    def __str__(self):
        return f"{self.name} ({self.size} bytes)"
