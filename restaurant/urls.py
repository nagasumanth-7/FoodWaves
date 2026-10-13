from django.urls import path
from . import views

app_name = "restaurant"

urlpatterns = [
    # Customer facing
    path("", views.home, name="home"),
    path("menu/", views.menu, name="menu"),
    path("reserve/", views.reservation, name="reservation"),
    path("bookings/", views.booking_history, name="booking_history"),
    path("bookings/<int:booking_id>/cancel/", views.cancel_booking, name="cancel_booking"),

    # Customer Authentication
    path("login/", views.login_page, name="login"),
    path("signup/", views.signup_page, name="signup"),
    path("logout/", views.logout_view, name="logout"),
    path("verification-sent/", views.verification_sent, name="verification_sent"),
    path("verify-email/<str:uidb64>/<str:token>/", views.verify_email, name="verify_email"),
    path("resend-verification/", views.resend_verification, name="resend_verification"),
    path("auth/callback/", views.auth_callback, name="auth_callback"),

    # Manager Menu & Category Management Portal (Sections 8 - 11 & 26)
    path("manager/", views.manager_dashboard, name="manager_dashboard"),
    path("manager/menu/", views.manager_menu_list, name="manager_menu_list"),
    path("manager/menu/add/", views.manager_menu_add, name="manager_menu_add"),
    path("manager/menu/<int:item_id>/edit/", views.manager_menu_edit, name="manager_menu_edit"),
    path("manager/menu/<int:item_id>/delete/", views.manager_menu_delete, name="manager_menu_delete"),
    path("manager/categories/", views.manager_categories_list, name="manager_categories_list"),
    path("manager/categories/add/", views.manager_category_add, name="manager_category_add"),
    path("manager/categories/<int:category_id>/edit/", views.manager_category_edit, name="manager_category_edit"),
    path("manager/categories/<int:category_id>/toggle/", views.manager_category_toggle, name="manager_category_toggle"),
    path("manager/staff-roles/", views.manager_staff_roles, name="manager_staff_roles"),

    # Customer QR Table Ordering Portal (Sections 2, 10, 14-16, 18)
    path("table/<str:qr_token>/", views.table_menu, name="table_menu"),
    path("table/<str:qr_token>/cart/", views.customer_cart, name="customer_cart"),
    path("table/<str:qr_token>/order/", views.customer_place_order, name="customer_place_order"),
    path("table/<str:qr_token>/orders/", views.customer_orders, name="customer_orders"),
    path("table/<str:qr_token>/bill/", views.customer_bill, name="customer_bill"),
    path("table/<str:qr_token>/request-bill/", views.customer_request_bill, name="customer_request_bill"),

    # Chef Menu Availability Portal (Sections 12, 13 & 26)
    path("chef/", views.chef_dashboard, name="chef_dashboard"),
    path("chef/menu/", views.chef_menu_list, name="chef_menu_list"),
    path("chef/menu/<int:item_id>/availability/", views.chef_menu_toggle_availability, name="chef_menu_toggle_availability"),
    path("chef/kds/", views.chef_kds, name="chef_kds"),
    path("chef/order-item/<int:item_id>/status/", views.chef_item_status, name="chef_item_status"),

    # Floor Waiter Portal
    path("waiter/", views.waiter_dashboard, name="waiter_dashboard"),
    path("waiter/table/<int:table_id>/", views.waiter_table_detail, name="waiter_table_detail"),
    path("waiter/table/<int:table_id>/open/", views.waiter_table_open, name="waiter_table_open"),
    path("waiter/table/<int:table_id>/order/", views.waiter_take_order, name="waiter_take_order"),
    path("waiter/table/<int:table_id>/request-bill/", views.waiter_request_bill, name="waiter_request_bill"),
    path("waiter/order-item/<int:item_id>/serve/", views.waiter_serve_item, name="waiter_serve_item"),

    # Cashier POS & Billing Portal
    path("cashier/", views.cashier_dashboard, name="cashier_dashboard"),
    path("cashier/bill/<int:bill_id>/settle/", views.cashier_settle_bill, name="cashier_settle_bill"),
    path("cashier/table/<int:table_id>/generate-bill/", views.cashier_generate_bill, name="cashier_generate_bill"),
    path("cashier/bill/<int:bill_id>/receipt/", views.cashier_receipt, name="cashier_receipt"),
    path("cashier/reports/", views.cashier_reports, name="cashier_reports"),

    # Validation APIs (Section 15 & 16)
    path("api/cart/validate/", views.api_validate_cart, name="api_validate_cart"),
]
