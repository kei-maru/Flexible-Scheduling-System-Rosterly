from django.test import RequestFactory, TestCase, override_settings

from core.views import _get_client_ip


class TrustedProxyIpTests(TestCase):
	def setUp(self):
		self.factory = RequestFactory()

	@override_settings(TRUSTED_PROXY_IPS={"10.0.0.10"})
	def test_trust_xff_only_from_trusted_proxy(self):
		req = self.factory.get(
			"/",
			HTTP_X_FORWARDED_FOR="203.0.113.1, 10.0.0.10",
			REMOTE_ADDR="10.0.0.10",
		)
		self.assertEqual(_get_client_ip(req), "203.0.113.1")

	@override_settings(TRUSTED_PROXY_IPS={"10.0.0.10"})
	def test_ignore_xff_from_untrusted_source(self):
		req = self.factory.get(
			"/",
			HTTP_X_FORWARDED_FOR="203.0.113.1, 10.0.0.10",
			REMOTE_ADDR="198.51.100.9",
		)
		self.assertEqual(_get_client_ip(req), "198.51.100.9")
