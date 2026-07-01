from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from bookings.models import Booking
from bookings.services import BookingCreateError, create_confirmed_booking_with_lock
from resources.models import Availability, Resource
from tenants.models import Tenant


class BookingCreateWithLockTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Test Tenant",
            slug="test-tenant",
            api_key="test-key",
            api_secret="test-secret",
        )
        self.resource = Resource.objects.create(
            tenant=self.tenant,
            name="Test Resource",
            is_active=True,
        )
        self.slot_start = timezone.now() + timedelta(days=2)
        self.slot_end = self.slot_start + timedelta(hours=4)
        Availability.objects.create(
            resource=self.resource,
            start_time=self.slot_start,
            end_time=self.slot_end,
            is_booked=False,
        )

    def test_creates_booking_inside_available_slot(self):
        booking = create_confirmed_booking_with_lock(
            tenant=self.tenant,
            resource=self.resource,
            customer_email="customer@example.com",
            customer_name="Customer",
            start_time=self.slot_start + timedelta(hours=1),
            end_time=self.slot_start + timedelta(hours=2),
        )

        self.assertEqual(booking.status, "CONFIRMED")
        self.assertEqual(Booking.objects.count(), 1)

    def test_rejects_booking_without_covering_availability(self):
        with self.assertRaises(BookingCreateError) as ctx:
            create_confirmed_booking_with_lock(
                tenant=self.tenant,
                resource=self.resource,
                customer_email="customer@example.com",
                customer_name="Customer",
                start_time=self.slot_start - timedelta(hours=2),
                end_time=self.slot_start - timedelta(hours=1),
            )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(Booking.objects.count(), 0)

    def test_rejects_conflicting_booking_after_lock(self):
        start_time = self.slot_start + timedelta(hours=1)
        end_time = self.slot_start + timedelta(hours=2)
        Booking.objects.create(
            tenant=self.tenant,
            resource=self.resource,
            customer_email="existing@example.com",
            customer_name="Existing",
            start_time=start_time,
            end_time=end_time,
            status="CONFIRMED",
        )

        with self.assertRaises(BookingCreateError) as ctx:
            create_confirmed_booking_with_lock(
                tenant=self.tenant,
                resource=self.resource,
                customer_email="customer@example.com",
                customer_name="Customer",
                start_time=start_time + timedelta(minutes=10),
                end_time=end_time + timedelta(minutes=10),
            )

        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(Booking.objects.count(), 1)

    def test_public_availability_excludes_confirmed_booking_and_buffer(self):
        booking_start = self.slot_start + timedelta(hours=1)
        booking_end = booking_start + timedelta(hours=1)
        Booking.objects.create(
            tenant=self.tenant,
            resource=self.resource,
            customer_email="existing@example.com",
            customer_name="Existing",
            start_time=booking_start,
            end_time=booking_end,
            status="CONFIRMED",
        )

        response = self.client.get(
            reverse(
                "dashboard_public_booking_availability",
                kwargs={"tenant_slug": self.tenant.slug},
            ),
            {"resource_id": str(self.resource.id)},
        )

        self.assertEqual(response.status_code, 200)
        slots = response.json()["slots"]
        self.assertEqual(len(slots), 2)
        self.assertEqual(
            parse_datetime(slots[0]["end"]),
            booking_start - timedelta(minutes=30),
        )
        self.assertEqual(
            parse_datetime(slots[1]["start"]),
            booking_end + timedelta(minutes=30),
        )

    def test_admin_can_cancel_booking_without_customer_email(self):
        admin = get_user_model().objects.create_user(
            username="tenant-admin",
            password="password",
            tenant=self.tenant,
            role="ADMIN",
        )
        booking = Booking.objects.create(
            tenant=self.tenant,
            resource=self.resource,
            customer_email="",
            customer_name="Customer",
            start_time=self.slot_start + timedelta(hours=1),
            end_time=self.slot_start + timedelta(hours=2),
            status="CONFIRMED",
        )

        self.client.force_login(admin)
        response = self.client.patch(
            reverse("dashboard_booking_action", kwargs={"booking_id": booking.id}),
            data={"status": "CANCELLED_BY_ADMIN"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(booking.status, "CANCELLED_BY_ADMIN")

    def test_staff_can_complete_own_booking(self):
        staff = get_user_model().objects.create_user(
            username="tenant-staff",
            password="password",
            tenant=self.tenant,
            role="STAFF",
        )
        self.resource.linked_user = staff
        self.resource.save(update_fields=["linked_user"])
        booking = Booking.objects.create(
            tenant=self.tenant,
            resource=self.resource,
            customer_email="customer@example.com",
            customer_name="Customer",
            start_time=self.slot_start + timedelta(hours=1),
            end_time=self.slot_start + timedelta(hours=2),
            status="CONFIRMED",
        )

        self.client.force_login(staff)
        response = self.client.patch(
            reverse("dashboard_booking_action", kwargs={"booking_id": booking.id}),
            data={"status": "COMPLETED"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(booking.status, "COMPLETED")

    def test_superuser_can_cancel_scoped_tenant_booking(self):
        other_tenant = Tenant.objects.create(
            name="Other Tenant",
            slug="other-tenant",
            api_key="other-key",
            api_secret="other-secret",
        )
        other_resource = Resource.objects.create(
            tenant=other_tenant,
            name="Other Resource",
            is_active=True,
        )
        booking = Booking.objects.create(
            tenant=other_tenant,
            resource=other_resource,
            customer_email="",
            customer_name="Customer",
            start_time=self.slot_start + timedelta(hours=1),
            end_time=self.slot_start + timedelta(hours=2),
            status="CONFIRMED",
        )
        superuser = get_user_model().objects.create_superuser(
            username="super-admin",
            password="password",
        )

        self.client.force_login(superuser)
        response = self.client.patch(
            reverse("dashboard_booking_action", kwargs={"booking_id": booking.id}) + f"?tenant_id={other_tenant.id}",
            data={"status": "CANCELLED_BY_ADMIN"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        booking.refresh_from_db()
        self.assertEqual(booking.status, "CANCELLED_BY_ADMIN")
