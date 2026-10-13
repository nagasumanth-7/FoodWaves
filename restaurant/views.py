import json
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout, get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.core.mail import send_mail
from django.conf import settings
from django.contrib import messages
from django.urls import reverse
from django.utils import timezone
from django.http import JsonResponse, HttpResponseForbidden

from django.contrib.auth.models import Group
from django.db.models import Q, Count

from .models import (
    UserProfile,
    Reservation,
    MenuCategory,
    MenuItem,
    RestaurantTable,
    TableSession,
    Order,
    OrderItem,
    Bill,
    Payment,
)
from .forms import (
    SignUpForm,
    LoginForm,
    ReservationForm,
    MenuCategoryForm,
    MenuItemForm,
    ChefAvailabilityForm,
    TableOpenForm,
    PaymentProcessForm,
)
from .supabase_auth import (
    supabase_sign_up,
    supabase_sign_in,
    supabase_resend_confirmation,
)
from .permissions import manager_required, chef_required, waiter_required, cashier_required
from .services.menu_service import (
    get_active_categories,
    get_active_menu_items,
    get_all_menu_items_for_manager,
    update_menu_item_availability,
    delete_or_archive_menu_item,
    validate_menu_item_availability,
)
from .services.order_service import (
    validate_cart_items,
    create_order_for_session,
    create_customer_order,
    update_order_item_status,
    get_kitchen_active_orders,
    get_pending_serves_for_waiter,
)
from .services.table_service import (
    get_all_tables,
    get_table_by_id,
    get_table_by_qr_token,
    get_active_session_for_table,
    get_or_create_active_session,
    open_table_session,
    close_table_session,
    set_table_bill_pending,
)
from .services.billing_service import (
    calculate_and_generate_bill,
    request_bill_from_customer,
    process_bill_payment,
    get_unpaid_bills,
    get_daily_cashier_summary,
)

User = get_user_model()



# ==============================================================================
# CUSTOMER FACING VIEWS
# ==============================================================================

def home(request):
    """Customer landing page."""
    return render(request, "restaurant/home.html")


def menu(request):
    """
    Customer Menu View (Section 14).
    Loads menu categories and menu items dynamically from PostgreSQL.
    """
    category_slug = request.GET.get("category", "").strip().lower()
    search_query = request.GET.get("q", "").strip()

    categories = get_active_categories()
    items_qs = get_active_menu_items(search_query=search_query)

    # Optional server-side category slug filtering
    selected_category = None
    if category_slug and category_slug != "all":
        for cat in categories:
            if cat.slug_identifier == category_slug or cat.name.lower() == category_slug:
                items_qs = items_qs.filter(category=cat)
                selected_category = cat
                break

    items_list = list(items_qs)
    total_count = len(items_list)
    available_count = sum(1 for item in items_list if item.is_available)

    # If requested via AJAX or JSON
    if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.GET.get("format") == "json":
        data = [
            {
                "id": item.id,
                "name": item.name,
                "category": item.category.name,
                "category_slug": item.category.slug_identifier,
                "price": str(item.price),
                "description": item.description,
                "preparation_time": item.preparation_time,
                "is_available": item.is_available,
                "availability_reason": item.availability_reason,
                "image_url": item.image.url if item.image else None,
            }
            for item in items_list
        ]
        return JsonResponse({"items": data, "total": total_count, "available": available_count})

    return render(
        request,
        "restaurant/menu.html",
        {
            "categories": categories,
            "items": items_list,
            "total_count": total_count,
            "available_count": available_count,
            "selected_category": selected_category,
            "search_query": search_query,
        },
    )


# ==============================================================================
# AUTHENTICATION VIEWS
# ==============================================================================

def signup_page(request):
    if request.user.is_authenticated:
        return redirect("restaurant:reservation")

    if request.method == "POST":
        form = SignUpForm(request.POST)
        if form.is_valid():
            name = form.cleaned_data["name"].strip()
            email = form.cleaned_data["email"].strip().lower()
            phone_number = form.cleaned_data["phone_number"].strip()
            password = form.cleaned_data["password"]

            request.session.pop("dev_verify_link", None)

            # If Supabase Auth is configured, register user with Supabase Auth
            if getattr(settings, "SUPABASE_ANON_KEY", None):
                callback_url = request.build_absolute_uri(reverse("restaurant:auth_callback"))
                success, data, err_msg = supabase_sign_up(
                    email=email,
                    password=password,
                    name=name,
                    phone_number=phone_number,
                    redirect_to=callback_url,
                )

                if not success:
                    messages.error(request, f"Registration failed: {err_msg}")
                    return render(request, "restaurant/signup.html", {"form": form})

                if data and isinstance(data, dict) and data.get("identities") == []:
                    messages.warning(
                        request,
                        f"An account with {email} already exists. If your account is not verified, please check your inbox or click 'Resend verification'.",
                    )
                else:
                    messages.success(
                        request,
                        f"A confirmation email has been dispatched by Supabase to {email}. You must confirm your email before signing in.",
                    )

                stale_users = User.objects.filter(email__iexact=email, is_active=False)
                stale_users.delete()

                user, created = User.objects.get_or_create(
                    username=email,
                    defaults={"email": email, "first_name": name, "is_active": False},
                )
                if not created:
                    user.first_name = name
                    user.is_active = False
                user.set_password(password)
                user.save()

                profile, _ = UserProfile.objects.get_or_create(user=user)
                profile.phone_number = phone_number
                profile.is_email_verified = False
                profile.save()

                request.session["verification_email"] = email
                return redirect("restaurant:verification_sent")
            else:
                # Fallback to local Django token verification
                stale_users = User.objects.filter(email__iexact=email, is_active=False)
                stale_users.delete()

                user = User.objects.create_user(
                    username=email,
                    email=email,
                    password=password,
                    first_name=name,
                    is_active=False,
                )

                UserProfile.objects.create(
                    user=user,
                    phone_number=phone_number,
                    is_email_verified=False,
                )

                uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
                token = default_token_generator.make_token(user)
                verify_url = request.build_absolute_uri(
                    reverse("restaurant:verify_email", kwargs={"uidb64": uidb64, "token": token})
                )

                try:
                    send_mail(
                        subject="Verify your email - Food Waves",
                        message=f"Hello {name},\n\nPlease verify your email:\n{verify_url}\n\nWarm regards,\nFood Waves Team",
                        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "Food Waves <noreply@foodwaves.com>"),
                        recipient_list=[email],
                        fail_silently=False,
                    )
                except Exception as e:
                    print(f"[FoodWaves Mail Error] {e}")

                request.session["verification_email"] = email
                messages.info(request, f"We sent a verification link to {email}.")
                return redirect("restaurant:verification_sent")
    else:
        form = SignUpForm()

    return render(request, "restaurant/signup.html", {"form": form})


def verification_sent(request):
    request.session.pop("dev_verify_link", None)
    email = request.session.get("verification_email", "")
    return render(
        request,
        "restaurant/verification_sent.html",
        {"email": email},
    )


def verify_email(request, uidb64, token):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if user is not None and default_token_generator.check_token(user, token):
        user.is_active = True
        user.save()
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.is_email_verified = True
        profile.save()

        request.session.pop("dev_verify_link", None)
        request.session.pop("verification_email", None)

        messages.success(request, "Your email has been verified! You can now log in.")
        return render(request, "restaurant/verify_email.html", {"success": True, "user": user})
    else:
        return render(request, "restaurant/verify_email.html", {"success": False})


def resend_verification(request):
    email = request.POST.get("email") or request.GET.get("email") or request.session.get("verification_email")
    if email:
        email = email.strip().lower()

        if getattr(settings, "SUPABASE_ANON_KEY", None):
            callback_url = request.build_absolute_uri(reverse("restaurant:auth_callback"))
            success, err_msg = supabase_resend_confirmation(email, redirect_to=callback_url)
            if success:
                messages.success(request, f"A new confirmation email has been dispatched by Supabase to {email}.")
            else:
                messages.error(request, f"Resend failed: {err_msg}")
        else:
            user = User.objects.filter(email__iexact=email, is_active=False).first()
            if user:
                uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
                token = default_token_generator.make_token(user)
                verify_url = request.build_absolute_uri(
                    reverse("restaurant:verify_email", kwargs={"uidb64": uidb64, "token": token})
                )
                try:
                    send_mail(
                        subject="Verify your email - Food Waves",
                        message=f"Hello,\n\nPlease verify your email:\n{verify_url}",
                        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "Food Waves <noreply@foodwaves.com>"),
                        recipient_list=[email],
                        fail_silently=False,
                    )
                except Exception as e:
                    print(f"[FoodWaves Mail Error] {e}")
                messages.success(request, f"A new verification link has been sent to {email}.")
            else:
                messages.info(request, "If an inactive account exists, a link has been sent.")
    else:
        messages.error(request, "Please provide an email address to resend the verification link.")

    return redirect("restaurant:verification_sent")


def auth_callback(request):
    """Callback endpoint triggered when user clicks the Supabase email confirmation link."""
    messages.success(request, "Your email address has been confirmed! Please sign in to continue.")
    return redirect("restaurant:login")


def get_user_home_url(user, next_url=None):
    """
    Returns the dedicated interface URL for a user based strictly on their assigned role:
    - WAITER -> /waiter/ (only allowed to proceed to next_url if next_url starts with /waiter/)
    - CHEF -> /chef/dashboard/ or /chef/kds/ (only allowed to proceed to next_url if next_url starts with /chef/)
    - CASHIER -> /cashier/ (only allowed to proceed to next_url if next_url starts with /cashier/)
    - MANAGER -> /manager/ (only allowed to proceed to next_url if next_url starts with /manager/)
    - ADMIN / SUPERUSER -> /manager/ (or valid next_url)
    - Regular Customer -> /reserve/ (or customer-facing next_url)
    Never redirects a staff member to another staff member's interface or to customer pages by mistake.
    """
    if not user.is_authenticated:
        return reverse("restaurant:home")

    if user.is_superuser or user.groups.filter(name="ADMIN").exists():
        if next_url and next_url.startswith("/") and not next_url.startswith("/login"):
            return next_url
        return reverse("restaurant:manager_dashboard")

    if user.groups.filter(name="WAITER").exists():
        if next_url and next_url.startswith("/waiter/"):
            return next_url
        return reverse("restaurant:waiter_dashboard")

    if user.groups.filter(name="CHEF").exists():
        if next_url and next_url.startswith("/chef/"):
            return next_url
        return reverse("restaurant:chef_dashboard")

    if user.groups.filter(name="CASHIER").exists():
        if next_url and next_url.startswith("/cashier/"):
            return next_url
        return reverse("restaurant:cashier_dashboard")

    if user.groups.filter(name="MANAGER").exists():
        if next_url and next_url.startswith("/manager/"):
            return next_url
        return reverse("restaurant:manager_dashboard")

    # Regular Customer (no staff group)
    if next_url and next_url.startswith("/") and not any(
        next_url.startswith(p) for p in ["/manager/", "/chef/", "/waiter/", "/cashier/", "/admin/", "/login"]
    ):
        return next_url
    return reverse("restaurant:reservation")


def login_page(request):
    if request.user.is_authenticated:
        return redirect(get_user_home_url(request.user))

    next_url = request.GET.get("next") or request.POST.get("next") or ""
    unverified_email = None

    if request.method == "POST":
        form = LoginForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"].strip().lower()
            password = form.cleaned_data["password"]
            remember_me = form.cleaned_data.get("remember_me", True)

            # Direct authentication for Staff, Managers, Chefs, Waiters, Cashiers, and Superusers
            local_user = (
                User.objects.filter(email__iexact=email).first()
                or User.objects.filter(username__iexact=email).first()
            )
            if (
                local_user
                and local_user.is_active
                and local_user.check_password(password)
                and (
                    local_user.is_staff
                    or local_user.is_superuser
                    or local_user.groups.filter(name__in=["MANAGER", "CHEF", "WAITER", "CASHIER", "ADMIN"]).exists()
                )
            ):
                login(request, local_user, backend="django.contrib.auth.backends.ModelBackend")
                if not remember_me:
                    request.session.set_expiry(0)
                messages.success(request, f"Welcome back, {local_user.first_name or local_user.username}!")
                return redirect(get_user_home_url(local_user, next_url=next_url))

            # Supabase Auth Authentication
            if getattr(settings, "SUPABASE_ANON_KEY", None):
                success, data_or_code, err_msg = supabase_sign_in(email, password)

                if not success:
                    if data_or_code == "email_not_confirmed":
                        unverified_email = email
                        messages.warning(
                            request,
                            "Email confirmation required: Users must confirm their email address before signing in for the first time. Please check your inbox for the confirmation link sent by Supabase.",
                        )
                        return render(
                            request,
                            "restaurant/login.html",
                            {"form": form, "next": next_url, "unverified_email": unverified_email},
                        )
                    else:
                        messages.error(request, err_msg or "Invalid email or password. Please try again.")
                        return render(
                            request,
                            "restaurant/login.html",
                            {"form": form, "next": next_url},
                        )

                supabase_user = data_or_code.get("user", {})
                user_meta = supabase_user.get("user_metadata", {})
                name = user_meta.get("name") or ""
                phone = user_meta.get("phone_number") or ""

                django_user, created = User.objects.get_or_create(
                    username=email,
                    defaults={"email": email, "first_name": name, "is_active": True},
                )
                django_user.is_active = True
                if name and not django_user.first_name:
                    django_user.first_name = name
                django_user.set_password(password)
                django_user.save()

                profile, _ = UserProfile.objects.get_or_create(user=django_user)
                profile.is_email_verified = True
                if phone and not profile.phone_number:
                    profile.phone_number = phone
                profile.save()

                login(request, django_user, backend="django.contrib.auth.backends.ModelBackend")
                request.session["supabase_token"] = data_or_code.get("access_token")

                if not remember_me:
                    request.session.set_expiry(0)

                messages.success(request, f"Welcome back, {django_user.first_name or django_user.email}!")
                return redirect(get_user_home_url(django_user, next_url=next_url))

            else:
                # Fallback to local Django authentication
                inactive_user = User.objects.filter(email__iexact=email, is_active=False).first()
                if inactive_user and inactive_user.check_password(password):
                    unverified_email = email
                    messages.warning(
                        request,
                        "Your email address is not verified yet. Please check your inbox or resend the verification link below.",
                    )
                    return render(
                        request,
                        "restaurant/login.html",
                        {"form": form, "next": next_url, "unverified_email": unverified_email},
                    )

                user = authenticate(request, email=email, password=password)
                if user is not None:
                    login(request, user)
                    if not remember_me:
                        request.session.set_expiry(0)
                    messages.success(request, f"Welcome back, {user.first_name or user.email}!")
                    return redirect(get_user_home_url(user, next_url=next_url))
                else:
                    messages.error(request, "Invalid email or password. Please try again.")
    else:
        form = LoginForm()

    return render(
        request,
        "restaurant/login.html",
        {"form": form, "next": next_url, "unverified_email": unverified_email},
    )


def logout_view(request):
    logout(request)
    request.session.flush()
    messages.info(request, "You have been logged out successfully.")
    return redirect("restaurant:home")


def custom_permission_denied_view(request, exception=None):
    """
    Renders custom 403 forbidden page explaining staff interface isolation.
    """
    return render(request, "403.html", {"exception": exception}, status=403)


# ==============================================================================
# TABLE RESERVATION VIEWS
# ==============================================================================

def reservation(request):
    initial_data = {}
    if request.user.is_authenticated:
        initial_data["name"] = request.user.get_full_name() or request.user.first_name
        initial_data["email"] = request.user.email
        if hasattr(request.user, "profile"):
            initial_data["phone_number"] = request.user.profile.phone_number

    if request.method == "POST":
        if not request.user.is_authenticated:
            messages.warning(request, "Please log in to pre-book a table.")
            return redirect(f"{reverse('restaurant:login')}?next={reverse('restaurant:reservation')}")

        form = ReservationForm(request.POST)
        if form.is_valid():
            booking = form.save(commit=False)
            booking.user = request.user
            booking.save()

            messages.success(
                request,
                f"Table pre-booked successfully! Reservation reference #{booking.id} for {booking.date} at {booking.time}.",
            )
            return redirect("restaurant:booking_history")
    else:
        form = ReservationForm(initial=initial_data)

    min_date = timezone.localdate().isoformat()
    return render(
        request,
        "restaurant/reservation.html",
        {
            "form": form,
            "min_date": min_date,
            "is_authenticated": request.user.is_authenticated,
        },
    )


@login_required(login_url="restaurant:login")
def booking_history(request):
    today = timezone.localdate()
    user_reservations = Reservation.objects.filter(user=request.user)

    upcoming_bookings = user_reservations.filter(date__gte=today).order_by("date", "time")
    past_bookings = user_reservations.filter(date__lt=today).order_by("-date", "-time")

    return render(
        request,
        "restaurant/booking_history.html",
        {
            "upcoming_bookings": upcoming_bookings,
            "past_bookings": past_bookings,
            "total_count": user_reservations.count(),
        },
    )


@login_required(login_url="restaurant:login")
def cancel_booking(request, booking_id):
    booking = get_object_or_404(Reservation, id=booking_id, user=request.user)

    if request.method == "POST":
        if booking.can_cancel:
            booking.status = "CANCELLED"
            booking.save()
            messages.success(request, f"Reservation #{booking.id} has been cancelled.")
        else:
            messages.error(request, "This reservation cannot be cancelled.")

    return redirect("restaurant:booking_history")


# ==============================================================================
# MANAGER DASHBOARD & MENU CRUD (Sections 8 - 11)
# ==============================================================================

@manager_required
def manager_dashboard(request):
    """
    Manager overview portal (Section 8).
    """
    total_items = MenuItem.objects.count()
    active_items = MenuItem.objects.filter(is_active=True).count()
    available_items = MenuItem.objects.filter(is_active=True, is_available=True).count()
    unavailable_items = MenuItem.objects.filter(is_active=True, is_available=False).count()
    archived_items = MenuItem.objects.filter(is_active=False).count()
    total_categories = MenuCategory.objects.count()

    recent_items = MenuItem.objects.select_related("category").order_by("-updated_at")[:6]

    return render(
        request,
        "restaurant/manager/dashboard.html",
        {
            "total_items": total_items,
            "active_items": active_items,
            "available_items": available_items,
            "unavailable_items": unavailable_items,
            "archived_items": archived_items,
            "total_categories": total_categories,
            "recent_items": recent_items,
        },
    )


@manager_required
def manager_menu_list(request):
    """
    Manager Menu Management screen (Section 8).
    Lists menu items with categories filter, search, and action controls.
    """
    category_id = request.GET.get("category")
    status_filter = request.GET.get("status")
    search_query = request.GET.get("q", "")

    items = get_all_menu_items_for_manager(
        category_id=category_id,
        status_filter=status_filter,
        search_query=search_query,
    )
    categories = MenuCategory.objects.all().order_by("name")

    return render(
        request,
        "restaurant/manager/menu.html",
        {
            "items": items,
            "categories": categories,
            "selected_category": int(category_id) if category_id and category_id.isdigit() else None,
            "selected_status": status_filter,
            "search_query": search_query,
            "total_count": items.count(),
        },
    )


@manager_required
def manager_menu_add(request):
    """
    Manager Add Menu Item view (Section 9).
    """
    if request.method == "POST":
        form = MenuItemForm(request.POST, request.FILES)
        if form.is_valid():
            item = form.save()
            messages.success(request, f"Menu item '{item.name}' added successfully.")
            return redirect("restaurant:manager_menu_list")
    else:
        form = MenuItemForm()

    return render(
        request,
        "restaurant/manager/menu_form.html",
        {
            "form": form,
            "is_edit": False,
            "page_title": "Add New Menu Item",
        },
    )


@manager_required
def manager_menu_edit(request, item_id):
    """
    Manager Edit Menu Item view (Section 10).
    """
    item = get_object_or_404(MenuItem, pk=item_id)

    if request.method == "POST":
        form = MenuItemForm(request.POST, request.FILES, instance=item)
        if form.is_valid():
            was_available = item.is_available
            updated_item = form.save(commit=False)

            # Track availability change
            if was_available != updated_item.is_available:
                updated_item.availability_updated_at = timezone.now()
                updated_item.availability_updated_by = request.user

            updated_item.save()
            messages.success(request, f"Menu item '{updated_item.name}' updated successfully.")
            return redirect("restaurant:manager_menu_list")
    else:
        form = MenuItemForm(instance=item)

    return render(
        request,
        "restaurant/manager/menu_form.html",
        {
            "form": form,
            "item": item,
            "is_edit": True,
            "page_title": f"Edit {item.name}",
        },
    )


@manager_required
def manager_menu_delete(request, item_id):
    """
    Manager Delete/Archive Menu Item view (Sections 11 & 32).
    Permanently deletes if unused; archives if historical orders exist.
    """
    item = get_object_or_404(MenuItem, pk=item_id)
    has_orders = item.has_historical_orders

    if request.method == "POST":
        item_name = item.name
        action_taken = delete_or_archive_menu_item(item)

        if action_taken == "archived":
            messages.info(
                request,
                f"'{item_name}' has historical orders and was archived instead of permanently deleted.",
            )
        else:
            messages.success(request, f"Menu item '{item_name}' was permanently deleted.")

        return redirect("restaurant:manager_menu_list")

    return render(
        request,
        "restaurant/manager/menu_confirm_delete.html",
        {
            "item": item,
            "has_orders": has_orders,
        },
    )


# ==============================================================================
# CHEF DASHBOARD & MENU AVAILABILITY (Sections 12 & 13)
# ==============================================================================

@chef_required
def chef_dashboard(request):
    """Chef overview portal."""
    active_items = MenuItem.objects.filter(is_active=True)
    available_count = active_items.filter(is_available=True).count()
    unavailable_count = active_items.filter(is_available=False).count()
    unavailable_items = active_items.filter(is_available=False).select_related("category")

    return render(
        request,
        "restaurant/chef/dashboard.html",
        {
            "total_active": active_items.count(),
            "available_count": available_count,
            "unavailable_count": unavailable_count,
            "unavailable_items": unavailable_items,
        },
    )


@chef_required
def chef_menu_list(request):
    """
    Chef Menu Availability view (Section 12).
    Shows all active menu items with availability controls.
    """
    category_id = request.GET.get("category")
    status_filter = request.GET.get("status")
    search_query = request.GET.get("q", "")

    items = MenuItem.objects.filter(is_active=True).select_related("category")
    if category_id and category_id.isdigit():
        items = items.filter(category_id=int(category_id))
    if status_filter == "available":
        items = items.filter(is_available=True)
    elif status_filter == "unavailable":
        items = items.filter(is_available=False)
    if search_query:
        query = search_query.strip()
        items = items.filter(name__icontains=query)

    categories = MenuCategory.objects.filter(is_active=True).order_by("name")

    return render(
        request,
        "restaurant/chef/menu.html",
        {
            "items": items.order_by("category__name", "name"),
            "categories": categories,
            "selected_category": int(category_id) if category_id and category_id.isdigit() else None,
            "selected_status": status_filter,
            "search_query": search_query,
        },
    )


@chef_required
def chef_menu_toggle_availability(request, item_id):
    """
    Chef marks an item as available or unavailable (Section 13).
    Chef CANNOT delete, change price, or change category.
    """
    item = get_object_or_404(MenuItem, pk=item_id, is_active=True)

    if request.method == "POST":
        action = request.POST.get("action")
        reason = request.POST.get("reason", "").strip()

        if action == "mark_available":
            update_menu_item_availability(item, is_available=True, reason="", user=request.user)
            messages.success(request, f"'{item.name}' marked as AVAILABLE.")
        elif action == "mark_unavailable":
            update_menu_item_availability(item, is_available=False, reason=reason, user=request.user)
            reason_display = f" (Reason: {reason})" if reason else ""
            messages.warning(request, f"'{item.name}' marked as UNAVAILABLE{reason_display}.")

    return redirect("restaurant:chef_menu_list")


# ==============================================================================
# CART & ORDER VALIDATION API (Sections 15 & 16)
# ==============================================================================

def api_validate_cart(request):
    """
    Server-side validation for cart items to prevent ordering unavailable/archived items (Section 15 & 16).
    Accepts POST with JSON array: [{'item_id': 1, 'quantity': 2}, ...]
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        data = json.loads(request.body.decode("utf-8"))
        cart_items = data.get("items", [])
    except Exception:
        return JsonResponse({"error": "Invalid JSON payload"}, status=400)

    is_valid, errors = validate_cart_items(cart_items)
    if not is_valid:
        return JsonResponse({"valid": False, "errors": errors}, status=400)

    return JsonResponse({"valid": True, "message": "All items are currently active and available."})


# ==============================================================================
# FLOOR WAITER VIEWS
# ==============================================================================

@waiter_required
def waiter_dashboard(request):
    """
    Floor Waiter Dashboard:
    - Overview of tables (Available, Occupied, Reserved, Bill Pending)
    - Filter by status and floor area
    - Ready-to-serve dish notifications
    """
    status_filter = request.GET.get("status", "ALL")
    area_filter = request.GET.get("area", "ALL")

    tables = get_all_tables(status_filter=status_filter, area_filter=area_filter)
    all_tables = get_all_tables()

    available_count = all_tables.filter(status="AVAILABLE").count()
    occupied_count = all_tables.filter(status="OCCUPIED").count()
    bill_pending_count = all_tables.filter(status="BILL_PENDING").count()
    reserved_count = all_tables.filter(status="RESERVED").count()

    pending_serves = get_pending_serves_for_waiter()

    return render(
        request,
        "restaurant/waiter/dashboard.html",
        {
            "tables": tables,
            "status_filter": status_filter,
            "area_filter": area_filter,
            "available_count": available_count,
            "occupied_count": occupied_count,
            "bill_pending_count": bill_pending_count,
            "reserved_count": reserved_count,
            "pending_serves": pending_serves,
        },
    )


@waiter_required
def waiter_table_detail(request, table_id):
    """
    Detailed Table & Session view for Waiters.
    Displays party details, placed orders, item preparation statuses, and bills.
    """
    table = get_table_by_id(table_id)
    session = get_active_session_for_table(table.id)
    orders = session.orders.prefetch_related("items__menu_item").order_by("-created_at") if session else []
    bill = getattr(session, "bill", None) if session else None
    open_form = TableOpenForm()

    return render(
        request,
        "restaurant/waiter/table_detail.html",
        {
            "table": table,
            "session": session,
            "orders": orders,
            "bill": bill,
            "open_form": open_form,
        },
    )


@waiter_required
def waiter_table_open(request, table_id):
    """
    Opens a new session on an available or reserved table.
    """
    table = get_table_by_id(table_id)
    if request.method == "POST":
        form = TableOpenForm(request.POST)
        if form.is_valid():
            guest_name = form.cleaned_data.get("guest_name", "")
            guest_count = form.cleaned_data.get("guest_count", 2)
            try:
                session = open_table_session(table, guest_name=guest_name, guest_count=guest_count, waiter_user=request.user)
                messages.success(request, f"Table {table.table_number} seated successfully for {session.guest_name}!")
            except Exception as e:
                messages.error(request, str(e))
        else:
            messages.error(request, "Please enter valid party details.")
    return redirect("restaurant:waiter_table_detail", table_id=table.id)


@waiter_required
def waiter_take_order(request, table_id):
    """
    Table-Side Ordering Interface for Waiters:
    Displays dynamic menu, checks availability, adds special instructions.
    """
    table = get_table_by_id(table_id)
    session = get_active_session_for_table(table.id)
    if not session:
        messages.error(request, f"Table {table.table_number} does not have an active session.")
        return redirect("restaurant:waiter_table_detail", table_id=table.id)

    categories = get_active_categories()
    items = get_active_menu_items()

    if request.method == "POST":
        items_data = []
        for key, value in request.POST.items():
            if key.startswith("qty_") and value:
                try:
                    qty = int(value)
                    if qty > 0:
                        item_id = int(key.replace("qty_", ""))
                        notes = request.POST.get(f"notes_{item_id}", "").strip()
                        items_data.append({
                            "item_id": item_id,
                            "quantity": qty,
                            "notes": notes,
                        })
                except ValueError:
                    pass

        instructions = request.POST.get("instructions", "").strip()
        if not items_data:
            messages.error(request, "Please select at least one dish quantity.")
        else:
            try:
                order = create_order_for_session(
                    session=session,
                    items_data=items_data,
                    placed_by=request.user,
                    source="WAITER",
                    instructions=instructions,
                )
                messages.success(request, f"Order #{order.id} sent to the kitchen ({len(items_data)} items)!")
                return redirect("restaurant:waiter_table_detail", table_id=table.id)
            except Exception as e:
                messages.error(request, f"Could not place order: {e}")

    return render(
        request,
        "restaurant/waiter/take_order.html",
        {
            "table": table,
            "session": session,
            "categories": categories,
            "items": items,
        },
    )


@waiter_required
def waiter_serve_item(request, item_id):
    """
    Marks an OrderItem as SERVED to the table.
    """
    if request.method == "POST":
        try:
            item = update_order_item_status(item_id, new_status="SERVED", updated_by=request.user)
            messages.success(request, f"Marked served: {item.quantity}x {item.menu_item.name} to Table {item.order.session.table.table_number}!")
        except Exception as e:
            messages.error(request, str(e))
    next_url = request.POST.get("next") or reverse("restaurant:waiter_dashboard")
    return redirect(next_url)


@waiter_required
def waiter_request_bill(request, table_id):
    """
    Calculates subtotal and transitions table to BILL_PENDING for cashier settlement.
    """
    table = get_table_by_id(table_id)
    session = get_active_session_for_table(table.id)
    if not session:
        messages.error(request, "No active session found for this table.")
        return redirect("restaurant:waiter_table_detail", table_id=table.id)

    try:
        bill = calculate_and_generate_bill(session, bill_status="REQUESTED")
        set_table_bill_pending(table.id)
        messages.success(request, f"Bill generated for Table {table.table_number} (₹{bill.total}). Sent to cashier for checkout.")
    except Exception as e:
        messages.error(request, f"Failed to generate bill: {e}")

    return redirect("restaurant:waiter_table_detail", table_id=table.id)


# ==============================================================================
# CASHIER POS & BILLING VIEWS
# ==============================================================================

@cashier_required
def cashier_dashboard(request):
    """
    Cashier POS Terminal:
    - Lists unpaid bills and tables waiting for settlement
    - Quick revenue metrics and payment methods overview
    """
    unpaid_bills = get_unpaid_bills()
    occupied_tables = RestaurantTable.objects.filter(is_active=True, status__in=["OCCUPIED", "BILL_PENDING"]).order_by("table_number")
    summary = get_daily_cashier_summary()

    return render(
        request,
        "restaurant/cashier/dashboard.html",
        {
            "unpaid_bills": unpaid_bills,
            "occupied_tables": occupied_tables,
            "summary": summary,
        },
    )


@cashier_required
def cashier_settle_bill(request, bill_id):
    """
    Cashier Checkout & Settlement:
    Calculates 5% GST, discounts, tips, and records payments (Cash, UPI, Card).
    """
    bill = get_object_or_404(Bill.objects.select_related("session__table", "session__waiter"), pk=bill_id)
    session = bill.session
    orders = session.orders.prefetch_related("items__menu_item").exclude(status="CANCELLED")

    if request.method == "POST":
        form = PaymentProcessForm(request.POST)
        if form.is_valid():
            method = form.cleaned_data["payment_method"]
            amount_paid = form.cleaned_data["amount_paid"]
            discount_pct = form.cleaned_data.get("discount_pct") or Decimal("0.00")
            tip_amount = form.cleaned_data.get("tip_amount") or Decimal("0.00")
            tx_id = form.cleaned_data.get("transaction_id", "")

            # Re-generate bill if discount or tip adjusted
            bill = calculate_and_generate_bill(
                session=session,
                cashier=request.user,
                discount_pct=discount_pct,
                tip_amount=tip_amount,
            )

            try:
                payment = process_bill_payment(
                    bill=bill,
                    payment_method=method,
                    amount_paid=amount_paid,
                    transaction_id=tx_id,
                    processed_by=request.user,
                )
                messages.success(request, f"Payment of ₹{payment.amount} settled via {payment.get_payment_method_display()}! Table {session.table.table_number} is now Available.")
                return redirect("restaurant:cashier_receipt", bill_id=bill.id)
            except Exception as e:
                messages.error(request, f"Payment error: {e}")
        else:
            messages.error(request, "Please check the payment form inputs.")
    else:
        form = PaymentProcessForm(initial={
            "amount_paid": bill.total,
            "discount_pct": bill.discount,
            "tip_amount": bill.tip,
        })

    return render(
        request,
        "restaurant/cashier/settle_bill.html",
        {
            "bill": bill,
            "session": session,
            "orders": orders,
            "form": form,
        },
    )


@cashier_required
def cashier_generate_bill(request, table_id):
    """
    Generates a bill for an active table session and directs to checkout.
    """
    table = get_table_by_id(table_id)
    session = get_active_session_for_table(table.id)
    if not session:
        messages.error(request, f"No active session on Table {table.table_number}.")
        return redirect("restaurant:cashier_dashboard")

    try:
        bill = calculate_and_generate_bill(session, cashier=request.user)
        return redirect("restaurant:cashier_settle_bill", bill_id=bill.id)
    except Exception as e:
        messages.error(request, f"Could not generate bill: {e}")
        return redirect("restaurant:cashier_dashboard")


@cashier_required
def cashier_receipt(request, bill_id):
    """
    Printable luxury receipt view for paid bills.
    """
    bill = get_object_or_404(Bill.objects.select_related("session__table", "session__waiter", "cashier"), pk=bill_id)
    payments = bill.payments.all()
    session = bill.session
    orders = session.orders.prefetch_related("items__menu_item").exclude(status="CANCELLED")

    return render(
        request,
        "restaurant/cashier/receipt.html",
        {
            "bill": bill,
            "session": session,
            "orders": orders,
            "payments": payments,
        },
    )


@cashier_required
def cashier_reports(request):
    """
    Cashier daily sales report with revenue breakdown and payment journal.
    """
    date_str = request.GET.get("date")
    target_date = None
    if date_str:
        try:
            target_date = timezone.datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            target_date = timezone.localdate()
    else:
        target_date = timezone.localdate()

    summary = get_daily_cashier_summary(target_date)
    return render(
        request,
        "restaurant/cashier/reports.html",
        {
            "summary": summary,
            "selected_date": target_date.strftime("%Y-%m-%d"),
        },
    )


# ==============================================================================
# CHEF KITCHEN DISPLAY SYSTEM (KDS) ENHANCEMENTS
# ==============================================================================

@chef_required
def chef_kds(request):
    """
    Chef Kitchen Display System:
    Live tickets with dish preparation status and advance controls.
    """
    orders = get_kitchen_active_orders()
    return render(
        request,
        "restaurant/chef/kds.html",
        {
            "orders": orders,
        },
    )


@chef_required
def chef_item_status(request, item_id):
    """
    Chef updates dish status from PREPARING to READY.
    """
    if request.method == "POST":
        new_status = request.POST.get("status")
        try:
            item = update_order_item_status(item_id, new_status, updated_by=request.user)
            messages.success(request, f"{item.menu_item.name} for Table {item.order.session.table.table_number} marked as {new_status}!")
        except Exception as e:
            messages.error(request, str(e))
    return redirect("restaurant:chef_kds")


# ==============================================================================
# CUSTOMER QR CODE ORDERING & TABLE SESSIONS (Sections 2, 10, 14-16, 18)
# ==============================================================================

def table_menu(request, qr_token):
    """
    Customer QR Table Menu View (Sections 2, 10, 14):
    Resolves table by qr_token, retrieves or starts active table session,
    and displays categories and items with live availability.
    """
    table = get_table_by_qr_token(qr_token)
    if table.status == "OUT_OF_SERVICE":
        return render(
            request,
            "restaurant/customer/table_out_of_service.html",
            {"table": table},
            status=503,
        )

    customer_user = request.user if request.user.is_authenticated else None
    session = get_or_create_active_session(table=table, customer=customer_user)

    category_slug = request.GET.get("category", "").strip().lower()
    search_query = request.GET.get("q", "").strip()

    categories = get_active_categories()
    items_qs = get_active_menu_items(search_query=search_query)

    selected_category = None
    if category_slug and category_slug != "all":
        for cat in categories:
            if cat.slug_identifier == category_slug or cat.name.lower() == category_slug:
                items_qs = items_qs.filter(category=cat)
                selected_category = cat
                break

    items_list = list(items_qs)
    session_orders = session.orders.exclude(status="CANCELLED")
    active_orders_count = session_orders.count()

    return render(
        request,
        "restaurant/customer/table_menu.html",
        {
            "table": table,
            "session": session,
            "categories": categories,
            "items": items_list,
            "selected_category": selected_category,
            "search_query": search_query,
            "qr_token": qr_token,
            "active_orders_count": active_orders_count,
            "session_subtotal": session.running_subtotal,
        },
    )


def customer_cart(request, qr_token):
    """
    Customer Cart View (Section 15 & 16):
    Review items in order before submitting to kitchen.
    """
    table = get_table_by_qr_token(qr_token)
    session = get_active_session_for_table(table.id)
    if not session:
        session = get_or_create_active_session(table, customer=request.user if request.user.is_authenticated else None)

    return render(
        request,
        "restaurant/customer/cart.html",
        {
            "table": table,
            "session": session,
            "qr_token": qr_token,
        },
    )


def customer_place_order(request, qr_token):
    """
    Customer Place Order Action:
    Processes order placement from QR session, validating item availability and prices server-side.
    Accepts both application/json and form-encoded POST.
    """
    if request.method != "POST":
        return redirect("restaurant:table_menu", qr_token=qr_token)

    table = get_table_by_qr_token(qr_token)
    session = get_active_session_for_table(table.id)
    if not session:
        session = get_or_create_active_session(table, customer=request.user if request.user.is_authenticated else None)

    items_data = []
    instructions = ""

    if request.content_type == "application/json" or (request.body and request.body.startswith(b"{")):
        try:
            payload = json.loads(request.body.decode("utf-8"))
            items_data = payload.get("items", [])
            instructions = payload.get("instructions", "").strip()
        except Exception:
            return JsonResponse({"status": "error", "message": "Invalid JSON format"}, status=400)
    else:
        instructions = request.POST.get("instructions", "").strip()
        for key, val in request.POST.items():
            if key.startswith("qty_") and val:
                try:
                    qty = int(val)
                    if qty > 0:
                        item_id = int(key.replace("qty_", ""))
                        notes = request.POST.get(f"notes_{item_id}", "").strip()
                        items_data.append({"item_id": item_id, "quantity": qty, "notes": notes})
                except ValueError:
                    pass

    if not items_data:
        if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.content_type == "application/json":
            return JsonResponse({"status": "error", "message": "No valid items selected."}, status=400)
        messages.error(request, "Please select at least one item to order.")
        return redirect("restaurant:customer_cart", qr_token=qr_token)

    try:
        order = create_customer_order(
            session=session,
            items_data=items_data,
            customer_user=request.user if request.user.is_authenticated else None,
            instructions=instructions,
        )
        if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.content_type == "application/json":
            return JsonResponse({
                "status": "success",
                "order_id": order.id,
                "message": f"Order #{order.id} sent to the kitchen!",
                "redirect_url": reverse("restaurant:customer_orders", kwargs={"qr_token": qr_token}),
            })
        messages.success(request, f"Order #{order.id} has been sent to the kitchen!")
        return redirect("restaurant:customer_orders", qr_token=qr_token)
    except Exception as e:
        if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.content_type == "application/json":
            return JsonResponse({"status": "error", "message": str(e)}, status=400)
        messages.error(request, f"Unable to place order: {e}")
        return redirect("restaurant:customer_cart", qr_token=qr_token)


def customer_orders(request, qr_token):
    """
    Customer Live Orders Tracker:
    Shows all orders placed for this table session, their live kitchen status,
    and estimated cooking times.
    """
    table = get_table_by_qr_token(qr_token)
    session = get_active_session_for_table(table.id)
    if not session:
        messages.info(request, "No active dining session found.")
        return redirect("restaurant:table_menu", qr_token=qr_token)

    orders = session.orders.prefetch_related("items__menu_item").order_by("-created_at")

    if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.GET.get("format") == "json":
        data = []
        for o in orders:
            items = []
            for item in o.items.all():
                items.append({
                    "id": item.id,
                    "name": item.menu_item.name,
                    "quantity": item.quantity,
                    "status": item.status,
                    "unit_price": str(item.unit_price),
                    "total_price": str(item.total_price),
                    "estimated_minutes": item.estimated_minutes,
                })
            data.append({
                "id": o.id,
                "status": o.status,
                "created_at": o.created_at.strftime("%H:%M"),
                "items": items,
            })
        return JsonResponse({"orders": data, "session_status": session.status})

    return render(
        request,
        "restaurant/customer/orders.html",
        {
            "table": table,
            "session": session,
            "orders": orders,
            "qr_token": qr_token,
        },
    )


def customer_bill(request, qr_token):
    """
    Customer Bill View:
    Displays consolidated session bill with items, 5% GST, discounts, and payment status.
    Allows requesting the bill to notify floor waiter and cashier.
    """
    table = get_table_by_qr_token(qr_token)
    session = get_active_session_for_table(table.id)
    if not session:
        messages.info(request, "No active dining session found.")
        return redirect("restaurant:table_menu", qr_token=qr_token)

    bill = None
    orders = session.orders.prefetch_related("items__menu_item").exclude(status="CANCELLED")

    if session.running_subtotal > Decimal("0.00"):
        bill = calculate_and_generate_bill(session)

    return render(
        request,
        "restaurant/customer/bill.html",
        {
            "table": table,
            "session": session,
            "bill": bill,
            "orders": orders,
            "qr_token": qr_token,
        },
    )


def customer_request_bill(request, qr_token):
    """
    Customer requests the bill (Sections 18 & 43).
    Marks Bill -> REQUESTED and Table -> BILL_PENDING.
    """
    if request.method != "POST":
        return redirect("restaurant:customer_bill", qr_token=qr_token)

    table = get_table_by_qr_token(qr_token)
    session = get_active_session_for_table(table.id)
    if not session:
        messages.error(request, "No active session for this table.")
        return redirect("restaurant:table_menu", qr_token=qr_token)

    try:
        bill = request_bill_from_customer(session)
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "status": "success",
                "message": "Bill requested! A waiter or cashier will assist you shortly.",
                "total": str(bill.total),
            })
        messages.success(request, f"Bill of ₹{bill.total} requested! A staff member will assist you shortly.")
    except Exception as e:
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"status": "error", "message": str(e)}, status=400)
        messages.error(request, f"Could not request bill: {e}")

    return redirect("restaurant:customer_bill", qr_token=qr_token)


# ==============================================================================
# MANAGER CATEGORIES & STAFF ROLE MANAGEMENT
# ==============================================================================

@manager_required
def manager_categories_list(request):
    """
    Manager Categories Management:
    Lists all categories with item counts and status controls.
    """
    categories = MenuCategory.objects.annotate(item_count=Count("items")).order_by("name")
    return render(
        request,
        "restaurant/manager/categories.html",
        {
            "categories": categories,
        },
    )


@manager_required
def manager_category_add(request):
    """Manager adds a new menu category."""
    if request.method == "POST":
        form = MenuCategoryForm(request.POST)
        if form.is_valid():
            cat = form.save()
            messages.success(request, f"Category '{cat.name}' created successfully.")
            return redirect("restaurant:manager_categories_list")
    else:
        form = MenuCategoryForm()

    return render(
        request,
        "restaurant/manager/category_form.html",
        {
            "form": form,
            "is_edit": False,
            "page_title": "Add Menu Category",
        },
    )


@manager_required
def manager_category_edit(request, category_id):
    """Manager edits an existing menu category."""
    category = get_object_or_404(MenuCategory, pk=category_id)
    if request.method == "POST":
        form = MenuCategoryForm(request.POST, instance=category)
        if form.is_valid():
            cat = form.save()
            messages.success(request, f"Category '{cat.name}' updated successfully.")
            return redirect("restaurant:manager_categories_list")
    else:
        form = MenuCategoryForm(instance=category)

    return render(
        request,
        "restaurant/manager/category_form.html",
        {
            "form": form,
            "category": category,
            "is_edit": True,
            "page_title": f"Edit Category: {category.name}",
        },
    )


@manager_required
def manager_category_toggle(request, category_id):
    """Manager toggles is_active for a category."""
    if request.method == "POST":
        category = get_object_or_404(MenuCategory, pk=category_id)
        category.is_active = not category.is_active
        category.save(update_fields=["is_active", "updated_at"])
        status_label = "activated" if category.is_active else "deactivated"
        messages.success(request, f"Category '{category.name}' has been {status_label}.")
    return redirect("restaurant:manager_categories_list")


@manager_required
def manager_staff_roles(request):
    """
    Manager Staff Role Management:
    Allows Managers and Admins to view users and assign staff roles
    (WAITER, CHEF, CASHIER, MANAGER, ADMIN, CUSTOMER).
    """
    roles = ["WAITER", "CHEF", "CASHIER", "MANAGER", "ADMIN", "CUSTOMER"]
    for r in roles:
        Group.objects.get_or_create(name=r)

    if request.method == "POST":
        user_id = request.POST.get("user_id")
        selected_role = request.POST.get("role")
        target_user = get_object_or_404(User, pk=user_id)

        if selected_role in roles:
            for r in roles:
                group = Group.objects.filter(name=r).first()
                if group:
                    target_user.groups.remove(group)

            new_group = Group.objects.get(name=selected_role)
            target_user.groups.add(new_group)

            if selected_role in ["WAITER", "CHEF", "CASHIER", "MANAGER", "ADMIN"]:
                target_user.is_staff = True
            else:
                target_user.is_staff = False
            target_user.save(update_fields=["is_staff"])

            messages.success(request, f"Assigned role '{selected_role}' to {target_user.username} ({target_user.email}).")
            return redirect("restaurant:manager_staff_roles")

    search_q = request.GET.get("q", "").strip()
    users_qs = User.objects.prefetch_related("groups").order_by("-date_joined")
    if search_q:
        users_qs = users_qs.filter(
            Q(username__icontains=search_q) |
            Q(email__icontains=search_q) |
            Q(first_name__icontains=search_q)
        )

    users_list = users_qs[:50]
    return render(
        request,
        "restaurant/manager/staff_roles.html",
        {
            "users": users_list,
            "roles": roles,
            "search_q": search_q,
        },
    )

