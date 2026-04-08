import hashlib
import hmac
import json
import time
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from django.core.cache import cache
from django.conf import settings
from rest_framework.test import APIRequestFactory

from tenants.models import SSOAuthCode, SaaSUser, Tenant
from tenants.permissions import IsTenantAuthorized


def _build_sig(secret: str, method: str, full_path: str, timestamp: int, body: bytes = b"") -> str:
	body_hash = hashlib.sha256(body).hexdigest()
	payload = f"{method.upper()}\n{full_path}\n{timestamp}\n{body_hash}".encode("utf-8")
	return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


@override_settings(
	SAAS_SIGNATURE_REPLAY_TTL_SECONDS=60,
	SAAS_SIGNATURE_MAX_SKEW_SECONDS=300,
	REST_FRAMEWORK={
		"DEFAULT_AUTHENTICATION_CLASSES": [],
		"DEFAULT_PERMISSION_CLASSES": [],
	},
	SYSTEM_B_SSO_CLIENTS={
		"system-a": {
			"client_secret": "client-secret-1",
			"redirect_uris": ["https://a.example.com/sso/callback"],
		}
	},
)
class TenantSecurityTests(TestCase):
	def setUp(self):
		cache.clear()
		self.factory = APIRequestFactory()
		self.tenant = Tenant.objects.create(
			name="Veludo",
			slug="veludo",
			api_key="tenant-key-1",
			api_secret="tenant-secret-1",
			is_api_enabled=True,
		)
		self.user = SaaSUser.objects.create_user(
			username="test-user",
			password="test-pass-123",
			tenant=self.tenant,
			role="STAFF",
		)
		self.client.force_login(self.user)

	def _sign_for_get_request(self, path: str, query: dict, ts: int = None):
		probe_req = self.factory.get(path, query)
		full_path = probe_req.get_full_path()
		ts_val = int(time.time()) if ts is None else int(ts)
		sig = _build_sig(self.tenant.api_secret, "GET", full_path, ts_val, probe_req.body or b"")
		return ts_val, sig

	def _make_signed_permission_request(self, path: str, query: dict, ts: int = None):
		ts_val = int(time.time()) if ts is None else int(ts)
		ts_header_name = str(getattr(settings, 'SAAS_TIMESTAMP_HEADER', 'X-Tenant-Timestamp')).upper().replace('-', '_')
		sig_header_name = str(getattr(settings, 'SAAS_SIGNING_HEADER', 'X-Tenant-Signature')).upper().replace('-', '_')
		extra = {
			'HTTP_X_TENANT_KEY': self.tenant.api_key,
			'HTTP_X_TENANT_TIMESTAMP': str(ts_val),
			'HTTP_X_TENANT_SIGNATURE': 'placeholder',
			f'HTTP_{ts_header_name}': str(ts_val),
			f'HTTP_{sig_header_name}': 'placeholder',
		}
		req = self.factory.get(
			path,
			query,
			**extra,
		)
		sig = _build_sig(self.tenant.api_secret, "GET", req.get_full_path(), ts_val, req.body or b"")
		req.META['HTTP_X_TENANT_SIGNATURE'] = sig
		req.META[f'HTTP_{sig_header_name}'] = sig
		req.META['HTTP_X_TENANT_TIMESTAMP'] = str(ts_val)
		req.META[f'HTTP_{ts_header_name}'] = str(ts_val)
		return req

	def _signed_get(self, path: str, query: dict):
		ts, sig = self._sign_for_get_request(path, query)
		ts_header = f"HTTP_{str(getattr(settings, 'SAAS_TIMESTAMP_HEADER', 'X-Tenant-Timestamp')).upper().replace('-', '_')}"
		sig_header = f"HTTP_{str(getattr(settings, 'SAAS_SIGNING_HEADER', 'X-Tenant-Signature')).upper().replace('-', '_')}"
		return self.factory.get(
			path,
			query,
			HTTP_X_TENANT_KEY=self.tenant.api_key,
			**{ts_header: str(ts), sig_header: sig},
		)

	@override_settings(SAAS_TIMESTAMP_HEADER='X-Tenant-Timestamp', SAAS_SIGNING_HEADER='X-Tenant-Signature')
	def test_integration_identity_requires_valid_signature(self):
		permission = IsTenantAuthorized()
		path = "/api/v1/integration/identity"
		query = {"user_id": str(self.user.id)}

		req_bad = self.factory.get(path, query, HTTP_X_TENANT_KEY=self.tenant.api_key)
		self.assertFalse(permission.has_permission(req_bad, view=None))

		req_ok = self._make_signed_permission_request(path, query)
		self.assertTrue(permission.has_permission(req_ok, view=None))

	@override_settings(SAAS_TIMESTAMP_HEADER='X-Tenant-Timestamp', SAAS_SIGNING_HEADER='X-Tenant-Signature')
	def test_integration_identity_blocks_replay_signature(self):
		permission = IsTenantAuthorized()
		path = "/api/v1/integration/identity"
		query = {"user_id": str(self.user.id)}
		ts = int(time.time())
		first = self._make_signed_permission_request(path, query, ts=ts)
		second = self._make_signed_permission_request(path, query, ts=ts)
		self.assertTrue(permission.has_permission(first, view=None))
		self.assertFalse(permission.has_permission(second, view=None))

	@override_settings(SAAS_TIMESTAMP_HEADER='X-Tenant-Timestamp', SAAS_SIGNING_HEADER='X-Tenant-Signature')
	def test_integration_identity_rejects_expired_timestamp(self):
		permission = IsTenantAuthorized()
		path = "/api/v1/integration/identity"
		query = {"user_id": str(self.user.id)}
		req = self._make_signed_permission_request(path, query, ts=int(time.time()) - 600)
		self.assertFalse(permission.has_permission(req, view=None))

	def _create_code(self, raw_code: str):
		return SSOAuthCode.objects.create(
			code_hash=hashlib.sha256(raw_code.encode("utf-8")).hexdigest(),
			client_id="system-a",
			redirect_uri="https://a.example.com/sso/callback",
			nonce="n-1",
			user=self.user,
			tenant=self.tenant,
			expires_at=timezone.now() + timedelta(minutes=5),
		)

	def test_sso_exchange_code_can_only_be_consumed_once(self):
		raw_code = "code-once-1"
		self._create_code(raw_code)
		payload = {
			"code": raw_code,
			"client_id": "system-a",
			"client_secret": "client-secret-1",
			"redirect_uri": "https://a.example.com/sso/callback",
		}

		first = self.client.post(
			"/api/v1/auth/sso/exchange",
			data=json.dumps(payload),
			content_type="application/json",
		)
		second = self.client.post(
			"/api/v1/auth/sso/exchange",
			data=json.dumps(payload),
			content_type="application/json",
		)

		self.assertEqual(first.status_code, 200)
		self.assertEqual(second.status_code, 400)
		self.assertEqual(second.json().get("error"), "invalid_grant")

	@override_settings(SYSTEM_B_SSO_EXCHANGE_IP_LIMIT_PER_MIN=1)
	def test_sso_exchange_rate_limit_blocks_second_request(self):
		self._create_code("rl-code-1")
		self._create_code("rl-code-2")
		base_payload = {
			"client_id": "system-a",
			"client_secret": "client-secret-1",
			"redirect_uri": "https://a.example.com/sso/callback",
		}

		first = self.client.post(
			"/api/v1/auth/sso/exchange",
			data=json.dumps({**base_payload, "code": "rl-code-1"}),
			content_type="application/json",
		)
		second = self.client.post(
			"/api/v1/auth/sso/exchange",
			data=json.dumps({**base_payload, "code": "rl-code-2"}),
			content_type="application/json",
		)

		self.assertEqual(first.status_code, 200)
		self.assertEqual(second.status_code, 429)
		self.assertEqual(second.json().get("error"), "rate_limited")
