import json
import re
import hashlib
import logging
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse
from uuid import uuid4
import secrets

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.messages import get_messages
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.views import LoginView
from django.core.files.storage import default_storage
from django.core.cache import cache
from django.db.models import F, Max
from django.shortcuts import redirect
from django.http import JsonResponse
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.crypto import constant_time_compare
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django import forms
from django.views import View
from django.views.generic import TemplateView
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction
from django.db.models import Q
from django.utils.text import slugify

from bookings.models import Booking, BookingReport, REPORT_REASON_CHOICES
from bookings.tasks import process_new_booking, send_cancellation_email_task
from dashboard.models import GlobalAnnouncement, UserBehaviorEvent
from dashboard import stripe_service
from resources.models import Availability, EmailTemplate, Resource, ResourceProfile, ServicePreset
from resources.services.binding_service import ensure_staff_resource_binding, normalize_profile_text
from resources.services.service_mapping import resolve_booking_service_name, resolve_service_by_duration
from resources.services.schedule_service import normalize_core_time_config, summarize_core_time_config
from tenants.models import SaaSUser, StaffInvite, Tenant


logger = logging.getLogger(__name__)


def _demo_admin_autologin_enabled() -> bool:
    return bool(getattr(settings, "SYSTEM_B_DEMO_ADMIN_AUTOLOGIN_ENABLED", False))


def _demo_admin_username() -> str:
    return (getattr(settings, "SYSTEM_B_DEMO_ADMIN_USERNAME", "demo_admin") or "demo_admin").strip()


def _demo_admin_access_token() -> str:
    return (getattr(settings, "SYSTEM_B_DEMO_ADMIN_ACCESS_TOKEN", "") or "").strip()


def _is_demo_admin_resource(resource) -> bool:
    linked_user = getattr(resource, "linked_user", None)
    if not linked_user:
        return False
    target_username = _demo_admin_username().lower()
    current_username = (getattr(linked_user, "username", "") or "").strip().lower()
    return bool(target_username) and current_username == target_username


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


def _effective_subscription_context(tenant):
    override_enabled = bool(getattr(tenant, "subscription_override_enabled", False))
    if override_enabled:
        return {
            "effective_subscription_status": (getattr(tenant, "subscription_override_status", "") or "NONE").upper(),
            "effective_subscription_started_at": getattr(tenant, "subscription_override_started_at", None),
            "effective_subscription_ends_at": getattr(tenant, "subscription_override_ends_at", None),
            "subscription_is_overridden": True,
            "subscription_override_note": (getattr(tenant, "subscription_override_note", "") or "").strip(),
        }

    return {
        "effective_subscription_status": (getattr(tenant, "subscription_status", "") or "NONE").upper(),
        "effective_subscription_started_at": getattr(tenant, "subscription_started_at", None),
        "effective_subscription_ends_at": getattr(tenant, "subscription_ends_at", None),
        "subscription_is_overridden": False,
        "subscription_override_note": "",
    }


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


def _normalize_report_detail_text(raw_text):
    text = str(raw_text or "")
    text = re.sub(r"\\u000[dD]\\u000[aA]", "\n", text)
    text = re.sub(r"\\u000[dD]", "\n", text)
    text = re.sub(r"\\u000[aA]", "\n", text)
    text = text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()


DEFAULT_EMAIL_FOOTER_TEXT = "キャンセルは予定時刻の二十四時間前までにDisocordまたはEmailにて連絡"
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
    if hasattr(cache, 'touch'):
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


class DashboardLoginView(TemplateView):
    template_name = "dashboard/login.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            if request.user.is_superuser:
                return redirect("dashboard_super_global")
            has_tenant_id = bool(getattr(request.user, "tenant_id", None))
            try:
                tenant_obj = request.user.tenant if has_tenant_id else None
            except Tenant.DoesNotExist:
                tenant_obj = None

            if not has_tenant_id or tenant_obj is None:
                list(get_messages(request))
                logout(request)
                messages.warning(request, "このアカウントにはスタッフシステム権限がありません。管理者にお問い合わせください。")
                return super().dispatch(request, *args, **kwargs)
            return redirect("shared_home")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        next_url = self.request.GET.get("next", "")
        if next_url and not url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        ):
            next_url = ""

        context["discord_oauth_ready"] = bool(
            settings.SYSTEM_B_DISCORD_CLIENT_ID and settings.SYSTEM_B_DISCORD_SECRET
        )
        context["next_url"] = next_url
        context["auth_blocked_message"] = self.request.session.pop("dashboard_auth_blocked_message", "")
        return context


class DashboardAdminDemoLoginView(View):
    def get(self, request, access_token, *args, **kwargs):
        if not _demo_admin_autologin_enabled():
            return JsonResponse({"error": "not_found"}, status=404)

        expected_token = _demo_admin_access_token()
        if not expected_token or not constant_time_compare(str(access_token or ""), expected_token):
            return JsonResponse({"error": "not_found"}, status=404)

        tenant_slug = (getattr(settings, "SYSTEM_B_DEMO_ADMIN_TENANT_SLUG", "rosterly") or "rosterly").strip()
        tenant = Tenant.objects.filter(slug=tenant_slug).first() or Tenant.objects.filter(slug__iexact=tenant_slug).first()
        if not tenant:
            tenant = Tenant.objects.create(
                name=(getattr(settings, "SYSTEM_B_DEMO_ADMIN_TENANT_NAME", "Demo Shop") or "Demo Shop").strip(),
                slug=tenant_slug,
                contact_email=(getattr(settings, "SYSTEM_B_DEMO_ADMIN_EMAIL", "") or "").strip() or None,
                api_key=secrets.token_urlsafe(24)[:32],
                api_secret=secrets.token_urlsafe(32),
                is_api_enabled=True,
                enable_saas_dashboard=True,
            )

        username = _demo_admin_username()
        email = (getattr(settings, "SYSTEM_B_DEMO_ADMIN_EMAIL", "") or "").strip()
        user, created = SaaSUser.objects.get_or_create(
            username=username,
            defaults={
                "email": email,
                "role": "ADMIN",
                "is_staff": True,
                "is_active": True,
                "tenant": tenant,
            },
        )

        update_fields = []
        if user.role != "ADMIN":
            user.role = "ADMIN"
            update_fields.append("role")
        if not user.is_staff:
            user.is_staff = True
            update_fields.append("is_staff")
        if not user.is_active:
            user.is_active = True
            update_fields.append("is_active")
        if user.tenant_id != tenant.id:
            user.tenant = tenant
            update_fields.append("tenant")
        if email and (user.email or "") != email:
            user.email = email
            update_fields.append("email")
        if update_fields:
            user.save(update_fields=update_fields)
        if created:
            user.set_unusable_password()
            user.save(update_fields=["password"])

        login(request, user, backend="django.contrib.auth.backends.ModelBackend")

        redirect_path = (getattr(settings, "SYSTEM_B_DEMO_ADMIN_REDIRECT_PATH", "/dashboard/") or "/dashboard/").strip()
        if not redirect_path.startswith("/"):
            redirect_path = f"/{redirect_path}"
        return redirect(redirect_path)


class DashboardRegisterShopRedirectView(View):
    def get(self, request, *args, **kwargs):
        next_url = request.GET.get("next", "")
        if next_url and not url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            next_url = ""

        if not (settings.SYSTEM_B_DISCORD_CLIENT_ID and settings.SYSTEM_B_DISCORD_SECRET):
            messages.warning(
                request,
                "Discord OAuth が未設定のため、店舗登録を開始できません。SYSTEM_B_DISCORD_CLIENT_ID / SYSTEM_B_DISCORD_SECRET を設定してください。",
            )
            return redirect("dashboard_login")

        request.session["allow_shop_signup"] = True
        request.session.pop("shop_signup_provisional_user_id", None)
        request.session.pop("allow_staff_invite_token", None)
        target_next = next_url or "/dashboard/register-shop/form/"
        return redirect(f"/accounts/discord/login/?process=login&next={quote(target_next, safe='')}")


class DashboardShopSignupForm(forms.Form):
    shop_name = forms.CharField(max_length=100, required=True)
    owner_email = forms.EmailField(required=True)
    logo = forms.ImageField(required=False)
    preset_services_json = forms.CharField(required=False)


class DashboardShopSignupFormView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard/shop_signup_form.html"

    def _form_seed_context(self, request=None):
        request = request or self.request
        return {
            "form_shop_name": (request.POST.get("shop_name") or "").strip(),
            "form_owner_email": (request.POST.get("owner_email") or "").strip(),
            "form_preset_services_json": (request.POST.get("preset_services_json") or "").strip(),
        }

    def _is_shop_signup_session(self):
        return bool(self.request.session.get("allow_shop_signup"))

    def _is_provisional_user(self, user):
        provisional_id = self.request.session.get("shop_signup_provisional_user_id")
        return provisional_id and str(provisional_id) == str(getattr(user, "id", ""))

    def _cleanup_provisional_identity(self, user):
        if not user:
            return
        if getattr(user, "tenant_id", None):
            return
        if not self._is_provisional_user(user):
            return

        from allauth.socialaccount.models import SocialAccount, SocialToken

        SocialToken.objects.filter(account__user=user).delete()
        SocialAccount.objects.filter(user=user).delete()
        user.delete()
        self.request.session.pop("shop_signup_provisional_user_id", None)

    def dispatch(self, request, *args, **kwargs):
        if not self._is_shop_signup_session():
            messages.warning(request, "店舗登録セッションが見つかりません。最初からやり直してください。")
            return redirect("dashboard_login")

        if getattr(request.user, "tenant_id", None):
            request.session.pop("allow_shop_signup", None)
            request.session.pop("shop_signup_provisional_user_id", None)
            messages.info(request, "このアカウントはすでに店舗登録済みです。")
            return redirect("tenant_dashboard")

        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["default_shop_name"] = f"{(self.request.user.username or 'Rosterly')[:50]} Shop"
        context["default_owner_email"] = self.request.user.email or ""
        context.update(self._form_seed_context())
        return context

    def post(self, request, *args, **kwargs):
        if request.POST.get("cancel_signup") == "true":
            self._cleanup_provisional_identity(request.user)
            logout(request)
            request.session.pop("allow_shop_signup", None)
            messages.info(request, "店舗登録をキャンセルしました。今回の認証情報を破棄しました。")
            return redirect("dashboard_login")

        form = DashboardShopSignupForm(request.POST, request.FILES)
        if not form.is_valid():
            for _field, errs in form.errors.items():
                for err in errs:
                    messages.error(request, str(err))
            context = self.get_context_data()
            return self.render_to_response(context)

        shop_name = form.cleaned_data["shop_name"].strip()
        owner_email = form.cleaned_data["owner_email"].strip()
        logo = form.cleaned_data.get("logo")

        try:
            with transaction.atomic():
                from tenants.adapters import SaaSDiscordSocialAdapter

                adapter = SaaSDiscordSocialAdapter()
                tenant_slug = adapter._build_unique_tenant_slug(shop_name)
                tenant = Tenant.objects.create(
                    name=shop_name,
                    slug=tenant_slug,
                    contact_email=owner_email,
                    logo=logo,
                    api_key=secrets.token_urlsafe(24)[:32],
                    api_secret=secrets.token_urlsafe(32),
                    enable_saas_dashboard=True,
                )

                user = request.user
                update_fields = []
                if user.email != owner_email:
                    user.email = owner_email
                    update_fields.append("email")
                if user.tenant_id != tenant.id:
                    user.tenant = tenant
                    update_fields.append("tenant")
                if user.role != "ADMIN":
                    user.role = "ADMIN"
                    update_fields.append("role")
                if not user.is_staff:
                    user.is_staff = True
                    update_fields.append("is_staff")
                if user.is_superuser:
                    user.is_superuser = False
                    update_fields.append("is_superuser")
                if update_fields:
                    user.save(update_fields=update_fields)

                # Admin owner should also get a bound Resource for schedule/booking operations.
                ensure_staff_resource_binding(user, tenant=tenant)

                preset_services = []
                raw_json = (form.cleaned_data.get("preset_services_json") or "").strip()
                if raw_json:
                    try:
                        parsed = json.loads(raw_json)
                        if isinstance(parsed, list):
                            preset_services = parsed
                    except json.JSONDecodeError:
                        preset_services = []

                max_order = 0
                for item in preset_services:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name") or "").strip()
                    if not name:
                        continue
                    try:
                        duration = int(item.get("duration_minutes") or 60)
                    except (TypeError, ValueError):
                        duration = 60
                    try:
                        price = Decimal(str(item.get("price") or "0"))
                    except (InvalidOperation, ValueError):
                        price = Decimal("0")
                    description = str(item.get("description") or "...").strip() or "..."
                    max_order += 1
                    ServicePreset.objects.create(
                        tenant=tenant,
                        name=name,
                        description=description,
                        price=max(price, Decimal("0")),
                        duration_minutes=max(1, duration),
                        is_active=True,
                        sort_order=max_order,
                    )
        except Exception as exc:
            logger.exception('shop register failed for user_id=%s', getattr(request.user, 'id', None))
            debug = bool(getattr(settings, "DEBUG", False))
            messages.error(request, f"店舗登録に失敗しました。{' ' + str(exc) if debug else '入力内容を確認して再試行してください。'}")
            context = self.get_context_data()
            return self.render_to_response(context)

        request.session.pop("allow_shop_signup", None)
        request.session.pop("shop_signup_provisional_user_id", None)
        messages.success(request, "店舗登録が完了しました。")
        return redirect("tenant_dashboard")


class DashboardInviteAcceptView(View):
    def get(self, request, token, *args, **kwargs):
        invite = StaffInvite.objects.filter(token=token).select_related("tenant").first()
        if not invite or not invite.is_available:
            messages.error(request, "招待リンクが無効または期限切れです。")
            return redirect("dashboard_login")

        request.session["allow_staff_invite_token"] = invite.token
        # Keep invite onboarding independent from public SSO onboarding.
        request.session.pop("allow_public_sso_login", None)
        request.session.pop("sso_role_hint", None)
        return redirect(f"/accounts/discord/login/?process=login&next={quote('/dashboard/login/', safe='')}")


class DashboardTermsView(TemplateView):
    template_name = "dashboard/terms.html"


class DashboardTokushohoView(TemplateView):
    template_name = "dashboard/tokushoho.html"


class DashboardPublicBookingView(TemplateView):
    template_name = "dashboard/public_booking.html"

    def _booking_resource_queryset(self, tenant):
        # Bookable resources must be active; linked staff must be active and agree to platform terms.
        return _exclude_demo_admin_resources(
            Resource.objects.filter(tenant=tenant, is_active=True)
            .filter(Q(linked_user__isnull=True) | Q(linked_user__is_active=True))
            .filter(Q(linked_user__isnull=True) | Q(profile__platform_terms_agreed=True))
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tenant_slug = kwargs.get("tenant_slug")
        tenant = Tenant.objects.filter(slug=tenant_slug).first()
        if tenant and getattr(tenant, "deleted_at", None):
            tenant = None
        if not tenant:
            context.update({"tenant": None, "resources": [], "services": []})
            return context

        resources = (
            self._booking_resource_queryset(tenant)
            .select_related("profile")
            .order_by("profile__display_order", "name")
        )
        services = ServicePreset.objects.filter(tenant=tenant, is_active=True).order_by("sort_order", "id")
        agreement_modules = _agreement_modules_for_template(
            tenant.custom_terms_body,
            legacy_label=tenant.custom_terms_label,
            legacy_body=tenant.custom_terms_body,
        )

        vrc_terms_url = (getattr(settings, "PUBLIC_VRC_TERMS_URL", "") or "").strip() or "https://hello.vrchat.com/legal"
        rosterly_terms_url = (getattr(settings, "PUBLIC_ROSTERLY_TERMS_URL", "") or "").strip()
        if not rosterly_terms_url:
            rosterly_terms_url = _absolute_public_url(self.request, reverse("dashboard_terms"))

        # Store contract links must be fully synchronized with owner settings.
        # Do not use fallback URLs here.
        store_contract_url_effective = (tenant.store_contract_url or "").strip()

        store_contract_items = []
        seen_store_items = set()

        def _append_store_item(item_type, label_value, content_value):
            label = (label_value or "詳細").strip() or "詳細"
            content = (content_value or "").strip()
            if not content:
                return
            key = (item_type, content)
            if key in seen_store_items:
                return
            seen_store_items.add(key)
            store_contract_items.append(
                {
                    "type": item_type,
                    "label": label,
                    "content": content,
                }
            )

        if _is_http_url(store_contract_url_effective):
            _append_store_item(
                "url",
                (tenant.store_contract_label or "店舗利用規約").strip() or "店舗利用規約",
                store_contract_url_effective,
            )

        for module in agreement_modules:
            module_title = module.get("title") or "追加条項"
            module_content = (module.get("content") or "").strip()
            if not module_content:
                continue
            if module.get("is_url") and _is_http_url(module_content):
                _append_store_item("url", module_title, module_content)
            else:
                _append_store_item("text", module_title, module_content)

        context.update(
            {
                "tenant": tenant,
                "resources": resources,
                "services": services,
                "tenant_logo_url": tenant.logo.url if getattr(tenant, "logo", None) else "",
                "booking_window_days": max(1, int(getattr(tenant, "booking_window_days", 14) or 14)),
                "cancellation_window_hours": max(1, int(getattr(tenant, "cancellation_window_hours", 2) or 2)),
                "store_contract_label": (tenant.store_contract_label or "店舗利用規約").strip() or "店舗利用規約",
                "store_contract_url": (tenant.store_contract_url or "").strip(),
                "vrc_terms_url": vrc_terms_url,
                "rosterly_terms_url": rosterly_terms_url,
                "store_contract_url_effective": store_contract_url_effective,
                "store_contract_items": store_contract_items,
                "tenant_is_subscribed": _tenant_is_subscribed(tenant),
                "is_core_time_store": _is_core_time_store(tenant),
                "core_time_summary": summarize_core_time_config(getattr(tenant, "core_time_week_config", {})),
                "subscription_status": (tenant.subscription_status or "").upper() or "NONE",
                "required_customer_fields": _normalize_required_customer_fields(
                    getattr(tenant, "required_customer_fields", ["VRCID", "DISCORDID", "EMAIL"])
                ),
                "agreement_modules": agreement_modules,
                **_tenant_api_ban_banner_context(tenant),
                "system_admin_contact_url": (getattr(settings, "SYSTEM_ADMIN_CONTACT_URL", "") or "mailto:support@rosterlyreverse.com").strip(),
            }
        )
        return context


class DashboardPublicBookingAvailabilityApi(View):
    def _booking_resource_queryset(self, tenant):
        return _exclude_demo_admin_resources(
            Resource.objects.filter(tenant=tenant, is_active=True)
            .filter(Q(linked_user__isnull=True) | Q(linked_user__is_active=True))
            .filter(Q(linked_user__isnull=True) | Q(profile__platform_terms_agreed=True))
        )

    def get(self, request, tenant_slug, *args, **kwargs):
        tenant = Tenant.objects.filter(slug=tenant_slug).first()
        if tenant and getattr(tenant, "deleted_at", None):
            tenant = None
        if not tenant:
            return JsonResponse({"error": "Tenant not found"}, status=404)

        resource_id = (request.GET.get("resource_id") or "").strip()
        if not resource_id:
            return JsonResponse({"error": "resource_id is required"}, status=400)

        try:
            resource = self._booking_resource_queryset(tenant).get(id=resource_id)
        except Resource.DoesNotExist:
            return JsonResponse({"error": "Resource not found"}, status=404)

        now = timezone.now()
        booking_window_days = max(1, int(getattr(tenant, "booking_window_days", 14) or 14))
        horizon = now + timedelta(days=booking_window_days)
        slots = (
            Availability.objects.filter(
                resource=resource,
                is_booked=False,
                end_time__gt=now + timedelta(hours=24),
                start_time__lt=horizon,
            )
            .order_by("start_time")[:300]
        )
        data = [
            {
                "id": str(slot.id),
                "start": slot.start_time.isoformat(),
                "end": slot.end_time.isoformat(),
            }
            for slot in slots
        ]
        return JsonResponse({"slots": data, "booking_window_days": booking_window_days})


class DashboardTrackingApi(View):
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        try:
            payload = json.loads(request.body or "{}")
        except (TypeError, ValueError):
            return JsonResponse({"status": "error", "message": "invalid_json"}, status=400)

        event_type = (payload.get("action") or "").strip().upper()
        if event_type not in {"VIEW_PAGE", "PAGE_DURATION", "CLICK_CAST", "CLICK_RESERVATION_INFO", "BOOKING_SUCCESS"}:
            return JsonResponse({"status": "ignored"})

        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        tenant_slug = (meta.get("tenant_slug") or payload.get("tenant_slug") or "").strip()
        tenant = Tenant.objects.filter(slug=tenant_slug).first() if tenant_slug else None

        booking = None
        booking_id = (meta.get("booking_id") or "").strip()
        if booking_id and tenant:
            booking = Booking.objects.filter(id=booking_id, tenant=tenant).first()

        visit_hour_jst = timezone.localtime(timezone.now()).hour
        visit_bucket = f"{visit_hour_jst:02d}:00-{(visit_hour_jst + 1) % 24:02d}:00"
        merged_meta = {
            **meta,
            "ip": _behavior_client_ip(request),
            "user_agent": request.META.get("HTTP_USER_AGENT", ""),
            "visit_hour_jst": visit_hour_jst,
            "visit_time_bucket_jst": visit_bucket,
        }

        if not request.session.session_key:
            request.session.save()

        UserBehaviorEvent.objects.create(
            user=request.user if request.user.is_authenticated else None,
            tenant=tenant,
            booking=booking,
            event_type=event_type,
            target=(payload.get("target") or "")[:255],
            page_url=(meta.get("page_url") or request.path)[:500],
            session_key=request.session.session_key or "",
            meta_data=merged_meta,
        )
        return JsonResponse({"status": "success"})


@method_decorator(csrf_exempt, name="dispatch")
class StripeWebhookView(View):
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        try:
            stripe_service.verify_webhook_signature(request.body, request.META.get("HTTP_STRIPE_SIGNATURE", ""))
            event = json.loads(request.body.decode("utf-8"))
        except stripe_service.StripeConfigError as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=500)
        except (ValueError, stripe_service.StripeApiError) as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)

        event_type = (event.get("type") or "").strip()
        data_object = (((event.get("data") or {}).get("object")) or {})
        tenant = None

        tenant_id = (((data_object.get("metadata") or {}).get("tenant_id")) or "").strip()
        if tenant_id:
            tenant = Tenant.objects.filter(id=tenant_id).first()

        if tenant is None:
            customer_id = (data_object.get("customer") or "").strip()
            if customer_id:
                tenant = Tenant.objects.filter(stripe_customer_id=customer_id).first()

        if tenant is None:
            return JsonResponse({"ok": True, "ignored": True})

        if event_type == "checkout.session.completed":
            subscription_id = (data_object.get("subscription") or "").strip()
            customer_id = (data_object.get("customer") or "").strip()
            update_fields = []
            if customer_id and tenant.stripe_customer_id != customer_id:
                tenant.stripe_customer_id = customer_id
                update_fields.append("stripe_customer_id")
            if subscription_id and tenant.stripe_subscription_id != subscription_id:
                tenant.stripe_subscription_id = subscription_id
                update_fields.append("stripe_subscription_id")
            if update_fields:
                tenant.save(update_fields=update_fields)

        if event_type in {"checkout.session.completed", "customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"}:
            stripe_service.sync_tenant_subscription(tenant)

        return JsonResponse({"ok": True})


class DashboardPublicBookingCreateApi(View):
    def _booking_resource_queryset(self, tenant):
        return _exclude_demo_admin_resources(
            Resource.objects.filter(tenant=tenant, is_active=True)
            .filter(Q(linked_user__isnull=True) | Q(linked_user__is_active=True))
            .filter(Q(linked_user__isnull=True) | Q(profile__platform_terms_agreed=True))
        )

    def post(self, request, tenant_slug, *args, **kwargs):
        tenant = Tenant.objects.filter(slug=tenant_slug).first()
        if tenant and getattr(tenant, "deleted_at", None):
            tenant = None
        if not tenant:
            return JsonResponse({"error": "Tenant not found"}, status=404)
        # Honeypot trap: bots often fill hidden fields.
        if (request.POST.get("website") or "").strip():
            return JsonResponse({"error": "不正なリクエストです。"}, status=400)
        if not _tenant_is_subscribed(tenant):
            return JsonResponse({"error": "この店舗は未契約のため、現在予約を受け付けていません。"}, status=403)
        if _is_core_time_store(tenant):
            return JsonResponse({"error": "この店舗はコアタイム制のため、公開予約は受け付けていません。"}, status=403)

        resource_id = (request.POST.get("resource_id") or "").strip()
        customer_name = (request.POST.get("customer_name") or "").strip()
        customer_email = (request.POST.get("customer_email") or "").strip()
        customer_vrcid = (request.POST.get("customer_vrcid") or "").strip()
        customer_discord_id = (request.POST.get("customer_discord_id") or "").strip()
        start_raw = (request.POST.get("start_time") or "").strip()
        service_id = (request.POST.get("service_id") or "").strip()

        required_fields = _normalize_required_customer_fields(
            getattr(tenant, "required_customer_fields", ["VRCID", "DISCORDID", "EMAIL"])
        )

        if not all([resource_id, start_raw]):
            return JsonResponse({"error": "Missing required fields"}, status=400)
        if "VRCID" in required_fields and not customer_vrcid:
            return JsonResponse({"error": "VRCID is required"}, status=400)
        if "DISCORDID" in required_fields and not customer_discord_id:
            return JsonResponse({"error": "DiscordID is required"}, status=400)
        if "EMAIL" in required_fields and not customer_email:
            return JsonResponse({"error": "Email is required"}, status=400)

        anti_abuse_fingerprint = "|".join(
            [
                customer_email.lower(),
                customer_vrcid.lower(),
                customer_discord_id.lower(),
                resource_id,
                start_raw,
            ]
        )
        if _public_booking_is_rate_limited(request, tenant_slug, anti_abuse_fingerprint):
            return JsonResponse({"error": "アクセスが集中しています。少し時間を空けてから再度お試しください。"}, status=429)

        # 店舗の必須項目設定に合わせて customer_name を自動決定する。
        if "VRCID" in required_fields and customer_vrcid:
            customer_name = customer_vrcid
        elif "DISCORDID" in required_fields and customer_discord_id:
            customer_name = customer_discord_id
        elif "EMAIL" in required_fields and customer_email:
            customer_name = customer_email
        elif not customer_name:
            customer_name = customer_vrcid or customer_discord_id or customer_email or "Guest"

        start_time = parse_datetime(start_raw)
        if not start_time:
            try:
                start_time = datetime.strptime(start_raw, "%Y-%m-%dT%H:%M")
            except ValueError:
                start_time = None
        if not start_time:
            return JsonResponse({"error": "Invalid start_time"}, status=400)
        if timezone.is_naive(start_time):
            start_time = timezone.make_aware(start_time, timezone.get_current_timezone())

        try:
            resource = self._booking_resource_queryset(tenant).get(id=resource_id)
        except Resource.DoesNotExist:
            return JsonResponse({"error": "Resource is not bookable"}, status=404)
        if _is_demo_admin_resource(resource):
            return JsonResponse({"error": "Resource is not bookable"}, status=403)

        selected_service = None
        duration_minutes = 60
        if service_id:
            selected_service = ServicePreset.objects.filter(tenant=tenant, id=service_id, is_active=True).first()
            if not selected_service:
                return JsonResponse({"error": "Invalid service"}, status=400)
            duration_minutes = selected_service.duration_minutes
        else:
            selected_service = resolve_service_by_duration(tenant, duration_minutes)

        end_time = start_time + timedelta(minutes=max(1, duration_minutes))
        if start_time < timezone.now() + timedelta(hours=24):
            return JsonResponse({"error": "Must book 24h in advance"}, status=400)

        in_slot = Availability.objects.filter(
            resource=resource,
            is_booked=False,
            start_time__lte=start_time,
            end_time__gte=end_time,
        ).exists()
        if not in_slot:
            return JsonResponse({"error": "Selected start time is outside available slots"}, status=400)

        conflict = Booking.objects.filter(
            tenant=tenant,
            resource=resource,
            start_time__lt=end_time + timedelta(minutes=30),
            end_time__gt=start_time - timedelta(minutes=30),
            status="CONFIRMED",
        ).exists()
        if conflict:
            return JsonResponse({"error": "Time slot unavailable"}, status=409)

        selected_service_name = selected_service.name if selected_service else ""
        with transaction.atomic():
            booking = Booking.objects.create(
                tenant=tenant,
                resource=resource,
                customer_id=customer_vrcid or None,
                customer_email=customer_email,
                customer_discord_id=customer_discord_id or None,
                customer_name=customer_name,
                selected_service=selected_service,
                selected_service_name=selected_service_name,
                start_time=start_time,
                end_time=end_time,
                booking_type="PUBLIC",
                status="CONFIRMED",
            )
            _ensure_booking_public_access(request, booking)
            UserBehaviorEvent.objects.create(
                user=request.user if request.user.is_authenticated else None,
                tenant=tenant,
                booking=booking,
                event_type="BOOKING_SUCCESS",
                target=resource.name,
                page_url=request.path[:500],
                session_key=request.session.session_key or "",
                meta_data={
                    "resource_id": str(resource.id),
                    "service_id": str(selected_service.id) if selected_service else "",
                    "service_name": selected_service_name,
                    "start_time": start_time.isoformat(),
                    "end_time": end_time.isoformat(),
                    "visit_time_bucket_jst": f"{timezone.localtime(timezone.now()).hour:02d}:00-{(timezone.localtime(timezone.now()).hour + 1) % 24:02d}:00",
                    "ip": _behavior_client_ip(request),
                },
            )
            transaction.on_commit(lambda: process_new_booking.delay(booking.id))

        return JsonResponse({"ok": True, "booking_id": str(booking.id), "public_detail_url": booking.public_detail_url})


class DashboardPublicBookingDetailView(TemplateView):
    template_name = "dashboard/public_booking_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        access_token = kwargs.get("access_token")
        booking = (
            Booking.objects.filter(public_access_token=access_token)
            .select_related("tenant", "resource", "selected_service")
            .first()
        )
        can_cancel = False
        cancellation_window_hours = 2
        cancellation_deadline_label = "-"
        too_late_message = "店舗規定により、キャンセル可能期限を過ぎたためキャンセルできません。ご不明点は店舗までお問い合わせください。"
        if booking:
            cancellation_window_hours = max(1, int(getattr(booking.tenant, "cancellation_window_hours", 2) or 2))
            cancellation_deadline = booking.start_time - timedelta(hours=cancellation_window_hours)
            cancellation_deadline_label = timezone.localtime(cancellation_deadline).strftime("%Y年%m月%d日 %H:%M")
        if booking and booking.status == "CONFIRMED":
            can_cancel = (booking.start_time - timezone.now()) >= timedelta(hours=cancellation_window_hours)

        context.update(
            {
                "booking": booking,
                "can_cancel": can_cancel,
                "too_late_to_cancel": bool(booking and booking.status == "CONFIRMED" and not can_cancel),
                "cancellation_window_hours": cancellation_window_hours,
                "cancellation_deadline_label": cancellation_deadline_label,
                "too_late_message": too_late_message,
                "report_reasons": REPORT_REASON_CHOICES,
                **_tenant_api_ban_banner_context(getattr(booking, "tenant", None)),
                "system_admin_contact_url": (getattr(settings, "SYSTEM_ADMIN_CONTACT_URL", "") or "mailto:support@rosterlyreverse.com").strip(),
            }
        )
        return context


class DashboardPublicBookingReportApi(View):
    def post(self, request, access_token, *args, **kwargs):
        booking = (
            Booking.objects.filter(public_access_token=access_token)
            .select_related("tenant", "resource")
            .first()
        )
        if not booking:
            return JsonResponse({"ok": False, "error": "予約が見つかりません。"}, status=404)

        reason = (request.POST.get("reason") or "").strip()
        detail = (request.POST.get("detail") or "").strip()
        media = request.FILES.get("media")
        valid_reasons = {choice[0] for choice in REPORT_REASON_CHOICES}
        if reason not in valid_reasons:
            return JsonResponse({"ok": False, "error": "通報理由を選択してください。"}, status=400)

        with transaction.atomic():
            BookingReport.objects.create(
                booking=booking,
                tenant=booking.tenant,
                reporter_role="CUSTOMER",
                reason=reason,
                detail=detail,
                media=media,
                reporter_name=booking.customer_name or "",
                reporter_email=booking.customer_email or "",
                is_read_by_admin=False,
            )
            booking.customer_report_count = F("customer_report_count") + 1
            booking.last_reported_at = timezone.now()
            booking.save(update_fields=["customer_report_count", "last_reported_at"])

        return JsonResponse({"ok": True})


class DashboardPublicBookingCancelApi(View):
    def post(self, request, access_token, *args, **kwargs):
        booking = (
            Booking.objects.filter(public_access_token=access_token)
            .select_related("resource")
            .first()
        )
        if not booking:
            return JsonResponse({"ok": False, "error": "予約が見つかりません。"}, status=404)
        if booking.status != "CONFIRMED":
            return JsonResponse({"ok": False, "error": "この予約は既にキャンセル済みです。"}, status=400)
        cancellation_window_hours = max(1, int(getattr(booking.tenant, "cancellation_window_hours", 2) or 2))
        if (booking.start_time - timezone.now()) < timedelta(hours=cancellation_window_hours):
            return JsonResponse({"ok": False, "error": "店舗規定により、キャンセル可能期限を過ぎたためキャンセルできません。ご不明点は店舗までお問い合わせください。"}, status=400)

        booking.status = "CANCELLED"
        booking.save(update_fields=["status"])

        if booking.resource and booking.resource.email:
            tokyo = timezone.get_current_timezone()
            start_label = timezone.localtime(booking.start_time, tokyo).strftime("%Y-%m-%d %H:%M")
            transaction.on_commit(
                lambda: send_cancellation_email_task.delay(
                    booking.resource.email,
                    booking.resource.name,
                    booking.customer_name,
                    start_label,
                )
            )

        return JsonResponse({"ok": True})


class DashboardAdminReportNotificationsApi(LoginRequiredMixin, UserPassesTestMixin, View):
    def test_func(self):
        role = getattr(self.request.user, "role", "STAFF")
        return self.request.user.is_superuser or role == "ADMIN"

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            return JsonResponse({"error": "管理者権限が必要です"}, status=403)
        return JsonResponse({"error": "ログインが必要です"}, status=401)

    def get(self, request, *args, **kwargs):
        tenant = getattr(request.user, "tenant", None)
        if not tenant:
            return JsonResponse({"unread_count": 0, "items": []})

        qs = (
            BookingReport.objects.filter(tenant=tenant)
            .select_related("booking", "booking__resource")
            .order_by("-created_at")[:20]
        )
        items = []
        for report in qs:
            reason_label = dict(REPORT_REASON_CHOICES).get(report.reason, report.reason)
            items.append(
                {
                    "id": report.id,
                    "booking_id": str(report.booking_id),
                    "booking_short": str(report.booking_id)[:8],
                    "reporter_role": report.reporter_role,
                    "reason": reason_label,
                    "detail": report.detail or "",
                    "reporter_name": report.reporter_name or "",
                    "is_read": report.is_read_by_admin,
                    "created_at": timezone.localtime(report.created_at).strftime("%Y/%m/%d %H:%M"),
                }
            )

        unread_count = BookingReport.objects.filter(tenant=tenant, is_read_by_admin=False).count()
        return JsonResponse({"unread_count": unread_count, "items": items})


def dashboard_logout(request):
    logout(request)
    return redirect("dashboard_login")


class DashboardSuperAdminLoginView(TemplateView):
    template_name = "dashboard/super_login.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.is_superuser:
            return redirect("dashboard_super_global")
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        username = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""
        next_url = (request.POST.get("next") or "").strip()
        if next_url and not url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            next_url = ""

        user = authenticate(request, username=username, password=password)
        if not user:
            messages.error(request, "ユーザー名またはパスワードが正しくありません。")
            return self.get(request, *args, **kwargs)
        if not user.is_superuser:
            messages.error(request, "この入口はスーパー管理者専用です。")
            return self.get(request, *args, **kwargs)

        login(request, user)
        return redirect(next_url or reverse("dashboard_super_global"))


class SuperAdminRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return bool(getattr(self.request.user, "is_superuser", False))

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            messages.warning(self.request, "スーパー管理者専用ページです。")
            return redirect("dashboard_login")
        return redirect("dashboard_super_login")


class DashboardSuperGlobalView(SuperAdminRequiredMixin, TemplateView):
    template_name = "dashboard/super_global_dashboard.html"

    def _build_unique_tenant_slug(self, base_text):
        base_slug = slugify(base_text or "debug-shop") or "debug-shop"
        base_slug = re.sub(r"[^a-z0-9-]", "", base_slug.lower()).strip("-") or "debug-shop"
        candidate = base_slug
        index = 2
        while Tenant.objects.filter(slug=candidate).exists():
            candidate = f"{base_slug}-{index}"
            index += 1
        return candidate

    def post(self, request, *args, **kwargs):
        action = (request.POST.get("action") or "").strip()
        if action == "create_global_announcement":
            title = (request.POST.get("announcement_title") or "").strip()
            body = (request.POST.get("announcement_body") or "").strip()
            link_url = (request.POST.get("announcement_link_url") or "").strip()
            is_pinned = request.POST.get("announcement_is_pinned") == "on"
            image = request.FILES.get("announcement_image")

            if not title:
                messages.error(request, "公告タイトルは必須です。")
                return redirect("dashboard_super_global")
            if link_url and not _is_http_url(link_url):
                messages.error(request, "リンクは http(s) 形式で入力してください。")
                return redirect("dashboard_super_global")

            GlobalAnnouncement.objects.create(
                title=title,
                body=body,
                link_url=link_url,
                image=image,
                is_pinned=is_pinned,
                is_active=True,
                created_by=request.user,
            )
            messages.success(request, "全体公告を送信しました。")
            return redirect("dashboard_super_global")

        if action == "deactivate_global_announcement":
            announcement_id = (request.POST.get("announcement_id") or "").strip()
            announcement = GlobalAnnouncement.objects.filter(id=announcement_id).first()
            if not announcement:
                messages.error(request, "公告が見つかりません。")
                return redirect("dashboard_super_global")
            if announcement.is_active:
                announcement.is_active = False
                announcement.save(update_fields=["is_active"])
            messages.success(request, "公告を停止しました。")
            return redirect("dashboard_super_global")

        if action == "create_debug_tenant":
            shop_name = (request.POST.get("shop_name") or "").strip() or "Debug Shop"
            contact_email = (request.POST.get("contact_email") or request.user.email or "").strip()
            requested_slug = (request.POST.get("tenant_slug") or "").strip().lower()
            if requested_slug and not re.fullmatch(r"[a-z0-9-]+", requested_slug):
                messages.error(request, "slug は英字・数字・ハイフンのみ使用できます。")
                return redirect("dashboard_super_global")

            tenant_slug = requested_slug
            if not tenant_slug:
                tenant_slug = self._build_unique_tenant_slug(shop_name)
            elif Tenant.objects.filter(slug=tenant_slug).exists():
                messages.error(request, f"slug 已存在: {tenant_slug}")
                return redirect("dashboard_super_global")

            tenant = Tenant.objects.create(
                name=shop_name,
                slug=tenant_slug,
                contact_email=contact_email or None,
                api_key=secrets.token_urlsafe(24)[:32],
                api_secret=secrets.token_urlsafe(32),
                is_api_enabled=True,
                enable_saas_dashboard=True,
            )

            user = request.user
            update_fields = []
            if user.tenant_id != tenant.id:
                user.tenant = tenant
                update_fields.append("tenant")
            if user.role != "ADMIN":
                user.role = "ADMIN"
                update_fields.append("role")
            if not user.is_staff:
                user.is_staff = True
                update_fields.append("is_staff")
            if update_fields:
                user.save(update_fields=update_fields)

            ensure_staff_resource_binding(user, tenant=tenant)
            messages.success(request, f"已创建调试店铺: {tenant.name} ({tenant.slug})")
            return redirect(f"{reverse('tenant_dashboard')}?tenant_id={tenant.id}&tab=shop")

        if action in {"disable_tenant_api", "enable_tenant_api"}:
            tenant_id = (request.POST.get("tenant_id") or "").strip()
            tenant = Tenant.objects.filter(id=tenant_id).first()
            if not tenant:
                messages.error(request, "店铺不存在。")
                return redirect("dashboard_super_global")
            target_enabled = action == "enable_tenant_api"
            if tenant.is_api_enabled != target_enabled:
                tenant.is_api_enabled = target_enabled
                update_fields = ["is_api_enabled"]
                if target_enabled:
                    tenant.api_ban_reason = ""
                    tenant.api_ban_note = ""
                    tenant.api_ban_media = None
                    tenant.api_banned_at = None
                    tenant.api_banned_by = None
                    update_fields.extend(["api_ban_reason", "api_ban_note", "api_ban_media", "api_banned_at", "api_banned_by"])
                else:
                    reason_code = (request.POST.get("ban_reason") or "").strip().upper()
                    if reason_code not in {code for code, _label in TENANT_API_BAN_REASON_CHOICES}:
                        messages.error(request, "封禁时必须选择有效原因。")
                        return redirect("dashboard_super_global")
                    tenant.api_ban_reason = reason_code
                    tenant.api_ban_note = (request.POST.get("ban_note") or "").strip()
                    media = request.FILES.get("ban_media")
                    if media:
                        tenant.api_ban_media = media
                        update_fields.append("api_ban_media")
                    tenant.api_banned_at = timezone.now()
                    tenant.api_banned_by = request.user
                    update_fields.extend(["api_ban_reason", "api_ban_note", "api_banned_at", "api_banned_by"])
                tenant.save(update_fields=list(dict.fromkeys(update_fields)))
            if target_enabled:
                messages.success(request, f"已解封店铺 API: {tenant.name}")
            else:
                messages.warning(request, f"已封禁店铺 API: {tenant.name}")
            return redirect("dashboard_super_global")

        if action == "save_subscription_override":
            tenant_id = (request.POST.get("tenant_id") or "").strip()
            tenant = Tenant.objects.filter(id=tenant_id).first()
            if not tenant:
                messages.error(request, "店铺不存在。")
                return redirect("dashboard_super_global")

            enabled = request.POST.get("override_enabled") == "on"
            status = (request.POST.get("override_status") or "NONE").strip().upper()
            started_raw = (request.POST.get("override_started_at") or "").strip()
            ends_raw = (request.POST.get("override_ends_at") or "").strip()
            note = (request.POST.get("override_note") or "").strip()
            if status not in {"ACTIVE", "TRIAL", "CANCELED", "NONE"}:
                messages.error(request, "手动订阅状态不合法。")
                return redirect("dashboard_super_global")

            started_at = parse_datetime(started_raw) if started_raw else None
            ends_at = parse_datetime(ends_raw) if ends_raw else None
            if started_at and timezone.is_naive(started_at):
                started_at = timezone.make_aware(started_at, timezone.get_current_timezone())
            if ends_at and timezone.is_naive(ends_at):
                ends_at = timezone.make_aware(ends_at, timezone.get_current_timezone())

            tenant.subscription_override_enabled = enabled
            tenant.subscription_override_status = status if enabled else ""
            tenant.subscription_override_started_at = started_at if enabled else None
            tenant.subscription_override_ends_at = ends_at if enabled else None
            tenant.subscription_override_note = note if enabled else ""
            tenant.save(
                update_fields=[
                    "subscription_override_enabled",
                    "subscription_override_status",
                    "subscription_override_started_at",
                    "subscription_override_ends_at",
                    "subscription_override_note",
                ]
            )
            messages.success(request, f"已更新店铺订阅手动覆盖: {tenant.name}")
            return redirect("dashboard_super_global")

        messages.error(request, "未识别的操作。")
        return redirect("dashboard_super_global")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        now = timezone.now()
        today = timezone.localdate()

        total_tenants = Tenant.objects.count()
        active_tenants = Tenant.objects.filter(subscription_status__in=["ACTIVE", "TRIAL"]).count()
        total_users = SaaSUser.objects.count()
        total_superusers = SaaSUser.objects.filter(is_superuser=True).count()
        total_staff_like = SaaSUser.objects.filter(role__in=["ADMIN", "STAFF"]).count()
        total_resources = Resource.objects.count()
        total_bookings = Booking.objects.count()
        today_bookings = Booking.objects.filter(start_time__date=today).count()
        upcoming_24h_bookings = Booking.objects.filter(start_time__gte=now, start_time__lte=now + timedelta(hours=24)).count()
        unread_reports_global = BookingReport.objects.filter(is_read_by_admin=False).count()
        active_announcements = list(
            GlobalAnnouncement.objects.filter(is_active=True)
            .select_related("created_by")
            .order_by("-is_pinned", "-created_at")[:20]
        )

        tenant_rows = []
        tenants = Tenant.objects.all().order_by("name")
        for tenant in tenants:
            tenant_rows.append(
                {
                    "id": str(tenant.id),
                    "name": tenant.name,
                    "slug": tenant.slug,
                    "contact_email": tenant.contact_email or "",
                    "subscription_status": (tenant.subscription_status or "NONE").upper(),
                    "effective_subscription_status": _effective_subscription_context(tenant)["effective_subscription_status"],
                    "subscription_override_enabled": bool(getattr(tenant, "subscription_override_enabled", False)),
                    "subscription_override_status": (getattr(tenant, "subscription_override_status", "") or "").upper(),
                    "subscription_override_started_at": getattr(tenant, "subscription_override_started_at", None),
                    "subscription_override_ends_at": getattr(tenant, "subscription_override_ends_at", None),
                    "subscription_override_note": (getattr(tenant, "subscription_override_note", "") or "").strip(),
                    "is_api_enabled": bool(getattr(tenant, "is_api_enabled", True)),
                    "api_ban_reason_label": _tenant_api_ban_reason_label(getattr(tenant, "api_ban_reason", "")),
                    "api_ban_note": (getattr(tenant, "api_ban_note", "") or "").strip(),
                    "users_count": SaaSUser.objects.filter(tenant=tenant).count(),
                    "resources_count": Resource.objects.filter(tenant=tenant).count(),
                    "bookings_count": Booking.objects.filter(tenant=tenant).count(),
                    "unread_reports": BookingReport.objects.filter(tenant=tenant, is_read_by_admin=False).count(),
                    "tenant_dashboard_url": f"{reverse('tenant_dashboard')}?tenant_id={tenant.id}",
                    "public_booking_url": reverse("dashboard_public_booking", kwargs={"tenant_slug": tenant.slug}),
                }
            )

        context.update(
            {
                "total_tenants": total_tenants,
                "active_tenants": active_tenants,
                "total_users": total_users,
                "total_superusers": total_superusers,
                "total_staff_like": total_staff_like,
                "total_resources": total_resources,
                "total_bookings": total_bookings,
                "today_bookings": today_bookings,
                "upcoming_24h_bookings": upcoming_24h_bookings,
                "unread_reports_global": unread_reports_global,
                "active_announcement_count": len(active_announcements),
                "active_announcements": active_announcements,
                "tenant_rows": tenant_rows,
                "ban_reason_choices": TENANT_API_BAN_REASON_CHOICES,
            }
        )
        return context


class LocalPasswordLoginView(LoginView):
    template_name = "account/local_login.html"
    authentication_form = AuthenticationForm
    redirect_authenticated_user = True


class AdminDashboardRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        role = getattr(self.request.user, "role", "STAFF")
        return self.request.user.is_superuser or role == "ADMIN"

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            messages.warning(self.request, "管理者専用ページです。共有スケジュールへ移動しました。")
            return redirect("shared_schedule")
        return super().handle_no_permission()


class TenantDashboardView(AdminDashboardRequiredMixin, TemplateView):
    template_name = "dashboard/tenant_dashboard.html"

    def _resolve_tenant(self):
        if self.request.user.is_superuser:
            requested_tenant_id = (self.request.GET.get("tenant_id") or self.request.POST.get("tenant_id") or "").strip()
            if requested_tenant_id:
                scoped_tenant = Tenant.objects.filter(id=requested_tenant_id).first()
                if scoped_tenant:
                    return scoped_tenant
        tenant = getattr(self.request.user, "tenant", None)
        if tenant:
            return tenant
        return Tenant.objects.first()

    def _public_booking_url(self, tenant):
        if not tenant:
            return ""
        return self._absolute_public_url(reverse("dashboard_public_booking", kwargs={"tenant_slug": tenant.slug}))

    def _absolute_public_url(self, path):
        base = (getattr(settings, "SYSTEM_B_PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
        if _is_http_url(base):
            return f"{base}{path}"

        try:
            built = self.request.build_absolute_uri(path)
            if _is_http_url(built):
                return built
        except Exception:
            pass

        host = (self.request.get_host() or "").strip()
        if host:
            scheme = "https" if self.request.is_secure() else "http"
            return f"{scheme}://{host}{path}"
        return path

    def _resolve_cast_avatar_url(self, resource, profile):
        current = self._normalize_avatar_url(getattr(profile, "avatar_url", "") if profile else "")
        if current:
            return current
        linked_user = getattr(resource, "linked_user", None)
        if not linked_user:
            return ""
        alt = (
            ResourceProfile.objects.filter(resource__tenant=resource.tenant, resource__linked_user=linked_user)
            .exclude(avatar_url__isnull=True)
            .exclude(avatar_url="")
            .order_by("-updated_at", "-id")
            .values_list("avatar_url", flat=True)
            .first()
        )
        return self._normalize_avatar_url(alt)

    def _save_template(self, request, tenant):
        event_type = request.POST.get("event_type")
        send_to_customer = request.POST.get("send_to_customer") == "on"
        send_to_cast = request.POST.get("send_to_cast") == "on"
        service_name = "{{ selected_service_name }}"

        defaults_data = {
            "subject_template": request.POST.get("subject"),
            "email_title": request.POST.get("email_title"),
            "email_greeting": request.POST.get("email_greeting"),
            "service_name": service_name,
            "button_text": request.POST.get("button_text"),
            "button_link": "{{ booking_public_url }}",
            "footer_title": request.POST.get("footer_title"),
            "footer_text": request.POST.get("footer_text"),
            "send_to_customer": send_to_customer,
            "send_to_cast": send_to_cast,
            "is_active": True,
        }

        EmailTemplate.objects.update_or_create(
            tenant=tenant,
            event_type=event_type,
            defaults=defaults_data,
        )

    def _save_tenant_settings(self, request, tenant):
        name = (request.POST.get("tenant_name") or "").strip()
        contact_email = (request.POST.get("tenant_contact_email") or "").strip()
        tenant_slug = (request.POST.get("tenant_slug") or tenant.slug or "").strip().lower()
        store_type = (request.POST.get("store_type") or "FLEX_SHIFT").strip().upper()
        booking_window_days_raw = (request.POST.get("booking_window_days") or "14").strip()
        cancellation_window_hours_raw = (request.POST.get("cancellation_window_hours") or "2").strip()
        booking_detail_redirect_url = (request.POST.get("booking_detail_redirect_url") or "").strip() or None
        store_contract_label = (request.POST.get("store_contract_label") or "").strip()
        store_contract_url = (request.POST.get("store_contract_url") or "").strip() or None
        required_customer_fields = _normalize_required_customer_fields(request.POST.getlist("required_customer_fields"))
        custom_agreements_json = (request.POST.get("custom_agreements_json") or "").strip()
        custom_agreements = _normalize_agreement_modules(custom_agreements_json)
        custom_terms_label = custom_agreements[0]["title"] if custom_agreements else ""
        custom_terms_body = json.dumps(custom_agreements, ensure_ascii=False)
        core_time_week_config_raw = (request.POST.get("core_time_week_config") or "").strip()
        try:
            core_time_week_config = normalize_core_time_config(json.loads(core_time_week_config_raw or "{}"))
        except (TypeError, ValueError):
            raise ValueError("Core Time 設定の形式が不正です")

        if not name:
            raise ValueError("店舗名は必須です")
        if not contact_email:
            raise ValueError("店舗メールは必須です")
        if not tenant_slug:
            raise ValueError("slug は必須です")
        if not re.fullmatch(r"[a-z0-9-]+", tenant_slug):
            raise ValueError("slug は英字・数字・ハイフンのみ入力できます")
        slug_conflict = Tenant.objects.filter(slug=tenant_slug).exclude(id=tenant.id).exists()
        if slug_conflict:
            raise ValueError("この slug は既に使用されています")
        if store_type not in {"CORE_TIME", "FLEX_SHIFT"}:
            raise ValueError("店舗タイプが不正です")
        if store_type == "CORE_TIME" and not core_time_week_config:
            raise ValueError("Core Time の営業時間を最低1枠設定してください")
        if not store_contract_label:
            raise ValueError("店舗契約は必須です")
        try:
            booking_window_days = max(1, int(booking_window_days_raw))
        except ValueError:
            raise ValueError("予約公開日数は1以上の整数で入力してください")
        try:
            cancellation_window_hours = max(1, int(cancellation_window_hours_raw))
        except ValueError:
            raise ValueError("キャンセル可能期限は1以上の整数で入力してください")
        if booking_detail_redirect_url and not _is_http_url(booking_detail_redirect_url):
            raise ValueError("予約詳細リダイレクトURLは http(s) 形式で入力してください")
        if store_contract_url and not _is_http_url(store_contract_url):
            raise ValueError("店舗契約URLは http(s) 形式で入力してください")

        update_fields = []
        if tenant.name != name:
            tenant.name = name
            update_fields.append("name")
        if (tenant.contact_email or "") != contact_email:
            tenant.contact_email = contact_email
            update_fields.append("contact_email")
        if (tenant.slug or "") != tenant_slug:
            tenant.slug = tenant_slug
            update_fields.append("slug")
        if (tenant.store_type or "FLEX_SHIFT") != store_type:
            tenant.store_type = store_type
            update_fields.append("store_type")
        if tenant.booking_window_days != booking_window_days:
            tenant.booking_window_days = booking_window_days
            update_fields.append("booking_window_days")
        if (getattr(tenant, "cancellation_window_hours", 2) or 2) != cancellation_window_hours:
            tenant.cancellation_window_hours = cancellation_window_hours
            update_fields.append("cancellation_window_hours")
        if (getattr(tenant, "booking_detail_redirect_url", None) or None) != booking_detail_redirect_url:
            tenant.booking_detail_redirect_url = booking_detail_redirect_url
            update_fields.append("booking_detail_redirect_url")
        if (tenant.store_contract_label or "") != store_contract_label:
            tenant.store_contract_label = store_contract_label
            update_fields.append("store_contract_label")
        if (tenant.store_contract_url or None) != store_contract_url:
            tenant.store_contract_url = store_contract_url
            update_fields.append("store_contract_url")
        if _normalize_required_customer_fields(getattr(tenant, "required_customer_fields", [])) != required_customer_fields:
            tenant.required_customer_fields = required_customer_fields
            update_fields.append("required_customer_fields")
        if (tenant.custom_terms_label or "") != custom_terms_label:
            tenant.custom_terms_label = custom_terms_label
            update_fields.append("custom_terms_label")
        if (tenant.custom_terms_body or "") != custom_terms_body:
            tenant.custom_terms_body = custom_terms_body
            update_fields.append("custom_terms_body")
        if (tenant.core_time_week_config or {}) != core_time_week_config:
            tenant.core_time_week_config = core_time_week_config
            update_fields.append("core_time_week_config")
        if request.FILES.get("tenant_logo"):
            tenant.logo = request.FILES["tenant_logo"]
            update_fields.append("logo")

        if update_fields:
            tenant.save(update_fields=update_fields)

    def _save_subscription_settings(self, request, tenant):
        subscription_status = (request.POST.get("subscription_status") or "ACTIVE").strip().upper()
        subscription_plan_code = (request.POST.get("subscription_plan_code") or "").strip()
        subscription_started_at_raw = (request.POST.get("subscription_started_at") or "").strip()
        subscription_ends_at_raw = (request.POST.get("subscription_ends_at") or "").strip()

        if subscription_status not in {"ACTIVE", "TRIAL", "CANCELED", "NONE"}:
            raise ValueError("サブスクリプション状態が不正です")

        subscription_started_at = parse_datetime(subscription_started_at_raw) if subscription_started_at_raw else None
        subscription_ends_at = parse_datetime(subscription_ends_at_raw) if subscription_ends_at_raw else None
        if subscription_started_at and timezone.is_naive(subscription_started_at):
            subscription_started_at = timezone.make_aware(subscription_started_at, timezone.get_current_timezone())
        if subscription_ends_at and timezone.is_naive(subscription_ends_at):
            subscription_ends_at = timezone.make_aware(subscription_ends_at, timezone.get_current_timezone())

        update_fields = []
        if (tenant.subscription_status or "").upper() != subscription_status:
            tenant.subscription_status = subscription_status
            update_fields.append("subscription_status")
        if (tenant.subscription_plan_code or "") != subscription_plan_code:
            tenant.subscription_plan_code = subscription_plan_code
            update_fields.append("subscription_plan_code")
        if tenant.subscription_started_at != subscription_started_at:
            tenant.subscription_started_at = subscription_started_at
            update_fields.append("subscription_started_at")
        if tenant.subscription_ends_at != subscription_ends_at:
            tenant.subscription_ends_at = subscription_ends_at
            update_fields.append("subscription_ends_at")

        if update_fields:
            tenant.save(update_fields=update_fields)

    def _create_stripe_checkout(self, tenant):
        success_url = self._absolute_public_url(f"{reverse('tenant_dashboard')}?tab=subscription")
        cancel_url = self._absolute_public_url(f"{reverse('tenant_dashboard')}?tab=subscription")
        session = stripe_service.create_checkout_session(tenant, success_url=success_url, cancel_url=cancel_url)
        return session.get("url") or ""

    def _open_stripe_portal(self, tenant):
        return_url = self._absolute_public_url(f"{reverse('tenant_dashboard')}?tab=subscription")
        session = stripe_service.create_billing_portal_session(tenant, return_url=return_url)
        return session.get("url") or ""

    def _sync_stripe_subscription(self, tenant):
        return stripe_service.sync_tenant_subscription(tenant)

    def _save_core_time_order(self, request, tenant):
        if not _is_core_time_store(tenant):
            raise ValueError("コアタイム制の店舗でのみ操作できます")

        booking_id = (request.POST.get("core_booking_id") or "").strip()
        resource_id = (request.POST.get("core_resource_id") or "").strip()
        customer_vrcid = (request.POST.get("core_customer_vrcid") or "").strip()
        service_preset_id = (request.POST.get("core_service_preset_id") or "").strip()
        start_raw = (request.POST.get("core_start_time") or "").strip()

        if not all([resource_id, customer_vrcid, service_preset_id, start_raw]):
            raise ValueError("必須項目を入力してください")

        start_time = parse_datetime(start_raw)
        if not start_time:
            raise ValueError("開始日時の形式が不正です")
        if timezone.is_naive(start_time):
            start_time = timezone.make_aware(start_time, timezone.get_current_timezone())

        resource = Resource.objects.filter(tenant=tenant, id=resource_id).first()
        if not resource:
            raise ValueError("担当者が見つかりません")
        service_preset = ServicePreset.objects.filter(tenant=tenant, id=service_preset_id, is_active=True).first()
        if not service_preset:
            raise ValueError("サービスが見つかりません")
        end_time = start_time + timedelta(minutes=service_preset.duration_minutes)

        if booking_id:
            booking = Booking.objects.filter(id=booking_id, tenant=tenant, booking_type="CORE_TIME").first()
            if not booking:
                raise ValueError("編集対象のコアタイム注文が見つかりません")
            booking.resource = resource
            booking.customer_name = customer_vrcid
            booking.customer_id = customer_vrcid
            booking.selected_service = service_preset
            booking.selected_service_name = service_preset.name
            booking.start_time = start_time
            booking.end_time = end_time
            booking.status = "CONFIRMED"
            booking.booking_type = "CORE_TIME"
            booking.save(
                update_fields=[
                    "resource",
                    "customer_name",
                    "customer_id",
                    "selected_service",
                    "selected_service_name",
                    "start_time",
                    "end_time",
                    "status",
                    "booking_type",
                ]
            )
            return

        Booking.objects.create(
            tenant=tenant,
            resource=resource,
            customer_name=customer_vrcid,
            customer_id=customer_vrcid,
            selected_service=service_preset,
            selected_service_name=service_preset.name,
            start_time=start_time,
            end_time=end_time,
            status="CONFIRMED",
            booking_type="CORE_TIME",
        )

    def _delete_core_time_order(self, request, tenant):
        if not _is_core_time_store(tenant):
            raise ValueError("コアタイム制の店舗でのみ操作できます")
        booking_id = (request.POST.get("core_booking_id") or "").strip()
        if not booking_id:
            raise ValueError("注文IDが必要です")
        deleted, _ = Booking.objects.filter(id=booking_id, tenant=tenant, booking_type="CORE_TIME").delete()
        if not deleted:
            raise ValueError("削除対象のコアタイム注文が見つかりません")

    def _create_staff_invite(self, request, tenant):
        role = (request.POST.get("invite_role") or "STAFF").strip().upper()
        if role not in {"STAFF", "ADMIN"}:
            role = "STAFF"

        expire_hours_raw = (request.POST.get("invite_expire_hours") or "72").strip()
        max_uses_raw = (request.POST.get("invite_max_uses") or "1").strip()
        try:
            expire_hours = max(1, int(expire_hours_raw))
        except ValueError:
            expire_hours = 72
        try:
            max_uses = max(1, int(max_uses_raw))
        except ValueError:
            max_uses = 1

        token = secrets.token_urlsafe(32)
        invite = StaffInvite.objects.create(
            token=token,
            tenant=tenant,
            role=role,
            max_uses=max_uses,
            expires_at=timezone.now() + timedelta(hours=expire_hours),
            created_by=request.user,
        )
        return invite

    def _deactivate_staff_invite(self, request, tenant):
        invite_id = (request.POST.get("invite_id") or "").strip()
        if not invite_id:
            raise ValueError("Missing invite_id")

        invite = StaffInvite.objects.filter(id=invite_id, tenant=tenant).first()
        if not invite:
            raise ValueError("Invite not found")

        if invite.is_active:
            invite.is_active = False
            invite.save(update_fields=["is_active"])

    def _store_profile_avatar(self, tenant, resource, avatar_file):
        ext = ""
        if "." in (avatar_file.name or ""):
            ext = "." + avatar_file.name.rsplit(".", 1)[-1].lower()
        rel_path = f"resource_avatars/tenant_{tenant.id}/resource_{resource.id}/{uuid4().hex}{ext}"
        stored_path = default_storage.save(rel_path, avatar_file)
        return default_storage.url(stored_path)

    def _normalize_avatar_url(self, url):
        text = (url or "").strip()
        if not text:
            return ""
        text = text.replace("\\/", "/")
        if text.startswith("media/"):
            text = "/" + text
        if text.startswith("/"):
            return text
        # Keep dashboard avatars loadable under HTTPS by upgrading known local hosts.
        if text.startswith("http://") and self.request.is_secure():
            host = (urlparse(text).hostname or "").lower()
            if host in {
                "138.3.221.225",
                "rosterlyreverse.com",
                "www.rosterlyreverse.com",
                "api.rosterlyreverse.com",
                "vr-veludo.com",
                "api.vr-veludo.com",
            }:
                return "https://" + text[len("http://"):]
        return text

    def _ensure_staff_resource_binding(self, user, tenant):
        return ensure_staff_resource_binding(user, tenant=tenant)

    def _save_cast_profile(self, request, tenant):
        resource_id = (request.POST.get("resource_id") or "").strip()
        if not resource_id:
            raise ValueError("Missing resource_id")

        resource = Resource.objects.filter(id=resource_id, tenant=tenant).first()
        if not resource:
            raise ValueError("Resource not found")

        profile, _ = ResourceProfile.objects.get_or_create(resource=resource)
        raw_tags = (request.POST.get("profile_tags") or "").replace("，", ",")
        parsed_tags = [tag.strip() for tag in raw_tags.split(",") if tag.strip()]

        valid_service_ids = {
            str(item)
            for item in ServicePreset.objects.filter(tenant=tenant).values_list("id", flat=True)
        }
        selected_service_ids = [sid for sid in request.POST.getlist("service_preset_ids") if sid in valid_service_ids]
        if not selected_service_ids:
            raise ValueError("対応サービスは最低 1 つ選択してください")
        selected_presets = list(ServicePreset.objects.filter(id__in=selected_service_ids, tenant=tenant))
        selected_durations = {preset.duration_minutes for preset in selected_presets}

        metadata = profile.metadata if isinstance(profile.metadata, dict) else {}
        metadata["service_preset_ids"] = selected_service_ids

        profile.intro = normalize_profile_text(request.POST.get("profile_intro"))
        profile.youtube_url = (request.POST.get("profile_youtube_url") or "").strip() or None
        profile.tags = parsed_tags
        profile.metadata = metadata
        profile.allow_30_min = 30 in selected_durations
        profile.allow_60_min = 60 in selected_durations
        profile.allow_120_min = 120 in selected_durations

        if request.POST.get("profile_avatar_clear") == "on":
            profile.avatar_url = None
        elif request.FILES.get("profile_avatar_file"):
            profile.avatar_url = self._store_profile_avatar(tenant, resource, request.FILES["profile_avatar_file"])

        profile.save()

    def _save_staff_profiles_batch(self, request, tenant):
        user_ids = [uid for uid in request.POST.getlist("user_ids") if uid]
        if not user_ids:
            raise ValueError("No user rows to update")

        updated_count = 0
        blocked_count = 0
        display_only_count = 0
        for user_id in user_ids:
            user = SaaSUser.objects.filter(id=user_id).first()
            if not user:
                continue

            # 管理パネル上の対象は「同一 tenant」または「未割当ユーザー」のみ
            if user.tenant_id not in [tenant.id, None]:
                continue

            display_name = (request.POST.get(f"username__{user_id}") or user.username).strip() or user.username
            email = (request.POST.get(f"email__{user_id}") or "").strip()
            role_raw = request.POST.get(f"role__{user_id}")
            role = role_raw if role_raw in ["ADMIN", "STAFF"] else user.role
            requested_active = request.POST.get(f"is_active__{user_id}") == "on"

            if user.role in {"STAFF", "ADMIN"} or role in {"STAFF", "ADMIN"}:
                linked = self._ensure_staff_resource_binding(user, tenant)
            else:
                linked = Resource.objects.filter(tenant=tenant, linked_user=user).first()

            profile = getattr(linked, "profile", None) if linked else None
            can_enable_booking = bool(profile and getattr(profile, "platform_terms_agreed", False))
            is_active = requested_active and can_enable_booking
            if requested_active and not can_enable_booking:
                blocked_count += 1

            update_fields = []
            if user.tenant_id is None:
                user.tenant = tenant
                update_fields.append("tenant")
            username_conflict = SaaSUser.objects.filter(username=display_name).exclude(pk=user.pk).exists()
            if not username_conflict and user.username != display_name:
                user.username = display_name
                update_fields.append("username")
            if (user.first_name or "") != display_name:
                user.first_name = display_name
                update_fields.append("first_name")
                if username_conflict:
                    display_only_count += 1
            if (user.email or "") != email:
                user.email = email
                update_fields.append("email")
            if user.role != role:
                user.role = role
                update_fields.append("role")
            if user.is_active != is_active:
                user.is_active = is_active
                update_fields.append("is_active")

            if update_fields:
                user.save(update_fields=update_fields)
                updated_count += 1

            if linked and display_name and linked.name != display_name:
                linked.name = display_name
                linked.save(update_fields=["name"])

            if linked and (linked.email or "") != (user.email or ""):
                linked.email = user.email or ""
                linked.save(update_fields=["email"])

            if linked and linked.is_active != user.is_active:
                linked.is_active = user.is_active
                linked.save(update_fields=["is_active"])

            if user.role in {"STAFF", "ADMIN"}:
                self._ensure_staff_resource_binding(user, tenant)

        return updated_count, blocked_count, display_only_count

    def _save_service_preset(self, request, tenant):
        service_id = (request.POST.get("service_id") or "").strip()
        name = (request.POST.get("service_name") or "").strip()
        description = (request.POST.get("service_description") or "").strip() or "..."
        price_raw = (request.POST.get("price") or "").strip()
        duration_raw = (request.POST.get("duration_minutes") or "").strip()
        is_active = request.POST.get("is_active") == "on"

        if not name:
            raise ValueError("Service name is required")
        try:
            price = Decimal(price_raw or "0")
        except (InvalidOperation, ValueError):
            raise ValueError("Price must be a valid number")
        if price < 0:
            raise ValueError("Price must be zero or greater")
        if not duration_raw.isdigit() or int(duration_raw) <= 0:
            raise ValueError("Duration must be a positive integer")
        duration_minutes = int(duration_raw)

        if service_id:
            preset = ServicePreset.objects.get(id=service_id, tenant=tenant)
            preset.name = name
            preset.description = description
            preset.price = price
            preset.duration_minutes = duration_minutes
            preset.is_active = is_active
            preset.save(update_fields=["name", "description", "price", "duration_minutes", "is_active", "updated_at"])
            return

        max_order = ServicePreset.objects.filter(tenant=tenant).aggregate(m=Max("sort_order")).get("m") or 0
        ServicePreset.objects.create(
            tenant=tenant,
            name=name,
            description=description,
            price=price,
            duration_minutes=duration_minutes,
            is_active=is_active,
            sort_order=max_order + 1,
        )

    def _delete_service_preset(self, request, tenant):
        service_id = (request.POST.get("service_id") or "").strip()
        if not service_id:
            raise ValueError("Missing service_id")
        ServicePreset.objects.filter(id=service_id, tenant=tenant).delete()

    def _request_tenant_deletion(self, request, tenant):
        confirm_text = (request.POST.get("tenant_delete_confirm") or "").strip().upper()
        if confirm_text != "DELETE":
            raise ValueError("削除確認テキストが一致しません。DELETE を入力してください。")
        if getattr(tenant, "deleted_at", None):
            raise ValueError("この店舗はすでに削除申請済みです。")

        now = timezone.now()
        tenant.deleted_at = now
        tenant.recoverable_until = now + timedelta(days=30)
        tenant.deletion_requested_by = request.user
        tenant.enable_saas_dashboard = False
        tenant.is_api_enabled = False
        tenant.save(
            update_fields=[
                "deleted_at",
                "recoverable_until",
                "deletion_requested_by",
                "enable_saas_dashboard",
                "is_api_enabled",
            ]
        )
        StaffInvite.objects.filter(tenant=tenant, is_active=True).update(is_active=False)

    def _recover_tenant_deletion(self, tenant):
        deleted_at = getattr(tenant, "deleted_at", None)
        recoverable_until = getattr(tenant, "recoverable_until", None)
        if not deleted_at:
            raise ValueError("この店舗は削除申請されていません。")
        if not recoverable_until or recoverable_until < timezone.now():
            raise ValueError("復元可能期間（30日）を過ぎています。")

        tenant.deleted_at = None
        tenant.recoverable_until = None
        tenant.deletion_requested_by = None
        tenant.enable_saas_dashboard = True
        tenant.is_api_enabled = True
        tenant.save(
            update_fields=[
                "deleted_at",
                "recoverable_until",
                "deletion_requested_by",
                "enable_saas_dashboard",
                "is_api_enabled",
            ]
        )

    def post(self, request, *args, **kwargs):
        tenant = self._resolve_tenant()
        target_tab = "shop"

        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            try:
                payload = json.loads(request.body or "{}")
            except (TypeError, ValueError):
                return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

            if payload.get("action") == "update_cast_order":
                ordered_ids = payload.get("order", [])
                if not isinstance(ordered_ids, list):
                    return JsonResponse({"status": "error", "message": "Invalid order payload"}, status=400)
                with transaction.atomic():
                    for index, resource_id in enumerate(ordered_ids):
                        resource = Resource.objects.filter(tenant=tenant, id=resource_id).first()
                        if not resource:
                            continue
                        profile, _ = ResourceProfile.objects.get_or_create(resource=resource)
                        if profile.display_order != index:
                            profile.display_order = index
                            profile.save(update_fields=["display_order"])
                return JsonResponse({"status": "success"})
            if payload.get("action") == "mark_reports_read":
                if tenant:
                    BookingReport.objects.filter(tenant=tenant, is_read_by_admin=False).update(is_read_by_admin=True)
                return JsonResponse({"status": "success"})

        try:
            if request.POST.get("save_template") == "true":
                self._save_template(request, tenant)
                messages.success(request, "メールテンプレートを保存しました。")
                target_tab = "email"
            elif request.POST.get("save_cast_profile") == "true":
                self._save_cast_profile(request, tenant)
                messages.success(request, "Cast CMS を更新しました。")
                target_tab = "content"
            elif request.POST.get("save_staff_batch") == "true" or request.POST.get("save_staff") == "true":
                updated_count, blocked_count, display_only_count = self._save_staff_profiles_batch(request, tenant)
                messages.success(request, f"ユーザー情報を更新しました（{updated_count} 件）。")
                if blocked_count:
                    messages.warning(
                        request,
                        f"{blocked_count} 件は、プロフィール画面でプラットフォーム利用規約への同意が未完了のため、予約対象として有効化されませんでした。",
                    )
                if display_only_count:
                    messages.warning(
                        request,
                        f"{display_only_count} 件は認証用ユーザー名の重複回避のため、表示名のみ更新しました。",
                    )
                target_tab = "users"
            elif request.POST.get("save_service") == "true":
                self._save_service_preset(request, tenant)
                messages.success(request, "サービスプリセットを保存しました。")
                target_tab = "services"
            elif request.POST.get("delete_service") == "true":
                self._delete_service_preset(request, tenant)
                messages.success(request, "サービスプリセットを削除しました。")
                target_tab = "services"
            elif request.POST.get("save_tenant_settings") == "true":
                self._save_tenant_settings(request, tenant)
                messages.success(request, "店舗設定を保存しました。")
                target_tab = "shop"
            elif request.POST.get("create_stripe_checkout") == "true":
                target_tab = "subscription"
                checkout_url = self._create_stripe_checkout(tenant)
                if checkout_url:
                    return redirect(checkout_url)
                messages.error(request, "Stripe Checkout URL の生成に失敗しました。")
            elif request.POST.get("open_stripe_portal") == "true":
                target_tab = "subscription"
                portal_url = self._open_stripe_portal(tenant)
                if portal_url:
                    return redirect(portal_url)
                messages.error(request, "Stripe Billing Portal URL の生成に失敗しました。")
            elif request.POST.get("sync_stripe_subscription") == "true":
                target_tab = "subscription"
                self._sync_stripe_subscription(tenant)
                messages.success(request, "Stripe の契約情報を同期しました。")
            elif request.POST.get("save_core_time_order") == "true":
                self._save_core_time_order(request, tenant)
                messages.success(request, "コアタイム注文を保存しました。")
                target_tab = "shifts"
            elif request.POST.get("delete_core_time_order") == "true":
                self._delete_core_time_order(request, tenant)
                messages.success(request, "コアタイム注文を削除しました。")
                target_tab = "shifts"
            elif request.POST.get("create_staff_invite") == "true":
                invite = self._create_staff_invite(request, tenant)
                messages.success(request, f"招待リンクを発行しました: /dashboard/invite/{invite.token}/")
                target_tab = "shop"
            elif request.POST.get("deactivate_staff_invite") == "true":
                self._deactivate_staff_invite(request, tenant)
                messages.success(request, "招待リンクを削除しました。")
                target_tab = "shop"
            elif request.POST.get("request_tenant_deletion") == "true":
                self._request_tenant_deletion(request, tenant)
                messages.success(request, "店舗の削除申請を受け付けました。30日以内であれば復元できます。")
                target_tab = "shop"
            elif request.POST.get("recover_tenant_deletion") == "true":
                self._recover_tenant_deletion(tenant)
                messages.success(request, "店舗を復元しました。")
                target_tab = "shop"
            else:
                messages.error(request, "未対応のダッシュボード操作です。")
        except SaaSUser.DoesNotExist:
            messages.error(request, "スタッフユーザーが見つかりません。")
        except ServicePreset.DoesNotExist:
            messages.error(request, "サービスプリセットが見つかりません。")
        except stripe_service.StripeConfigError as exc:
            messages.error(request, str(exc))
        except stripe_service.StripeApiError as exc:
            messages.error(request, f"Stripe 連携に失敗しました: {exc}")
        except Exception:
            logger.exception('tenant dashboard operation failed tenant_id=%s', getattr(tenant, 'id', None))
            messages.error(request, "処理中にエラーが発生しました。時間をおいて再試行してください。")

        redirect_url = f"{reverse('tenant_dashboard')}?tab={target_tab}"
        if request.user.is_superuser and tenant:
            redirect_url += f"&tenant_id={tenant.id}"
        return redirect(redirect_url)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tenant = self._resolve_tenant()
        requested_tab = (self.request.GET.get("tab") or "").strip().lower()

        orders = Booking.objects.none()
        resources = Resource.objects.none()
        staff_users = SaaSUser.objects.none()
        staff_rows = []
        service_presets = ServicePreset.objects.none()
        upcoming_shifts = Availability.objects.none()
        cast_rows = []
        service_name_suggestions = []
        recent_invites = []
        core_time_orders = []
        unread_report_count = 0
        report_notifications = []

        if tenant:
            if requested_tab == "subscription":
                try:
                    stripe_service.sync_tenant_subscription(tenant)
                    tenant.refresh_from_db()
                except (stripe_service.StripeConfigError, stripe_service.StripeApiError):
                    logger.warning("stripe subscription auto-sync skipped tenant_id=%s", tenant.id, exc_info=True)

            now = timezone.now()
            staff_users = list(
                SaaSUser.objects.filter(
                    tenant=tenant,
                    role__in=["STAFF", "ADMIN"],
                ).order_by("role", "username")
            )

            # Keep Users & Roles and Cast CMS aligned by ensuring each staff/admin has a bound Resource.
            for staff_user in staff_users:
                self._ensure_staff_resource_binding(staff_user, tenant)

            orders_qs = (
                Booking.objects.filter(tenant=tenant)
                .select_related("resource", "resource__profile", "selected_service")
                .order_by("-start_time")
            )
            orders = list(orders_qs)
            report_reason_labels = dict(REPORT_REASON_CHOICES)
            order_ids = [order.id for order in orders]
            report_text_map = {}
            if order_ids:
                report_rows = (
                    BookingReport.objects.filter(tenant=tenant, booking_id__in=order_ids)
                    .order_by("-created_at")
                )
                for report in report_rows:
                    key = str(report.booking_id)
                    bucket = report_text_map.setdefault(key, [])
                    if len(bucket) >= 5:
                        continue
                    role_label = "顧客" if report.reporter_role == "CUSTOMER" else "キャスト"
                    reason_label = report_reason_labels.get(report.reason, report.reason)
                    created_label = timezone.localtime(report.created_at).strftime("%Y/%m/%d %H:%M")
                    detail_text = _normalize_report_detail_text(report.detail) or "詳細なし"
                    bucket.append(f"[{created_label}] {role_label} / {reason_label}\n{detail_text}")

            for order in orders:
                order.display_service_name = resolve_booking_service_name(order, tenant)
                order.report_content_text = "\n\n".join(report_text_map.get(str(order.id), [])) if report_text_map.get(str(order.id)) else "通報内容はありません。"
            core_time_orders = list(
                Booking.objects.filter(tenant=tenant, booking_type="CORE_TIME")
                .select_related("resource")
                .order_by("-start_time")[:200]
            )
            resources = list(
                Resource.objects.filter(tenant=tenant)
                .select_related("profile", "linked_user")
                .order_by("profile__display_order", "name")
            )
            resource_by_user_id = {r.linked_user_id: r for r in resources if r.linked_user_id}

            for u in staff_users:
                linked_resource = resource_by_user_id.get(u.id)
                linked_profile = getattr(linked_resource, "profile", None) if linked_resource else None
                platform_terms_agreed = bool(linked_profile and getattr(linked_profile, "platform_terms_agreed", False))
                staff_rows.append(
                    {
                        "user": u,
                        "linked_resource_name": linked_resource.name if linked_resource else "未割当",
                        "platform_terms_agreed": platform_terms_agreed,
                    }
                )
            upcoming_shifts = Availability.objects.filter(
                resource__tenant=tenant,
                start_time__gte=now,
                start_time__lt=now + timedelta(days=31),
            ).select_related("resource").order_by("start_time")[:200]
            service_presets = ServicePreset.objects.filter(tenant=tenant).order_by("sort_order", "id")

            preset_names = list(service_presets.values_list("name", flat=True))
            recent_booking_names = list(
                Booking.objects.filter(tenant=tenant)
                .exclude(selected_service_name__isnull=True)
                .exclude(selected_service_name="")
                .values_list("selected_service_name", flat=True)
                .distinct()[:30]
            )
            for name in [*recent_booking_names, *preset_names]:
                if name and name not in service_name_suggestions:
                    service_name_suggestions.append(name)

            for resource in resources:
                profile = getattr(resource, "profile", None)
                tags = profile.tags if profile and isinstance(profile.tags, list) else []
                metadata = profile.metadata if profile and isinstance(profile.metadata, dict) else {}
                selected_service_ids = metadata.get("service_preset_ids") or []
                cast_rows.append(
                    {
                        "resource_id": str(resource.id),
                        "resource_name": resource.name,
                        "resource_email": resource.email or "",
                        "linked_username": resource.linked_user.username if resource.linked_user else "",
                        "intro": normalize_profile_text(profile.intro) if profile else "",
                        "avatar_url": self._resolve_cast_avatar_url(resource, profile),
                        "youtube_url": profile.youtube_url if profile else "",
                        "tags_text": ", ".join(str(tag).strip() for tag in tags if str(tag).strip()),
                        "selected_service_ids": [str(item) for item in selected_service_ids],
                    }
                )

            recent_invites = list(
                StaffInvite.objects.filter(tenant=tenant, is_active=True)
                .order_by("-created_at")[:5]
            )
            for invite in recent_invites:
                invite.invite_url = self._absolute_public_url(
                    reverse("dashboard_invite_accept", kwargs={"token": invite.token})
                )

            reports = list(
                BookingReport.objects.filter(tenant=tenant)
                .select_related("booking", "booking__resource")
                .order_by("-created_at")[:20]
            )
            unread_report_count = sum(1 for r in reports if not r.is_read_by_admin)
            reason_labels = dict(REPORT_REASON_CHOICES)
            for report in reports:
                report_notifications.append(
                    {
                        "id": report.id,
                        "booking_id": str(report.booking_id),
                        "booking_short": str(report.booking_id)[:8],
                        "reporter_role": report.reporter_role,
                        "reason": reason_labels.get(report.reason, report.reason),
                        "detail": report.detail or "",
                        "reporter_name": report.reporter_name or "",
                        "is_read": report.is_read_by_admin,
                        "created_at": timezone.localtime(report.created_at).strftime("%Y/%m/%d %H:%M"),
                    }
                )

        default_service_name_prefill = service_name_suggestions[0] if service_name_suggestions else "{{ selected_service_name }}"

        templates_data = {}
        for event_type in ["BOOKING_CONFIRMED", "BOOKING_CANCELLED"]:
            try:
                t = EmailTemplate.objects.get(tenant=tenant, event_type=event_type)
                logo_url = tenant.logo.url if tenant and tenant.logo else (t.logo.url if t.logo else "")
                templates_data[event_type] = {
                    "subject": t.subject_template,
                    "email_title": t.email_title,
                    "email_greeting": t.email_greeting,
                    "service_name": t.service_name,
                    "button_text": t.button_text,
                    "button_link": t.button_link,
                    "footer_title": t.footer_title,
                    "footer_text": t.footer_text,
                    "logo_url": logo_url,
                    "send_to_customer": t.send_to_customer,
                    "send_to_cast": t.send_to_cast,
                }
            except EmailTemplate.DoesNotExist:
                is_cancel = event_type == "BOOKING_CANCELLED"
                templates_data[event_type] = {
                    "subject": "",
                    "email_title": "予約キャンセルのお知らせ" if is_cancel else "予約が確定しました。",
                    "email_greeting": "予約がキャンセルされました。" if is_cancel else "以下の内容で予約を承りました。",
                    "service_name": "{{ selected_service_name }}",
                    "button_text": "トップページへ" if is_cancel else "詳細を見る",
                    "button_link": "{{ booking_public_url }}",
                    "footer_title": "当社のキャンセルポリシー",
                    "footer_text": DEFAULT_EMAIL_FOOTER_TEXT,
                    "logo_url": tenant.logo.url if tenant and tenant.logo else "",
                    "send_to_customer": True,
                    "send_to_cast": True,
                }

        next_24h_count = sum(1 for s in upcoming_shifts if s.start_time <= timezone.now() + timedelta(hours=24))

        tenant_logo_url = ""
        if tenant and getattr(tenant, "logo", None):
            try:
                tenant_logo_url = tenant.logo.url
            except Exception:
                tenant_logo_url = ""

        tenant_deleted_at = getattr(tenant, "deleted_at", None) if tenant else None
        tenant_recoverable_until = getattr(tenant, "recoverable_until", None) if tenant else None
        tenant_can_recover = bool(
            tenant_deleted_at and tenant_recoverable_until and tenant_recoverable_until >= timezone.now()
        )

        context.update(
            {
                "orders": orders,
                "resources": resources,
                "staff_users": staff_users,
                "staff_rows": staff_rows,
                "upcoming_shifts": upcoming_shifts,
                "service_presets": service_presets,
                "cast_rows": cast_rows,
                "next_24h_count": next_24h_count,
                "templates_json": json.dumps(templates_data),
                "service_name_suggestions_json": json.dumps(service_name_suggestions, ensure_ascii=False),
                "default_service_name_prefill": default_service_name_prefill,
                "tenant_name": tenant.name if tenant else "未設定店舗",
                "tenant_slug": tenant.slug if tenant else "",
                "tenant_contact_email": tenant.contact_email if tenant else "",
                "store_type": (tenant.store_type if tenant else "FLEX_SHIFT"),
                "is_core_time_store": _is_core_time_store(tenant) if tenant else False,
                "core_time_week_config_json": json.dumps(normalize_core_time_config(getattr(tenant, "core_time_week_config", {}) if tenant else {}), ensure_ascii=False),
                "core_time_summary": summarize_core_time_config(getattr(tenant, "core_time_week_config", {}) if tenant else {}),
                "subscription_status": (tenant.subscription_status if tenant else "ACTIVE"),
                "subscription_plan_code": (tenant.subscription_plan_code if tenant else ""),
                "subscription_started_at": (tenant.subscription_started_at if tenant else None),
                "subscription_ends_at": (tenant.subscription_ends_at if tenant else None),
                "stripe_customer_id": (tenant.stripe_customer_id if tenant else ""),
                "stripe_subscription_id": (tenant.stripe_subscription_id if tenant else ""),
                "stripe_price_id": (tenant.stripe_price_id if tenant else ""),
                "stripe_first_credit_amount": getattr(tenant, "stripe_first_credit_amount", getattr(settings, "STRIPE_FIRST_MONTH_DISCOUNT_JPY", 2000)) if tenant else getattr(settings, "STRIPE_FIRST_MONTH_DISCOUNT_JPY", 2000),
                "stripe_first_credit_applied_at": (tenant.stripe_first_credit_applied_at if tenant else None),
                "stripe_synced_at": (tenant.stripe_synced_at if tenant else None),
                "stripe_publishable_key": (getattr(settings, "STRIPE_PUBLISHABLE_KEY", "") or "").strip(),
                "stripe_basic_price_display": getattr(settings, "STRIPE_BASIC_MONTHLY_PRICE_JPY", 5000),
                "stripe_checkout_ready": stripe_service.stripe_checkout_ready(),
                "stripe_subscription_price_id": (getattr(settings, "STRIPE_SUBSCRIPTION_PRICE_ID", "") or "").strip(),
                "booking_window_days": tenant.booking_window_days if tenant else 14,
                "cancellation_window_hours": tenant.cancellation_window_hours if tenant else 2,
                "booking_detail_redirect_url": tenant.booking_detail_redirect_url if tenant else "",
                "store_contract_label": tenant.store_contract_label if tenant else "店舗利用規約",
                "store_contract_url": tenant.store_contract_url if tenant else "",
                "required_customer_fields": _normalize_required_customer_fields(
                    getattr(tenant, "required_customer_fields", ["VRCID", "DISCORDID", "EMAIL"])
                ) if tenant else ["VRCID", "DISCORDID", "EMAIL"],
                "custom_agreements_json": json.dumps(
                    _normalize_agreement_modules(
                        tenant.custom_terms_body if tenant else "",
                        legacy_label=tenant.custom_terms_label if tenant else "",
                        legacy_body=tenant.custom_terms_body if tenant else "",
                    ),
                    ensure_ascii=False,
                ),
                "tenant_logo_url": tenant_logo_url,
                "tenant_deleted_at": tenant_deleted_at,
                "tenant_recoverable_until": tenant_recoverable_until,
                "tenant_can_recover": tenant_can_recover,
                "public_booking_url": self._public_booking_url(tenant),
                "recent_invites": recent_invites,
                "core_time_orders": core_time_orders,
                "report_unread_count": unread_report_count,
                "report_notifications_json": json.dumps(report_notifications, ensure_ascii=False),
                "is_super_admin": bool(self.request.user.is_superuser),
                "selected_tenant_id": str(tenant.id) if tenant else "",
                **_tenant_api_ban_banner_context(tenant),
                **(_effective_subscription_context(tenant) if tenant else {}),
                "system_admin_contact_url": (getattr(settings, "SYSTEM_ADMIN_CONTACT_URL", "") or "mailto:support@rosterlyreverse.com").strip(),
            }
        )
        return context


class TenantMessageCenterView(AdminDashboardRequiredMixin, TemplateView):
    template_name = "dashboard/tenant_messages.html"

    def _resolve_tenant(self):
        if self.request.user.is_superuser:
            requested_tenant_id = (self.request.GET.get("tenant_id") or "").strip()
            if requested_tenant_id:
                scoped_tenant = Tenant.objects.filter(id=requested_tenant_id).first()
                if scoped_tenant:
                    return scoped_tenant
        tenant = getattr(self.request.user, "tenant", None)
        if tenant:
            return tenant
        return Tenant.objects.first()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tenant = self._resolve_tenant()
        announcements = list(
            GlobalAnnouncement.objects.filter(is_active=True)
            .select_related("created_by")
            .order_by("-is_pinned", "-created_at")[:50]
        )
        report_qs = BookingReport.objects.none()
        unread_report_count = 0
        if tenant:
            report_qs = (
                BookingReport.objects.filter(tenant=tenant)
                .select_related("booking", "booking__resource")
                .order_by("-created_at")[:80]
            )
            unread_report_count = BookingReport.objects.filter(tenant=tenant, is_read_by_admin=False).count()

        reason_labels = dict(REPORT_REASON_CHOICES)
        message_items = []

        for item in announcements:
            message_items.append(
                {
                    "item_type": "announcement",
                    "item_id": item.id,
                    "is_pinned": bool(item.is_pinned),
                    "is_read": True,
                    "title": item.title,
                    "summary": (item.body or "").strip()[:120],
                    "created_at": item.created_at,
                }
            )

        for report in report_qs:
            reason_label = reason_labels.get(report.reason, report.reason)
            message_items.append(
                {
                    "item_type": "report",
                    "item_id": report.id,
                    "is_pinned": False,
                    "is_read": bool(report.is_read_by_admin),
                    "title": f"通報 / {reason_label}",
                    "summary": (report.detail or "").strip()[:120],
                    "created_at": report.created_at,
                }
            )

        message_items.sort(
            key=lambda row: (
                0 if (row["item_type"] == "announcement" and row.get("is_pinned")) else 1,
                -row["created_at"].timestamp(),
            ),
        )

        tenant_logo_url = ""
        if tenant and getattr(tenant, "logo", None):
            try:
                tenant_logo_url = tenant.logo.url
            except Exception:
                tenant_logo_url = ""

        context.update(
            {
                "tenant": tenant,
                "tenant_name": tenant.name if tenant else "未設定店舗",
                "tenant_logo_url": tenant_logo_url,
                "message_items": message_items,
                "active_announcement_count": len(announcements),
                "report_unread_count": unread_report_count,
                "is_super_admin": bool(self.request.user.is_superuser),
                "selected_tenant_id": str(tenant.id) if tenant else "",
            }
        )
        return context


class TenantMessageDetailView(AdminDashboardRequiredMixin, TemplateView):
    template_name = "dashboard/tenant_message_detail.html"

    def _resolve_tenant(self):
        if self.request.user.is_superuser:
            requested_tenant_id = (self.request.GET.get("tenant_id") or "").strip()
            if requested_tenant_id:
                scoped_tenant = Tenant.objects.filter(id=requested_tenant_id).first()
                if scoped_tenant:
                    return scoped_tenant
        tenant = getattr(self.request.user, "tenant", None)
        if tenant:
            return tenant
        return Tenant.objects.first()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tenant = self._resolve_tenant()
        message_type = (self.kwargs.get("message_type") or "").strip().lower()
        message_id = self.kwargs.get("message_id")
        detail = None

        if message_type == "announcement":
            announcement = GlobalAnnouncement.objects.filter(id=message_id, is_active=True).select_related("created_by").first()
            if not announcement:
                messages.error(self.request, "お知らせが見つからないか、公開終了しました。")
                return redirect("dashboard_tenant_messages")
            detail = {
                "item_type": "announcement",
                "title": announcement.title,
                "created_at": announcement.created_at,
                "body": announcement.body,
                "link_url": announcement.link_url,
                "image_url": announcement.image.url if announcement.image else "",
                "is_pinned": bool(announcement.is_pinned),
                "creator": announcement.created_by.username if announcement.created_by else "",
            }
        elif message_type == "report":
            report = (
                BookingReport.objects.filter(id=message_id, tenant=tenant)
                .select_related("booking", "booking__resource")
                .first()
            )
            if not report:
                messages.error(self.request, "通報が見つかりません。")
                return redirect("dashboard_tenant_messages")
            if not report.is_read_by_admin:
                report.is_read_by_admin = True
                report.save(update_fields=["is_read_by_admin"])
            reason_label = dict(REPORT_REASON_CHOICES).get(report.reason, report.reason)
            detail = {
                "item_type": "report",
                "title": f"通報 / {reason_label}",
                "created_at": report.created_at,
                "body": report.detail or "",
                "reporter_name": report.reporter_name or "-",
                "reporter_role": report.reporter_role,
                "booking_id": str(report.booking_id),
                "booking_short": str(report.booking_id)[:8],
                "resource_name": report.booking.resource.name if report.booking and report.booking.resource else "-",
                "media_url": report.media.url if report.media else "",
            }
        else:
            messages.error(self.request, "メッセージ種別が不正です。")
            return redirect("dashboard_tenant_messages")

        tenant_logo_url = ""
        if tenant and getattr(tenant, "logo", None):
            try:
                tenant_logo_url = tenant.logo.url
            except Exception:
                tenant_logo_url = ""

        context.update(
            {
                "tenant": tenant,
                "tenant_name": tenant.name if tenant else "未設定店舗",
                "tenant_logo_url": tenant_logo_url,
                "detail": detail,
                "is_super_admin": bool(self.request.user.is_superuser),
                "selected_tenant_id": str(tenant.id) if tenant else "",
            }
        )
        return context
