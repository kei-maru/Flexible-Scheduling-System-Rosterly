from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
from datetime import timedelta
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
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None


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
    return datetime.fromtimestamp(int(value), tz=dt_timezone.utc)


def _add_months_utc(source: datetime, months: int) -> datetime:
    month_index = (source.month - 1) + months
    year = source.year + (month_index // 12)
    month = (month_index % 12) + 1
    if month == 12:
        next_month = datetime(year + 1, 1, 1, tzinfo=dt_timezone.utc)
    else:
        next_month = datetime(year, month + 1, 1, tzinfo=dt_timezone.utc)
    last_day = (next_month - timedelta(days=1)).day
    day = min(source.day, last_day)
    return source.replace(year=year, month=month, day=day)


def _derive_period_end(start_dt: datetime | None, recurring: dict | None) -> datetime | None:
    if not start_dt or not isinstance(recurring, dict):
        return None
    interval = str(recurring.get("interval") or "month").strip().lower()
    interval_count = int(recurring.get("interval_count") or 1)
    interval_count = max(1, interval_count)

    if interval == "day":
        return start_dt + timedelta(days=interval_count)
    if interval == "week":
        return start_dt + timedelta(weeks=interval_count)
    if interval == "year":
        return _add_months_utc(start_dt, 12 * interval_count)
    return _add_months_utc(start_dt, interval_count)


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


def _resolve_first_month_credit_amount(tenant) -> int:
    try:
        amount = int((getattr(settings, "STRIPE_FIRST_MONTH_DISCOUNT_JPY", 2000) or 2000))
    except (TypeError, ValueError):
        amount = 2000
    amount = max(0, amount)

    if getattr(tenant, "stripe_first_credit_amount", None) != amount:
        tenant.stripe_first_credit_amount = amount
        tenant.save(update_fields=["stripe_first_credit_amount"])
    return amount


def apply_first_month_credit_if_needed(tenant):
    if getattr(tenant, "stripe_first_credit_applied_at", None):
        return False
    amount = _resolve_first_month_credit_amount(tenant)
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
        "payment_method_collection": "always",
        "payment_method_types[0]": "card",
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
    checkout_session_id = (getattr(tenant, "stripe_checkout_session_id", "") or "").strip()
    if not customer_id and not subscription_id and not checkout_session_id:
        return StripeSubscriptionSnapshot()

    subscription = {}
    if subscription_id:
        try:
            subscription = _api_request("GET", f"/subscriptions/{subscription_id}", params={"expand[]": "items.data.price"})
        except StripeApiError:
            subscription = {}
    else:
        subscriptions = _api_request(
            "GET",
            "/subscriptions",
            params={"customer": customer_id, "status": "all", "limit": 1, "expand[]": ["data.items.data.price"]},
        )
        rows = subscriptions.get("data") or []
        subscription = rows[0] if rows else {}

    if not subscription and checkout_session_id:
        session = _api_request(
            "GET",
            f"/checkout/sessions/{checkout_session_id}",
            params={"expand[]": "subscription.items.data.price"},
        )
        session_subscription = (session or {}).get("subscription")
        if isinstance(session_subscription, dict):
            subscription = session_subscription
        elif isinstance(session_subscription, str) and session_subscription:
            subscription = _api_request("GET", f"/subscriptions/{session_subscription}", params={"expand[]": "items.data.price"})

    items = ((subscription or {}).get("items") or {}).get("data") or []
    price_id = ""
    recurring = None
    if items:
        price_payload = ((items[0] or {}).get("price") or {})
        price_id = price_payload.get("id") or ""
        recurring = price_payload.get("recurring") if isinstance(price_payload, dict) else None
    snapshot_customer_id = customer_id or ((subscription or {}).get("customer") or "")
    period_start_dt = _to_datetime(
        (subscription or {}).get("current_period_start")
        or (subscription or {}).get("start_date")
        or (subscription or {}).get("billing_cycle_anchor")
    )
    period_end_dt = _to_datetime(
        (subscription or {}).get("current_period_end")
        or (subscription or {}).get("cancel_at")
    )
    if period_end_dt is None:
        period_end_dt = _derive_period_end(period_start_dt, recurring)

    return StripeSubscriptionSnapshot(
        customer_id=snapshot_customer_id,
        subscription_id=(subscription or {}).get("id", "") or subscription_id,
        price_id=price_id,
        status=((subscription or {}).get("status") or "").upper(),
        current_period_start=period_start_dt,
        current_period_end=period_end_dt,
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
        "stripe_synced_at": timezone.now(),
    }
    for field, value in values.items():
        if getattr(tenant, field) != value:
            setattr(tenant, field, value)
            update_fields.append(field)

    if update_fields:
        tenant.save(update_fields=update_fields)
    return snapshot
