import io
import json
import logging
import qrcode
from decimal import Decimal

logger = logging.getLogger(__name__)
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
from django.http import JsonResponse, HttpResponseForbidden, HttpResponse

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
    ForgotPasswordForm,
    ResetPasswordForm,
    ReservationForm,
    MenuCategoryForm,
    MenuItemForm,
    ChefAvailabilityForm,
    TableOpenForm,
    PaymentProcessForm,
    RestaurantTableForm,
)
from .supabase_auth import (
    supabase_sign_up,
    supabase_sign_in,
    supabase_resend_confirmation,
    supabase_sign_out,
    supabase_reset_password_request,
    supabase_update_user_password,
    supabase_verify_token,
)
from .permissions import (
    manager_required,
    chef_required,
    waiter_required,
    cashier_required,
    cashier_or_manager_required,
)
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
    get_reservations_for_waiter,
    assign_table_to_reservation,
    unassign_table_from_reservation,
    seat_reservation,
)
from .services.billing_service import (
    calculate_and_generate_bill,
    request_bill_from_customer,
    process_bill_payment,
    record_payment_failure,
    get_unpaid_bills,
    get_daily_cashier_summary,
    get_sales_report_summary,
    create_direct_counter_bill,
    GST_RATE,
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

    try:
        categories = list(get_active_categories())
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
    except Exception as db_err:
        logger.error(f"Error loading menu from database: {db_err}")
        categories = []
        items_list = []
        selected_category = None

    total_count = len(items_list)
    available_count = sum(1 for item in items_list if getattr(item, "is_available", False))

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

            # Register user via Supabase Auth API
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

            # Check if Supabase sent confirmation or if confirmation was bypassed in Supabase
            if data and isinstance(data, dict) and data.get("identities") == []:
                messages.warning(
                    request,
                    f"An account with {email} already exists. If your account is not verified, please check your inbox or click 'Resend verification'.",
                )
            elif data and isinstance(data, dict) and data.get("confirmed_at"):
                # Supabase created the user with confirmed_at (email confirmation disabled in Supabase).
                # Send verification email via Django so customer still must verify email before dining access.
                uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
                token = default_token_generator.make_token(user)
                verify_url = request.build_absolute_uri(
                    reverse("restaurant:verify_email", kwargs={"uidb64": uidb64, "token": token})
                )
                try:
                    send_mail(
                        subject="Verify your Food Waves Account",
                        message=(
                            f"Hello {name or 'Food Waves Guest'},\n\n"
                            f"Thank you for creating an account with Food Waves Luxury Dining.\n\n"
                            f"Please click the link below to verify your email address:\n"
                            f"{verify_url}\n\n"
                            f"Warm regards,\nFood Waves Team"
                        ),
                        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "Food Waves <noreply@foodwaves.com>"),
                        recipient_list=[email],
                        fail_silently=False,
                    )
                    messages.success(
                        request,
                        f"A verification link has been dispatched to {email}. Please check your inbox to activate your dining account.",
                    )
                except Exception as mail_err:
                    messages.warning(
                        request,
                        f"Account created successfully, but sending verification email encountered an issue: {mail_err}. "
                        "You can resend verification or check your mail server configuration.",
                    )
            else:
                messages.success(
                    request,
                    f"A confirmation email has been dispatched by Supabase to {email}. Please check your inbox and click the verification link to activate your dining account.",
                )

            request.session["verification_email"] = email
            return redirect("restaurant:verification_sent")
    else:
        form = SignUpForm()

    return render(request, "restaurant/signup.html", {"form": form})


def verification_sent(request):
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

        request.session.pop("verification_email", None)
        messages.success(request, "Your email has been verified! You can now log in.")
        return render(request, "restaurant/verify_email.html", {"success": True, "user": user})
    else:
        return render(request, "restaurant/verify_email.html", {"success": False})


def resend_verification(request):
    email = request.POST.get("email") or request.GET.get("email") or request.session.get("verification_email")
    if email:
        email = email.strip().lower()
        callback_url = request.build_absolute_uri(reverse("restaurant:auth_callback"))
        success, err_msg = supabase_resend_confirmation(email, redirect_to=callback_url)
        if success:
            messages.success(request, f"A new confirmation email has been dispatched by Supabase to {email}.")
        else:
            # Fallback to Django mail verification if user exists locally
            user = User.objects.filter(email__iexact=email, is_active=False).first()
            if user:
                uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
                token = default_token_generator.make_token(user)
                verify_url = request.build_absolute_uri(
                    reverse("restaurant:verify_email", kwargs={"uidb64": uidb64, "token": token})
                )
                try:
                    send_mail(
                        subject="Verify your Food Waves Account",
                        message=(
                            f"Hello {user.first_name or 'Food Waves Guest'},\n\n"
                            f"Here is your requested email verification link:\n"
                            f"{verify_url}\n\n"
                            f"Warm regards,\nFood Waves Team"
                        ),
                        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "Food Waves <noreply@foodwaves.com>"),
                        recipient_list=[email],
                        fail_silently=False,
                    )
                    messages.success(request, f"A new verification link has been sent to {email}.")
                except Exception as mail_err:
                    messages.error(request, f"Resend failed: {err_msg} (Django mailer error: {mail_err})")
            else:
                messages.error(request, f"Resend failed: {err_msg}")
    else:
        messages.error(request, "Please provide an email address to resend the verification link.")

    return redirect("restaurant:verification_sent")


def auth_callback(request):
    """
    Callback endpoint triggered when user clicks a Supabase email confirmation or recovery link.
    Supabase verifies the token and redirects the browser here.
    """
    error = request.GET.get("error") or request.GET.get("error_code")
    error_desc = request.GET.get("error_description")
    if error or error_desc:
        messages.error(
            request,
            f"Authentication link failed: {error_desc or error}. Please request a new link.",
        )
        return redirect("restaurant:verification_sent")

    flow_type = request.GET.get("type", "").lower()
    token_hash = request.GET.get("token_hash")

    if flow_type == "recovery" or request.GET.get("next") == "reset-password":
        return redirect(reverse("restaurant:reset_password") + (f"?token_hash={token_hash}" if token_hash else ""))

    # If Supabase provided token_hash for email verification
    if token_hash:
        try:
            supabase_verify_token(token_hash, type_="email")
        except Exception:
            pass

    messages.success(request, "Your email address has been confirmed! Please sign in with your password to continue.")
    return redirect("restaurant:login")


def forgot_password_page(request):
    """
    Customer requests a password recovery email using Supabase Auth.
    Supports standard HTML forms as well as AJAX JSON requests.
    """
    is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"

    if request.user.is_authenticated:
        if is_ajax:
            return JsonResponse({"success": False, "error": "Already logged in."}, status=400)
        return redirect("restaurant:home")

    if request.method == "POST":
        form = ForgotPasswordForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"].strip().lower()
            callback_url = request.build_absolute_uri(reverse("restaurant:reset_password"))
            success, err_msg = supabase_reset_password_request(email, redirect_to=callback_url)

            if success:
                msg = f"A password reset link has been dispatched by Supabase to {email}. Please check your inbox."
                messages.success(request, msg)
                request.session["reset_email"] = email
                if is_ajax:
                    return JsonResponse({"success": True, "message": msg, "email": email})
                return render(request, "restaurant/forgot_password_sent.html", {"email": email})
            else:
                # Fallback to local Django mail if user exists
                local_user = User.objects.filter(email__iexact=email).first()
                if local_user:
                    uidb64 = urlsafe_base64_encode(force_bytes(local_user.pk))
                    token = default_token_generator.make_token(local_user)
                    reset_url = request.build_absolute_uri(
                        f"{reverse('restaurant:reset_password')}?uidb64={uidb64}&token={token}"
                    )
                    try:
                        send_mail(
                            subject="Reset your Food Waves Password",
                            message=(
                                f"Hello {local_user.first_name or 'Food Waves Guest'},\n\n"
                                f"We received a request to reset your password.\n\n"
                                f"Click the link below to set a new password:\n{reset_url}\n\n"
                                f"Warm regards,\nFood Waves Team"
                            ),
                            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "Food Waves <noreply@foodwaves.com>"),
                            recipient_list=[email],
                            fail_silently=False,
                        )
                        msg = f"A password reset link has been dispatched to {email}. Please check your inbox."
                        messages.success(request, msg)
                        request.session["reset_email"] = email
                        if is_ajax:
                            return JsonResponse({"success": True, "message": msg, "email": email})
                        return render(request, "restaurant/forgot_password_sent.html", {"email": email})
                    except Exception as mail_err:
                        err_text = f"Password reset request failed: {err_msg} ({mail_err})"
                        messages.error(request, err_text)
                        if is_ajax:
                            return JsonResponse({"success": False, "error": err_text}, status=400)
                else:
                    err_text = f"Password reset request failed: {err_msg}"
                    messages.error(request, err_text)
                    if is_ajax:
                        return JsonResponse({"success": False, "error": err_text}, status=400)
        else:
            err_text = "Please enter a valid email address."
            messages.error(request, err_text)
            if is_ajax:
                return JsonResponse({"success": False, "error": err_text}, status=400)
    else:
        form = ForgotPasswordForm()

    return render(request, "restaurant/forgot_password.html", {"form": form})


def reset_password_page(request):
    """
    Renders password reset form and updates user password in Supabase Auth and Django DB.
    Handles access_token from Supabase recovery redirect (hash fragment or query params)
    as well as Django uidb64/token fallback.
    """
    if request.user.is_authenticated:
        return redirect("restaurant:home")

    # Check for error in query params from Supabase redirect
    error = request.GET.get("error") or request.GET.get("error_code")
    error_desc = request.GET.get("error_description")
    if error or error_desc:
        messages.error(request, f"Password reset link is invalid or expired: {error_desc or error}. Please request a new link.")
        return redirect("restaurant:forgot_password")

    uidb64 = request.GET.get("uidb64") or request.POST.get("uidb64")
    token = request.GET.get("token") or request.POST.get("token")
    token_hash = request.GET.get("token_hash") or request.POST.get("token_hash")

    if request.method == "POST":
        form = ResetPasswordForm(request.POST)
        if form.is_valid():
            new_password = form.cleaned_data["new_password"]
            access_token = form.cleaned_data.get("access_token")
            form_token_hash = form.cleaned_data.get("token_hash") or token_hash
            user_email = form.cleaned_data.get("email")
            updated = False

            # 1. Update via Supabase Auth access_token
            if access_token:
                success, sb_user, err_msg = supabase_update_user_password(access_token, new_password)
                if success:
                    updated = True
                    if sb_user and isinstance(sb_user, dict):
                        user_email = sb_user.get("email") or user_email
                else:
                    messages.error(request, f"Failed to reset password with Supabase: {err_msg}")
                    return render(request, "restaurant/reset_password.html", {"form": form, "uidb64": uidb64, "token": token, "token_hash": token_hash})

            # 2. Or verify token_hash with Supabase
            elif form_token_hash:
                success, data, err_msg = supabase_verify_token(form_token_hash, type_="recovery")
                if success:
                    t_access = (data or {}).get("access_token")
                    if t_access:
                        s_ok, sb_user, s_err = supabase_update_user_password(t_access, new_password)
                        if s_ok:
                            updated = True
                            user_email = (sb_user or {}).get("email") or user_email
                    else:
                        updated = True
                        user_email = ((data or {}).get("user") or {}).get("email") or user_email
                else:
                    messages.error(request, f"Reset token is invalid or expired: {err_msg}")
                    return render(request, "restaurant/reset_password.html", {"form": form, "uidb64": uidb64, "token": token, "token_hash": token_hash})

            # 3. Fallback: Django token validation
            elif uidb64 and token:
                try:
                    uid = force_str(urlsafe_base64_decode(uidb64))
                    local_user = User.objects.get(pk=uid)
                except Exception:
                    local_user = None

                if local_user and default_token_generator.check_token(local_user, token):
                    local_user.set_password(new_password)
                    local_user.is_active = True
                    local_user.save()
                    profile, _ = UserProfile.objects.get_or_create(user=local_user)
                    profile.is_email_verified = True
                    profile.save()
                    updated = True
                    user_email = local_user.email
                else:
                    messages.error(request, "The password reset link is invalid or has expired.")
                    return render(request, "restaurant/reset_password.html", {"form": form, "uidb64": uidb64, "token": token, "token_hash": token_hash})

            if updated:
                # Ensure Django user password is in sync
                if user_email:
                    django_user = User.objects.filter(email__iexact=user_email).first()
                    if django_user:
                        django_user.set_password(new_password)
                        django_user.is_active = True
                        django_user.save()
                        profile, _ = UserProfile.objects.get_or_create(user=django_user)
                        profile.is_email_verified = True
                        profile.save()

                messages.success(request, "Your password has been successfully reset! Please sign in with your new password.")
                return redirect("restaurant:login")
            else:
                messages.error(request, "Invalid or missing recovery session. Please use the reset link sent to your email or request a new one.")
    else:
        form = ResetPasswordForm(initial={"token_hash": token_hash})

    return render(request, "restaurant/reset_password.html", {
        "form": form,
        "uidb64": uidb64,
        "token": token,
        "token_hash": token_hash,
    })


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
        action = request.POST.get("action")
        is_ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"

        # Handle inline forgot password request directly from the sign-in page
        if action == "forgot_password":
            email = (request.POST.get("email") or "").strip().lower()
            if not email:
                if is_ajax:
                    return JsonResponse({"success": False, "error": "Please enter your registered email address."}, status=400)
                messages.error(request, "Please enter your registered email address.")
                return redirect("restaurant:login")

            callback_url = request.build_absolute_uri(reverse("restaurant:reset_password"))
            success, err_msg = supabase_reset_password_request(email, redirect_to=callback_url)

            if success:
                msg = f"A password reset link has been dispatched by Supabase to {email}. Please check your inbox."
                messages.success(request, msg)
                request.session["reset_email"] = email
                if is_ajax:
                    return JsonResponse({"success": True, "message": msg, "email": email})
                return redirect("restaurant:login")
            else:
                local_user = User.objects.filter(email__iexact=email).first()
                if local_user:
                    uidb64 = urlsafe_base64_encode(force_bytes(local_user.pk))
                    token = default_token_generator.make_token(local_user)
                    reset_url = request.build_absolute_uri(
                        f"{reverse('restaurant:reset_password')}?uidb64={uidb64}&token={token}"
                    )
                    try:
                        send_mail(
                            subject="Reset your Food Waves Password",
                            message=(
                                f"Hello {local_user.first_name or 'Food Waves Guest'},\n\n"
                                f"We received a request to reset your password.\n\n"
                                f"Click the link below to set a new password:\n{reset_url}\n\n"
                                f"Warm regards,\nFood Waves Team"
                            ),
                            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "Food Waves <noreply@foodwaves.com>"),
                            recipient_list=[email],
                            fail_silently=False,
                        )
                        msg = f"A password reset link has been dispatched to {email}. Please check your inbox."
                        messages.success(request, msg)
                        request.session["reset_email"] = email
                        if is_ajax:
                            return JsonResponse({"success": True, "message": msg, "email": email})
                        return redirect("restaurant:login")
                    except Exception as mail_err:
                        err_text = f"Password reset request failed: {err_msg} ({mail_err})"
                        messages.error(request, err_text)
                        if is_ajax:
                            return JsonResponse({"success": False, "error": err_text}, status=400)
                else:
                    err_text = f"Password reset request failed: {err_msg}"
                    messages.error(request, err_text)
                    if is_ajax:
                        return JsonResponse({"success": False, "error": err_text}, status=400)
                return redirect("restaurant:login")

        form = LoginForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"].strip().lower()
            password = form.cleaned_data["password"]
            remember_me = form.cleaned_data.get("remember_me", True)

            try:
                # Direct authentication for Staff, Managers, Chefs, Waiters, Cashiers, and Superusers
                local_user = (
                    User.objects.filter(email__iexact=email).first()
                    or User.objects.filter(username__iexact=email).first()
                )
            except Exception as db_err:
                logger.error(f"Database error during staff lookup: {db_err}")
                local_user = None

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

            # Supabase Auth Authentication for Customer Accounts
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

            try:
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
            except Exception as db_err:
                logger.error(f"Error persisting user to database: {db_err}")
                messages.error(request, "Unable to synchronize user session with database. Please try again.")
                return render(request, "restaurant/login.html", {"form": form, "next": next_url})

            login(request, django_user, backend="django.contrib.auth.backends.ModelBackend")
            request.session["supabase_token"] = data_or_code.get("access_token")

            if not remember_me:
                request.session.set_expiry(0)

            messages.success(request, f"Welcome back, {django_user.first_name or django_user.email}!")
            return redirect(get_user_home_url(django_user, next_url=next_url))
    else:
        form = LoginForm()

    return render(
        request,
        "restaurant/login.html",
        {"form": form, "next": next_url, "unverified_email": unverified_email},
    )


def logout_view(request):
    token = request.session.get("supabase_token")
    if token:
        try:
            supabase_sign_out(token)
        except Exception:
            pass
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
            if booking.table and booking.table.status == "RESERVED":
                other_res = Reservation.objects.filter(
                    table=booking.table,
                    date=booking.date,
                    status__in=["CONFIRMED", "PENDING"],
                ).exclude(pk=booking.pk).exists()
                if not other_res:
                    booking.table.status = "AVAILABLE"
                    booking.table.save(update_fields=["status"])
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
    """
    Chef overview portal:
    - Live kitchen orders queue (table number, item names, waiter/customer source)
    - Dish preparation action controls (Completed / Not Available / Cooking)
    - Recipe & availability metrics
    """
    active_items = MenuItem.objects.filter(is_active=True)
    available_count = active_items.filter(is_available=True).count()
    unavailable_count = active_items.filter(is_available=False).count()
    unavailable_items = active_items.filter(is_available=False).select_related("category")

    active_orders = get_kitchen_active_orders()
    active_items_queue = OrderItem.objects.filter(
        order__in=active_orders,
        status__in=["PENDING", "PREPARING"]
    ).select_related("order__session__table", "order__bill", "order__placed_by", "menu_item").order_by("created_at")

    pending_items_count = active_items_queue.filter(status="PENDING").count()
    preparing_items_count = active_items_queue.filter(status="PREPARING").count()
    ready_items_count = OrderItem.objects.filter(order__in=active_orders, status="READY").count()

    return render(
        request,
        "restaurant/chef/dashboard.html",
        {
            "total_active": active_items.count(),
            "available_count": available_count,
            "unavailable_count": unavailable_count,
            "unavailable_items": unavailable_items,
            "active_orders": active_orders,
            "active_items_queue": active_items_queue,
            "pending_items_count": pending_items_count,
            "preparing_items_count": preparing_items_count,
            "ready_items_count": ready_items_count,
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
    - Table reservations overview & assignment
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

    # Reservations for today
    today = timezone.localdate()
    today_reservations = get_reservations_for_waiter(date="TODAY")
    today_reservations_count = today_reservations.count()
    unassigned_today_count = today_reservations.filter(table__isnull=True).count()
    confirmed_today_count = today_reservations.filter(status="CONFIRMED").count()
    available_tables = all_tables.filter(status__in=["AVAILABLE", "RESERVED"])

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
            "today_reservations": today_reservations,
            "today_reservations_count": today_reservations_count,
            "unassigned_today_count": unassigned_today_count,
            "confirmed_today_count": confirmed_today_count,
            "available_tables": available_tables,
            "today_date": today,
        },
    )


@waiter_required
def waiter_reservations(request):
    """
    Comprehensive Waiter Reservations Portal:
    - View all customer reservations (who booked, party size, preferences, special requests)
    - Filter by date (Today, Upcoming, Past, All)
    - Filter by status (Confirmed, Arrived, Completed, Cancelled)
    - Filter by table assignment (Assigned, Unassigned)
    - Assign available tables to reservations
    - Seat reserved parties directly
    """
    date_filter = request.GET.get("date", "TODAY")
    status_filter = request.GET.get("status", "ALL")
    assignment_filter = request.GET.get("assignment", "ALL")
    search_query = request.GET.get("q", "")

    reservations = get_reservations_for_waiter(
        date=date_filter,
        status=status_filter,
        search_query=search_query,
        assignment=assignment_filter,
    )
    all_tables = get_all_tables()
    available_tables = all_tables.filter(status__in=["AVAILABLE", "RESERVED"])

    today = timezone.localdate()
    today_count = Reservation.objects.filter(date=today).count()
    upcoming_count = Reservation.objects.filter(date__gte=today).count()
    unassigned_today_count = Reservation.objects.filter(date=today, table__isnull=True).count()
    confirmed_today_count = Reservation.objects.filter(date=today, status="CONFIRMED").count()

    return render(
        request,
        "restaurant/waiter/reservations.html",
        {
            "reservations": reservations,
            "available_tables": available_tables,
            "date_filter": date_filter,
            "status_filter": status_filter,
            "assignment_filter": assignment_filter,
            "search_query": search_query,
            "today_count": today_count,
            "upcoming_count": upcoming_count,
            "unassigned_today_count": unassigned_today_count,
            "confirmed_today_count": confirmed_today_count,
            "today_date": today,
        },
    )


@waiter_required
def waiter_assign_table(request, reservation_id):
    """
    Assigns or updates a restaurant table for a customer reservation.
    """
    if request.method == "POST":
        reservation = get_object_or_404(Reservation, pk=reservation_id)
        table_id = request.POST.get("table_id")
        if not table_id:
            messages.error(request, "Please select a table to assign.")
        else:
            table = get_table_by_id(table_id)
            try:
                assign_table_to_reservation(reservation, table, waiter_user=request.user)
                messages.success(
                    request,
                    f"Table {table.table_number} ({table.capacity} seats, {table.get_floor_area_display()}) successfully assigned and opened for {reservation.name}! Ready to take orders."
                )
            except Exception as e:
                messages.error(request, str(e))

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or reverse("restaurant:waiter_reservations")
    return redirect(next_url)


@waiter_required
def waiter_unassign_table(request, reservation_id):
    """
    Removes table assignment from a reservation and frees the table if appropriate.
    """
    if request.method == "POST":
        reservation = get_object_or_404(Reservation, pk=reservation_id)
        try:
            unassign_table_from_reservation(reservation)
            messages.success(request, f"Table unassigned from reservation #{reservation.id} ({reservation.name}).")
        except Exception as e:
            messages.error(request, str(e))

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or reverse("restaurant:waiter_reservations")
    return redirect(next_url)


@waiter_required
def waiter_seat_reservation(request, reservation_id):
    """
    Seats a customer party from their reservation:
    Opens active TableSession, marks reservation ARRIVED, and marks table OCCUPIED.
    """
    if request.method == "POST":
        reservation = get_object_or_404(Reservation, pk=reservation_id)
        table_id = request.POST.get("table_id")
        target_table = get_table_by_id(table_id) if table_id else reservation.table

        if not target_table:
            messages.error(request, "Please assign a table before seating guests.")
            return redirect(request.META.get("HTTP_REFERER") or reverse("restaurant:waiter_reservations"))

        try:
            session = seat_reservation(reservation, table=target_table, waiter_user=request.user)
            messages.success(
                request,
                f"Table {target_table.table_number} seated successfully for reservation #{reservation.id} ({session.guest_name})!"
            )
            return redirect("restaurant:waiter_table_detail", table_id=target_table.id)
        except Exception as e:
            messages.error(request, str(e))

    return redirect(request.META.get("HTTP_REFERER") or reverse("restaurant:waiter_reservations"))


@waiter_required
def waiter_table_detail(request, table_id):
    """
    Detailed Table & Session view for Waiters.
    Displays party details, placed orders, item preparation statuses, bills,
    and assigned/available reservations for seating.
    """
    table = get_table_by_id(table_id)
    session = get_active_session_for_table(table.id)
    orders = session.orders.prefetch_related("items__menu_item").order_by("-created_at") if session else []
    bill = getattr(session, "bill", None) if session else None
    open_form = TableOpenForm()

    today = timezone.localdate()
    assigned_reservation = Reservation.objects.filter(
        table=table,
        date=today,
        status__in=["CONFIRMED", "PENDING"],
    ).first()

    today_unseated_reservations = Reservation.objects.filter(
        date=today,
        status__in=["CONFIRMED", "PENDING"],
    ).filter(Q(table__isnull=True) | Q(table=table)).order_by("time")

    return render(
        request,
        "restaurant/waiter/table_detail.html",
        {
            "table": table,
            "session": session,
            "orders": orders,
            "bill": bill,
            "open_form": open_form,
            "assigned_reservation": assigned_reservation,
            "today_unseated_reservations": today_unseated_reservations,
        },
    )


@waiter_required
def waiter_table_open(request, table_id):
    """
    Opens a new session on an available or reserved table for walk-ins or reservations.
    """
    table = get_table_by_id(table_id)
    if request.method == "POST":
        reservation_id = request.POST.get("reservation_id")
        if reservation_id:
            try:
                reservation = get_object_or_404(Reservation, pk=reservation_id)
                session = seat_reservation(reservation, table=table, waiter_user=request.user)
                messages.success(
                    request,
                    f"Table {table.table_number} seated successfully for reservation #{reservation.id} ({session.guest_name})!"
                )
                return redirect("restaurant:waiter_table_detail", table_id=table.id)
            except Exception as e:
                messages.error(request, str(e))
                return redirect("restaurant:waiter_table_detail", table_id=table.id)

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
def cashier_direct_bill(request):
    """
    Cashier Direct / Counter Billing:
    Allows cashier to select dishes from the menu, specify quantities,
    customer details, order type (COUNTER / TAKEAWAY), apply discounts or tips,
    and either generate an unpaid bill or immediately settle payment (Cash, UPI, Card).
    """
    if request.method == "POST":
        items_data = []
        is_ajax = (
            request.headers.get("x-requested-with") == "XMLHttpRequest"
            or request.content_type == "application/json"
        )

        customer_name = "Counter Guest"
        customer_phone = ""
        order_type = "COUNTER"
        discount_pct = Decimal("0.00")
        tip_amount = Decimal("0.00")
        instructions = ""
        settle_immediately = False
        payment_method = "CASH"
        transaction_id = ""

        if request.content_type == "application/json" or (request.body and request.body.startswith(b"{")):
            try:
                payload = json.loads(request.body.decode("utf-8"))
                items_data = payload.get("items", [])
                customer_name = payload.get("customer_name", "Counter Guest")
                customer_phone = payload.get("customer_phone", "")
                order_type = payload.get("order_type", "COUNTER")
                discount_pct = Decimal(str(payload.get("discount_pct", 0) or 0))
                tip_amount = Decimal(str(payload.get("tip_amount", 0) or 0))
                instructions = payload.get("instructions", "")
                settle_immediately = bool(payload.get("settle_immediately", False))
                payment_method = payload.get("payment_method", "CASH")
                transaction_id = payload.get("transaction_id", "")
            except Exception as e:
                return JsonResponse({"status": "error", "message": f"Invalid JSON payload: {e}"}, status=400)
        else:
            customer_name = request.POST.get("customer_name", "Counter Guest").strip() or "Counter Guest"
            customer_phone = request.POST.get("customer_phone", "").strip()
            order_type = request.POST.get("order_type", "COUNTER")
            instructions = request.POST.get("instructions", "").strip()
            action = request.POST.get("action", "generate")
            settle_immediately = (action == "settle" or request.POST.get("settle_immediately") == "1")
            payment_method = request.POST.get("payment_method", "CASH")
            transaction_id = request.POST.get("transaction_id", "").strip()

            try:
                discount_pct = Decimal(request.POST.get("discount_pct") or "0.00")
            except Exception:
                discount_pct = Decimal("0.00")

            try:
                tip_amount = Decimal(request.POST.get("tip_amount") or "0.00")
            except Exception:
                tip_amount = Decimal("0.00")

            cart_json = request.POST.get("cart_data", "")
            if cart_json:
                try:
                    items_data = json.loads(cart_json)
                except Exception:
                    pass

            if not items_data:
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
            if is_ajax:
                return JsonResponse({"status": "error", "message": "Please add at least one item to generate a bill."}, status=400)
            messages.error(request, "Please add at least one item to generate a bill.")
            return redirect("restaurant:cashier_direct_bill")

        try:
            bill = create_direct_counter_bill(
                items_data=items_data,
                cashier_user=request.user,
                customer_name=customer_name,
                customer_phone=customer_phone,
                order_type=order_type,
                discount_pct=discount_pct,
                tip_amount=tip_amount,
                instructions=instructions,
                settle_immediately=settle_immediately,
                payment_method=payment_method,
                transaction_id=transaction_id,
            )

            if settle_immediately:
                msg = f"Direct bill #{bill.id} settled via {payment_method}! Total: ₹{bill.total}"
                messages.success(request, msg)
                redirect_target = reverse("restaurant:cashier_receipt", kwargs={"bill_id": bill.id})
            elif request.POST.get("show_receipt") == "1" or request.GET.get("show_receipt") == "1":
                msg = f"Direct bill #{bill.id} generated for {bill.display_customer}! Total: ₹{bill.total}"
                messages.success(request, msg)
                redirect_target = reverse("restaurant:cashier_receipt", kwargs={"bill_id": bill.id})
            else:
                msg = f"Direct bill #{bill.id} generated for {bill.display_customer}! Total: ₹{bill.total}"
                messages.success(request, msg)
                redirect_target = reverse("restaurant:cashier_settle_bill", kwargs={"bill_id": bill.id})

            if is_ajax:
                return JsonResponse({
                    "status": "success",
                    "bill_id": bill.id,
                    "message": msg,
                    "redirect_url": redirect_target,
                })
            return redirect(redirect_target)

        except Exception as e:
            if is_ajax:
                return JsonResponse({"status": "error", "message": str(e)}, status=400)
            messages.error(request, f"Error generating direct bill: {e}")
            return redirect("restaurant:cashier_direct_bill")

    categories = get_active_categories()
    items = get_active_menu_items()
    return render(
        request,
        "restaurant/cashier/direct_bill.html",
        {
            "categories": categories,
            "items": items,
        },
    )


@cashier_required
def cashier_settle_bill(request, bill_id):
    """
    Cashier Checkout & Settlement:
    Calculates 5% GST, discounts, tips, and records payments (Cash, UPI, Card).
    Supports both table session bills and direct counter bills.
    """
    bill = get_object_or_404(Bill.objects.select_related("session__table", "session__waiter", "cashier"), pk=bill_id)
    session = bill.session
    orders = bill.all_orders

    if request.method == "POST":
        form = PaymentProcessForm(request.POST)
        if form.is_valid():
            method = form.cleaned_data["payment_method"]
            amount_paid = form.cleaned_data["amount_paid"]
            discount_pct = form.cleaned_data.get("discount_pct") or Decimal("0.00")
            tip_amount = form.cleaned_data.get("tip_amount") or Decimal("0.00")
            tx_id = form.cleaned_data.get("transaction_id", "")

            if session:
                # Re-generate table bill if discount or tip adjusted
                bill = calculate_and_generate_bill(
                    session=session,
                    cashier=request.user,
                    discount_pct=discount_pct,
                    tip_amount=tip_amount,
                )
            else:
                # Direct counter bill: update discount/tip/tax/total on bill
                subtotal = bill.subtotal
                discount_amount = (subtotal * (discount_pct / Decimal("100.00"))).quantize(Decimal("0.01"))
                taxable_amount = max(Decimal("0.00"), subtotal - discount_amount)
                tax_amount = (taxable_amount * GST_RATE).quantize(Decimal("0.01"))
                total_amount = (taxable_amount + tax_amount + tip_amount).quantize(Decimal("0.01"))
                bill.discount = discount_amount
                bill.tax = tax_amount
                bill.tip = tip_amount
                bill.total = total_amount
                bill.cashier = request.user
                bill.save(update_fields=["discount", "tax", "tip", "total", "cashier", "updated_at"])

            try:
                payment = process_bill_payment(
                    bill=bill,
                    payment_method=method,
                    amount_paid=amount_paid,
                    transaction_id=tx_id,
                    processed_by=request.user,
                )
                if session:
                    messages.success(request, f"Payment of ₹{payment.amount} settled via {payment.get_payment_method_display()}! Table {session.table.table_number} is now Available.")
                else:
                    messages.success(request, f"Payment of ₹{payment.amount} settled via {payment.get_payment_method_display()} for {bill.display_title}!")
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
    Printable luxury receipt view for paid and unpaid bills.
    Supports on-screen view, print invoice, and direct download.
    """
    bill = get_object_or_404(Bill.objects.select_related("session__table", "session__waiter", "cashier"), pk=bill_id)
    payments = bill.payments.all()
    session = bill.session
    orders = bill.all_orders

    response = render(
        request,
        "restaurant/cashier/receipt.html",
        {
            "bill": bill,
            "session": session,
            "orders": orders,
            "payments": payments,
        },
    )
    if request.GET.get("download") == "1":
        response["Content-Disposition"] = f'attachment; filename="FoodWaves_Bill_{bill.id}.html"'
    return response


@cashier_required
def cashier_download_bill(request, bill_id):
    """
    Direct invoice download endpoint: returns standalone HTML invoice as attachment.
    """
    bill = get_object_or_404(Bill.objects.select_related("session__table", "session__waiter", "cashier"), pk=bill_id)
    payments = bill.payments.all()
    session = bill.session
    orders = bill.all_orders

    response = render(
        request,
        "restaurant/cashier/receipt.html",
        {
            "bill": bill,
            "session": session,
            "orders": orders,
            "payments": payments,
            "is_download": True,
        },
    )
    response["Content-Disposition"] = f'attachment; filename="FoodWaves_Invoice_Bill_{bill.id}.html"'
    return response


@cashier_or_manager_required
def cashier_reports(request):
    """
    Sales and financial settlement report accessible by Cashiers and Managers.
    Supports preset periods ('today', 'yesterday', 'week', 'month') as well as
    custom date ranges with validation constraints (start_date <= end_date, safe parsing).
    """
    today = timezone.localdate()
    period = request.GET.get("period", "").strip().lower()

    single_date_str = request.GET.get("date", "").strip()
    start_date_str = request.GET.get("start_date", "").strip()
    end_date_str = request.GET.get("end_date", "").strip()

    date_error = None
    start_date = None
    end_date = None

    # Handle backwards compatibility with single ?date=YYYY-MM-DD
    if not period and single_date_str:
        period = "custom"
        start_date_str = single_date_str
        end_date_str = single_date_str

    if period == "custom" or (start_date_str or end_date_str):
        period = "custom"
        try:
            if start_date_str:
                start_date = timezone.datetime.strptime(start_date_str, "%Y-%m-%d").date()
            else:
                start_date = today
        except ValueError:
            date_error = f"Invalid start date '{start_date_str}'. Expected format is YYYY-MM-DD."
            start_date = today

        try:
            if end_date_str:
                end_date = timezone.datetime.strptime(end_date_str, "%Y-%m-%d").date()
            else:
                end_date = start_date or today
        except ValueError:
            date_error = f"Invalid end date '{end_date_str}'. Expected format is YYYY-MM-DD."
            end_date = start_date or today

        # Date constraint enforcement: start_date cannot be greater than end_date
        if start_date and end_date and start_date > end_date:
            date_error = f"Date constraint: Start date ({start_date}) cannot be after End date ({end_date}). Dates have been adjusted."
            start_date, end_date = end_date, start_date

    if not period:
        period = "today"

    if date_error:
        messages.warning(request, date_error)

    summary = get_sales_report_summary(start_date=start_date, end_date=end_date, period=period)

    # Determine caller context (Manager vs Cashier) for back button & breadcrumbs
    is_manager = (
        request.path.startswith("/manager/")
        or (request.user.groups.filter(name="MANAGER").exists() and not request.user.groups.filter(name="CASHIER").exists())
    )
    if is_manager:
        back_url = reverse("restaurant:manager_dashboard")
        back_label = "← Back to Manager Dashboard"
    else:
        back_url = reverse("restaurant:cashier_dashboard")
        back_label = "← Back to Cashier Dashboard"

    return render(
        request,
        "restaurant/cashier/reports.html",
        {
            "summary": summary,
            "period": summary["period"],
            "period_label": summary["period_label"],
            "selected_date": summary["start_date"].strftime("%Y-%m-%d"),
            "start_date": summary["start_date"].strftime("%Y-%m-%d"),
            "end_date": summary["end_date"].strftime("%Y-%m-%d"),
            "back_url": back_url,
            "back_label": back_label,
            "is_manager": is_manager,
            "today_str": today.strftime("%Y-%m-%d"),
        },
    )


@manager_required
def manager_reports(request):
    """
    Manager endpoint for Sales & Financial Reports.
    """
    return cashier_reports(request)


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
    Chef updates dish status:
    - PREPARING / Cooking
    - READY / COMPLETED (Food cooked & ready for waiter to serve)
    - NOT_AVAILABLE / CANCELLED (Cannot make dish; marks unavailable)
    """
    if request.method == "POST":
        new_status = request.POST.get("status", "").strip().upper()
        if new_status in ["COMPLETED", "READY"]:
            new_status = "READY"
        elif new_status in ["NOT_AVAILABLE", "UNAVAILABLE", "CANCELLED"]:
            new_status = "CANCELLED"

        reason = request.POST.get("reason", "").strip() or "Not available in kitchen"
        mark_menu_unavailable = request.POST.get("mark_menu_unavailable", "true").lower() in ("true", "1", "yes")

        try:
            item = OrderItem.objects.select_related("order__session__table", "order__bill", "menu_item").get(pk=item_id)
            table_name = item.table_display

            if new_status == "CANCELLED":
                # Chef marks item as not available
                update_order_item_status(item_id, "CANCELLED", updated_by=request.user)
                if mark_menu_unavailable:
                    update_menu_item_availability(
                        item.menu_item,
                        is_available=False,
                        reason=f"Kitchen sold out during order for {table_name}",
                        user=request.user,
                    )
                messages.warning(
                    request,
                    f"'{item.menu_item.name}' for {table_name} marked as NOT AVAILABLE. Menu item updated to sold out."
                )
            elif new_status == "READY":
                update_order_item_status(item_id, "READY", updated_by=request.user)
                messages.success(
                    request,
                    f"'{item.menu_item.name}' for {table_name} marked as COMPLETED (Ready for waiter to serve)!"
                )
            elif new_status == "PREPARING":
                est = request.POST.get("estimated_minutes")
                update_order_item_status(item_id, "PREPARING", updated_by=request.user, estimated_minutes=est)
                messages.info(
                    request,
                    f"Started cooking '{item.menu_item.name}' for {table_name}."
                )
            else:
                update_order_item_status(item_id, new_status, updated_by=request.user)
                messages.success(request, f"{item.menu_item.name} for {table_name} updated to {new_status}.")
        except Exception as e:
            messages.error(request, str(e))

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or reverse("restaurant:chef_kds")
    return redirect(next_url)


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
    session = TableSession.objects.filter(table=table, status="ACTIVE").first()
    if not session:
        try:
            session = get_or_create_active_session(table, customer=request.user if request.user.is_authenticated else None)
        except Exception as e:
            messages.error(request, str(e))
            return redirect("restaurant:table_menu", qr_token=qr_token)

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
    session = TableSession.objects.filter(table=table, status="ACTIVE").first()
    if not session:
        try:
            session = get_or_create_active_session(table, customer=request.user if request.user.is_authenticated else None)
        except Exception as e:
            if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.content_type == "application/json":
                return JsonResponse({"status": "error", "message": str(e)}, status=400)
            messages.error(request, str(e))
            return redirect("restaurant:table_menu", qr_token=qr_token)

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
    session = TableSession.objects.filter(table=table, status__in=["ACTIVE", "BILL_PENDING"]).first()
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
    session = TableSession.objects.filter(table=table, status__in=["ACTIVE", "BILL_PENDING"]).first()
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
    session = TableSession.objects.filter(table=table, status__in=["ACTIVE", "BILL_PENDING"]).first()
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


# ==============================================================================
# MANAGER TABLE & QR CODE MANAGEMENT (Section 4)
# ==============================================================================

@manager_required
def manager_tables_list(request):
    """
    Manager Tables List:
    Displays all dining tables, seating capacity, active session status,
    and links to generate or print QR codes.
    """
    tables = RestaurantTable.objects.prefetch_related("sessions").order_by("table_number")
    total_count = tables.count()
    active_count = tables.filter(is_active=True).count()
    occupied_count = tables.filter(status="OCCUPIED").count()
    bill_pending_count = tables.filter(status="BILL_PENDING").count()

    return render(
        request,
        "restaurant/manager/tables_list.html",
        {
            "tables": tables,
            "total_count": total_count,
            "active_count": active_count,
            "occupied_count": occupied_count,
            "bill_pending_count": bill_pending_count,
        },
    )


@manager_required
def manager_table_add(request):
    """
    Manager Add Dining Table:
    Creates a new table with automatically generated unique QR token.
    """
    if request.method == "POST":
        form = RestaurantTableForm(request.POST)
        if form.is_valid():
            table = form.save()
            messages.success(request, f"Table {table.table_number} added successfully.")
            return redirect("restaurant:manager_tables_list")
    else:
        form = RestaurantTableForm()

    return render(
        request,
        "restaurant/manager/table_form.html",
        {
            "form": form,
            "title": "Add New Dining Table",
            "button_text": "Create Table",
        },
    )


@manager_required
def manager_table_edit(request, table_id):
    """
    Manager Edit Dining Table:
    Updates table capacity, area, status, and active state.
    """
    table = get_object_or_404(RestaurantTable, pk=table_id)
    if request.method == "POST":
        form = RestaurantTableForm(request.POST, instance=table)
        if form.is_valid():
            form.save()
            messages.success(request, f"Table {table.table_number} updated successfully.")
            return redirect("restaurant:manager_tables_list")
    else:
        form = RestaurantTableForm(instance=table)

    return render(
        request,
        "restaurant/manager/table_form.html",
        {
            "form": form,
            "table": table,
            "title": f"Edit Table {table.table_number}",
            "button_text": "Save Changes",
        },
    )


@manager_required
def manager_table_qr_view(request, table_id=None):
    """
    Manager Table QR Display & Print View (Section 4):
    Displays high-resolution QR codes with print styling for a single table
    or all active tables for table-top stands and cards.
    """
    if table_id:
        table = get_object_or_404(RestaurantTable, pk=table_id)
        tables = [table]
        single_mode = True
    else:
        tables = list(RestaurantTable.objects.filter(is_active=True).order_by("table_number"))
        single_mode = False

    table_data = []
    for t in tables:
        menu_url = request.build_absolute_uri(
            reverse("restaurant:table_menu", kwargs={"qr_token": t.qr_token})
        )
        qr_img_url = reverse("restaurant:manager_table_qr_image", kwargs={"table_id": t.id})
        table_data.append({
            "table": t,
            "menu_url": menu_url,
            "qr_img_url": qr_img_url,
        })

    return render(
        request,
        "restaurant/manager/table_qr.html",
        {
            "table_data": table_data,
            "single_mode": single_mode,
            "primary_table": tables[0] if single_mode and tables else None,
        },
    )


@manager_required
def manager_table_qr_image(request, table_id):
    """
    Streams a dynamically generated PNG QR code image for a table.
    The QR code embeds the absolute URL to the customer table ordering route.
    """
    table = get_object_or_404(RestaurantTable, pk=table_id)
    target_url = request.build_absolute_uri(
        reverse("restaurant:table_menu", kwargs={"qr_token": table.qr_token})
    )

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=3,
    )
    qr.add_data(target_url)
    qr.make(fit=True)

    img = qr.make_image(fill_color="#030813", back_color="#ffffff")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return HttpResponse(buffer.getvalue(), content_type="image/png")

