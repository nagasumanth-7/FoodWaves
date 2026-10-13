from django.urls import reverse


def staff_role_context(request):
    """
    Context processor that provides role booleans and the primary portal URL for the logged-in user.
    Ensures staff members only see links to their assigned interface in the navigation bar,
    and regular/logged-out users see clean customer navigation without staff clutter.
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {
            "is_staff_user": False,
            "is_waiter": False,
            "is_chef": False,
            "is_cashier": False,
            "is_manager": False,
            "is_admin_user": False,
            "user_portal_url": None,
            "user_portal_name": None,
        }

    is_admin = user.is_superuser or user.groups.filter(name="ADMIN").exists()
    is_waiter = user.groups.filter(name="WAITER").exists()
    is_chef = user.groups.filter(name="CHEF").exists()
    is_cashier = user.groups.filter(name="CASHIER").exists()
    is_manager = user.groups.filter(name="MANAGER").exists()

    portal_url = None
    portal_name = None

    if is_waiter:
        portal_url = reverse("restaurant:waiter_dashboard")
        portal_name = "Waiter Floor"
    elif is_chef:
        portal_url = reverse("restaurant:chef_dashboard")
        portal_name = "Chef Kitchen"
    elif is_cashier:
        portal_url = reverse("restaurant:cashier_dashboard")
        portal_name = "Cashier POS"
    elif is_manager or is_admin:
        portal_url = reverse("restaurant:manager_dashboard")
        portal_name = "Manager Panel"

    is_staff_member = is_waiter or is_chef or is_cashier or is_manager or is_admin or user.is_staff

    return {
        "is_staff_user": is_staff_member,
        "is_waiter": is_waiter,
        "is_chef": is_chef,
        "is_cashier": is_cashier,
        "is_manager": is_manager,
        "is_admin_user": is_admin,
        "user_portal_url": portal_url,
        "user_portal_name": portal_name,
    }

