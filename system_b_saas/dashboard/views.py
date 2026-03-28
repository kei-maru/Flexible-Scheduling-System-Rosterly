import json
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urlparse
from uuid import uuid4
import secrets

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.messages import get_messages
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.views import LoginView
from django.core.files.storage import default_storage
from django.db.models import Max
from django.shortcuts import redirect
from django.http import JsonResponse
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django import forms
from django.views import View
from django.views.generic import TemplateView
from django.db import transaction
from django.db.models import Q

from bookings.models import Booking
from bookings.tasks import process_new_booking
from resources.models import Availability, EmailTemplate, Resource, ResourceProfile, ServicePreset
from resources.services.binding_service import ensure_staff_resource_binding, normalize_profile_text
from resources.services.service_mapping import resolve_booking_service_name, resolve_service_by_duration
from tenants.models import SaaSUser, StaffInvite, Tenant


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
    status = (getattr(tenant, "subscription_status", "") or "").upper()
    if status not in {"ACTIVE", "TRIAL"}:
        return False
    ends_at = getattr(tenant, "subscription_ends_at", None)
    if ends_at and ends_at <= timezone.now():
        return False
    return True


def _is_core_time_store(tenant):
    return (getattr(tenant, "store_type", "FLEX_SHIFT") or "FLEX_SHIFT").upper() == "CORE_TIME"


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


class DashboardLoginView(TemplateView):
    template_name = "dashboard/login.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            if not getattr(request.user, "tenant_id", None):
                list(get_messages(request))
                logout(request)
                messages.warning(request, "このアカウントにはスタッフシステム権限がありません。管理者にお問い合わせください。")
                return redirect("dashboard_login")

            role = getattr(request.user, "role", "STAFF")
            if request.user.is_superuser or role == "ADMIN":
                return redirect("tenant_dashboard")
            return redirect("shared_schedule")
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
            return self.get(request, *args, **kwargs)

        shop_name = form.cleaned_data["shop_name"].strip()
        owner_email = form.cleaned_data["owner_email"].strip()
        logo = form.cleaned_data.get("logo")

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
        request.session["allow_public_sso_login"] = True
        request.session["sso_role_hint"] = invite.role if invite.role in {"ADMIN", "STAFF"} else "STAFF"
        return redirect(f"/accounts/discord/login/?process=login&next={quote('/dashboard/login/', safe='')}")


class DashboardTermsView(TemplateView):
    template_name = "dashboard/terms.html"


class DashboardPublicBookingView(TemplateView):
    template_name = "dashboard/public_booking.html"

    def _booking_resource_queryset(self, tenant):
        # Bookable resources must be active; linked staff must be active and agree to platform terms.
        return (
            Resource.objects.filter(tenant=tenant, is_active=True)
            .filter(Q(linked_user__isnull=True) | Q(linked_user__is_active=True))
            .filter(Q(linked_user__isnull=True) | Q(profile__platform_terms_agreed=True))
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tenant_slug = kwargs.get("tenant_slug")
        tenant = Tenant.objects.filter(slug=tenant_slug).first()
        if not tenant:
            context.update({"tenant": None, "resources": [], "services": []})
            return context

        resources = (
            self._booking_resource_queryset(tenant)
            .select_related("profile")
            .order_by("profile__display_order", "name")
        )
        services = ServicePreset.objects.filter(tenant=tenant, is_active=True).order_by("sort_order", "id")

        context.update(
            {
                "tenant": tenant,
                "resources": resources,
                "services": services,
                "tenant_logo_url": tenant.logo.url if getattr(tenant, "logo", None) else "",
                "booking_window_days": max(1, int(getattr(tenant, "booking_window_days", 14) or 14)),
                "store_contract_label": (tenant.store_contract_label or "店舗利用規約").strip() or "店舗利用規約",
                "store_contract_url": (tenant.store_contract_url or "").strip(),
                "tenant_is_subscribed": _tenant_is_subscribed(tenant),
                "subscription_status": (tenant.subscription_status or "").upper() or "NONE",
                "required_customer_fields": _normalize_required_customer_fields(
                    getattr(tenant, "required_customer_fields", ["VRCID", "DISCORDID", "EMAIL"])
                ),
                "agreement_modules": _agreement_modules_for_template(
                    tenant.custom_terms_body,
                    legacy_label=tenant.custom_terms_label,
                    legacy_body=tenant.custom_terms_body,
                ),
            }
        )
        return context


class DashboardPublicBookingAvailabilityApi(View):
    def _booking_resource_queryset(self, tenant):
        return (
            Resource.objects.filter(tenant=tenant, is_active=True)
            .filter(Q(linked_user__isnull=True) | Q(linked_user__is_active=True))
            .filter(Q(linked_user__isnull=True) | Q(profile__platform_terms_agreed=True))
        )

    def get(self, request, tenant_slug, *args, **kwargs):
        tenant = Tenant.objects.filter(slug=tenant_slug).first()
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


class DashboardPublicBookingCreateApi(View):
    def _booking_resource_queryset(self, tenant):
        return (
            Resource.objects.filter(tenant=tenant, is_active=True)
            .filter(Q(linked_user__isnull=True) | Q(linked_user__is_active=True))
            .filter(Q(linked_user__isnull=True) | Q(profile__platform_terms_agreed=True))
        )

    def post(self, request, tenant_slug, *args, **kwargs):
        tenant = Tenant.objects.filter(slug=tenant_slug).first()
        if not tenant:
            return JsonResponse({"error": "Tenant not found"}, status=404)
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

        if not all([resource_id, customer_name, start_raw]):
            return JsonResponse({"error": "Missing required fields"}, status=400)
        if "VRCID" in required_fields and not customer_vrcid:
            return JsonResponse({"error": "VRCID is required"}, status=400)
        if "DISCORDID" in required_fields and not customer_discord_id:
            return JsonResponse({"error": "DiscordID is required"}, status=400)
        if "EMAIL" in required_fields and not customer_email:
            return JsonResponse({"error": "Email is required"}, status=400)

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
            transaction.on_commit(lambda: process_new_booking.delay(booking.id))

        return JsonResponse({"ok": True, "booking_id": str(booking.id)})


def dashboard_logout(request):
    logout(request)
    return redirect("dashboard_login")


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
        tenant = getattr(self.request.user, "tenant", None)
        if tenant:
            return tenant
        return Tenant.objects.first()

    def _public_booking_url(self, tenant):
        if not tenant:
            return ""
        return self.request.build_absolute_uri(reverse("dashboard_public_booking", kwargs={"tenant_slug": tenant.slug}))

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
            "button_link": request.POST.get("button_link"),
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
        store_type = (request.POST.get("store_type") or "FLEX_SHIFT").strip().upper()
        booking_window_days_raw = (request.POST.get("booking_window_days") or "14").strip()
        store_contract_label = (request.POST.get("store_contract_label") or "").strip()
        store_contract_url = (request.POST.get("store_contract_url") or "").strip() or None
        required_customer_fields = _normalize_required_customer_fields(request.POST.getlist("required_customer_fields"))
        custom_agreements_json = (request.POST.get("custom_agreements_json") or "").strip()
        custom_agreements = _normalize_agreement_modules(custom_agreements_json)
        custom_terms_label = custom_agreements[0]["title"] if custom_agreements else ""
        custom_terms_body = json.dumps(custom_agreements, ensure_ascii=False)

        if not name:
            raise ValueError("店舗名は必須です")
        if not contact_email:
            raise ValueError("店舗メールは必須です")
        if store_type not in {"CORE_TIME", "FLEX_SHIFT"}:
            raise ValueError("店舗タイプが不正です")
        if not store_contract_label:
            raise ValueError("店舗契約は必須です")
        try:
            booking_window_days = max(1, int(booking_window_days_raw))
        except ValueError:
            raise ValueError("予約公開日数は1以上の整数で入力してください")
        if store_contract_url and not _is_http_url(store_contract_url):
            raise ValueError("店舗契約URLは http(s) 形式で入力してください")

        update_fields = []
        if tenant.name != name:
            tenant.name = name
            update_fields.append("name")
        if (tenant.contact_email or "") != contact_email:
            tenant.contact_email = contact_email
            update_fields.append("contact_email")
        if (tenant.store_type or "FLEX_SHIFT") != store_type:
            tenant.store_type = store_type
            update_fields.append("store_type")
        if tenant.booking_window_days != booking_window_days:
            tenant.booking_window_days = booking_window_days
            update_fields.append("booking_window_days")
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
        for user_id in user_ids:
            user = SaaSUser.objects.filter(id=user_id).first()
            if not user:
                continue

            # 管理パネル上の対象は「同一 tenant」または「未割当ユーザー」のみ
            if user.tenant_id not in [tenant.id, None]:
                continue

            username = (request.POST.get(f"username__{user_id}") or user.username).strip() or user.username
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
            if user.username != username:
                user.username = username
                update_fields.append("username")
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

            if linked and linked.is_active != user.is_active:
                linked.is_active = user.is_active
                linked.save(update_fields=["is_active"])

        return updated_count, blocked_count

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
                updated_count, blocked_count = self._save_staff_profiles_batch(request, tenant)
                messages.success(request, f"ユーザー情報を更新しました（{updated_count} 件）。")
                if blocked_count:
                    messages.warning(
                        request,
                        f"{blocked_count} 件は、プロフィール画面でプラットフォーム利用規約への同意が未完了のため、予約対象として有効化されませんでした。",
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
            elif request.POST.get("save_subscription_settings") == "true":
                self._save_subscription_settings(request, tenant)
                messages.success(request, "サブスクリプション設定を保存しました。")
                target_tab = "subscription"
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
            else:
                messages.error(request, "未対応のダッシュボード操作です。")
        except SaaSUser.DoesNotExist:
            messages.error(request, "スタッフユーザーが見つかりません。")
        except ServicePreset.DoesNotExist:
            messages.error(request, "サービスプリセットが見つかりません。")
        except Exception as exc:
            messages.error(request, f"エラー: {exc}")

        return redirect(f"{reverse('tenant_dashboard')}?tab={target_tab}")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tenant = self._resolve_tenant()

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

        if tenant:
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
                .order_by("-start_time")[:100]
            )
            orders = list(orders_qs)
            for order in orders:
                order.display_service_name = resolve_booking_service_name(order, tenant)
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
                        "avatar_url": profile.avatar_url if profile else "",
                        "youtube_url": profile.youtube_url if profile else "",
                        "tags_text": ", ".join(str(tag).strip() for tag in tags if str(tag).strip()),
                        "selected_service_ids": [str(item) for item in selected_service_ids],
                    }
                )

            recent_invites = list(
                StaffInvite.objects.filter(tenant=tenant, is_active=True)
                .order_by("-created_at")[:5]
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
                    "subject": "【予約キャンセル】" if is_cancel else "【予約確定】",
                    "email_title": "予約キャンセルのお知らせ" if is_cancel else "予約が確定しました。",
                    "email_greeting": "予約がキャンセルされました。" if is_cancel else "以下の内容で予約を承りました。",
                    "service_name": "{{ selected_service_name }}",
                    "button_text": "トップページへ" if is_cancel else "詳細を見る",
                    "button_link": "#",
                    "footer_title": "当社のキャンセルポリシー",
                    "footer_text": "...",
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
                "subscription_status": (tenant.subscription_status if tenant else "ACTIVE"),
                "subscription_plan_code": (tenant.subscription_plan_code if tenant else ""),
                "subscription_started_at": (tenant.subscription_started_at if tenant else None),
                "subscription_ends_at": (tenant.subscription_ends_at if tenant else None),
                "booking_window_days": tenant.booking_window_days if tenant else 14,
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
                "public_booking_url": self._public_booking_url(tenant),
                "recent_invites": recent_invites,
                "core_time_orders": core_time_orders,
            }
        )
        return context
