from django.test import TestCase

from resources.integration_views import IntegrationResourceView
from resources.models import Resource, ResourceProfile
from resources.services.binding_service import ensure_staff_resource_binding
from tenants.models import SaaSUser, Tenant


class ResourceSyncTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Test Tenant",
            slug="resource-sync-test",
            api_key="test-key",
            api_secret="test-secret",
        )
        self.user = SaaSUser.objects.create_user(
            username="staff-user",
            password="test-password",
            tenant=self.tenant,
            role="STAFF",
            is_active=True,
        )
        self.resource = Resource.objects.create(
            tenant=self.tenant,
            linked_user=self.user,
            external_id="system-a-user-1",
            name="Staff User",
            is_active=False,
        )

    def test_staff_binding_does_not_reactivate_hidden_resource(self):
        ensure_staff_resource_binding(self.user, tenant=self.tenant)

        self.resource.refresh_from_db()
        self.assertFalse(self.resource.is_active)

    def test_system_a_profile_sync_can_auto_accept_platform_terms(self):
        view = IntegrationResourceView()
        view._apply_profile_updates(
            self.resource,
            {
                "profile": {
                    "intro": "Updated profile",
                    "display_order": 7,
                    "platform_terms_agreed": True,
                }
            },
        )

        profile = ResourceProfile.objects.get(resource=self.resource)
        self.assertTrue(profile.platform_terms_agreed)
        self.assertEqual(profile.display_order, 7)
