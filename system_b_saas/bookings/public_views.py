import json
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views import View
from django.views.generic import TemplateView

from bookings.models import Booking, BookingReport, REPORT_REASON_CHOICES
from bookings.services import BookingCreateError, create_confirmed_booking_with_lock
from bookings.tasks import process_new_booking, send_cancellation_email_task
from dashboard.models import UserBehaviorEvent
from dashboard.utils import (
    _absolute_public_url,
    _agreement_modules_for_template,
    _behavior_client_ip,
    _ensure_booking_public_access,
    _exclude_demo_admin_resources,
    _is_core_time_store,
    _is_demo_admin_resource,
    _is_http_url,
    _normalize_required_customer_fields,
    _public_booking_is_rate_limited,
    _tenant_api_ban_banner_context,
    _tenant_is_subscribed,
)
from resources.models import Availability, Resource, ServicePreset
from resources.services.schedule_service import summarize_core_time_config
from resources.services.service_mapping import resolve_service_by_duration
from tenants.models import Tenant


class PublicBookingView(TemplateView):
    """Render the tenant-facing public booking page."""

    template_name = "dashboard/public_booking.html"

    def _booking_resource_queryset(self, tenant):
        # Bookable resources must be active; linked staff must be active and agree to platform terms.
        return _exclude_demo_admin_resources(
            Resource.objects.filter(tenant=tenant, is_active=True)
            .filter(Q(linked_user__isnull=True) | Q(linked_user__is_active=True))
            .filter(Q(linked_user__isnull=True) | Q(profile__platform_terms_agreed=True))
        )

    def _display_resource_queryset(self, tenant):
        # Demo resources may be displayed, but the template marks them as non-bookable.
        return (
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

        resources = list(
            self._display_resource_queryset(tenant)
            .select_related("profile")
            .order_by("profile__display_order", "name")
        )
        for resource in resources:
            resource.is_publicly_bookable = not _is_demo_admin_resource(resource)
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

        # Store contract links must be exactly what the tenant owner configured.
        store_contract_url_effective = (tenant.store_contract_url or "").strip()

        store_contract_items = []
        seen_store_items = set()

        def append_store_item(item_type, label_value, content_value):
            label = (label_value or "詳細").strip() or "詳細"
            content = (content_value or "").strip()
            if not content:
                return
            key = (item_type, content)
            if key in seen_store_items:
                return
            seen_store_items.add(key)
            store_contract_items.append({"type": item_type, "label": label, "content": content})

        if _is_http_url(store_contract_url_effective):
            append_store_item(
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
                append_store_item("url", module_title, module_content)
            else:
                append_store_item("text", module_title, module_content)

        context.update(
            {
                "tenant": tenant,
                "resources": resources,
                "services": services,
                "tenant_logo_url": tenant.logo.url if getattr(tenant, "logo", None) else "",
                "booking_window_days": max(1, int(getattr(tenant, "booking_window_days", 14) or 14)),
                "cancellation_window_hours": max(1, int(getattr(tenant, "cancellation_window_hours", 2) or 2)),
                "store_contract_label": (tenant.store_contract_label or "").strip(),
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


class PublicBookingAvailabilityApi(View):
    """Return bookable availability after confirmed bookings are removed."""

    def _booking_resource_queryset(self, tenant):
        return (
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
        booking_deadline = now + timedelta(hours=24)
        booking_window_days = max(1, int(getattr(tenant, "booking_window_days", 14) or 14))
        horizon = now + timedelta(days=booking_window_days)
        availabilities = list(
            Availability.objects.filter(
                resource=resource,
                is_booked=False,
                end_time__gt=booking_deadline,
                start_time__lt=horizon,
            )
            .order_by("start_time")[:300]
        )
        bookings = list(
            Booking.objects.filter(
                tenant=tenant,
                resource=resource,
                status="CONFIRMED",
                start_time__lt=horizon + timedelta(minutes=30),
                end_time__gt=booking_deadline - timedelta(minutes=30),
            ).order_by("start_time")
        )

        buffer_time = timedelta(minutes=30)
        data = []
        for availability in availabilities:
            segments = [
                (
                    max(availability.start_time, booking_deadline),
                    min(availability.end_time, horizon),
                )
            ]
            for booking in bookings:
                blocked_start = booking.start_time - buffer_time
                blocked_end = booking.end_time + buffer_time
                next_segments = []
                for segment_start, segment_end in segments:
                    if blocked_end <= segment_start or blocked_start >= segment_end:
                        next_segments.append((segment_start, segment_end))
                        continue
                    if segment_start < blocked_start:
                        next_segments.append((segment_start, blocked_start))
                    if blocked_end < segment_end:
                        next_segments.append((blocked_end, segment_end))
                segments = next_segments

            data.extend(
                {
                    "id": str(availability.id),
                    "start": segment_start.isoformat(),
                    "end": segment_end.isoformat(),
                }
                for segment_start, segment_end in segments
                if segment_start < segment_end
            )

        response = JsonResponse({"slots": data, "booking_window_days": booking_window_days})
        response["Cache-Control"] = "no-store"
        return response


class PublicBookingCreateApi(View):
    """Create a public booking from the browser-facing tenant booking page."""

    def _booking_resource_queryset(self, tenant):
        return (
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
            return JsonResponse({"error": "お名前は必須です"}, status=400)
        if "DISCORDID" in required_fields and not customer_discord_id:
            return JsonResponse({"error": "DiscordID is required"}, status=400)
        if "EMAIL" in required_fields and not customer_email:
            return JsonResponse({"error": "Email is required"}, status=400)

        anti_abuse_fingerprint = "|".join(
            [customer_email.lower(), customer_vrcid.lower(), customer_discord_id.lower(), resource_id, start_raw]
        )
        if _public_booking_is_rate_limited(request, tenant_slug, anti_abuse_fingerprint):
            return JsonResponse({"error": "アクセスが集中しています。少し時間を空けてから再度お試しください。"}, status=429)

        # Tenant settings decide which customer identifier becomes Booking.customer_name.
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

        selected_service_name = selected_service.name if selected_service else ""
        try:
            def after_create(booking):
                local_now = timezone.localtime(timezone.now())
                next_hour = (local_now.hour + 1) % 24

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
                        "visit_time_bucket_jst": f"{local_now.hour:02d}:00-{next_hour:02d}:00",
                        "ip": _behavior_client_ip(request),
                    },
                )
                transaction.on_commit(lambda: process_new_booking.delay(booking.id))

            booking = create_confirmed_booking_with_lock(
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
                after_create=after_create,
            )
        except BookingCreateError as exc:
            return JsonResponse({"error": exc.message}, status=exc.status_code)

        return JsonResponse(
            {
                "ok": True,
                "booking_id": str(booking.id),
                "public_detail_url": booking.public_detail_url,
                "has_customer_email": bool(customer_email),
            }
        )


class PublicBookingDetailView(TemplateView):
    """Show a booking detail page authorized by public_access_token."""

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


class PublicBookingReportApi(View):
    """Create a customer-side report for a token-authorized booking."""

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


class PublicBookingCancelApi(View):
    """Cancel a token-authorized public booking within the tenant cancellation window."""

    def post(self, request, access_token, *args, **kwargs):
        booking = (
            Booking.objects.filter(public_access_token=access_token)
            .select_related("tenant", "resource")
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


# Backward-compatible aliases keep existing URL names and imports stable while
# the implementation lives in the bookings app.
DashboardPublicBookingView = PublicBookingView
DashboardPublicBookingAvailabilityApi = PublicBookingAvailabilityApi
DashboardPublicBookingCreateApi = PublicBookingCreateApi
DashboardPublicBookingDetailView = PublicBookingDetailView
DashboardPublicBookingCancelApi = PublicBookingCancelApi
DashboardPublicBookingReportApi = PublicBookingReportApi
