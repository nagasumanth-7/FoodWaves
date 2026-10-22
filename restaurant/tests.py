import json
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User, Group
from django.utils import timezone
from datetime import timedelta
from unittest.mock import patch
from django.core.management import call_command

from .models import (
    UserProfile,
    Reservation,
    MenuCategory,
    MenuItem,
    RestaurantTable,
    TableSession,
    Order,
    OrderItem,
)
from .services.menu_service import (
    delete_or_archive_menu_item,
    validate_menu_item_availability,
    update_menu_item_availability,
)
from .services.order_service import validate_order_item_selection


class SupabaseAuthAndReservationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.signup_url = reverse("restaurant:signup")
        self.login_url = reverse("restaurant:login")
        self.logout_url = reverse("restaurant:logout")
        self.reserve_url = reverse("restaurant:reservation")
        self.bookings_url = reverse("restaurant:booking_history")
        self.auth_callback_url = reverse("restaurant:auth_callback")
        self.resend_url = reverse("restaurant:resend_verification")

    @patch("restaurant.views.supabase_sign_up")
    def test_signup_creates_inactive_user_and_sends_verification(self, mock_sign_up):
        mock_sign_up.return_value = (True, {"id": "sb-user-123", "identities": [{"id": "id1"}]}, None)

        data = {
            "name": "Sarah Connor",
            "email": "sarah@example.com",
            "phone_number": "+91 9876543210",
            "password": "Password123!",
            "confirm_password": "Password123!",
        }
        response = self.client.post(self.signup_url, data)
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("restaurant:verification_sent"))

        self.assertTrue(mock_sign_up.called)

        user = User.objects.get(email="sarah@example.com")
        self.assertFalse(user.is_active)
        self.assertEqual(user.first_name, "Sarah Connor")
        self.assertEqual(user.profile.phone_number, "+91 9876543210")
        self.assertFalse(user.profile.is_email_verified)

    @patch("restaurant.views.supabase_sign_in")
    def test_login_with_unconfirmed_email_fails(self, mock_sign_in):
        mock_sign_in.return_value = (
            False,
            "email_not_confirmed",
            "Email not confirmed. Please check your inbox for the confirmation link sent by Supabase.",
        )

        response = self.client.post(self.login_url, {
            "email": "unconfirmed@example.com",
            "password": "SecretPassword1",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Email confirmation required")
        self.assertContains(response, "unconfirmed@example.com")
        self.assertContains(response, "Resend Verification Email")

    @patch("restaurant.views.supabase_sign_in")
    def test_login_with_confirmed_email_succeeds(self, mock_sign_in):
        mock_sign_in.return_value = (
            True,
            {
                "access_token": "mock-jwt-token",
                "user": {
                    "id": "sb-uid-999",
                    "email": "verified@example.com",
                    "user_metadata": {
                        "name": "Alice Wonderland",
                        "phone_number": "+91 9999999999",
                    },
                },
            },
            None,
        )

        response = self.client.post(self.login_url, {
            "email": "VERIFIED@example.com",
            "password": "SecretPassword1",
        })
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, self.reserve_url)

        user = User.objects.get(email="verified@example.com")
        self.assertTrue(user.is_active)
        self.assertTrue(user.profile.is_email_verified)
        self.assertEqual(self.client.session.get("supabase_token"), "mock-jwt-token")

    @patch("restaurant.views.supabase_resend_confirmation")
    def test_resend_verification_supabase(self, mock_resend):
        mock_resend.return_value = (True, None)
        response = self.client.post(self.resend_url, {"email": "resend@example.com"})
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("restaurant:verification_sent"))
        self.assertTrue(mock_resend.called)

    def test_auth_callback_redirects_to_login(self):
        response = self.client.get(self.auth_callback_url)
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, self.login_url)

    def test_auth_callback_with_error_redirects_to_verification_sent(self):
        url = f"{self.auth_callback_url}?error=access_denied&error_description=Email+link+expired"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("restaurant:verification_sent"))

    @patch("restaurant.views.supabase_sign_out")
    def test_logout_revokes_supabase_token_and_flushes_session(self, mock_sign_out):
        session = self.client.session
        session["supabase_token"] = "jwt-session-token-123"
        session.save()

        response = self.client.get(self.logout_url)
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("restaurant:home"))
        mock_sign_out.assert_called_once_with("jwt-session-token-123")
        self.assertNotIn("supabase_token", self.client.session)

    def test_login_page_renders_view_password_and_forgot_password_link(self):
        response = self.client.get(self.login_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-target="id_password"')
        self.assertContains(response, "btn-toggle-password")
        self.assertContains(response, "Forgot password?")
        self.assertContains(response, reverse("restaurant:forgot_password"))
        self.assertContains(response, 'id="id_recovery_email"')
        self.assertContains(response, "Secured with Supabase Password Recovery")
        self.assertContains(response, "Send Supabase Reset Link")

    @patch("restaurant.views.supabase_reset_password_request")
    def test_login_page_inline_forgot_password_post_dispatches_supabase(self, mock_reset):
        mock_reset.return_value = (True, None)

        response = self.client.post(self.login_url, {
            "action": "forgot_password",
            "email": "customer@example.com",
        })
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, self.login_url)
        self.assertTrue(mock_reset.called)
        call_args = mock_reset.call_args
        self.assertEqual(call_args[0][0], "customer@example.com")
        self.assertIn("reset-password", call_args[1]["redirect_to"])

    @patch("restaurant.views.supabase_reset_password_request")
    def test_login_page_inline_forgot_password_ajax_returns_json(self, mock_reset):
        mock_reset.return_value = (True, None)

        response = self.client.post(
            self.login_url,
            {"action": "forgot_password", "email": "customer@example.com"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(mock_reset.called)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertIn("dispatched by Supabase", data["message"])
        self.assertEqual(data["email"], "customer@example.com")

    @patch("restaurant.views.supabase_reset_password_request")
    def test_forgot_password_page_ajax_returns_json(self, mock_reset):
        mock_reset.return_value = (True, None)

        forgot_url = reverse("restaurant:forgot_password")
        response = self.client.post(
            forgot_url,
            {"email": "customer@example.com"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(mock_reset.called)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertIn("customer@example.com", data["email"])

    def test_signup_page_renders_view_password_toggles(self):
        response = self.client.get(self.signup_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-target="id_password"')
        self.assertContains(response, 'data-target="id_confirm_password"')
        self.assertContains(response, "btn-toggle-password")

    @patch("restaurant.views.supabase_reset_password_request")
    def test_forgot_password_page_dispatches_supabase_recovery(self, mock_reset):
        mock_reset.return_value = (True, None)

        forgot_url = reverse("restaurant:forgot_password")
        response_get = self.client.get(forgot_url)
        self.assertEqual(response_get.status_code, 200)
        self.assertContains(response_get, "Forgot password?")

        response_post = self.client.post(forgot_url, {"email": "customer@example.com"})
        self.assertEqual(response_post.status_code, 200)
        self.assertTrue(mock_reset.called)
        self.assertContains(response_post, "customer@example.com")
        self.assertContains(response_post, "Check your email")

    @patch("restaurant.views.supabase_update_user_password")
    def test_reset_password_page_updates_password_via_supabase(self, mock_update_pw):
        mock_update_pw.return_value = (True, {"email": "customer@example.com"}, None)

        # Create user in Django DB
        user = User.objects.create_user(
            username="customer@example.com",
            email="customer@example.com",
            password="OldPassword123!",
            is_active=True,
        )

        reset_url = reverse("restaurant:reset_password")
        response_get = self.client.get(reset_url)
        self.assertEqual(response_get.status_code, 200)
        self.assertContains(response_get, "btn-toggle-password")
        self.assertContains(response_get, 'data-target="id_new_password"')
        self.assertContains(response_get, 'data-target="id_confirm_new_password"')

        # Post new password with Supabase access_token
        response_post = self.client.post(reset_url, {
            "new_password": "NewSecurePassword456!",
            "confirm_new_password": "NewSecurePassword456!",
            "access_token": "mock-supabase-access-token",
            "email": "customer@example.com",
        })
        self.assertEqual(response_post.status_code, 302)
        self.assertRedirects(response_post, self.login_url)

        self.assertTrue(mock_update_pw.called)
        user.refresh_from_db()
        self.assertTrue(user.check_password("NewSecurePassword456!"))

    def test_prebook_table_requires_login(self):
        booking_data = {
            "name": "Guest User",
            "email": "guest@example.com",
            "phone_number": "1234567890",
            "date": (timezone.localdate() + timedelta(days=2)).isoformat(),
            "time": "07:30 PM",
            "guests": 4,
            "seating_preference": "Indoor",
            "special_requests": "Window seat please",
        }
        response = self.client.post(self.reserve_url, booking_data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(self.login_url))

    def test_prebook_table_and_view_history(self):
        user = User.objects.create_user(
            username="diner@example.com",
            email="diner@example.com",
            password="Password123",
            first_name="David",
            is_active=True,
        )
        UserProfile.objects.create(user=user, phone_number="9876543210", is_email_verified=True)
        self.client.force_login(user)

        tomorrow = timezone.localdate() + timedelta(days=1)
        booking_data = {
            "name": "David Miller",
            "email": "diner@example.com",
            "phone_number": "9876543210",
            "date": tomorrow.isoformat(),
            "time": "07:30 PM",
            "guests": 3,
            "seating_preference": "Outdoor",
            "special_requests": "Anniversary dinner",
        }
        response = self.client.post(self.reserve_url, booking_data)
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, self.bookings_url)

        booking = Reservation.objects.filter(user=user).first()
        self.assertIsNotNone(booking)
        self.assertEqual(booking.guests, 3)
        self.assertEqual(booking.seating_preference, "Outdoor")
        self.assertEqual(booking.status, "CONFIRMED")

        history_response = self.client.get(self.bookings_url)
        self.assertEqual(history_response.status_code, 200)
        self.assertContains(history_response, "David Miller")
        self.assertContains(history_response, "07:30 PM")
        self.assertContains(history_response, "Outdoor")

    def test_cancel_upcoming_reservation(self):
        user = User.objects.create_user(
            username="canceler@example.com",
            email="canceler@example.com",
            password="Password123",
            is_active=True,
        )
        UserProfile.objects.create(user=user, phone_number="1234567890", is_email_verified=True)
        self.client.force_login(user)

        future_date = timezone.localdate() + timedelta(days=3)
        booking = Reservation.objects.create(
            user=user,
            name="Canceler",
            email="canceler@example.com",
            phone_number="1234567890",
            date=future_date,
            time="08:00 PM",
            guests=2,
            status="CONFIRMED",
        )

        cancel_url = reverse("restaurant:cancel_booking", kwargs={"booking_id": booking.id})
        response = self.client.post(cancel_url)
        self.assertEqual(response.status_code, 302)

        booking.refresh_from_db()
        self.assertEqual(booking.status, "CANCELLED")


# ==============================================================================
# MENU MANAGEMENT AND SECURITY TESTS (Section 33)
# ==============================================================================

class MenuManagementAndSecurityTests(TestCase):
    def setUp(self):
        self.client = Client()

        # Groups
        self.manager_group, _ = Group.objects.get_or_create(name="MANAGER")
        self.chef_group, _ = Group.objects.get_or_create(name="CHEF")
        self.waiter_group, _ = Group.objects.get_or_create(name="WAITER")
        self.cashier_group, _ = Group.objects.get_or_create(name="CASHIER")
        self.customer_group, _ = Group.objects.get_or_create(name="CUSTOMER")

        # Users
        self.customer = User.objects.create_user(username="cust@test.com", email="cust@test.com", password="Pass", is_active=True)
        self.customer.groups.add(self.customer_group)

        self.manager = User.objects.create_user(username="mgr@test.com", email="mgr@test.com", password="Pass", is_active=True)
        self.manager.groups.add(self.manager_group)

        self.chef = User.objects.create_user(username="chef@test.com", email="chef@test.com", password="Pass", is_active=True)
        self.chef.groups.add(self.chef_group)

        self.waiter = User.objects.create_user(username="waiter@test.com", email="waiter@test.com", password="Pass", is_active=True)
        self.waiter.groups.add(self.waiter_group)

        self.cashier = User.objects.create_user(username="cashier@test.com", email="cashier@test.com", password="Pass", is_active=True)
        self.cashier.groups.add(self.cashier_group)

        # Initial Categories & Items
        self.cat_starters = MenuCategory.objects.create(name="Starters", description="Hot starters")
        self.cat_biryani = MenuCategory.objects.create(name="Rice & Biryani", description="Dum Biryani")

        self.item_biryani = MenuItem.objects.create(
            category=self.cat_biryani,
            name="Chicken Biryani",
            description="Royal Dum Biryani",
            price=Decimal("280.00"),
            preparation_time=25,
            is_active=True,
            is_available=True,
        )

        self.item_tikka = MenuItem.objects.create(
            category=self.cat_starters,
            name="Paneer Tikka",
            description="Tandoor cottage cheese",
            price=Decimal("240.00"),
            preparation_time=15,
            is_active=True,
            is_available=True,
        )

        # URLs
        self.menu_url = reverse("restaurant:menu")
        self.mgr_dash_url = reverse("restaurant:manager_dashboard")
        self.mgr_menu_url = reverse("restaurant:manager_menu_list")
        self.mgr_add_url = reverse("restaurant:manager_menu_add")
        self.chef_dash_url = reverse("restaurant:chef_dashboard")
        self.chef_menu_url = reverse("restaurant:chef_menu_list")

    # 1. Customer can see menu loaded from PostgreSQL (Item 1 & 2)
    def test_customer_can_see_menu_from_database(self):
        response = self.client.get(self.menu_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Chicken Biryani")
        self.assertContains(response, "280.00")
        self.assertContains(response, "Paneer Tikka")
        self.assertContains(response, "240.00")
        self.assertContains(response, "Starters")
        self.assertContains(response, "Rice &amp; Biryani")

    # 2. Categories filter in view (Item 3)
    def test_menu_category_filtering(self):
        response = self.client.get(f"{self.menu_url}?category={self.cat_starters.slug_identifier}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Paneer Tikka")

    # 3. Manager can add new menu item (Item 4)
    def test_manager_can_add_menu_item(self):
        self.client.force_login(self.manager)
        data = {
            "category": self.cat_starters.id,
            "name": "Andhra Chicken 65",
            "description": "Crispy boneless chicken",
            "price": "290.00",
            "preparation_time": 15,
            "is_available": True,
        }
        response = self.client.post(self.mgr_add_url, data)
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, self.mgr_menu_url)

        item = MenuItem.objects.filter(name="Andhra Chicken 65").first()
        self.assertIsNotNone(item)
        self.assertEqual(item.price, Decimal("290.00"))
        self.assertEqual(item.preparation_time, 15)

    # 4. Manager can edit menu item (Item 5)
    def test_manager_can_edit_menu_item(self):
        self.client.force_login(self.manager)
        edit_url = reverse("restaurant:manager_menu_edit", kwargs={"item_id": self.item_biryani.id})
        data = {
            "category": self.cat_biryani.id,
            "name": "Special Chicken Biryani",
            "description": "Updated description",
            "price": "320.00",
            "preparation_time": 30,
            "is_available": True,
        }
        response = self.client.post(edit_url, data)
        self.assertEqual(response.status_code, 302)

        self.item_biryani.refresh_from_db()
        self.assertEqual(self.item_biryani.name, "Special Chicken Biryani")
        self.assertEqual(self.item_biryani.price, Decimal("320.00"))
        self.assertEqual(self.item_biryani.preparation_time, 30)

    # 5. Manager can permanently delete unused item (Item 6)
    def test_manager_delete_unused_item_permanently_deletes(self):
        self.client.force_login(self.manager)
        del_url = reverse("restaurant:manager_menu_delete", kwargs={"item_id": self.item_tikka.id})

        # GET confirmation view
        get_resp = self.client.get(del_url)
        self.assertEqual(get_resp.status_code, 200)
        self.assertContains(get_resp, "Paneer Tikka")

        # POST delete
        post_resp = self.client.post(del_url)
        self.assertEqual(post_resp.status_code, 302)

        self.assertFalse(MenuItem.objects.filter(id=self.item_tikka.id).exists())

    # 6. Manager archives used item instead of permanently deleting (Item 7 & 32)
    def test_manager_delete_item_with_historical_orders_archives_it(self):
        self.client.force_login(self.manager)

        # Create historical table session and order item
        table = RestaurantTable.objects.create(table_number="T01", capacity=4)
        session = TableSession.objects.create(table=table, guest_name="VIP Guest", guest_count=2)
        order = Order.objects.create(session=session, source="CUSTOMER")
        OrderItem.objects.create(order=order, menu_item=self.item_biryani, quantity=2, unit_price=Decimal("280.00"))

        del_url = reverse("restaurant:manager_menu_delete", kwargs={"item_id": self.item_biryani.id})

        # GET confirms archive warning
        get_resp = self.client.get(del_url)
        self.assertEqual(get_resp.status_code, 200)
        self.assertContains(get_resp, "Historical Preservation Notice")

        # POST should archive (is_active = False)
        post_resp = self.client.post(del_url)
        self.assertEqual(post_resp.status_code, 302)

        self.item_biryani.refresh_from_db()
        self.assertTrue(MenuItem.objects.filter(id=self.item_biryani.id).exists())
        self.assertFalse(self.item_biryani.is_active)
        self.assertFalse(self.item_biryani.is_available)

    # 7. Chef can mark item unavailable (Item 8)
    def test_chef_can_mark_item_unavailable(self):
        self.client.force_login(self.chef)
        toggle_url = reverse("restaurant:chef_menu_toggle_availability", kwargs={"item_id": self.item_tikka.id})
        response = self.client.post(toggle_url, {
            "action": "mark_unavailable",
            "reason": "Paneer stock finished",
        })
        self.assertEqual(response.status_code, 302)

        self.item_tikka.refresh_from_db()
        self.assertFalse(self.item_tikka.is_available)
        self.assertEqual(self.item_tikka.availability_reason, "Paneer stock finished")
        self.assertEqual(self.item_tikka.availability_updated_by, self.chef)

    # 8. Chef can mark item available (Item 9)
    def test_chef_can_mark_item_available(self):
        self.item_tikka.is_available = False
        self.item_tikka.availability_reason = "Out of stock"
        self.item_tikka.save()

        self.client.force_login(self.chef)
        toggle_url = reverse("restaurant:chef_menu_toggle_availability", kwargs={"item_id": self.item_tikka.id})
        response = self.client.post(toggle_url, {
            "action": "mark_available",
        })
        self.assertEqual(response.status_code, 302)

        self.item_tikka.refresh_from_db()
        self.assertTrue(self.item_tikka.is_available)
        self.assertEqual(self.item_tikka.availability_reason, "")

    # 9. Customer sees unavailable status (Item 10 & 11)
    def test_customer_sees_unavailable_status_and_no_add_button(self):
        self.item_tikka.is_available = False
        self.item_tikka.availability_reason = "Paneer stock finished"
        self.item_tikka.save()

        response = self.client.get(self.menu_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Currently Unavailable")
        self.assertContains(response, "Paneer stock finished")
        self.assertContains(response, "Kitchen Sold Out")

    # 10. Backend rejects unavailable item (Item 12, 15, 16)
    def test_backend_rejects_unavailable_item(self):
        self.item_tikka.is_available = False
        self.item_tikka.save()

        # Service level rejection
        with self.assertRaises(Exception):
            validate_order_item_selection(self.item_tikka.id, quantity=1)

        # API validation rejection
        api_url = reverse("restaurant:api_validate_cart")
        payload = json.dumps({"items": [{"item_id": self.item_tikka.id, "quantity": 1}]})
        response = self.client.post(api_url, payload, content_type="application/json")
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data.get("valid"))
        self.assertTrue(any("Paneer Tikka" in err for err in data.get("errors", [])))

    # 11. Security: Customer cannot access manager URLs (Item 13)
    def test_customer_cannot_access_manager_urls(self):
        self.client.force_login(self.customer)
        response = self.client.get(self.mgr_dash_url)
        self.assertEqual(response.status_code, 403)

        response_add = self.client.get(self.mgr_add_url)
        self.assertEqual(response_add.status_code, 403)

    # 12. Security: Customer cannot access chef URLs (Item 14)
    def test_customer_cannot_access_chef_urls(self):
        self.client.force_login(self.customer)
        response = self.client.get(self.chef_dash_url)
        self.assertEqual(response.status_code, 403)

        response_menu = self.client.get(self.chef_menu_url)
        self.assertEqual(response_menu.status_code, 403)

    # 13. Security: Chef cannot delete or edit menu items (Item 15 & 16)
    def test_chef_cannot_access_manager_delete_or_edit(self):
        self.client.force_login(self.chef)
        edit_url = reverse("restaurant:manager_menu_edit", kwargs={"item_id": self.item_biryani.id})
        response_edit = self.client.get(edit_url)
        self.assertEqual(response_edit.status_code, 403)

        del_url = reverse("restaurant:manager_menu_delete", kwargs={"item_id": self.item_biryani.id})
        response_del = self.client.get(del_url)
        self.assertEqual(response_del.status_code, 403)

    # 14. Security: Waiter and Cashier cannot access manager menu management (Item 17 & 18)
    def test_waiter_and_cashier_cannot_access_manager_urls(self):
        self.client.force_login(self.waiter)
        self.assertEqual(self.client.get(self.mgr_dash_url).status_code, 403)

        self.client.force_login(self.cashier)
        self.assertEqual(self.client.get(self.mgr_dash_url).status_code, 403)

    # 15. Seed menu command is idempotent and does not create duplicates (Item 22)
    def test_seed_menu_command_is_idempotent(self):
        call_command("seed_menu")
        count_first = MenuItem.objects.count()

        call_command("seed_menu")
        count_second = MenuItem.objects.count()

        self.assertEqual(count_first, count_second)

    # 16. Staff direct login via username or email
    def test_staff_direct_login(self):
        # Manager login via email
        response = self.client.post(reverse("restaurant:login"), {
            "email": self.manager.email,
            "password": "Pass",
        })
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("restaurant:manager_dashboard"))

        self.client.logout()

        # Chef login via email/username
        response = self.client.post(reverse("restaurant:login"), {
            "email": self.chef.username,
            "password": "Pass",
        })
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("restaurant:chef_dashboard"))

    # 17. Waiter end-to-end workflow: Seat table, take order, serve dish, request bill
    def test_waiter_workflow_seat_order_serve_bill(self):
        table = RestaurantTable.objects.create(table_number="T-99", capacity=4, status="AVAILABLE")

        self.client.force_login(self.waiter)

        # 1. Seat guests
        seat_url = reverse("restaurant:waiter_table_open", kwargs={"table_id": table.id})
        resp = self.client.post(seat_url, {
            "guest_name": "Roy Family",
            "guest_count": 3,
        })
        self.assertEqual(resp.status_code, 302)
        table.refresh_from_db()
        self.assertEqual(table.status, "OCCUPIED")
        session = table.current_session
        self.assertIsNotNone(session)
        self.assertEqual(session.guest_name, "Roy Family")

        # 2. Take order table-side
        order_url = reverse("restaurant:waiter_take_order", kwargs={"table_id": table.id})
        resp_order = self.client.post(order_url, {
            f"qty_{self.item_biryani.id}": 2,
            f"notes_{self.item_biryani.id}": "Mild spice",
            "instructions": "Serve together",
        })
        self.assertEqual(resp_order.status_code, 302)
        self.assertEqual(session.orders.count(), 1)
        order = session.orders.first()
        self.assertEqual(order.items.count(), 1)
        item = order.items.first()
        self.assertEqual(item.status, "PENDING")

        # 3. Chef accepts and starts preparing dish, then marks item ready
        self.client.force_login(self.chef)
        chef_url = reverse("restaurant:chef_item_status", kwargs={"item_id": item.id})
        resp_prep = self.client.post(chef_url, {"status": "PREPARING", "estimated_minutes": 15})
        self.assertEqual(resp_prep.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.status, "PREPARING")

        resp_chef = self.client.post(chef_url, {"status": "READY"})
        self.assertEqual(resp_chef.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.status, "READY")

        # 4. Waiter serves dish
        self.client.force_login(self.waiter)
        serve_url = reverse("restaurant:waiter_serve_item", kwargs={"item_id": item.id})
        resp_serve = self.client.post(serve_url)
        self.assertEqual(resp_serve.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.status, "SERVED")

        # 5. Waiter requests bill
        req_bill_url = reverse("restaurant:waiter_request_bill", kwargs={"table_id": table.id})
        resp_bill = self.client.post(req_bill_url)
        self.assertEqual(resp_bill.status_code, 302)
        table.refresh_from_db()
        self.assertEqual(table.status, "BILL_PENDING")
        bill = session.bill
        self.assertIsNotNone(bill)
        self.assertEqual(bill.status, "REQUESTED")
        self.assertEqual(bill.subtotal, Decimal("560.00")) # 2 * 280.00

    # 18. Cashier settlement, payment recording, and freeing table
    def test_cashier_settle_bill_and_free_table(self):
        table = RestaurantTable.objects.create(table_number="T-98", capacity=2, status="OCCUPIED")
        session = TableSession.objects.create(
            table=table,
            guest_name="Kapoor",
            guest_count=2,
            waiter=self.waiter,
            status="ACTIVE",
        )
        order = Order.objects.create(session=session, status="ACCEPTED")
        OrderItem.objects.create(
            order=order,
            menu_item=self.item_biryani,
            quantity=1,
            unit_price=Decimal("280.00"),
            status="SERVED",
        )
        from restaurant.services.billing_service import calculate_and_generate_bill
        bill = calculate_and_generate_bill(session)

        self.client.force_login(self.cashier)

        # 1. Cashier dashboard loads
        dash_resp = self.client.get(reverse("restaurant:cashier_dashboard"))
        self.assertEqual(dash_resp.status_code, 200)

        # 2. Cashier settles bill with UPI
        settle_url = reverse("restaurant:cashier_settle_bill", kwargs={"bill_id": bill.id})
        resp_settle = self.client.post(settle_url, {
            "payment_method": "UPI",
            "amount_paid": str(bill.total + Decimal("20.00")),
            "discount_pct": "0.00",
            "tip_amount": "20.00",
            "transaction_id": "UPI12345678",
        })
        self.assertEqual(resp_settle.status_code, 302)
        self.assertRedirects(resp_settle, reverse("restaurant:cashier_receipt", kwargs={"bill_id": bill.id}))

        # Verify bill, session, and table status
        bill.refresh_from_db()
        self.assertEqual(bill.status, "PAID")
        session.refresh_from_db()
        self.assertEqual(session.status, "CLOSED")
        table.refresh_from_db()
        self.assertEqual(table.status, "AVAILABLE")

        # 3. Receipt view returns 200
        receipt_resp = self.client.get(reverse("restaurant:cashier_receipt", kwargs={"bill_id": bill.id}))
        self.assertEqual(receipt_resp.status_code, 200)
        self.assertContains(receipt_resp, "PAID IN FULL")

        # 4. Reports view returns 200
        reports_resp = self.client.get(reverse("restaurant:cashier_reports"))
        self.assertEqual(reports_resp.status_code, 200)

    # 19. Security checks for Waiter and Cashier portals
    def test_role_permissions_waiter_and_cashier(self):
        # Customer blocked from Waiter & Cashier
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(reverse("restaurant:waiter_dashboard")).status_code, 403)
        self.assertEqual(self.client.get(reverse("restaurant:cashier_dashboard")).status_code, 403)

        # Waiter blocked from Cashier
        self.client.force_login(self.waiter)
        self.assertEqual(self.client.get(reverse("restaurant:cashier_dashboard")).status_code, 403)
        self.assertEqual(self.client.get(reverse("restaurant:waiter_dashboard")).status_code, 200)

        # Cashier blocked from Waiter
        self.client.force_login(self.cashier)
        self.assertEqual(self.client.get(reverse("restaurant:waiter_dashboard")).status_code, 403)
        self.assertEqual(self.client.get(reverse("restaurant:cashier_dashboard")).status_code, 200)

    # 20. Sales report accessible by Manager and Cashier with period presets and date constraints
    def test_sales_report_manager_and_cashier_periods_and_constraints(self):
        from restaurant.models import Bill, Payment
        from datetime import timedelta
        today = timezone.localdate()

        # Create a paid bill & payment today
        bill_today = Bill.objects.create(
            order_type="COUNTER",
            customer_name="Today Guest",
            cashier=self.cashier,
            subtotal=Decimal("400.00"),
            tax=Decimal("20.00"),
            discount=Decimal("0.00"),
            tip=Decimal("10.00"),
            total=Decimal("430.00"),
            status="PAID",
        )
        Payment.objects.create(
            bill=bill_today,
            amount=Decimal("430.00"),
            payment_method="UPI",
            status="SUCCESS",
            transaction_id="UPI-TODAY-001",
            processed_by=self.cashier,
        )

        # 1. Manager can access via manager_reports and cashier_reports
        self.client.force_login(self.manager)
        resp_mgr_1 = self.client.get(reverse("restaurant:manager_reports"))
        self.assertEqual(resp_mgr_1.status_code, 200)
        self.assertContains(resp_mgr_1, "Sales &amp; Revenue Report")
        self.assertContains(resp_mgr_1, "Back to Manager Dashboard")
        self.assertContains(resp_mgr_1, "430.00")

        resp_mgr_2 = self.client.get(reverse("restaurant:cashier_reports"))
        self.assertEqual(resp_mgr_2.status_code, 200)

        # 2. Cashier can access via cashier_reports
        self.client.force_login(self.cashier)
        resp_cash = self.client.get(reverse("restaurant:cashier_reports"))
        self.assertEqual(resp_cash.status_code, 200)
        self.assertContains(resp_cash, "Back to Cashier Dashboard")

        # 3. Waiter and Customer are blocked (403 Forbidden)
        self.client.force_login(self.waiter)
        self.assertEqual(self.client.get(reverse("restaurant:manager_reports")).status_code, 403)
        self.assertEqual(self.client.get(reverse("restaurant:cashier_reports")).status_code, 403)

        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(reverse("restaurant:manager_reports")).status_code, 403)
        self.assertEqual(self.client.get(reverse("restaurant:cashier_reports")).status_code, 403)

        # 4. Period presets testing
        self.client.force_login(self.manager)

        # Today preset
        resp_today = self.client.get(f"{reverse('restaurant:manager_reports')}?period=today")
        self.assertEqual(resp_today.status_code, 200)
        self.assertEqual(resp_today.context["period"], "today")
        self.assertEqual(resp_today.context["summary"]["total_revenue"], Decimal("430.00"))

        # Yesterday preset
        resp_yesterday = self.client.get(f"{reverse('restaurant:manager_reports')}?period=yesterday")
        self.assertEqual(resp_yesterday.status_code, 200)
        self.assertEqual(resp_yesterday.context["period"], "yesterday")

        # Week (Last 7 Days) preset
        resp_week = self.client.get(f"{reverse('restaurant:manager_reports')}?period=week")
        self.assertEqual(resp_week.status_code, 200)
        self.assertEqual(resp_week.context["period"], "week")
        self.assertEqual(resp_week.context["summary"]["total_revenue"], Decimal("430.00"))

        # Month (Last 30 Days) preset
        resp_month = self.client.get(f"{reverse('restaurant:manager_reports')}?period=month")
        self.assertEqual(resp_month.status_code, 200)
        self.assertEqual(resp_month.context["period"], "month")

        # 5. Date constraints testing
        # Start date after end date: sanitized automatically without 500 error
        future_or_reversed = f"{reverse('restaurant:manager_reports')}?period=custom&start_date=2026-10-25&end_date=2026-10-10"
        resp_constrained = self.client.get(future_or_reversed)
        self.assertEqual(resp_constrained.status_code, 200)
        # Dates swapped so start_date <= end_date
        self.assertTrue(resp_constrained.context["summary"]["start_date"] <= resp_constrained.context["summary"]["end_date"])

        # Invalid date format handled gracefully with fallback to today
        invalid_url = f"{reverse('restaurant:manager_reports')}?period=custom&start_date=invalid-date&end_date=not-a-date"
        resp_invalid = self.client.get(invalid_url)
        self.assertEqual(resp_invalid.status_code, 200)
        self.assertEqual(resp_invalid.context["summary"]["start_date"], today)


# ==============================================================================
# SECTION 46 & QR ORDERING ARCHITECTURE TESTS
# ==============================================================================

class CustomerQROrderingAndSystemCompletenessTests(TestCase):
    def setUp(self):
        self.client = Client()

        # Seed roles
        self.manager_group, _ = Group.objects.get_or_create(name="MANAGER")
        self.chef_group, _ = Group.objects.get_or_create(name="CHEF")
        self.cashier_group, _ = Group.objects.get_or_create(name="CASHIER")
        self.waiter_group, _ = Group.objects.get_or_create(name="WAITER")

        self.manager = User.objects.create_user(username="test_mgr", email="mgr@foodwaves.test", password="Pass", is_active=True, is_staff=True)
        self.manager.groups.add(self.manager_group)

        self.chef = User.objects.create_user(username="test_chef", email="chef@foodwaves.test", password="Pass", is_active=True, is_staff=True)
        self.chef.groups.add(self.chef_group)

        self.cashier = User.objects.create_user(username="test_cashier", email="cashier@foodwaves.test", password="Pass", is_active=True, is_staff=True)
        self.cashier.groups.add(self.cashier_group)

        self.waiter = User.objects.create_user(username="test_waiter", email="waiter@foodwaves.test", password="Pass", is_active=True, is_staff=True)
        self.waiter.groups.add(self.waiter_group)

        self.category = MenuCategory.objects.create(name="Main Courses", description="Signature mains")
        self.item_curry = MenuItem.objects.create(
            category=self.category,
            name="Butter Chicken",
            price=Decimal("280.00"),
            preparation_time=15,
            is_active=True,
            is_available=True,
        )

        self.table = RestaurantTable.objects.create(
            table_number="T-10",
            capacity=4,
            status="AVAILABLE",
            floor_area="INDOOR",
        )

    # 1. Scanning valid QR code opens table menu and creates active TableSession
    def test_customer_qr_scan_opens_table_menu_and_starts_session(self):
        url = reverse("restaurant:table_menu", kwargs={"qr_token": self.table.qr_token})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Table T-10")
        self.assertContains(response, "Butter Chicken")

        self.table.refresh_from_db()
        self.assertEqual(self.table.status, "OCCUPIED")
        active_session = self.table.current_session
        self.assertIsNotNone(active_session)
        self.assertEqual(active_session.status, "ACTIVE")

    # 2. Scanning invalid QR token returns 404
    def test_invalid_qr_token_returns_404(self):
        import uuid
        fake_token = str(uuid.uuid4())
        url = reverse("restaurant:table_menu", kwargs={"qr_token": fake_token})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    # 3. Multiple customers scanning the same table QR code join the same active session
    def test_multiple_customers_share_same_active_session(self):
        url = reverse("restaurant:table_menu", kwargs={"qr_token": self.table.qr_token})

        client1 = Client()
        client2 = Client()

        resp1 = client1.get(url)
        self.assertEqual(resp1.status_code, 200)
        session_id_1 = resp1.context["session"].id

        resp2 = client2.get(url)
        self.assertEqual(resp2.status_code, 200)
        session_id_2 = resp2.context["session"].id

        self.assertEqual(session_id_1, session_id_2)
        self.assertEqual(TableSession.objects.filter(table=self.table, status="ACTIVE").count(), 1)

    # 4. Out of service table returns 503 out of service page
    def test_out_of_service_table_renders_error(self):
        self.table.status = "OUT_OF_SERVICE"
        self.table.save()

        url = reverse("restaurant:table_menu", kwargs={"qr_token": self.table.qr_token})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "Table Out of Service", status_code=503)

    # 5. Customer places order and OrderItem freezes unit price
    def test_order_creation_freezes_item_price_against_future_updates(self):
        session = TableSession.objects.create(table=self.table, guest_name="Royals", status="ACTIVE")
        self.table.status = "OCCUPIED"
        self.table.save()

        order_url = reverse("restaurant:customer_place_order", kwargs={"qr_token": self.table.qr_token})
        payload = json.dumps({
            "items": [{"item_id": self.item_curry.id, "quantity": 2, "notes": "Extra butter"}],
            "instructions": "Serve hot",
        })

        resp = self.client.post(order_url, payload, content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "success")

        # Verify Order & OrderItem
        order = session.orders.first()
        self.assertIsNotNone(order)
        self.assertEqual(order.source, "CUSTOMER")
        order_item = order.items.first()
        self.assertEqual(order_item.quantity, 2)
        self.assertEqual(order_item.unit_price, Decimal("280.00"))
        self.assertEqual(order_item.status, "PENDING")

        # Now Manager raises price to ₹350.00
        self.item_curry.price = Decimal("350.00")
        self.item_curry.save()

        # Existing order item price must remain frozen at ₹280.00
        order_item.refresh_from_db()
        self.assertEqual(order_item.unit_price, Decimal("280.00"))
        self.assertEqual(order_item.total_price, Decimal("560.00"))

    # 6. Customer cannot order unavailable item
    def test_customer_cannot_order_unavailable_dish(self):
        session = TableSession.objects.create(table=self.table, guest_name="Guest", status="ACTIVE")
        self.item_curry.is_available = False
        self.item_curry.availability_reason = "Out of cream"
        self.item_curry.save()

        order_url = reverse("restaurant:customer_place_order", kwargs={"qr_token": self.table.qr_token})
        payload = json.dumps({
            "items": [{"item_id": self.item_curry.id, "quantity": 1}],
        })

        resp = self.client.post(order_url, payload, content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertEqual(data["status"], "error")
        self.assertIn("Out of cream", data["message"])

    # 7. Customer requests bill from table: Bill -> REQUESTED, Table -> BILL_PENDING
    def test_customer_requests_bill(self):
        session = TableSession.objects.create(table=self.table, guest_name="VIP", status="ACTIVE")
        order = Order.objects.create(session=session, status="ACCEPTED")
        OrderItem.objects.create(
            order=order,
            menu_item=self.item_curry,
            quantity=1,
            unit_price=Decimal("280.00"),
            status="SERVED",
        )

        req_url = reverse("restaurant:customer_request_bill", kwargs={"qr_token": self.table.qr_token})
        resp = self.client.post(req_url)
        self.assertEqual(resp.status_code, 302)

        self.table.refresh_from_db()
        self.assertEqual(self.table.status, "BILL_PENDING")
        session.refresh_from_db()
        self.assertEqual(session.status, "BILL_PENDING")
        bill = session.bill
        self.assertEqual(bill.status, "REQUESTED")
        self.assertEqual(bill.subtotal, Decimal("280.00"))
        # 5% GST = 14.00, total = 294.00
        self.assertEqual(bill.tax, Decimal("14.00"))
        self.assertEqual(bill.total, Decimal("294.00"))

    # 8. Duplicate payment rejection
    def test_duplicate_payment_rejected(self):
        from restaurant.services.billing_service import calculate_and_generate_bill, process_bill_payment
        from django.core.exceptions import ValidationError

        session = TableSession.objects.create(table=self.table, guest_name="Guest", status="ACTIVE")
        order = Order.objects.create(session=session, status="ACCEPTED")
        OrderItem.objects.create(order=order, menu_item=self.item_curry, quantity=1, unit_price=Decimal("280.00"), status="SERVED")
        bill = calculate_and_generate_bill(session)

        # First payment succeeds
        process_bill_payment(bill, payment_method="CASH", amount_paid=bill.total, processed_by=self.cashier)
        bill.refresh_from_db()
        self.assertEqual(bill.status, "PAID")

        # Second payment fails
        with self.assertRaises(ValidationError):
            process_bill_payment(bill, payment_method="UPI", amount_paid=bill.total, processed_by=self.cashier)

    # 9. Manager category management CRUD & Staff role assignments
    def test_manager_categories_and_staff_roles(self):
        self.client.force_login(self.manager)

        # 1. Add Category
        add_cat_url = reverse("restaurant:manager_category_add")
        resp_add = self.client.post(add_cat_url, {
            "name": "Artisan Desserts",
            "description": "Rich artisanal desserts",
            "is_active": True,
        })
        self.assertEqual(resp_add.status_code, 302)
        new_cat = MenuCategory.objects.get(name="Artisan Desserts")
        self.assertTrue(new_cat.is_active)

        # 2. Toggle Category
        toggle_cat_url = reverse("restaurant:manager_category_toggle", kwargs={"category_id": new_cat.id})
        resp_toggle = self.client.post(toggle_cat_url)
        self.assertEqual(resp_toggle.status_code, 302)
        new_cat.refresh_from_db()
        self.assertFalse(new_cat.is_active)

        # 3. Staff Roles Assignment
        roles_url = reverse("restaurant:manager_staff_roles")
        test_user = User.objects.create_user(username="newhire", email="newhire@test.com", password="Pass")
        resp_roles = self.client.post(roles_url, {
            "user_id": test_user.id,
            "role": "WAITER",
        })
        self.assertEqual(resp_roles.status_code, 302)
        test_user.refresh_from_db()
        self.assertTrue(test_user.groups.filter(name="WAITER").exists())
        self.assertTrue(test_user.is_staff)

    # 10. Failed payments do not close session; successful payments close session and release table
    def test_payment_failure_and_success_lifecycle(self):
        from restaurant.services.billing_service import calculate_and_generate_bill, record_payment_failure, process_bill_payment

        session = TableSession.objects.create(table=self.table, guest_name="Guest Party", status="ACTIVE")
        order = Order.objects.create(session=session, status="ACCEPTED")
        OrderItem.objects.create(order=order, menu_item=self.item_curry, quantity=1, unit_price=Decimal("280.00"), status="SERVED")
        bill = calculate_and_generate_bill(session, bill_status="REQUESTED")

        self.table.refresh_from_db()
        self.assertEqual(self.table.status, "BILL_PENDING")
        session.refresh_from_db()
        self.assertEqual(session.status, "BILL_PENDING")

        # 1. Record failed payment
        failed_pmt = record_payment_failure(bill, payment_method="UPI", amount=bill.total, reason="Network timeout", processed_by=self.cashier)
        self.assertEqual(failed_pmt.status, "FAILED")
        bill.refresh_from_db()
        self.assertNotEqual(bill.status, "PAID")
        session.refresh_from_db()
        self.assertEqual(session.status, "BILL_PENDING")
        self.table.refresh_from_db()
        self.assertEqual(self.table.status, "BILL_PENDING")

        # 2. Process successful payment
        success_pmt = process_bill_payment(bill, payment_method="CASH", amount_paid=bill.total, processed_by=self.cashier)
        self.assertEqual(success_pmt.status, "SUCCESS")
        bill.refresh_from_db()
        self.assertEqual(bill.status, "PAID")
        self.assertEqual(bill.cashier, self.cashier)

        # Session closed and table released to AVAILABLE
        session.refresh_from_db()
        self.assertEqual(session.status, "CLOSED")
        self.assertIsNotNone(session.closed_at)
        self.table.refresh_from_db()
        self.assertEqual(self.table.status, "AVAILABLE")

    # 11. Category deletion is protected when items belong to it
    def test_category_deletion_is_protected(self):
        from django.db.models import ProtectedError
        cat = MenuCategory.objects.create(name="Tandoor Delights")
        item = MenuItem.objects.create(
            category=cat,
            name="Murgh Malai Tikka",
            price=Decimal("320.00"),
            is_active=True,
        )
        with self.assertRaises(ProtectedError):
            cat.delete()

        # Item still exists intact
        self.assertTrue(MenuItem.objects.filter(pk=item.pk).exists())

    # 12. Manager tables management & QR code generation endpoints
    def test_manager_tables_and_qr_generation(self):
        self.client.force_login(self.manager)

        # Tables list view
        resp_list = self.client.get(reverse("restaurant:manager_tables_list"))
        self.assertEqual(resp_list.status_code, 200)
        self.assertContains(resp_list, self.table.table_number)

        # Add table
        resp_add = self.client.post(reverse("restaurant:manager_table_add"), {
            "table_number": "T-99",
            "capacity": 6,
            "floor_area": "TERRACE",
            "status": "AVAILABLE",
            "is_active": True,
        })
        self.assertEqual(resp_add.status_code, 302)
        new_table = RestaurantTable.objects.get(table_number="T-99")
        self.assertIsNotNone(new_table.qr_token)

        # QR Printable Card View
        resp_qr = self.client.get(reverse("restaurant:manager_table_qr_view", kwargs={"table_id": new_table.id}))
        self.assertEqual(resp_qr.status_code, 200)
        self.assertContains(resp_qr, "T-99")

        # QR PNG image endpoint
        resp_img = self.client.get(reverse("restaurant:manager_table_qr_image", kwargs={"table_id": new_table.id}))
        self.assertEqual(resp_img.status_code, 200)
        self.assertEqual(resp_img["Content-Type"], "image/png")
        # Valid PNG magic header
        self.assertTrue(resp_img.content.startswith(b"\x89PNG\r\n\x1a\n"))

    # 13. Staff role isolation: Waiter, Chef, Cashier cannot cross boundaries
    def test_staff_role_isolation_and_403(self):
        # Waiter tries to access cashier and chef portals -> 403 Forbidden
        self.client.force_login(self.waiter)
        resp_cashier = self.client.get(reverse("restaurant:cashier_dashboard"))
        self.assertEqual(resp_cashier.status_code, 403)
        resp_chef = self.client.get(reverse("restaurant:chef_dashboard"))
        self.assertEqual(resp_chef.status_code, 403)

        # Chef tries to access waiter and cashier portals -> 403 Forbidden
        self.client.force_login(self.chef)
        resp_waiter = self.client.get(reverse("restaurant:waiter_dashboard"))
        self.assertEqual(resp_waiter.status_code, 403)
        resp_cashier = self.client.get(reverse("restaurant:cashier_dashboard"))
        self.assertEqual(resp_cashier.status_code, 403)

        # Cashier tries to access waiter and chef portals -> 403 Forbidden
        self.client.force_login(self.cashier)
        resp_waiter = self.client.get(reverse("restaurant:waiter_dashboard"))
        self.assertEqual(resp_waiter.status_code, 403)
        resp_chef = self.client.get(reverse("restaurant:chef_dashboard"))
        self.assertEqual(resp_chef.status_code, 403)

        # Logout completely flushes session and redirects to public home
        resp_logout = self.client.get(reverse("restaurant:logout"))
        self.assertEqual(resp_logout.status_code, 302)
        self.assertRedirects(resp_logout, reverse("restaurant:home"))

        resp_home = self.client.get(reverse("restaurant:home"))
        self.assertEqual(resp_home.status_code, 200)
        self.assertNotContains(resp_home, reverse("restaurant:waiter_dashboard"))
        self.assertNotContains(resp_home, reverse("restaurant:chef_dashboard"))
        self.assertNotContains(resp_home, reverse("restaurant:cashier_dashboard"))
        self.assertNotContains(resp_home, reverse("restaurant:manager_dashboard"))

    # 14. Cashier direct counter bill creation and DB price & 5% GST calculations
    def test_cashier_direct_counter_bill_service_calculation(self):
        from restaurant.services.billing_service import create_direct_counter_bill

        # Create second item
        item_paneer = MenuItem.objects.create(
            category=self.category,
            name="Paneer Butter Masala",
            price=Decimal("240.00"),
            is_active=True,
            is_available=True,
        )

        items_data = [
            {"item_id": self.item_curry.id, "quantity": 2},  # 280.00 * 2 = 560.00
            {"item_id": item_paneer.id, "quantity": 1},      # 240.00 * 1 = 240.00
        ]
        # Subtotal = 800.00, 10% discount = 80.00, Taxable = 720.00, 5% GST = 36.00, Tip = 20.00, Total = 776.00
        bill = create_direct_counter_bill(
            items_data=items_data,
            cashier_user=self.cashier,
            customer_name="Ramesh Sharma",
            customer_phone="9876543210",
            order_type="COUNTER",
            discount_pct=Decimal("10.00"),
            tip_amount=Decimal("20.00"),
            settle_immediately=False,
        )

        self.assertIsNone(bill.session)
        self.assertEqual(bill.order_type, "COUNTER")
        self.assertEqual(bill.customer_name, "Ramesh Sharma")
        self.assertEqual(bill.subtotal, Decimal("800.00"))
        self.assertEqual(bill.discount, Decimal("80.00"))
        self.assertEqual(bill.tax, Decimal("36.00"))
        self.assertEqual(bill.tip, Decimal("20.00"))
        self.assertEqual(bill.total, Decimal("776.00"))
        self.assertEqual(bill.status, "UNPAID")
        self.assertEqual(bill.total_items_count, 2)

        # Check linked order and items
        order = bill.orders.first()
        self.assertIsNotNone(order)
        self.assertEqual(order.source, "CASHIER")
        self.assertEqual(order.status, "SERVED")
        self.assertEqual(order.items.count(), 2)

    # 15. Cashier direct counter bill with immediate settlement
    def test_cashier_direct_counter_bill_immediate_settlement(self):
        from restaurant.services.billing_service import create_direct_counter_bill

        items_data = [{"item_id": self.item_curry.id, "quantity": 1}]
        # Subtotal = 280.00, 5% GST = 14.00, Total = 294.00
        bill = create_direct_counter_bill(
            items_data=items_data,
            cashier_user=self.cashier,
            customer_name="Sneha Patel",
            customer_phone="9123456789",
            order_type="TAKEAWAY",
            settle_immediately=True,
            payment_method="UPI",
            transaction_id="UPI-TXN-9988",
        )

        self.assertEqual(bill.status, "PAID")
        self.assertEqual(bill.total, Decimal("294.00"))
        payment = bill.payments.first()
        self.assertIsNotNone(payment)
        self.assertEqual(payment.payment_method, "UPI")
        self.assertEqual(payment.status, "SUCCESS")
        self.assertEqual(payment.amount, Decimal("294.00"))
        self.assertEqual(payment.transaction_id, "UPI-TXN-9988")

    # 16. Cashier direct bill views: GET catalog, POST generate, settle bill, and receipt
    def test_cashier_direct_bill_views_and_checkout_flow(self):
        from restaurant.models import Bill
        self.client.force_login(self.cashier)

        # 1. GET direct bill interface
        url_new_bill = reverse("restaurant:cashier_direct_bill")
        resp_get = self.client.get(url_new_bill)
        self.assertEqual(resp_get.status_code, 200)
        self.assertContains(resp_get, "Butter Chicken")

        # 2. POST to generate unpaid bill
        payload = {
            "customer_name": "Walk-in Guest",
            "order_type": "COUNTER",
            "cart_data": json.dumps([{"item_id": self.item_curry.id, "quantity": 1}]),
            "discount_pct": "0.00",
            "tip_amount": "0.00",
            "action": "generate",
        }
        resp_post = self.client.post(url_new_bill, payload)
        self.assertEqual(resp_post.status_code, 302)

        bill = Bill.objects.filter(customer_name="Walk-in Guest").first()
        self.assertIsNotNone(bill)
        self.assertEqual(bill.status, "UNPAID")
        self.assertRedirects(resp_post, reverse("restaurant:cashier_settle_bill", kwargs={"bill_id": bill.id}))

        # 3. Settle the unpaid direct bill via Cash
        url_settle = reverse("restaurant:cashier_settle_bill", kwargs={"bill_id": bill.id})
        resp_settle_get = self.client.get(url_settle)
        self.assertEqual(resp_settle_get.status_code, 200)
        self.assertContains(resp_settle_get, "Direct Counter Bill Checkout")

        resp_settle_post = self.client.post(url_settle, {
            "payment_method": "CASH",
            "amount_paid": str(bill.total),
            "discount_pct": "0.00",
            "tip_amount": "0.00",
        })
        self.assertEqual(resp_settle_post.status_code, 302)
        self.assertRedirects(resp_settle_post, reverse("restaurant:cashier_receipt", kwargs={"bill_id": bill.id}))

        # 4. View Receipt
        resp_receipt = self.client.get(reverse("restaurant:cashier_receipt", kwargs={"bill_id": bill.id}))
        self.assertEqual(resp_receipt.status_code, 200)
        self.assertContains(resp_receipt, "Walk-in Guest")
        self.assertContains(resp_receipt, "PAID IN FULL")

    # 17. Direct counter bill allows items marked is_available=False without throwing error
    def test_cashier_direct_bill_with_kitchen_unavailable_item(self):
        from restaurant.models import Bill, MenuItem
        self.client.force_login(self.cashier)

        # Mark an item as unavailable in kitchen (e.g. Cold Coffee)
        cold_coffee = MenuItem.objects.create(
            category=self.category,
            name="Cold Coffee",
            price=Decimal("120.00"),
            is_active=True,
            is_available=False,
        )

        url_new_bill = reverse("restaurant:cashier_direct_bill")
        payload = {
            "customer_name": "Direct Guest With Coffee",
            "order_type": "COUNTER",
            "cart_data": json.dumps([{"item_id": cold_coffee.id, "quantity": 2}]),
            "discount_pct": "0.00",
            "tip_amount": "0.00",
            "action": "generate",
            "show_receipt": "1",
        }
        resp = self.client.post(url_new_bill, payload)
        self.assertEqual(resp.status_code, 302)

        bill = Bill.objects.filter(customer_name="Direct Guest With Coffee").first()
        self.assertIsNotNone(bill)
        # Should redirect directly to receipt view when show_receipt=1
        self.assertRedirects(resp, reverse("restaurant:cashier_receipt", kwargs={"bill_id": bill.id}))

        # Bill details check
        self.assertEqual(bill.subtotal, Decimal("240.00"))
        # GST 5% = 12.00, total = 252.00
        self.assertEqual(bill.total, Decimal("252.00"))

        # Verify receipt view displays the item and options
        resp_receipt = self.client.get(reverse("restaurant:cashier_receipt", kwargs={"bill_id": bill.id}))
        self.assertEqual(resp_receipt.status_code, 200)
        self.assertContains(resp_receipt, "Cold Coffee")
        self.assertContains(resp_receipt, "UNPAID / DUE")
        self.assertContains(resp_receipt, "Print Bill")
        self.assertContains(resp_receipt, "Download PDF")
        self.assertContains(resp_receipt, "Download File")

    # 18. Cashier bill download view and attachment headers
    def test_cashier_bill_download_view_and_pdf_support(self):
        from restaurant.models import Bill
        self.client.force_login(self.cashier)

        bill = Bill.objects.create(
            order_type="COUNTER",
            customer_name="Invoice Downloader",
            cashier=self.cashier,
            subtotal=Decimal("200.00"),
            tax=Decimal("10.00"),
            discount=Decimal("0.00"),
            tip=Decimal("0.00"),
            total=Decimal("210.00"),
            status="UNPAID",
        )

        # 1. Download endpoint returns attachment
        url_download = reverse("restaurant:cashier_download_bill", kwargs={"bill_id": bill.id})
        resp_download = self.client.get(url_download)
        self.assertEqual(resp_download.status_code, 200)
        self.assertIn("attachment; filename=", resp_download.headers.get("Content-Disposition", ""))
        self.assertIn(f"FoodWaves_Invoice_Bill_{bill.id}.html", resp_download.headers.get("Content-Disposition", ""))
        self.assertContains(resp_download, "Invoice Downloader")

        # 2. Receipt endpoint with ?download=1 query param
        url_receipt_download = f"{reverse('restaurant:cashier_receipt', kwargs={'bill_id': bill.id})}?download=1"
        resp_receipt_dl = self.client.get(url_receipt_download)
        self.assertEqual(resp_receipt_dl.status_code, 200)
        self.assertIn("attachment; filename=", resp_receipt_dl.headers.get("Content-Disposition", ""))


# ==============================================================================
# WAITER RESERVATION VIEWING & TABLE ASSIGNMENT TESTS
# ==============================================================================

class WaiterReservationAndTableAssignmentTests(TestCase):
    def setUp(self):
        self.client = Client()

        # Role Groups
        self.waiter_group, _ = Group.objects.get_or_create(name="WAITER")
        self.customer_group, _ = Group.objects.get_or_create(name="CUSTOMER")
        self.chef_group, _ = Group.objects.get_or_create(name="CHEF")

        # Waiter User
        self.waiter = User.objects.create_user(
            username="waiter_sam",
            email="waiter_sam@foodwaves.test",
            password="Password123",
            first_name="Sam",
            is_active=True,
        )
        self.waiter.groups.add(self.waiter_group)

        # Customer User
        self.customer = User.objects.create_user(
            username="ananya_sen",
            email="ananya@example.com",
            password="Password123",
            first_name="Ananya",
            is_active=True,
        )
        self.customer.groups.add(self.customer_group)
        UserProfile.objects.create(user=self.customer, phone_number="9876543210", is_email_verified=True)

        # Chef User
        self.chef = User.objects.create_user(
            username="chef_arjun",
            email="chef_arjun@foodwaves.test",
            password="Password123",
            is_active=True,
        )
        self.chef.groups.add(self.chef_group)

        # Tables
        self.table_1 = RestaurantTable.objects.create(
            table_number="T-01",
            capacity=4,
            floor_area="INDOOR",
            status="AVAILABLE",
        )
        self.table_2 = RestaurantTable.objects.create(
            table_number="T-02",
            capacity=6,
            floor_area="WINDOW",
            status="AVAILABLE",
        )

        # Today's Reservation
        self.today = timezone.localdate()
        self.reservation_today = Reservation.objects.create(
            user=self.customer,
            name="Ananya Sen",
            email="ananya@example.com",
            phone_number="9876543210",
            date=self.today,
            time="07:30 PM",
            guests=4,
            seating_preference="Window",
            special_requests="Window side table for wedding anniversary",
            status="CONFIRMED",
        )

        # Future Reservation
        self.future_date = self.today + timedelta(days=2)
        self.reservation_future = Reservation.objects.create(
            user=self.customer,
            name="Ananya Sen Future",
            email="ananya@example.com",
            phone_number="9876543210",
            date=self.future_date,
            time="08:00 PM",
            guests=2,
            seating_preference="Indoor",
            status="CONFIRMED",
        )

    def test_waiter_can_view_reservations_on_dashboard_and_reservations_page(self):
        self.client.force_login(self.waiter)

        # 1. Waiter Floor Dashboard shows today's reserved guests
        resp_dashboard = self.client.get(reverse("restaurant:waiter_dashboard"))
        self.assertEqual(resp_dashboard.status_code, 200)
        self.assertContains(resp_dashboard, "Ananya Sen")
        self.assertContains(resp_dashboard, "07:30 PM")
        self.assertContains(resp_dashboard, "4 Guests")
        self.assertContains(resp_dashboard, "9876543210")
        self.assertContains(resp_dashboard, "Today's Reserved Guests")

        # 2. Dedicated Waiter Reservations Portal
        resp_reservations = self.client.get(reverse("restaurant:waiter_reservations"))
        self.assertEqual(resp_reservations.status_code, 200)
        self.assertContains(resp_reservations, "Ananya Sen")
        self.assertContains(resp_reservations, "ananya@example.com")
        self.assertContains(resp_reservations, "wedding anniversary")
        self.assertContains(resp_reservations, "No Table Assigned Yet")

        # Filter by UPCOMING date
        resp_upcoming = self.client.get(reverse("restaurant:waiter_reservations"), {"date": "UPCOMING"})
        self.assertEqual(resp_upcoming.status_code, 200)
        self.assertContains(resp_upcoming, "Ananya Sen Future")

        # Search by query
        resp_search = self.client.get(reverse("restaurant:waiter_reservations"), {"q": "9876543210"})
        self.assertEqual(resp_search.status_code, 200)
        self.assertContains(resp_search, "Ananya Sen")

    def test_waiter_assigns_table_to_reservation(self):
        self.client.force_login(self.waiter)

        assign_url = reverse("restaurant:waiter_assign_table", kwargs={"reservation_id": self.reservation_today.id})
        resp = self.client.post(assign_url, {"table_id": self.table_2.id})
        self.assertEqual(resp.status_code, 302)

        self.reservation_today.refresh_from_db()
        self.table_2.refresh_from_db()

        self.assertEqual(self.reservation_today.table, self.table_2)
        # Assigning immediately opens the table so it is ready to take orders
        self.assertEqual(self.table_2.status, "OCCUPIED")
        self.assertIsNotNone(self.table_2.current_session)
        self.assertEqual(self.table_2.current_session.guest_name, self.reservation_today.name)

        # Customer booking history shows the assigned table
        self.client.force_login(self.customer)
        resp_history = self.client.get(reverse("restaurant:booking_history"))
        self.assertEqual(resp_history.status_code, 200)
        self.assertContains(resp_history, f"Table {self.table_2.table_number}")
        self.assertContains(resp_history, "Window View")

    def test_waiter_unassigns_table_from_reservation(self):
        self.client.force_login(self.waiter)

        # First assign table 1
        assign_url = reverse("restaurant:waiter_assign_table", kwargs={"reservation_id": self.reservation_today.id})
        self.client.post(assign_url, {"table_id": self.table_1.id})
        self.table_1.refresh_from_db()
        self.assertEqual(self.table_1.status, "OCCUPIED")
        self.assertIsNotNone(self.table_1.current_session)

        # Now unassign
        unassign_url = reverse("restaurant:waiter_unassign_table", kwargs={"reservation_id": self.reservation_today.id})
        resp = self.client.post(unassign_url)
        self.assertEqual(resp.status_code, 302)

        self.reservation_today.refresh_from_db()
        self.table_1.refresh_from_db()

        self.assertIsNone(self.reservation_today.table)
        self.assertEqual(self.table_1.status, "AVAILABLE")
        self.assertIsNone(self.table_1.current_session)

    def test_waiter_seats_reservation_directly(self):
        self.client.force_login(self.waiter)

        # Assign table 1 first
        self.reservation_today.table = self.table_1
        self.reservation_today.save()
        self.table_1.status = "RESERVED"
        self.table_1.save()

        seat_url = reverse("restaurant:waiter_seat_reservation", kwargs={"reservation_id": self.reservation_today.id})
        resp = self.client.post(seat_url)
        self.assertEqual(resp.status_code, 302)
        self.assertRedirects(resp, reverse("restaurant:waiter_table_detail", kwargs={"table_id": self.table_1.id}))

        self.reservation_today.refresh_from_db()
        self.table_1.refresh_from_db()

        self.assertEqual(self.reservation_today.status, "ARRIVED")
        self.assertEqual(self.table_1.status, "OCCUPIED")

        session = self.table_1.current_session
        self.assertIsNotNone(session)
        self.assertEqual(session.guest_name, "Ananya Sen")
        self.assertEqual(session.guest_count, 4)
        self.assertEqual(session.customer, self.customer)
        self.assertEqual(session.waiter, self.waiter)
        self.assertEqual(session.reservation, self.reservation_today)

    def test_waiter_seats_reservation_via_table_detail_page(self):
        self.client.force_login(self.waiter)

        # Table detail shows option to seat this reservation
        detail_url = reverse("restaurant:waiter_table_detail", kwargs={"table_id": self.table_1.id})
        resp_detail = self.client.get(detail_url)
        self.assertEqual(resp_detail.status_code, 200)
        self.assertContains(resp_detail, "Ananya Sen")

        # Seat via waiter_table_open with reservation_id
        open_url = reverse("restaurant:waiter_table_open", kwargs={"table_id": self.table_1.id})
        resp_open = self.client.post(open_url, {"reservation_id": self.reservation_today.id})
        self.assertEqual(resp_open.status_code, 302)

        self.reservation_today.refresh_from_db()
        self.table_1.refresh_from_db()

        self.assertEqual(self.reservation_today.status, "ARRIVED")
        self.assertEqual(self.reservation_today.table, self.table_1)
        self.assertEqual(self.table_1.status, "OCCUPIED")

    def test_unauthorized_users_cannot_access_waiter_reservation_endpoints(self):
        # 1. Anonymous user redirected to login
        url_reservations = reverse("restaurant:waiter_reservations")
        resp_anon = self.client.get(url_reservations)
        self.assertEqual(resp_anon.status_code, 302)
        self.assertIn(reverse("restaurant:login"), resp_anon.url)

        # 2. Customer user gets 403 Forbidden
        self.client.force_login(self.customer)
        resp_cust = self.client.get(url_reservations)
        self.assertEqual(resp_cust.status_code, 403)

        # 3. Chef gets 403 Forbidden
        self.client.force_login(self.chef)
        resp_chef = self.client.get(url_reservations)
        self.assertEqual(resp_chef.status_code, 403)

        assign_url = reverse("restaurant:waiter_assign_table", kwargs={"reservation_id": self.reservation_today.id})
        resp_assign = self.client.post(assign_url, {"table_id": self.table_1.id})
        self.assertEqual(resp_assign.status_code, 403)


class ChefOrderWorkflowTests(TestCase):
    def setUp(self):
        self.client = Client()

        # Groups
        self.waiter_group, _ = Group.objects.get_or_create(name="WAITER")
        self.chef_group, _ = Group.objects.get_or_create(name="CHEF")
        self.customer_group, _ = Group.objects.get_or_create(name="CUSTOMER")

        # Users
        self.waiter = User.objects.create_user(
            username="waiter_rahul",
            email="rahul@foodwaves.test",
            password="Password123",
            first_name="Rahul",
            is_active=True,
        )
        self.waiter.groups.add(self.waiter_group)

        self.chef = User.objects.create_user(
            username="chef_vikram",
            email="vikram@foodwaves.test",
            password="Password123",
            first_name="Vikram",
            is_active=True,
        )
        self.chef.groups.add(self.chef_group)

        self.customer = User.objects.create_user(
            username="cust_priya",
            email="priya@example.com",
            password="Password123",
            first_name="Priya",
            is_active=True,
        )
        self.customer.groups.add(self.customer_group)
        UserProfile.objects.create(user=self.customer, phone_number="9876500000", is_email_verified=True)

        # Category and Menu Items
        self.category = MenuCategory.objects.create(name="Main Course", description="Delicious main course")
        self.item_biryani = MenuItem.objects.create(
            name="Dum Biryani",
            description="Fragrant Dum Biryani",
            category=self.category,
            price=Decimal("320.00"),
            is_available=True,
            is_active=True,
        )
        self.item_paneer = MenuItem.objects.create(
            name="Paneer Butter Masala",
            description="Creamy cottage cheese curry",
            category=self.category,
            price=Decimal("260.00"),
            is_available=True,
            is_active=True,
        )

        # Tables & Sessions
        self.table_waiter = RestaurantTable.objects.create(
            table_number="T-07",
            capacity=4,
            floor_area="INDOOR",
            status="OCCUPIED",
        )
        self.session_waiter = TableSession.objects.create(
            table=self.table_waiter,
            guest_name="Karan",
            guest_count=2,
            waiter=self.waiter,
            status="ACTIVE",
        )

        self.table_customer = RestaurantTable.objects.create(
            table_number="T-08",
            capacity=2,
            floor_area="WINDOW",
            status="OCCUPIED",
        )
        self.session_customer = TableSession.objects.create(
            table=self.table_customer,
            guest_name="Priya",
            guest_count=2,
            customer=self.customer,
            status="ACTIVE",
        )

    def test_chef_kds_and_dashboard_display_item_and_table_for_waiter_order(self):
        # 1. Waiter places order for Table T-07
        order = Order.objects.create(
            session=self.session_waiter,
            placed_by=self.waiter,
            source="WAITER",
            status="PENDING",
        )
        order_item = OrderItem.objects.create(
            order=order,
            menu_item=self.item_biryani,
            quantity=2,
            unit_price=self.item_biryani.price,
            special_instructions="Extra spicy, no raita",
            status="PENDING",
        )

        self.assertEqual(order.table_display, "Table T-07")
        self.assertEqual(order_item.table_display, "Table T-07")
        self.assertIn("Waiter (Rahul)", order.source_display)

        # 2. Chef views KDS and Dashboard
        self.client.force_login(self.chef)

        # Check Chef Dashboard
        resp_dash = self.client.get(reverse("restaurant:chef_dashboard"))
        self.assertEqual(resp_dash.status_code, 200)
        self.assertContains(resp_dash, "Dum Biryani")
        self.assertContains(resp_dash, "Table T-07")
        self.assertContains(resp_dash, "Extra spicy, no raita")
        self.assertContains(resp_dash, "Waiter: Rahul")

        # Check Chef KDS
        resp_kds = self.client.get(reverse("restaurant:chef_kds"))
        self.assertEqual(resp_kds.status_code, 200)
        self.assertContains(resp_kds, "Dum Biryani")
        self.assertContains(resp_kds, "Table T-07")
        self.assertContains(resp_kds, "Waiter Order")

    def test_chef_kds_and_dashboard_display_item_and_table_for_customer_order(self):
        # 1. Customer places order for Table T-08
        order = Order.objects.create(
            session=self.session_customer,
            placed_by=self.customer,
            source="CUSTOMER",
            status="PENDING",
        )
        order_item = OrderItem.objects.create(
            order=order,
            menu_item=self.item_paneer,
            quantity=1,
            unit_price=self.item_paneer.price,
            special_instructions="Mild spicy with butter on top",
            status="PENDING",
        )

        self.assertEqual(order.table_display, "Table T-08")
        self.assertEqual(order_item.table_display, "Table T-08")
        self.assertIn("Customer", order.source_display)

        # 2. Chef views KDS and Dashboard
        self.client.force_login(self.chef)

        # Check Chef Dashboard
        resp_dash = self.client.get(reverse("restaurant:chef_dashboard"))
        self.assertEqual(resp_dash.status_code, 200)
        self.assertContains(resp_dash, "Paneer Butter Masala")
        self.assertContains(resp_dash, "Table T-08")
        self.assertContains(resp_dash, "Mild spicy with butter on top")
        self.assertContains(resp_dash, "Customer QR: Priya")

        # Check Chef KDS
        resp_kds = self.client.get(reverse("restaurant:chef_kds"))
        self.assertEqual(resp_kds.status_code, 200)
        self.assertContains(resp_kds, "Paneer Butter Masala")
        self.assertContains(resp_kds, "Table T-08")
        self.assertContains(resp_kds, "Customer QR")

    def test_chef_marks_status_completed(self):
        order = Order.objects.create(
            session=self.session_waiter,
            placed_by=self.waiter,
            source="WAITER",
            status="PENDING",
        )
        order_item = OrderItem.objects.create(
            order=order,
            menu_item=self.item_biryani,
            quantity=1,
            unit_price=self.item_biryani.price,
            status="PENDING",
        )

        self.client.force_login(self.chef)
        status_url = reverse("restaurant:chef_item_status", kwargs={"item_id": order_item.id})

        # Chef clicks "Completed"
        resp = self.client.post(status_url, {"status": "COMPLETED"})
        self.assertEqual(resp.status_code, 302)

        order_item.refresh_from_db()
        self.assertEqual(order_item.status, "READY")
        self.assertIsNotNone(order_item.ready_at)

        # Waiter table detail reflects dish ready to serve
        self.client.force_login(self.waiter)
        resp_table = self.client.get(reverse("restaurant:waiter_table_detail", kwargs={"table_id": self.table_waiter.id}))
        self.assertEqual(resp_table.status_code, 200)
        self.assertContains(resp_table, "Serve ✓")

    def test_chef_marks_status_not_available(self):
        order = Order.objects.create(
            session=self.session_waiter,
            placed_by=self.waiter,
            source="WAITER",
            status="PENDING",
        )
        order_item = OrderItem.objects.create(
            order=order,
            menu_item=self.item_paneer,
            quantity=1,
            unit_price=self.item_paneer.price,
            status="PENDING",
        )

        self.client.force_login(self.chef)
        status_url = reverse("restaurant:chef_item_status", kwargs={"item_id": order_item.id})

        # Chef clicks "Not Available"
        resp = self.client.post(status_url, {"status": "NOT_AVAILABLE"})
        self.assertEqual(resp.status_code, 302)

        order_item.refresh_from_db()
        self.assertEqual(order_item.status, "CANCELLED")

        # Menu item is automatically marked out-of-stock
        self.item_paneer.refresh_from_db()
        self.assertFalse(self.item_paneer.is_available)
        self.assertEqual(self.item_paneer.availability_updated_by, self.chef)

        # Waiter view shows "Not Available"
        self.client.force_login(self.waiter)
        resp_waiter = self.client.get(reverse("restaurant:waiter_table_detail", kwargs={"table_id": self.table_waiter.id}))
        self.assertEqual(resp_waiter.status_code, 200)
        self.assertContains(resp_waiter, "Not Available")

    def test_chef_portal_zero_orders_initially_and_displays_only_on_order(self):
        self.client.force_login(self.chef)

        # 1. Initially 0 orders exist
        resp_dash = self.client.get(reverse("restaurant:chef_dashboard"))
        self.assertEqual(resp_dash.status_code, 200)
        self.assertEqual(resp_dash.context["active_orders"].count(), 0)
        self.assertEqual(resp_dash.context["active_items_queue"].count(), 0)
        self.assertContains(resp_dash, "No orders currently cooking")

        resp_kds = self.client.get(reverse("restaurant:chef_kds"))
        self.assertEqual(resp_kds.status_code, 200)
        self.assertEqual(resp_kds.context["orders"].count(), 0)
        self.assertContains(resp_kds, "Kitchen Queue is Clear!")

        # 2. Waiter places an order on active table session
        order = Order.objects.create(
            session=self.session_waiter,
            placed_by=self.waiter,
            source="WAITER",
            status="PENDING",
        )
        OrderItem.objects.create(
            order=order,
            menu_item=self.item_biryani,
            quantity=1,
            unit_price=self.item_biryani.price,
            status="PENDING",
        )

        # Now chef portal immediately shows the new order
        resp_dash = self.client.get(reverse("restaurant:chef_dashboard"))
        self.assertEqual(resp_dash.context["active_orders"].count(), 1)
        self.assertEqual(resp_dash.context["active_items_queue"].count(), 1)
        self.assertContains(resp_dash, "Dum Biryani")
        self.assertContains(resp_dash, "Table T-07")

        resp_kds = self.client.get(reverse("restaurant:chef_kds"))
        self.assertEqual(resp_kds.context["orders"].count(), 1)
        self.assertContains(resp_kds, "Dum Biryani")
        self.assertContains(resp_kds, "Table T-07")

        # 3. Cashier direct order or closed session orders do not appear in chef kitchen queue
        cashier_order = Order.objects.create(
            session=None,
            source="CASHIER",
            status="SERVED",
        )
        OrderItem.objects.create(
            order=cashier_order,
            menu_item=self.item_biryani,
            quantity=1,
            unit_price=self.item_biryani.price,
            status="SERVED",
        )
        # Still only 1 active kitchen order
        resp_dash = self.client.get(reverse("restaurant:chef_dashboard"))
        self.assertEqual(resp_dash.context["active_orders"].count(), 1)






