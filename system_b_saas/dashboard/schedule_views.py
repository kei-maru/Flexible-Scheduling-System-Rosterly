import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.serializers.json import DjangoJSONEncoder
from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from allauth.socialaccount.models import SocialAccount
from bookings.models import Booking
from resources.models import Resource
from resources.services import schedule_service
from tenants.models import Tenant


class SharedBaseMixin(LoginRequiredMixin):
    def _tenant(self):
        tenant = getattr(self.request.user, "tenant", None)
        if tenant:
            return tenant
        return Tenant.objects.first()

    def _staff_default_resource(self, tenant):
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
        return {
            "is_admin": self._is_admin(),
            "user_avatar_url": self._avatar_url(),
            "user_initial": (self.request.user.username[:1] or "U").upper(),
        }


class SharedHomeView(SharedBaseMixin, TemplateView):
    template_name = "dashboard/shared_home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self._base_nav_context())
        return context


class SharedProfileView(SharedBaseMixin, TemplateView):
    template_name = "dashboard/shared_profile.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(self._base_nav_context())
        return context

    def post(self, request, *args, **kwargs):
        user = request.user
        user.username = (request.POST.get("username") or user.username).strip() or user.username
        user.email = (request.POST.get("email") or "").strip()
        user.discord_id = (request.POST.get("discord_id") or "").strip() or None
        user.save(update_fields=["username", "email", "discord_id"])
        return redirect("shared_profile")


class SharedScheduleView(SharedBaseMixin, TemplateView):
    template_name = "dashboard/shared_schedule.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tenant = self._tenant()
        role = getattr(self.request.user, "role", "STAFF")
        resource = None

        if tenant:
            if role == "STAFF":
                resource = self._staff_default_resource(tenant)
            else:
                requested_id = self.request.GET.get("resource_id")
                if requested_id:
                    try:
                        resource = schedule_service.resolve_resource(tenant, requested_id)
                    except schedule_service.ScheduleServiceError:
                        resource = None
                if not resource:
                    resource = Resource.objects.filter(tenant=tenant, is_active=True).order_by("name").first()

        context["is_cast"] = bool(resource)
        context["current_resource_id"] = str(resource.id) if resource else ""
        context.update(self._base_nav_context())
        return context


class SharedBookingListView(SharedBaseMixin, TemplateView):
    template_name = "dashboard/shared_bookings.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tenant = self._tenant()
        role = getattr(self.request.user, "role", "STAFF")
        is_admin = role == "ADMIN" or self.request.user.is_superuser

        bookings = Booking.objects.none()
        staff_resource = None
        if tenant:
            qs = Booking.objects.filter(tenant=tenant).select_related("resource").order_by("-start_time")
            if not is_admin:
                staff_resource = self._staff_default_resource(tenant)
                if staff_resource:
                    qs = qs.filter(resource=staff_resource)
                else:
                    qs = Booking.objects.none()
            bookings = qs[:200]

        context.update(
            {
                "bookings": bookings,
                "is_admin": is_admin,
                "is_staff_view": not is_admin,
                "staff_resource_id": str(staff_resource.id) if staff_resource else "",
                "now_iso": timezone.now().isoformat(),
            }
        )
        context.update(self._base_nav_context())
        return context


class _ScheduleApiBase(LoginRequiredMixin, View):
    def _tenant(self, request):
        tenant = getattr(request.user, "tenant", None)
        if tenant:
            return tenant
        return Tenant.objects.first()

    def _staff_default_resource(self, request, tenant):
        linked = getattr(request.user, "resource_profile", None)
        if linked and linked.tenant_id == tenant.id:
            return linked
        if request.user.email:
            return Resource.objects.filter(tenant=tenant, email=request.user.email).first()
        return None

    def _resolve_dashboard_resource(self, request, resource_id_raw):
        tenant = self._tenant(request)
        if not tenant:
            raise schedule_service.ScheduleValidationError("Tenant not found")

        role = getattr(request.user, "role", "STAFF")

        if role == "STAFF":
            resource = self._staff_default_resource(request, tenant)
            if not resource:
                raise schedule_service.SchedulePermissionError("No resource linked to current staff account")
            if resource_id_raw and str(resource.id) != str(resource_id_raw):
                raise schedule_service.SchedulePermissionError("You can only manage your own schedule")
            return tenant, resource

        if not resource_id_raw:
            resource = Resource.objects.filter(tenant=tenant, is_active=True).order_by("name").first()
            if not resource:
                raise schedule_service.ScheduleValidationError("No active resources")
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
            status_code = 409 if str(exc) == "Time slot conflict" else 400
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
        if new_status not in ["CANCELLED", "COMPLETED"]:
            return self._json_error("Invalid status", status=400)

        tenant = self._tenant(request)
        role = getattr(request.user, "role", "STAFF")
        is_admin = role == "ADMIN" or request.user.is_superuser

        try:
            booking = Booking.objects.select_related("resource").get(id=booking_id, tenant=tenant)
        except Booking.DoesNotExist:
            return self._json_error("Booking not found", status=404)

        if new_status == "CANCELLED":
            if not is_admin:
                return self._json_error("Only admin can cancel from this panel", status=403)
            if (booking.start_time - timezone.now()).total_seconds() < 2 * 3600:
                return self._json_error("Cancellation requires at least 2 hours lead time", status=400)
            booking.status = "CANCELLED"
            booking.save(update_fields=["status"])
            return JsonResponse({"status": "CANCELLED"})

        if new_status == "COMPLETED":
            if booking.status != "CONFIRMED":
                return self._json_error("Only CONFIRMED bookings can be completed", status=400)
            if not is_admin:
                try:
                    _tenant, staff_resource = self._resolve_dashboard_resource(request, None)
                except schedule_service.ScheduleServiceError as exc:
                    return self._json_error(str(exc), status=403)
                if booking.resource_id != staff_resource.id:
                    return self._json_error("You can only complete bookings of your own resource", status=403)
            booking.status = "COMPLETED"
            booking.save(update_fields=["status"])
            return JsonResponse({"status": "COMPLETED"})

        return self._json_error("Unsupported action", status=400)
