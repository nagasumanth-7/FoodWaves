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
        self.assertEqual(item.quantity, 2)
        self.assertEqual(item.status, "PREPARING")

        # 3. Chef marks item ready
        self.client.force_login(self.chef)
        chef_url = reverse("restaurant:chef_item_status", kwargs={"item_id": item.id})
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

        self.manager = User.objects.create_user(username="test_mgr", email="mgr@foodwaves.test", password="Pass", is_active=True)
        self.manager.groups.add(self.manager_group)

        self.chef = User.objects.create_user(username="test_chef", email="chef@foodwaves.test", password="Pass", is_active=True)
        self.chef.groups.add(self.chef_group)

        self.cashier = User.objects.create_user(username="test_cashier", email="cashier@foodwaves.test", password="Pass", is_active=True)
        self.cashier.groups.add(self.cashier_group)

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
        self.assertEqual(order_item.total_price, Decimal("560.00"))
        self.assertEqual(order_item.status, "PREPARING")

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



