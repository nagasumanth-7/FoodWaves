from django.utils import timezone
from django.db.models import Q
from ..models import MenuCategory, MenuItem


def get_active_categories():
    """Returns all active menu categories ordered by name."""
    return MenuCategory.objects.filter(is_active=True).order_by("name")


def get_active_menu_items(category_id=None, search_query=None):
    """
    Returns active menu items, optionally filtered by category and search query.
    """
    queryset = MenuItem.objects.filter(is_active=True).select_related("category")
    if category_id:
        queryset = queryset.filter(category_id=category_id)
    if search_query:
        query = search_query.strip()
        queryset = queryset.filter(
            Q(name__icontains=query) |
            Q(description__icontains=query) |
            Q(category__name__icontains=query)
        )
    return queryset.order_by("category__name", "name")


def get_all_menu_items_for_manager(category_id=None, status_filter=None, search_query=None):
    """
    Returns menu items for the manager catalog with available, unavailable, and archived status filters.
    """
    queryset = MenuItem.objects.select_related("category").all()
    if category_id:
        queryset = queryset.filter(category_id=category_id)
    if status_filter == "available":
        queryset = queryset.filter(is_active=True, is_available=True)
    elif status_filter == "unavailable":
        queryset = queryset.filter(is_active=True, is_available=False)
    elif status_filter == "archived":
        queryset = queryset.filter(is_active=False)
    if search_query:
        query = search_query.strip()
        queryset = queryset.filter(
            Q(name__icontains=query) |
            Q(description__icontains=query) |
            Q(category__name__icontains=query)
        )
    return queryset.order_by("category__name", "name")


def validate_menu_item_availability(menu_item):
    """
    Validates whether a menu item can currently be ordered.
    Returns: (is_valid: bool, error_message: str | None)
    """
    if not menu_item.is_active:
        return False, f"'{menu_item.name}' has been archived and is no longer on the menu."
    if not menu_item.is_available:
        reason = f" ({menu_item.availability_reason})" if menu_item.availability_reason else ""
        return False, f"'{menu_item.name}' is currently unavailable{reason}. Please remove it from your selection."
    return True, None


def update_menu_item_availability(menu_item, is_available: bool, reason: str = "", user=None):
    """
    Updates the availability of a menu item (used by Chef and Manager).
    Restores archived items to active state when toggled.
    """
    menu_item.is_active = True
    menu_item.is_available = is_available
    menu_item.availability_reason = reason.strip() if not is_available else ""
    menu_item.availability_updated_at = timezone.now()
    if user and user.is_authenticated:
        menu_item.availability_updated_by = user
    menu_item.save(update_fields=[
        "is_active",
        "is_available",
        "availability_reason",
        "availability_updated_at",
        "availability_updated_by",
        "updated_at",
    ])
    return menu_item


def delete_or_archive_menu_item(menu_item):
    """
    Section 11 & Section 32:
    - If a MenuItem has never been used in an order: permanently deletes it.
    - If a MenuItem has historical orders: preserves it by setting is_active = False ('Archived').
    Returns: ('deleted' | 'archived')
    """
    if menu_item.has_historical_orders:
        menu_item.is_active = False
        menu_item.is_available = False
        menu_item.save(update_fields=["is_active", "is_available", "updated_at"])
        return "archived"
    else:
        menu_item.delete()
        return "deleted"

