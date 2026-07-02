from django.test import TestCase

from accounts.forms import CastProfileForm


class CastProfileFormTests(TestCase):
    def test_profile_edit_does_not_expose_display_order(self):
        self.assertNotIn("display_order", CastProfileForm().fields)
