import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import quote
from uuid import uuid4

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
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from bookings.models import Booking
from resources.models import Availability, EmailTemplate, Resource, ResourceProfile, ServicePreset
from resources.services.binding_service import ensure_staff_resource_binding, normalize_profile_text
from tenants.models import SaaSUser, Tenant


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
        target_next = next_url or "/dashboard/login/"
        return redirect(f"/accounts/discord/login/?process=login&next={quote(target_next, safe='')}")


class DashboardTermsView(TemplateView):
    template_name = "dashboard/terms.html"


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

        if "logo" in request.FILES:
            defaults_data["logo"] = request.FILES["logo"]

        EmailTemplate.objects.update_or_create(
            tenant=tenant,
            event_type=event_type,
            defaults=defaults_data,
        )

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
            is_active = request.POST.get(f"is_active__{user_id}") == "on"

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

            if user.role == "STAFF":
                linked = self._ensure_staff_resource_binding(user, tenant)
            else:
                linked = Resource.objects.filter(tenant=tenant, linked_user=user).first()

            if linked and linked.is_active != user.is_active:
                linked.is_active = user.is_active
                linked.save(update_fields=["is_active"])

        return updated_count

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

        try:
            if request.POST.get("save_template") == "true":
                self._save_template(request, tenant)
                messages.success(request, "メールテンプレートを保存しました。")
            elif request.POST.get("save_cast_profile") == "true":
                self._save_cast_profile(request, tenant)
                messages.success(request, "Cast CMS を更新しました。")
            elif request.POST.get("save_staff_batch") == "true" or request.POST.get("save_staff") == "true":
                updated_count = self._save_staff_profiles_batch(request, tenant)
                messages.success(request, f"ユーザー情報を更新しました（{updated_count} 件）。")
            elif request.POST.get("save_service") == "true":
                self._save_service_preset(request, tenant)
                messages.success(request, "サービスプリセットを保存しました。")
            elif request.POST.get("delete_service") == "true":
                self._delete_service_preset(request, tenant)
                messages.success(request, "サービスプリセットを削除しました。")
            else:
                messages.error(request, "未対応のダッシュボード操作です。")
        except SaaSUser.DoesNotExist:
            messages.error(request, "スタッフユーザーが見つかりません。")
        except ServicePreset.DoesNotExist:
            messages.error(request, "サービスプリセットが見つかりません。")
        except Exception as exc:
            messages.error(request, f"エラー: {exc}")

        return redirect("tenant_dashboard")

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

        if tenant:
            now = timezone.now()
            orders = Booking.objects.filter(tenant=tenant).select_related("resource").order_by("-created_at")[:100]
            resources = Resource.objects.filter(tenant=tenant).select_related("profile", "linked_user").order_by("name")
            staff_users = SaaSUser.objects.filter(
                tenant=tenant,
                role__in=["STAFF", "ADMIN"],
            ).order_by("role", "username")
            for u in staff_users:
                linked_resource = Resource.objects.filter(tenant=tenant, linked_user=u).first()
                staff_rows.append(
                    {
                        "user": u,
                        "linked_resource_name": linked_resource.name if linked_resource else "未割当",
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

        default_service_name_prefill = service_name_suggestions[0] if service_name_suggestions else "{{ selected_service_name }}"

        templates_data = {}
        for event_type in ["BOOKING_CONFIRMED", "BOOKING_CANCELLED"]:
            try:
                t = EmailTemplate.objects.get(tenant=tenant, event_type=event_type)
                logo_url = t.logo.url if t.logo else ""
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
                    "logo_url": "",
                    "send_to_customer": True,
                    "send_to_cast": True,
                }

        next_24h_count = sum(1 for s in upcoming_shifts if s.start_time <= timezone.now() + timedelta(hours=24))

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
            }
        )
        return context
