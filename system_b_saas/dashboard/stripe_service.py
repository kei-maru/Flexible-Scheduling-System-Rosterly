from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import time
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.utils import timezone


STRIPE_API_BASE = "https://api.stripe.com/v1"


class StripeConfigError(Exception):
    pass


class StripeApiError(Exception):
    pass


@dataclass
class StripeSubscriptionSnapshot:
    customer_id: str = ""
    subscription_id: str = ""
    price_id: str = ""
    status: str = ""
    current_period_start = None
    current_period_end = None


def _get_secret_key() -> str:
    secret = (getattr(settings, "STRIPE_SECRET_KEY", "") or "").strip()
    if not secret:
        raise StripeConfigError("STRIPE_SECRET_KEY is not configured")
    return secret


def _api_request(method: str, path: str, data=None, params=None):
    response = requests.request(
        method,
        f"{STRIPE_API_BASE}{path}",
        auth=(_get_secret_key(), ""),
        data=data,
        params=params,
        timeout=20,
    )
    payload = response.json()
    if response.status_code >= 400:
        message = (((payload or {}).get("error") or {}).get("message") or "Stripe API error").strip()
        raise StripeApiError(message)
    return payload


def _to_datetime(value):
    if not value:
        return None
    return timezone.datetime.fromtimestamp(int(value), tz=timezone.utc)


def verify_webhook_signature(payload: bytes, signature_header: str, tolerance: int = 300):
    secret = (getattr(settings, "STRIPE_WEBHOOK_SECRET", "") or "").strip()
    if not secret:
        raise StripeConfigError("STRIPE_WEBHOOK_SECRET is not configured")
    if not signature_header:
        raise StripeApiError("Missing Stripe-Signature header")

    parts = {}
    for item in signature_header.split(","):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        parts.setdefault(key, []).append(value)

    timestamp = int((parts.get("t") or ["0"])[0])
    if not timestamp or abs(int(time.time()) - timestamp) > tolerance:
        raise StripeApiError("Webhook timestamp is outside the tolerance window")

    signed_payload = f"{timestamp}.{payload.decode('utf-8')}".encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    signatures = parts.get("v1") or []
    if not any(hmac.compare_digest(expected, value) for value in signatures):
        raise StripeApiError("Invalid Stripe webhook signature")


def ensure_customer_for_tenant(tenant):
    if getattr(tenant, "stripe_customer_id", ""):
        return tenant.stripe_customer_id

    payload = {
        "name": tenant.name,
        "email": tenant.contact_email or "",
        "metadata[tenant_id]": str(tenant.id),
        "metadata[tenant_slug]": tenant.slug,
    }
    customer = _api_request("POST", "/customers", data=payload)
    tenant.stripe_customer_id = customer.get("id", "")
    tenant.save(update_fields=["stripe_customer_id"])
    return tenant.stripe_customer_id


def apply_first_month_credit_if_needed(tenant):
    if getattr(tenant, "stripe_first_credit_applied_at", None):
        return False
    amount = int(getattr(tenant, "stripe_first_credit_amount", 2000) or 0)
    if amount <= 0:
        return False

    customer_id = ensure_customer_for_tenant(tenant)
    _api_request(
        "POST",
        "/customers/%s/balance_transactions" % customer_id,
        data={
            "amount": -amount,
            "currency": "jpy",
            "description": f"{tenant.name} first-month discount",
        },
    )
    tenant.stripe_first_credit_applied_at = timezone.now()
    tenant.save(update_fields=["stripe_first_credit_applied_at"])
    return True


def create_checkout_session(tenant, success_url: str, cancel_url: str):
    price_id = (getattr(settings, "STRIPE_SUBSCRIPTION_PRICE_ID", "") or "").strip()
    if not price_id:
        raise StripeConfigError("STRIPE_SUBSCRIPTION_PRICE_ID is not configured")

    customer_id = ensure_customer_for_tenant(tenant)
    if not getattr(tenant, "stripe_subscription_id", ""):
        apply_first_month_credit_if_needed(tenant)

    data = {
        "mode": "subscription",
        "customer": customer_id,
        "success_url": success_url,
        "cancel_url": cancel_url,
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": 1,
        "allow_promotion_codes": "true",
        "subscription_data[metadata][tenant_id]": str(tenant.id),
        "subscription_data[metadata][tenant_slug]": tenant.slug,
    }
    session = _api_request("POST", "/checkout/sessions", data=data)
    tenant.stripe_checkout_session_id = session.get("id", "")
    tenant.stripe_price_id = price_id
    tenant.save(update_fields=["stripe_checkout_session_id", "stripe_price_id"])
    return session


def stripe_checkout_ready() -> bool:
    return bool(
        (getattr(settings, "STRIPE_SECRET_KEY", "") or "").strip()
        and (getattr(settings, "STRIPE_PUBLISHABLE_KEY", "") or "").strip()
        and (getattr(settings, "STRIPE_SUBSCRIPTION_PRICE_ID", "") or "").strip()
    )


def create_billing_portal_session(tenant, return_url: str):
    customer_id = ensure_customer_for_tenant(tenant)
    return _api_request(
        "POST",
        "/billing_portal/sessions",
        data={"customer": customer_id, "return_url": return_url},
    )


def fetch_subscription_snapshot(tenant) -> StripeSubscriptionSnapshot:
    customer_id = (getattr(tenant, "stripe_customer_id", "") or "").strip()
    subscription_id = (getattr(tenant, "stripe_subscription_id", "") or "").strip()
    if not customer_id and not subscription_id:
        return StripeSubscriptionSnapshot()

    if subscription_id:
        subscription = _api_request("GET", f"/subscriptions/{subscription_id}", params={"expand[]": "items.data.price"})
    else:
        subscriptions = _api_request(
            "GET",
            "/subscriptions",
            params={"customer": customer_id, "status": "all", "limit": 1, "expand[]": ["data.items.data.price"]},
        )
        rows = subscriptions.get("data") or []
        subscription = rows[0] if rows else {}

    items = ((subscription or {}).get("items") or {}).get("data") or []
    price_id = ""
    if items:
        price_id = ((items[0] or {}).get("price") or {}).get("id") or ""

    return StripeSubscriptionSnapshot(
        customer_id=customer_id,
        subscription_id=(subscription or {}).get("id", "") or subscription_id,
        price_id=price_id,
        status=((subscription or {}).get("status") or "").upper(),
        current_period_start=_to_datetime((subscription or {}).get("current_period_start")),
        current_period_end=_to_datetime((subscription or {}).get("current_period_end")),
    )


def sync_tenant_subscription(tenant):
    snapshot = fetch_subscription_snapshot(tenant)
    update_fields = []

    stripe_status = snapshot.status
    if stripe_status in {"ACTIVE", "TRIALING", "PAST_DUE", "UNPAID"}:
        mapped_status = "TRIAL" if stripe_status == "TRIALING" else "ACTIVE"
    elif stripe_status in {"CANCELED", "INCOMPLETE_EXPIRED"}:
        mapped_status = "CANCELED"
    else:
        mapped_status = "NONE" if not stripe_status else "ACTIVE"

    values = {
        "stripe_customer_id": snapshot.customer_id,
        "stripe_subscription_id": snapshot.subscription_id,
        "stripe_price_id": snapshot.price_id or (getattr(tenant, "stripe_price_id", "") or ""),
        "subscription_status": mapped_status,
        "subscription_started_at": snapshot.current_period_start,
        "subscription_ends_at": snapshot.current_period_end,
        "subscription_plan_code": snapshot.price_id or (getattr(tenant, "subscription_plan_code", "") or ""),
    }
    for field, value in values.items():
        if getattr(tenant, field) != value:
            setattr(tenant, field, value)
            update_fields.append(field)

    if update_fields:
        tenant.save(update_fields=update_fields)
    return snapshot
