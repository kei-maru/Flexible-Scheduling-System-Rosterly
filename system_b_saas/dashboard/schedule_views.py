import json
from datetime import timedelta
from uuid import uuid4
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth import logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.serializers.json import DjangoJSONEncoder
from django.core.files.storage import default_storage
from django.db import DataError, IntegrityError, transaction
from django.db.models import Count, F
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from allauth.socialaccount.models import SocialAccount
from bookings.models import Booking, BookingReport, REPORT_REASON_CHOICES
from resources.models import Availability, Resource, ResourceProfile, ServicePreset
from resources.services.binding_service import ensure_staff_resource_binding, normalize_profile_text
from resources.services.service_mapping import resolve_booking_service_name
from resources.services import schedule_service
from tenants.models import Tenant


logger = logging.getLogger(__name__)


def _tenant_api_ban_reason_label(reason_code: str) -> str:
    reason_map = {
        "ABUSE": "不正利用・スパム",
        "PAYMENT": "料金・契約違反",
        "SECURITY": "セキュリティ違反",
        "LEGAL": "法令・規約違反",
        "OTHER": "その他",
    }
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
        "tenant_ban_admin_help": False,
    }


class SharedBaseMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        try:
            if request.user.is_authenticated:
                if request.user.is_superuser:
                    return super().dispatch(request, *args, **kwargs)
                has_tenant_id = bool(getattr(request.user, "tenant_id", None))
                try:
                    tenant_obj = request.user.tenant if has_tenant_id else None
                except Tenant.DoesNotExist:
                    tenant_obj = None

                if not has_tenant_id or tenant_obj is None:
                    logout(request)
                    messages.warning(request, "このアカウントにはスタッフシステム権限がありません。業務側の入口からログインしてください。")
                    return redirect("dashboard_login")
            return super().dispatch(request, *args, **kwargs)
        except Exception as exc:
            logger.exception("Shared dashboard dispatch failed", exc_info=exc)
            logout(request)
            messages.error(request, "画面表示でエラーが発生しました。再度ログインしてください。")
            return redirect("dashboard_login")

    def _tenant(self):
        try:
            return getattr(self.request.user, "tenant", None)
        except Tenant.DoesNotExist:
            return None

    def _staff_default_resource(self, tenant):
        if getattr(self.request.user, "role", "") in {"STAFF", "ADMIN"}:
            auto_bound = ensure_staff_resource_binding(self.request.user, tenant=tenant)
            if auto_bound:
                return auto_bound

        linked = getattr(self.request.user, "resource_profile", None)
        if linked and linked.tenant_id == tenant.id:
            return linked

        if self.request.user.email:
            return Resource.objects.filter(tenant=tenant, email=self.request.user.email).first()
        return None

    def _is_admin(self):
        role = getattr(self.request.user, "role", "STAFF")
        return role == "ADMIN" or self.request.user.is_superuser

    def _avatar_url(self):
        tenant = self._tenant()
        if tenant:
            resource = self._staff_default_resource(tenant)
            profile = getattr(resource, "profile", None) if resource else None
            if profile and profile.avatar_url:
                return profile.avatar_url

        social = SocialAccount.objects.filter(user=self.request.user, provider="discord").first()
        if not social:
            return ""
        extra = social.extra_data or {}
        discord_id = extra.get("id")
        avatar_hash = extra.get("avatar")
        if discord_id and avatar_hash:
            return f"https://cdn.discordapp.com/avatars/{discord_id}/{avatar_hash}.png"
        return ""

    def _base_nav_context(self):
        tenant = self._tenant()
        tenant_logo_url = ""
        if tenant and getattr(tenant, "logo", None):
            try:
                tenant_logo_url = tenant.logo.url
            except Exception:
                tenant_logo_url = ""
        context = {
            "is_admin": self._is_admin(),
            "user_avatar_url": self._avatar_url(),
            "user_initial": (self.request.user.username[:1] or "U").upper(),
            "tenant_name": tenant.name if tenant else "未設定店舗",
            "tenant_logo_url": tenant_logo_url,
            "system_admin_contact_url": (getattr(settings, "SYSTEM_ADMIN_CONTACT_URL", "") or "mailto:support@rosterlyreverse.com").strip(),
        }
        context.update(_tenant_api_ban_banner_context(tenant))
        return context


class SharedHomeView(SharedBaseMixin, TemplateView):
    template_name = "dashboard/shared_home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self._base_nav_context())
        return context


class SharedProfileView(SharedBaseMixin, TemplateView):
    template_name = "dashboard/shared_profile.html"

    def _store_profile_avatar(self, tenant, resource, avatar_file):
        ext = ""
        if "." in (avatar_file.name or ""):
            ext = "." + avatar_file.name.rsplit(".", 1)[-1].lower()
        rel_path = f"resource_avatars/tenant_{tenant.id}/resource_{resource.id}/{uuid4().hex}{ext}"
        stored_path = default_storage.save(rel_path, avatar_file)
        return default_storage.url(stored_path)

    def _profile_resource(self):
        tenant = self._tenant()
        if not tenant:
            return None
        return self._staff_default_resource(tenant)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tenant = self._tenant()
        resource = self._profile_resource()
        profile = getattr(resource, "profile", None) if resource else None
        tags = profile.tags if profile and isinstance(profile.tags, list) else []
        metadata = profile.metadata if profile and isinstance(profile.metadata, dict) else {}
        selected_service_ids = [str(item) for item in (metadata.get("service_preset_ids") or [])]
        service_presets = ServicePreset.objects.none()
        if tenant:
            service_presets = ServicePreset.objects.filter(tenant=tenant).order_by("sort_order", "id")

        context.update(self._base_nav_context())
        context.update(
            {
                "profile_display_name": (self.request.user.first_name or self.request.user.username or "").strip(),
                "profile_resource": resource,
                "profile_intro": normalize_profile_text(profile.intro) if profile else "",
                "profile_tags_text": ", ".join(str(tag).strip() for tag in tags if str(tag).strip()),
                "profile_avatar_url": profile.avatar_url if profile else "",
                "profile_youtube_url": profile.youtube_url if profile else "",
                "profile_platform_terms_agreed": profile.platform_terms_agreed if profile else False,
                "allow_30_min": profile.allow_30_min if profile else False,
                "allow_60_min": profile.allow_60_min if profile else True,
                "allow_120_min": profile.allow_120_min if profile else False,
                "service_presets": service_presets,
                "selected_service_ids": selected_service_ids,
                "platform_terms_url": (
                    (tenant.store_contract_url or "").strip()
                    if tenant and getattr(tenant, "store_contract_url", None)
                    else reverse("dashboard_terms")
                ),
            }
        )
        return context

    def post(self, request, *args, **kwargs):
        resource = self._profile_resource()
        if resource:
            tenant = self._tenant()
            agreed = request.POST.get("platform_terms_agreed") == "on"
            if not agreed:
                messages.error(request, "プロフィールを保存するには、プラットフォーム利用規約への同意が必要です。")
                return redirect("shared_profile")

        user = request.user
        requested_username = (request.POST.get("username") or user.username).strip() or user.username
        if len(requested_username) > 150:
            messages.error(request, "ユーザー名は150文字以内で入力してください。")
            return redirect("shared_profile")

        UserModel = get_user_model()
        username_conflict = UserModel.objects.filter(username=requested_username).exclude(pk=user.pk).exists()
        if not username_conflict:
            user.username = requested_username
        user.first_name = requested_username
        user.email = (request.POST.get("email") or "").strip()
        try:
            update_fields = ["first_name", "email"]
            if not username_conflict:
                update_fields.insert(0, "username")
            user.save(update_fields=update_fields)
        except (IntegrityError, DataError) as exc:
            logger.warning("profile save failed for user id=%s due to user field constraint: %s", user.id, exc)
            messages.error(request, "プロフィールの保存に失敗しました。ユーザー名や入力値を確認してください。")
            return redirect("shared_profile")

        if resource:
            tenant = self._tenant()
            if requested_username and resource.name != requested_username:
                resource.name = requested_username
                resource.save(update_fields=["name"])

            profile, _ = ResourceProfile.objects.get_or_create(resource=resource)
            raw_tags = (request.POST.get("profile_tags") or "").replace("，", ",")
            parsed_tags = [tag.strip() for tag in raw_tags.split(",") if tag.strip()]
            valid_service_ids = {
                str(item)
                for item in ServicePreset.objects.filter(tenant=tenant).values_list("id", flat=True)
            } if tenant else set()
            selected_service_ids = [sid for sid in request.POST.getlist("service_preset_ids") if sid in valid_service_ids]
            selected_presets = ServicePreset.objects.filter(id__in=selected_service_ids, tenant=tenant) if tenant else ServicePreset.objects.none()
            selected_durations = set(selected_presets.values_list("duration_minutes", flat=True))
            metadata = profile.metadata if isinstance(profile.metadata, dict) else {}
            metadata["service_preset_ids"] = selected_service_ids

            profile.intro = normalize_profile_text(request.POST.get("profile_intro"))
            profile.youtube_url = (request.POST.get("profile_youtube_url") or "").strip() or None
            profile.tags = parsed_tags
            profile.metadata = metadata
            profile.platform_terms_agreed = True
            profile.allow_30_min = 30 in selected_durations
            profile.allow_60_min = 60 in selected_durations
            profile.allow_120_min = 120 in selected_durations

            if request.POST.get("profile_avatar_clear") == "on":
                profile.avatar_url = None
            elif request.FILES.get("profile_avatar_file"):
                profile.avatar_url = self._store_profile_avatar(tenant, resource, request.FILES["profile_avatar_file"])

            try:
                profile.save()
            except Exception as exc:
                logger.exception("profile extension save failed for user id=%s resource id=%s err=%s", user.id, resource.id, exc)
                messages.error(request, "拡張プロフィールの保存に失敗しました。入力内容を確認して再試行してください。")
                return redirect("shared_profile")
            if username_conflict:
                messages.warning(
                    request,
                    "同名が既に存在するため認証用ユーザー名は変更せず、表示名のみ更新しました。",
                )
            messages.success(request, "プロフィール情報を保存しました。")
        else:
            messages.warning(
                request,
                "基本プロフィールは保存しましたが、紐付けリソースが未設定のため拡張プロフィールは保存されませんでした。",
            )

        return redirect("shared_profile")


class SharedScheduleView(SharedBaseMixin, TemplateView):
    template_name = "dashboard/shared_schedule.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tenant = self._tenant()
        resource = None
        initial_focus_date = ""

        if tenant:
            # Shared (staff-facing) schedule should always prioritize the current user's own resource.
            resource = self._staff_default_resource(tenant)
            if not resource and self._is_admin():
                requested_id = self.request.GET.get("resource_id")
                if requested_id:
                    try:
                        resource = schedule_service.resolve_resource(tenant, requested_id)
                    except schedule_service.ScheduleServiceError:
                        resource = None
                if not resource:
                    resource = Resource.objects.filter(tenant=tenant, is_active=True).order_by("name").first()

        if resource:
            visible_after = timezone.now() + timedelta(hours=24)
            next_shift_start = (
                Availability.objects.filter(resource=resource, is_booked=False, end_time__gt=visible_after)
                .order_by("start_time")
                .values_list("start_time", flat=True)
                .first()
            )
            if next_shift_start:
                initial_focus_date = next_shift_start.date().isoformat()

        context["is_cast"] = bool(resource)
        context["current_resource_id"] = str(resource.id) if resource else ""
        context["initial_focus_date"] = initial_focus_date
        context["is_core_time_store"] = bool(tenant and (getattr(tenant, "store_type", "FLEX_SHIFT") or "").upper() == "CORE_TIME")
        context["core_time_week_config_json"] = json.dumps(schedule_service.normalize_core_time_config(getattr(tenant, "core_time_week_config", {}) if tenant else {}), ensure_ascii=False)
        context["core_time_summary"] = schedule_service.summarize_core_time_config(getattr(tenant, "core_time_week_config", {}) if tenant else {})
        context.update(self._base_nav_context())
        return context


class SharedBookingListView(SharedBaseMixin, TemplateView):
    template_name = "dashboard/shared_bookings.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tenant = self._tenant()

        bookings = Booking.objects.none()
        staff_resource = None
        if tenant:
            qs = (
                Booking.objects.filter(tenant=tenant)
                .select_related("resource", "resource__profile", "selected_service")
                .order_by("-start_time")
            )
            # Shared bookings page is always staff-facing and locked to own bookings.
            staff_resource = self._staff_default_resource(tenant)
            if staff_resource:
                qs = qs.filter(resource=staff_resource)
            else:
                qs = Booking.objects.none()
            bookings = list(qs[:200])
            for booking in bookings:
                booking.display_service_name = resolve_booking_service_name(booking, tenant)

            booking_ids = [b.id for b in bookings]
            counts = {}
            if booking_ids:
                rows = (
                    BookingReport.objects.filter(booking_id__in=booking_ids)
                    .values("booking_id", "reporter_role")
                    .annotate(c=Count("id"))
                )
                for row in rows:
                    key = (str(row["booking_id"]), row["reporter_role"])
                    counts[key] = row["c"]

            for booking in bookings:
                bid = str(booking.id)
                booking.customer_report_count_ui = counts.get((bid, "CUSTOMER"), 0)
                booking.cast_report_count_ui = counts.get((bid, "CAST"), 0)

        context.update(
            {
                "bookings": bookings,
                "is_admin": False,
                "is_staff_view": True,
                "staff_resource_id": str(staff_resource.id) if staff_resource else "",
                "now_iso": timezone.now().isoformat(),
                "report_reasons": REPORT_REASON_CHOICES,
            }
        )
        context.update(self._base_nav_context())
        return context


class _ScheduleApiBase(LoginRequiredMixin, View):
    def _tenant(self, request):
        requested_tenant_id = (request.GET.get("tenant_id") or "").strip()
        if requested_tenant_id and (request.user.is_superuser or getattr(request.user, "role", "") == "ADMIN"):
            scoped_tenant = Tenant.objects.filter(id=requested_tenant_id).first()
            if scoped_tenant:
                return scoped_tenant
        return getattr(request.user, "tenant", None)

    def _staff_default_resource(self, request, tenant):
        if getattr(request.user, "role", "") in {"STAFF", "ADMIN"}:
            auto_bound = ensure_staff_resource_binding(request.user, tenant=tenant)
            if auto_bound:
                return auto_bound
        linked = getattr(request.user, "resource_profile", None)
        if linked and linked.tenant_id == tenant.id:
            return linked
        if request.user.email:
            return Resource.objects.filter(tenant=tenant, email=request.user.email).first()
        return None

    def _resolve_dashboard_resource(self, request, resource_id_raw):
        tenant = self._tenant(request)
        if not tenant:
            raise schedule_service.ScheduleValidationError("店舗情報が見つかりません")

        role = getattr(request.user, "role", "STAFF")

        if role == "STAFF":
            resource = self._staff_default_resource(request, tenant)
            if not resource:
                raise schedule_service.SchedulePermissionError("現在のスタッフアカウントに担当リソースが紐付いていません")
            if resource_id_raw and str(resource.id) != str(resource_id_raw):
                raise schedule_service.SchedulePermissionError("自分のスケジュールのみ操作できます")
            return tenant, resource

        if not resource_id_raw:
            resource = Resource.objects.filter(tenant=tenant, is_active=True).order_by("name").first()
            if not resource:
                raise schedule_service.ScheduleValidationError("有効なリソースがありません")
            return tenant, resource

        resource = schedule_service.resolve_resource(tenant, resource_id_raw)
        return tenant, resource

    def _json_error(self, message, status=400):
        return JsonResponse({"error": message}, status=status)

    def _payload(self, request):
        if request.body:
            try:
                return json.loads(request.body.decode("utf-8"))
            except json.JSONDecodeError:
                return {}
        return {}


class DashboardScheduleEventsApi(_ScheduleApiBase):
    def get(self, request):
        try:
            tenant, resource = self._resolve_dashboard_resource(request, request.GET.get("resource_id"))
            events = schedule_service.list_events(
                tenant=tenant,
                resource_id_raw=str(resource.id),
                start_str=request.GET.get("start"),
                end_str=request.GET.get("end"),
                mode=request.GET.get("mode", "raw"),
            )
            return JsonResponse(events, safe=False, encoder=DjangoJSONEncoder)
        except schedule_service.SchedulePermissionError as exc:
            return self._json_error(str(exc), status=403)
        except schedule_service.ScheduleValidationError as exc:
            return self._json_error(str(exc), status=400)
        except schedule_service.ScheduleNotFoundError:
            return JsonResponse([], safe=False)


class DashboardScheduleAvailabilityApi(_ScheduleApiBase):
    def post(self, request):
        payload = self._payload(request)

        try:
            tenant, resource = self._resolve_dashboard_resource(request, payload.get("resource_id"))

            if payload.get("week_config"):
                result = schedule_service.create_recurring_availability(
                    tenant=tenant,
                    resource_id_raw=str(resource.id),
                    range_start=payload.get("range_start"),
                    range_end=payload.get("range_end"),
                    week_config=payload.get("week_config"),
                )
            else:
                result = schedule_service.create_single_availability(
                    tenant=tenant,
                    resource_id_raw=str(resource.id),
                    start_str=payload.get("start"),
                    end_str=payload.get("end"),
                )
            return JsonResponse(result, encoder=DjangoJSONEncoder)
        except schedule_service.SchedulePermissionError as exc:
            return self._json_error(str(exc), status=403)
        except schedule_service.ScheduleValidationError as exc:
            status_code = 409 if str(exc) in {"Time slot conflict", "時間帯が重複しています"} else 400
            return self._json_error(str(exc), status=status_code)
        except schedule_service.ScheduleNotFoundError as exc:
            return self._json_error(str(exc), status=404)

    def delete(self, request):
        payload = self._payload(request)
        availability_id = request.GET.get("id") or payload.get("id")
        resource_id_raw = request.GET.get("resource_id") or payload.get("resource_id")

        try:
            tenant, _resource = self._resolve_dashboard_resource(request, resource_id_raw)
            schedule_service.delete_availability(tenant, availability_id)
            return JsonResponse({"status": "deleted"})
        except schedule_service.SchedulePermissionError as exc:
            return self._json_error(str(exc), status=403)
        except schedule_service.ScheduleValidationError as exc:
            return self._json_error(str(exc), status=400)
        except schedule_service.ScheduleNotFoundError as exc:
            return self._json_error(str(exc), status=404)


class DashboardScheduleRecurringConfigApi(_ScheduleApiBase):
    def get(self, request):
        try:
            _tenant, resource = self._resolve_dashboard_resource(request, request.GET.get("resource_id"))
            tenant = self._tenant(request)
            data = schedule_service.get_recurring_config(tenant, str(resource.id))
            return JsonResponse(data, encoder=DjangoJSONEncoder)
        except schedule_service.SchedulePermissionError as exc:
            return self._json_error(str(exc), status=403)
        except schedule_service.ScheduleValidationError as exc:
            return self._json_error(str(exc), status=400)


class DashboardScheduleTemplateApi(_ScheduleApiBase):
    def get(self, request):
        try:
            tenant, resource = self._resolve_dashboard_resource(request, request.GET.get("resource_id"))
            templates = schedule_service.list_templates(tenant, str(resource.id))
            return JsonResponse(templates, safe=False, encoder=DjangoJSONEncoder)
        except schedule_service.SchedulePermissionError as exc:
            return self._json_error(str(exc), status=403)
        except schedule_service.ScheduleValidationError as exc:
            return self._json_error(str(exc), status=400)

    def post(self, request):
        payload = self._payload(request)
        try:
            tenant, resource = self._resolve_dashboard_resource(request, payload.get("resource_id"))
            result = schedule_service.save_template(
                tenant=tenant,
                resource_id_raw=str(resource.id),
                name=payload.get("name"),
                week_config=payload.get("week_config"),
            )
            return JsonResponse(result, encoder=DjangoJSONEncoder)
        except schedule_service.SchedulePermissionError as exc:
            return self._json_error(str(exc), status=403)
        except schedule_service.ScheduleValidationError as exc:
            return self._json_error(str(exc), status=400)

    def delete(self, request):
        payload = self._payload(request)
        template_id = request.GET.get("id") or payload.get("id")

        try:
            tenant = self._tenant(request)
            schedule_service.delete_template(tenant, template_id)
            return JsonResponse({"status": "deleted"})
        except schedule_service.ScheduleValidationError as exc:
            return self._json_error(str(exc), status=400)


class DashboardBookingActionApi(_ScheduleApiBase):
    def patch(self, request, booking_id):
        payload = self._payload(request)
        new_status = payload.get("status")
        if new_status not in ["CANCELLED", "CANCELLED_BY_ADMIN", "COMPLETED"]:
            return self._json_error("不正なステータスです", status=400)

        tenant = self._tenant(request)
        role = getattr(request.user, "role", "STAFF")
        is_admin = role == "ADMIN" or request.user.is_superuser

        try:
            booking = Booking.objects.select_related("resource").get(id=booking_id, tenant=tenant)
        except Booking.DoesNotExist:
            return self._json_error("予約が見つかりません", status=404)

        if new_status == "CANCELLED":
            if not is_admin:
                return self._json_error("この画面からのキャンセルは管理者のみ可能です", status=403)
            if (booking.start_time - timezone.now()).total_seconds() < 2 * 3600:
                return self._json_error("キャンセルは開始2時間前まで可能です", status=400)
            booking.status = "CANCELLED"
            booking.save(update_fields=["status"])
            return JsonResponse({"status": "CANCELLED"})

        if new_status == "CANCELLED_BY_ADMIN":
            if not is_admin:
                return self._json_error("管理者キャンセルは管理者のみ可能です", status=403)
            if booking.status != "CONFIRMED":
                return self._json_error("確定済み予約のみ管理者キャンセルできます", status=400)
            booking.status = "CANCELLED_BY_ADMIN"
            booking.save(update_fields=["status"])
            return JsonResponse({"status": "CANCELLED_BY_ADMIN", "status_label": "管理者キャンセル"})

        if new_status == "COMPLETED":
            if booking.status != "CONFIRMED":
                return self._json_error("確定済み予約のみ完了にできます", status=400)
            if not is_admin:
                try:
                    _tenant, staff_resource = self._resolve_dashboard_resource(request, None)
                except schedule_service.ScheduleServiceError as exc:
                    return self._json_error(str(exc), status=403)
                if booking.resource_id != staff_resource.id:
                    return self._json_error("自分の担当予約のみ完了にできます", status=403)
            booking.status = "COMPLETED"
            booking.save(update_fields=["status"])
            return JsonResponse({"status": "COMPLETED"})

        return self._json_error("未対応の操作です", status=400)


class DashboardBookingReportApi(_ScheduleApiBase):
    def post(self, request, booking_id):
        tenant = self._tenant(request)
        if not tenant:
            return self._json_error("店舗情報が見つかりません", status=400)

        role = getattr(request.user, "role", "STAFF")
        is_admin = role == "ADMIN" or request.user.is_superuser

        try:
            booking = Booking.objects.select_related("resource").get(id=booking_id, tenant=tenant)
        except Booking.DoesNotExist:
            return self._json_error("予約が見つかりません", status=404)

        if not is_admin:
            try:
                _tenant, staff_resource = self._resolve_dashboard_resource(request, None)
            except schedule_service.ScheduleServiceError as exc:
                return self._json_error(str(exc), status=403)
            if booking.resource_id != staff_resource.id:
                return self._json_error("自分の担当予約のみ通報できます", status=403)

        reason = (request.POST.get("reason") or "").strip()
        detail = (request.POST.get("detail") or "").strip()
        media = request.FILES.get("media")
        valid_reasons = {choice[0] for choice in REPORT_REASON_CHOICES}
        if reason not in valid_reasons:
            return self._json_error("通報理由を選択してください", status=400)

        with transaction.atomic():
            BookingReport.objects.create(
                booking=booking,
                tenant=tenant,
                reporter_role="CAST",
                reason=reason,
                detail=detail,
                media=media,
                reporter_name=(request.user.username or "").strip(),
                reporter_email=(request.user.email or "").strip(),
                is_read_by_admin=False,
            )
            booking.cast_report_count = F("cast_report_count") + 1
            booking.last_reported_at = timezone.now()
            booking.save(update_fields=["cast_report_count", "last_reported_at"])

        return JsonResponse({"ok": True})
