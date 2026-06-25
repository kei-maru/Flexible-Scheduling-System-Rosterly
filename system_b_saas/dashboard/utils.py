import hashlib
import json
import secrets
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from django.conf import settings
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone


PUBLIC_BOOKING_IP_LIMIT_10MIN = 8
PUBLIC_BOOKING_IP_LIMIT_1H = 24
PUBLIC_BOOKING_FINGERPRINT_LIMIT_10MIN = 3

TENANT_API_BAN_REASON_CHOICES = [
    ("ABUSE", "不正利用・スパム"),
    ("PAYMENT", "料金・契約違反"),
    ("SECURITY", "セキュリティ違反"),
    ("LEGAL", "法令・規約違反"),
    ("OTHER", "その他"),
]


def _demo_admin_username() -> str:
    return (getattr(settings, "SYSTEM_B_DEMO_ADMIN_USERNAME", "demo_admin") or "demo_admin").strip()


def _is_demo_admin_resource(resource) -> bool:
    linked_user = getattr(resource, "linked_user", None)
    if linked_user:
        target_username = _demo_admin_username().lower()
        current_username = (getattr(linked_user, "username", "") or "").strip().lower()
        if bool(target_username) and current_username == target_username:
            return True

    profile = getattr(resource, "profile", None)
    metadata = getattr(profile, "metadata", None) if profile else None
    if not isinstance(metadata, dict):
        return False
    rank = str(metadata.get("rank") or "").strip().upper()
    return (
        rank == "DEMO"
        or metadata.get("demo_non_bookable") is True
        or metadata.get("publicly_bookable") is False
    )


def _exclude_demo_admin_resources(queryset):
    target_username = _demo_admin_username()
    if not target_username:
        return queryset
    return queryset.exclude(linked_user__username__iexact=target_username)


def _is_http_url(value):
    text = (value or "").strip()
    if not text:
        return False
    parsed = urlparse(text)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _normalize_agreement_modules(raw_json, legacy_label="", legacy_body=""):
    modules = []
    parsed_as_list = False
    if raw_json:
        try:
            parsed = json.loads(raw_json)
        except (TypeError, ValueError):
            parsed = []
        if isinstance(parsed, list):
            parsed_as_list = True
            for row in parsed:
                if not isinstance(row, dict):
                    continue
                title = (row.get("title") or "").strip()
                content = (row.get("content") or "").strip()
                if title or content:
                    modules.append({"title": title or "追加条項", "content": content})

    if not modules and not parsed_as_list:
        fallback_title = (legacy_label or "").strip()
        fallback_body = (legacy_body or "").strip()
        if fallback_title or fallback_body:
            modules.append({"title": fallback_title or "追加条項", "content": fallback_body})

    return modules[:20]


def _normalize_required_customer_fields(values):
    allowed = {"VRCID", "DISCORDID", "EMAIL"}
    if isinstance(values, str):
        raw = [part.strip().upper() for part in values.split(",") if part.strip()]
    elif isinstance(values, (list, tuple, set)):
        raw = [str(part).strip().upper() for part in values if str(part).strip()]
    else:
        raw = []

    result = []
    for item in raw:
        if item in allowed and item not in result:
            result.append(item)
    return result


def _tenant_is_subscribed(tenant):
    if getattr(tenant, "subscription_override_enabled", False):
        status = (getattr(tenant, "subscription_override_status", "") or "").upper()
        if status not in {"ACTIVE", "TRIAL"}:
            return False
        ends_at = getattr(tenant, "subscription_override_ends_at", None)
        if ends_at and ends_at <= timezone.now():
            return False
        return True

    status = (getattr(tenant, "subscription_status", "") or "").upper()
    if status not in {"ACTIVE", "TRIAL"}:
        return False
    ends_at = getattr(tenant, "subscription_ends_at", None)
    if ends_at and ends_at <= timezone.now():
        return False
    return True


def _is_core_time_store(tenant):
    return (getattr(tenant, "store_type", "FLEX_SHIFT") or "FLEX_SHIFT").upper() == "CORE_TIME"


def _behavior_client_ip(request):
    remote_addr = (request.META.get("REMOTE_ADDR") or "").strip()
    trusted_proxies = set(getattr(settings, "TRUSTED_PROXY_IPS", set()) or set())
    forwarded = (request.META.get("HTTP_X_FORWARDED_FOR") or "").strip()
    if forwarded and remote_addr in trusted_proxies:
        return forwarded.split(",")[0].strip()
    return remote_addr or ""


def _agreement_modules_for_template(raw_json, legacy_label="", legacy_body=""):
    items = []
    for row in _normalize_agreement_modules(raw_json, legacy_label=legacy_label, legacy_body=legacy_body):
        content = row.get("content") or ""
        items.append(
            {
                "title": row.get("title") or "追加条項",
                "content": content,
                "is_url": _is_http_url(content),
            }
        )
    return items


def _tenant_api_ban_reason_label(reason_code: str) -> str:
    reason_map = dict(TENANT_API_BAN_REASON_CHOICES)
    return reason_map.get((reason_code or "").strip().upper(), "規約違反")


def _tenant_api_ban_banner_context(tenant):
    if not tenant or getattr(tenant, "is_api_enabled", True):
        return {
            "tenant_is_banned": False,
            "tenant_ban_reason_label": "",
            "tenant_ban_note": "",
            "tenant_ban_media_url": "",
            "tenant_ban_admin_help": False,
        }
    media_url = ""
    media_file = getattr(tenant, "api_ban_media", None)
    if media_file:
        try:
            media_url = media_file.url
        except Exception:
            media_url = ""
    return {
        "tenant_is_banned": True,
        "tenant_ban_reason_label": _tenant_api_ban_reason_label(getattr(tenant, "api_ban_reason", "")),
        "tenant_ban_note": (getattr(tenant, "api_ban_note", "") or "").strip(),
        "tenant_ban_media_url": media_url,
        "tenant_ban_admin_help": True,
    }


def _absolute_public_url(request, path):
    base = (getattr(settings, "SYSTEM_B_PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
    if _is_http_url(base):
        return f"{base}{path}"

    try:
        built = request.build_absolute_uri(path)
        if _is_http_url(built):
            return built
    except Exception:
        pass

    host = (request.get_host() or "").strip()
    if host:
        scheme = "https" if request.is_secure() else "http"
        return f"{scheme}://{host}{path}"
    return path


def _resolve_booking_redirect_url(tenant, token, fallback_url):
    custom_url = (getattr(tenant, "booking_detail_redirect_url", "") or "").strip()
    if not _is_http_url(custom_url):
        return fallback_url

    parsed = urlparse(custom_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.setdefault("booking_token", token)
    if getattr(tenant, "slug", ""):
        query.setdefault("tenant", tenant.slug)
    return urlunparse(parsed._replace(query=urlencode(query)))


def _ensure_booking_public_access(request, booking):
    token = (booking.public_access_token or "").strip()
    if not token:
        token = secrets.token_urlsafe(24)
    detail_path = reverse("dashboard_public_booking_detail", kwargs={"access_token": token})
    canonical_detail_url = _absolute_public_url(request, detail_path)
    detail_url = _resolve_booking_redirect_url(booking.tenant, token, canonical_detail_url)

    update_fields = []
    if booking.public_access_token != token:
        booking.public_access_token = token
        update_fields.append("public_access_token")
    if booking.public_detail_url != detail_url:
        booking.public_detail_url = detail_url
        update_fields.append("public_detail_url")
    if update_fields:
        booking.save(update_fields=update_fields)

    return detail_url


def _client_ip(request):
    remote_addr = (request.META.get("REMOTE_ADDR") or "").strip()
    trusted_proxies = set(getattr(settings, "TRUSTED_PROXY_IPS", set()) or set())
    forwarded = (request.META.get("HTTP_X_FORWARDED_FOR") or "").strip()
    if forwarded and remote_addr in trusted_proxies:
        return forwarded.split(",")[0].strip()
    return remote_addr or "unknown"


def _cache_bump(key, ttl_seconds):
    cache.add(key, 0, ttl_seconds)
    try:
        current = cache.incr(key)
    except ValueError:
        cache.set(key, 1, ttl_seconds)
        current = 1
    if hasattr(cache, "touch"):
        cache.touch(key, ttl_seconds)
    return int(current)


def _public_booking_is_rate_limited(request, tenant_slug, fingerprint=""):
    ip = _client_ip(request)
    key_10m = f"pb:ip10m:{tenant_slug}:{ip}"
    key_1h = f"pb:ip1h:{tenant_slug}:{ip}"

    ip_count_10m = _cache_bump(key_10m, 600)
    ip_count_1h = _cache_bump(key_1h, 3600)
    if ip_count_10m > PUBLIC_BOOKING_IP_LIMIT_10MIN or ip_count_1h > PUBLIC_BOOKING_IP_LIMIT_1H:
        return True

    if fingerprint:
        fp_hash = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:24]
        fp_key = f"pb:fp10m:{tenant_slug}:{fp_hash}"
        fp_count = _cache_bump(fp_key, 600)
        if fp_count > PUBLIC_BOOKING_FINGERPRINT_LIMIT_10MIN:
            return True

    return False
