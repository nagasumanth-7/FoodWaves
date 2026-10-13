from functools import wraps
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.contrib import messages
from django.urls import reverse


def user_has_any_role(user, allowed_roles):
    """
    Checks if a user belongs to any of the allowed roles, or is a superuser.
    Strictly checks group membership so that staff status does not bypass role boundaries.
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=allowed_roles).exists()


def role_required(allowed_roles, redirect_url="restaurant:home"):
    """
    Decorator to restrict view access strictly to specific roles.
    If unauthenticated, redirects to login with next parameter.
    If authenticated but unauthorized, raises PermissionDenied (HTTP 403).
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                messages.warning(request, "Please log in to access this staff portal.")
                login_url = f"{reverse('restaurant:login')}?next={request.get_full_path()}"
                return redirect(login_url)

            if not user_has_any_role(request.user, allowed_roles):
                messages.error(request, "Access denied: You do not have permissions for this section.")
                raise PermissionDenied("Access denied: You do not have permissions to access this page.")

            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator


def manager_required(view_func):
    """Restricts view strictly to MANAGER and ADMIN roles."""
    return role_required(["MANAGER", "ADMIN"])(view_func)


def chef_required(view_func):
    """Restricts view strictly to CHEF and ADMIN roles."""
    return role_required(["CHEF", "ADMIN"])(view_func)


def waiter_required(view_func):
    """Restricts view strictly to WAITER and ADMIN roles."""
    return role_required(["WAITER", "ADMIN"])(view_func)


def cashier_required(view_func):
    """Restricts view strictly to CASHIER and ADMIN roles."""
    return role_required(["CASHIER", "ADMIN"])(view_func)


