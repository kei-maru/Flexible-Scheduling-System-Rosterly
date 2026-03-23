import json
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.messages import get_messages
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.views import LoginView
from django.db.models import Max
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.views.generic import TemplateView

from bookings.models import Booking
from resources.models import Availability, EmailTemplate, Resource, ServicePreset
from tenants.models import SaaSUser, Tenant


class DashboardLoginView(TemplateView):
    template_name = "dashboard/login.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            if not getattr(request.user, "tenant_id", None):
                list(get_messages(request))
                logout(request)
                messages.warning(request, "该账号未开通员工系统权限，请联系管理员。")
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
            messages.warning(self.request, "Dashboard is admin-only. Redirected to shared schedule.")
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

        defaults_data = {
            "subject_template": request.POST.get("subject"),
            "email_title": request.POST.get("email_title"),
            "email_greeting": request.POST.get("email_greeting"),
            "service_name": request.POST.get("service_name"),
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

    def _save_staff_profile(self, request, tenant):
        user_id = request.POST.get("user_id")
        if not user_id:
            raise ValueError("Missing user_id")

        user = SaaSUser.objects.get(id=user_id, tenant=tenant)
        user.username = (request.POST.get("username") or user.username).strip()
        user.email = (request.POST.get("email") or "").strip()
        user.discord_id = (request.POST.get("discord_id") or "").strip() or None
        user.role = request.POST.get("role") if request.POST.get("role") in ["ADMIN", "STAFF"] else user.role
        user.is_active = request.POST.get("is_active") == "on"

        linked_resource_id = (request.POST.get("linked_resource_id") or "").strip()
        if linked_resource_id:
            resource = Resource.objects.filter(id=linked_resource_id, tenant=tenant).first()
            user.resource_profile = resource if resource else user.resource_profile
        else:
            user.resource_profile = None

        user.save()

    def _save_service_preset(self, request, tenant):
        service_id = (request.POST.get("service_id") or "").strip()
        name = (request.POST.get("service_name") or "").strip()
        duration_raw = (request.POST.get("duration_minutes") or "").strip()
        is_active = request.POST.get("is_active") == "on"

        if not name:
            raise ValueError("Service name is required")
        if not duration_raw.isdigit() or int(duration_raw) <= 0:
            raise ValueError("Duration must be a positive integer")
        duration_minutes = int(duration_raw)

        if service_id:
            preset = ServicePreset.objects.get(id=service_id, tenant=tenant)
            preset.name = name
            preset.duration_minutes = duration_minutes
            preset.is_active = is_active
            preset.save(update_fields=["name", "duration_minutes", "is_active", "updated_at"])
            return

        max_order = ServicePreset.objects.filter(tenant=tenant).aggregate(m=Max("sort_order")).get("m") or 0
        ServicePreset.objects.create(
            tenant=tenant,
            name=name,
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
                messages.success(request, "Email template saved.")
            elif request.POST.get("save_staff") == "true":
                self._save_staff_profile(request, tenant)
                messages.success(request, "Staff profile updated.")
            elif request.POST.get("save_service") == "true":
                self._save_service_preset(request, tenant)
                messages.success(request, "Service preset saved.")
            elif request.POST.get("delete_service") == "true":
                self._delete_service_preset(request, tenant)
                messages.success(request, "Service preset deleted.")
            else:
                messages.error(request, "Unsupported dashboard action.")
        except SaaSUser.DoesNotExist:
            messages.error(request, "Staff user not found.")
        except ServicePreset.DoesNotExist:
            messages.error(request, "Service preset not found.")
        except Exception as exc:
            messages.error(request, f"Error: {exc}")

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

        if tenant:
            orders = Booking.objects.filter(tenant=tenant).order_by("-created_at")[:80]
            resources = Resource.objects.filter(tenant=tenant).order_by("name")
            staff_users = SaaSUser.objects.filter(tenant=tenant).order_by("role", "username")
            for u in staff_users:
                linked_resource = Resource.objects.filter(tenant=tenant, linked_user=u).first()
                staff_rows.append(
                    {
                        "user": u,
                        "linked_resource_id": str(linked_resource.id) if linked_resource else "",
                    }
                )
            upcoming_shifts = Availability.objects.filter(
                resource__tenant=tenant,
                start_time__gte=timezone.now(),
            ).select_related("resource").order_by("start_time")[:60]
            service_presets = ServicePreset.objects.filter(tenant=tenant).order_by("sort_order", "id")

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
                    "service_name": "{{ duration_minutes }}分VRASMR施術コース (PCVR)",
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
                "next_24h_count": next_24h_count,
                "templates_json": json.dumps(templates_data),
            }
        )
        return context
