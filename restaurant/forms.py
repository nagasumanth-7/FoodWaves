import re
from django import forms
from django.contrib.auth.models import User
from django.utils import timezone
from .models import Reservation


class SignUpForm(forms.Form):
    name = forms.CharField(
        max_length=150,
        required=True,
        widget=forms.TextInput(attrs={
            "placeholder": "Your full name",
            "autocomplete": "name",
            "class": "form-control",
        }),
    )
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={
            "placeholder": "you@example.com",
            "autocomplete": "email",
            "class": "form-control",
        }),
    )
    phone_number = forms.CharField(
        max_length=20,
        required=True,
        widget=forms.TextInput(attrs={
            "placeholder": "+91 98765 43210",
            "autocomplete": "tel",
            "class": "form-control",
        }),
    )
    password = forms.CharField(
        min_length=6,
        required=True,
        widget=forms.PasswordInput(attrs={
            "placeholder": "Create a secure password (min 6 chars)",
            "autocomplete": "new-password",
            "class": "form-control",
        }),
    )
    confirm_password = forms.CharField(
        min_length=6,
        required=True,
        widget=forms.PasswordInput(attrs={
            "placeholder": "Re-enter your password",
            "autocomplete": "new-password",
            "class": "form-control",
        }),
    )

    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip().lower()
        active_user_exists = User.objects.filter(email__iexact=email, is_active=True).exists()
        if active_user_exists:
            raise forms.ValidationError("An account with this email address already exists. Please log in.")
        return email

    def clean_phone_number(self):
        phone = self.cleaned_data.get("phone_number", "").strip()
        digits = re.sub(r"\D", "", phone)
        if len(digits) < 7:
            raise forms.ValidationError("Please enter a valid phone number with at least 7 digits.")
        return phone

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        confirm_password = cleaned_data.get("confirm_password")

        if password and confirm_password and password != confirm_password:
            self.add_error("confirm_password", "Passwords do not match.")
        return cleaned_data


class LoginForm(forms.Form):
    email = forms.CharField(
        label="Email or Username",
        required=True,
        widget=forms.TextInput(attrs={
            "placeholder": "you@example.com or staff username",
            "autocomplete": "username",
            "class": "form-control",
        }),
    )
    password = forms.CharField(
        required=True,
        widget=forms.PasswordInput(attrs={
            "placeholder": "Enter your password",
            "autocomplete": "current-password",
            "class": "form-control",
        }),
    )
    remember_me = forms.BooleanField(
        required=False,
        initial=True,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )


class ForgotPasswordForm(forms.Form):
    email = forms.EmailField(
        label="Email address",
        required=True,
        widget=forms.EmailInput(attrs={
            "placeholder": "Enter your registered email address",
            "autocomplete": "email",
            "class": "form-control",
        }),
    )

    def clean_email(self):
        return self.cleaned_data.get("email", "").strip().lower()


class ResetPasswordForm(forms.Form):
    new_password = forms.CharField(
        label="New Password",
        min_length=6,
        required=True,
        widget=forms.PasswordInput(attrs={
            "placeholder": "Enter new secure password (min 6 chars)",
            "autocomplete": "new-password",
            "class": "form-control",
            "id": "id_new_password",
        }),
    )
    confirm_new_password = forms.CharField(
        label="Confirm New Password",
        min_length=6,
        required=True,
        widget=forms.PasswordInput(attrs={
            "placeholder": "Re-enter new password",
            "autocomplete": "new-password",
            "class": "form-control",
            "id": "id_confirm_new_password",
        }),
    )
    access_token = forms.CharField(required=False, widget=forms.HiddenInput(attrs={"id": "id_access_token"}))
    token_hash = forms.CharField(required=False, widget=forms.HiddenInput(attrs={"id": "id_token_hash"}))
    email = forms.CharField(required=False, widget=forms.HiddenInput(attrs={"id": "id_email"}))

    def clean(self):
        cleaned_data = super().clean()
        pw = cleaned_data.get("new_password")
        cpw = cleaned_data.get("confirm_new_password")
        if pw and cpw and pw != cpw:
            self.add_error("confirm_new_password", "Passwords do not match.")
        return cleaned_data


class ReservationForm(forms.ModelForm):
    TIME_CHOICES = [
        ("", "Select reservation time"),
        ("12:00 PM", "12:00 PM (Lunch)"),
        ("12:30 PM", "12:30 PM (Lunch)"),
        ("01:00 PM", "01:00 PM (Lunch)"),
        ("01:30 PM", "01:30 PM (Lunch)"),
        ("02:00 PM", "02:00 PM (Lunch)"),
        ("02:30 PM", "02:30 PM (Lunch)"),
        ("06:30 PM", "06:30 PM (Dinner)"),
        ("07:00 PM", "07:00 PM (Dinner)"),
        ("07:30 PM", "07:30 PM (Dinner)"),
        ("08:00 PM", "08:00 PM (Dinner)"),
        ("08:30 PM", "08:30 PM (Dinner)"),
        ("09:00 PM", "09:00 PM (Dinner)"),
        ("09:30 PM", "09:30 PM (Dinner)"),
    ]

    time = forms.ChoiceField(
        choices=TIME_CHOICES,
        widget=forms.Select(attrs={"class": "form-control"}),
    )

    class Meta:
        model = Reservation
        fields = [
            "name",
            "email",
            "phone_number",
            "date",
            "time",
            "guests",
            "seating_preference",
            "special_requests",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Full Name", "class": "form-control"}),
            "email": forms.EmailInput(attrs={"placeholder": "you@example.com", "class": "form-control"}),
            "phone_number": forms.TextInput(attrs={"placeholder": "+91 98765 43210", "class": "form-control"}),
            "date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "guests": forms.NumberInput(attrs={"min": 1, "max": 20, "class": "form-control"}),
            "seating_preference": forms.Select(attrs={"class": "form-control"}),
            "special_requests": forms.Textarea(attrs={
                "rows": 3,
                "placeholder": "Celebration notes, dietary needs, or table placement preferences...",
                "class": "form-control",
            }),
        }

    def clean_date(self):
        date = self.cleaned_data.get("date")
        if date and date < timezone.localdate():
            raise forms.ValidationError("Reservation date cannot be in the past.")
        return date

    def clean_phone_number(self):
        phone = self.cleaned_data.get("phone_number", "").strip()
        digits = re.sub(r"\D", "", phone)
        if len(digits) < 7:
            raise forms.ValidationError("Please enter a valid phone number.")
        return phone


class MenuCategoryForm(forms.ModelForm):
    class Meta:
        from .models import MenuCategory
        model = MenuCategory
        fields = ["name", "description", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. Tandoori Starters", "class": "form-control"}),
            "description": forms.Textarea(attrs={
                "rows": 3,
                "placeholder": "Category description...",
                "class": "form-control",
            }),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def clean_name(self):
        name = self.cleaned_data.get("name", "").strip()
        if not name:
            raise forms.ValidationError("Category name is required.")
        return name


class MenuItemForm(forms.ModelForm):
    class Meta:
        from .models import MenuItem
        model = MenuItem
        fields = [
            "category",
            "name",
            "description",
            "price",
            "image",
            "preparation_time",
            "is_active",
        ]
        widgets = {
            "category": forms.Select(attrs={"class": "form-control"}),
            "name": forms.TextInput(attrs={"placeholder": "e.g. Andhra Chicken 65", "class": "form-control"}),
            "description": forms.Textarea(attrs={
                "rows": 3,
                "placeholder": "Dish description, aromatic spices, origin...",
                "class": "form-control",
            }),
            "price": forms.NumberInput(attrs={"min": "0", "step": "0.01", "placeholder": "290.00", "class": "form-control"}),
            "image": forms.FileInput(attrs={"class": "form-control", "accept": "image/*"}),
            "preparation_time": forms.NumberInput(attrs={"min": "1", "max": "180", "placeholder": "15", "class": "form-control"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def clean_name(self):
        name = self.cleaned_data.get("name", "").strip()
        if not name:
            raise forms.ValidationError("Menu item name is required.")
        return name

    def clean_price(self):
        price = self.cleaned_data.get("price")
        if price is None:
            raise forms.ValidationError("Price is required.")
        if price < 0:
            raise forms.ValidationError("Price must be a positive number (>= 0).")
        return price

    def clean_preparation_time(self):
        prep = self.cleaned_data.get("preparation_time")
        if prep is None:
            raise forms.ValidationError("Preparation time is required.")
        if prep < 0:
            raise forms.ValidationError("Preparation time cannot be negative.")
        return prep

    def clean_image(self):
        image = self.cleaned_data.get("image")
        if image and hasattr(image, "name"):
            valid_extensions = [".jpg", ".jpeg", ".png", ".webp", ".avif"]
            extension = image.name.lower().rsplit(".", 1)[-1] if "." in image.name else ""
            if f".{extension}" not in valid_extensions:
                raise forms.ValidationError("Invalid image format. Allowed: JPG, PNG, WEBP, AVIF.")
            # Size check: 5MB limit
            if image.size > 5 * 1024 * 1024:
                raise forms.ValidationError("Image file size exceeds the 5MB limit.")
        return image


class ChefAvailabilityForm(forms.Form):
    is_available = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs={"class": "form-check-input"}))
    availability_reason = forms.CharField(
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={
            "placeholder": "Reason if marking unavailable (e.g. stock finished, preparation prep delay)",
            "class": "form-control",
        }),
    )


class TableOpenForm(forms.Form):
    guest_name = forms.CharField(
        required=False,
        max_length=150,
        widget=forms.TextInput(attrs={
            "placeholder": "Guest / Party Name (e.g. Sharma Family)",
            "class": "form-control",
        }),
    )
    guest_count = forms.IntegerField(
        required=True,
        min_value=1,
        initial=2,
        widget=forms.NumberInput(attrs={
            "placeholder": "Number of guests",
            "class": "form-control",
        }),
    )


class PaymentProcessForm(forms.Form):
    payment_method = forms.ChoiceField(
        choices=[
            ("UPI", "UPI / QR Scanner"),
            ("CASH", "Cash"),
            ("CARD", "Credit / Debit Card"),
        ],
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    amount_paid = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
    discount_pct = forms.DecimalField(
        required=False,
        initial=0,
        min_value=0,
        max_value=100,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "0%", "step": "0.5"}),
    )
    tip_amount = forms.DecimalField(
        required=False,
        initial=0,
        min_value=0,
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "₹0.00", "step": "10"}),
    )
    transaction_id = forms.CharField(
        required=False,
        max_length=100,
        widget=forms.TextInput(attrs={
            "placeholder": "Transaction ref / UPI UTR / Card Auth code (optional)",
            "class": "form-control",
        }),
    )


class RestaurantTableForm(forms.ModelForm):
    class Meta:
        from .models import RestaurantTable
        model = RestaurantTable
        fields = ["table_number", "capacity", "floor_area", "status", "is_active"]
        widgets = {
            "table_number": forms.TextInput(attrs={"placeholder": "e.g. T-10 or 12", "class": "form-control"}),
            "capacity": forms.NumberInput(attrs={"min": 1, "max": 50, "class": "form-control"}),
            "floor_area": forms.Select(attrs={"class": "form-control"}),
            "status": forms.Select(attrs={"class": "form-control"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def clean_table_number(self):
        number = self.cleaned_data.get("table_number", "").strip()
        if not number:
            raise forms.ValidationError("Table number is required.")
        return number



