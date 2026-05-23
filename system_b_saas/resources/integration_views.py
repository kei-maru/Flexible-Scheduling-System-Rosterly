from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from django.db import transaction
from django.conf import settings

from resources.models import Resource, ResourceProfile, ResourceMedia
from resources.services.binding_service import normalize_profile_text
from resources.services.service_mapping import course_flags_to_service_preset_ids
from resources.services import schedule_service
from tenants.permissions import IsTenantAuthorized


PROFILE_FIELDS = {
    "intro",
    "tags",
    "avatar_url",
    "youtube_url",
    "display_order",
    "allow_30_min",
    "allow_60_min",
    "allow_120_min",
    "metadata",
}


def _demo_admin_username() -> str:
    return (getattr(settings, "SYSTEM_B_DEMO_ADMIN_USERNAME", "demo_admin") or "demo_admin").strip()


def _is_demo_admin_resource(resource) -> bool:
    linked_user = getattr(resource, "linked_user", None)
    if not linked_user:
        return False
    target_username = _demo_admin_username().lower()
    current_username = (getattr(linked_user, "username", "") or "").strip().lower()
    return bool(target_username) and current_username == target_username


def _coerce_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None


def _serialize_resource(resource):
    profile = getattr(resource, "profile", None)
    linked_user = getattr(resource, "linked_user", None)
    medias = []
    if profile:
        medias = [
            {
                "id": m.id,
                "title": m.title,
                "media_type": m.media_type,
                "image_url": m.image_url,
                "video_url": m.video_url,
                "cover_url": m.cover_url,
                "order": m.order,
                "is_active": m.is_active,
            }
            for m in profile.medias.all()
            if m.is_active
        ]

    return {
        "id": str(resource.id),
        "external_id": resource.external_id,
        "linked_user_id": str(linked_user.id) if linked_user else "",
        "linked_user_discord_id": getattr(linked_user, "discord_id", "") if linked_user else "",
        "name": resource.name,
        "email": resource.email,
        "is_active": resource.is_active,
        "profile": {
            "intro": normalize_profile_text(profile.intro) if profile else "",
            "tags": profile.tags if profile else [],
            "avatar_url": profile.avatar_url if profile else None,
            "youtube_url": profile.youtube_url if profile else None,
            "display_order": profile.display_order if profile else 0,
            "allow_30_min": profile.allow_30_min if profile else False,
            "allow_60_min": profile.allow_60_min if profile else True,
            "allow_120_min": profile.allow_120_min if profile else False,
            "metadata": profile.metadata if profile else {},
            "medias": medias,
        },
    }


def _normalize_profile_payload(raw_payload):
    profile_payload = dict(raw_payload.get("profile") or {})
    for field in PROFILE_FIELDS:
        if field in raw_payload and field not in profile_payload:
            profile_payload[field] = raw_payload.get(field)
    return profile_payload


class IntegrationAvailabilityView(APIView):
    permission_classes = [IsTenantAuthorized]

    def get(self, request):
        mode = request.query_params.get("mode", "raw")
        resource_id_raw = request.query_params.get("resource_id")
        start_str = request.query_params.get("start")
        end_str = request.query_params.get("end")

        try:
            resource = schedule_service.resolve_resource(request.tenant, resource_id_raw)
            if _is_demo_admin_resource(resource):
                return Response([], status=200)

            events = schedule_service.list_events(
                tenant=request.tenant,
                resource_id_raw=resource_id_raw,
                start_str=start_str,
                end_str=end_str,
                mode=mode,
            )
            return Response(events)
        except schedule_service.ScheduleNotFoundError:
            return Response([], status=200)
        except schedule_service.ScheduleValidationError as exc:
            return Response({"error": str(exc)}, status=400)

    def post(self, request):
        resource_id_raw = request.data.get("resource_id")
        week_config = request.data.get("week_config")

        try:
            if week_config:
                result = schedule_service.create_recurring_availability(
                    tenant=request.tenant,
                    resource_id_raw=resource_id_raw,
                    range_start=request.data.get("range_start"),
                    range_end=request.data.get("range_end"),
                    week_config=week_config,
                )
            else:
                result = schedule_service.create_single_availability(
                    tenant=request.tenant,
                    resource_id_raw=resource_id_raw,
                    start_str=request.data.get("start"),
                    end_str=request.data.get("end"),
                )

            return Response(result, status=201)
        except schedule_service.ScheduleNotFoundError:
            return Response({"error": "Resource not found"}, status=404)
        except schedule_service.ScheduleValidationError as exc:
            code = status.HTTP_409_CONFLICT if str(exc) == "Time slot conflict" else 400
            return Response({"error": str(exc)}, status=code)

    def delete(self, request, pk=None):
        availability_id = pk or request.data.get("id")
        try:
            schedule_service.delete_availability(request.tenant, availability_id)
            return Response(status=204)
        except schedule_service.ScheduleValidationError as exc:
            return Response({"error": str(exc)}, status=400)
        except schedule_service.ScheduleNotFoundError as exc:
            return Response({"error": str(exc)}, status=404)


class RecurringConfigView(APIView):
    permission_classes = [IsTenantAuthorized]

    def get(self, request):
        resource_id_raw = request.query_params.get("resource_id")

        try:
            data = schedule_service.get_recurring_config(request.tenant, resource_id_raw)
            return Response(data)
        except schedule_service.ScheduleNotFoundError:
            return Response({})


class ScheduleTemplateView(APIView):
    permission_classes = [IsTenantAuthorized]

    def get(self, request):
        resource_id_raw = request.query_params.get("resource_id")
        try:
            templates = schedule_service.list_templates(request.tenant, resource_id_raw)
            return Response(templates)
        except schedule_service.ScheduleValidationError as exc:
            return Response({"error": str(exc)}, status=400)
        except schedule_service.ScheduleNotFoundError:
            return Response({"error": "Resource not found"}, status=404)

    def post(self, request):
        try:
            result = schedule_service.save_template(
                tenant=request.tenant,
                resource_id_raw=request.data.get("resource_id"),
                name=request.data.get("name"),
                week_config=request.data.get("week_config"),
            )
            return Response(result, status=201)
        except schedule_service.ScheduleValidationError as exc:
            return Response({"error": str(exc)}, status=400)
        except schedule_service.ScheduleNotFoundError:
            return Response({"error": "Resource not found"}, status=404)

    def delete(self, request):
        try:
            schedule_service.delete_template(request.tenant, request.data.get("id"))
            return Response(status=204)
        except schedule_service.ScheduleValidationError as exc:
            return Response({"error": str(exc)}, status=400)


class IntegrationResourceView(APIView):
    permission_classes = [IsTenantAuthorized]

    def get(self, request, pk=None):
        queryset = (
            Resource.objects
            .filter(tenant=request.tenant)
            .exclude(linked_user__username__iexact=_demo_admin_username())
            .select_related("profile")
            .prefetch_related("profile__medias")
            .order_by("name")
        )

        active_only = request.query_params.get("active_only", "false").lower() == "true"
        if active_only:
            queryset = queryset.filter(is_active=True)

        external_id = request.query_params.get("external_id")
        if external_id:
            queryset = queryset.filter(external_id=str(external_id))

        if pk:
            resource = queryset.filter(id=pk).first()
            if not resource:
                return Response({"error": "Resource not found"}, status=404)
            return Response(_serialize_resource(resource))

        return Response([_serialize_resource(resource) for resource in queryset])

    def _apply_profile_updates(self, resource, payload):
        profile_payload = _normalize_profile_payload(payload)
        medias_payload = payload.get("medias")

        should_update_profile = bool(profile_payload) or medias_payload is not None
        if not should_update_profile:
            return

        profile, _ = ResourceProfile.objects.get_or_create(resource=resource)

        for field in PROFILE_FIELDS:
            if field in profile_payload:
                if field in {"allow_30_min", "allow_60_min", "allow_120_min"}:
                    parsed = _coerce_bool(profile_payload[field])
                    if parsed is not None:
                        setattr(profile, field, parsed)
                elif field == "intro":
                    setattr(profile, field, normalize_profile_text(profile_payload[field]))
                else:
                    setattr(profile, field, profile_payload[field])

        metadata = profile.metadata if isinstance(profile.metadata, dict) else {}
        profile.metadata = metadata

        has_allow_flags = any(
            key in profile_payload for key in {"allow_30_min", "allow_60_min", "allow_120_min"}
        )
        payload_metadata = profile_payload.get("metadata") if isinstance(profile_payload.get("metadata"), dict) else {}
        explicit_service_ids_in_payload = "service_preset_ids" in payload_metadata
        if has_allow_flags and not explicit_service_ids_in_payload:
            metadata["service_preset_ids"] = course_flags_to_service_preset_ids(
                resource.tenant,
                allow_30=profile.allow_30_min,
                allow_60=profile.allow_60_min,
                allow_120=profile.allow_120_min,
            )

        profile.save()

        if medias_payload is None:
            return

        profile.medias.all().delete()
        media_rows = []
        for index, item in enumerate(medias_payload):
            if not isinstance(item, dict):
                continue
            media_rows.append(
                ResourceMedia(
                    profile=profile,
                    title=(item.get("title") or "").strip(),
                    media_type=item.get("media_type") or "IMAGE",
                    image_url=item.get("image_url"),
                    video_url=item.get("video_url"),
                    cover_url=item.get("cover_url"),
                    order=item.get("order", index),
                    is_active=_coerce_bool(item.get("is_active", True)) is not False,
                )
            )
        if media_rows:
            ResourceMedia.objects.bulk_create(media_rows)

    def post(self, request):
        external_id = request.data.get("external_id")
        name = request.data.get("name")
        email = request.data.get("email")
        is_active = request.data.get("is_active")

        if not all([external_id, name]):
            return Response({"error": "Missing external_id or name"}, status=400)

        with transaction.atomic():
            defaults = {"name": name, "email": email or ""}
            if is_active is not None:
                parsed_active = _coerce_bool(is_active)
                if parsed_active is not None:
                    defaults["is_active"] = parsed_active

            resource, created = Resource.objects.update_or_create(
                tenant=request.tenant,
                external_id=external_id,
                defaults=defaults,
            )
            self._apply_profile_updates(resource, request.data)

        return Response(
            {"saas_id": str(resource.id), "status": "created" if created else "updated"},
            status=201,
        )

    def patch(self, request, pk=None):
        if not pk:
            return Response({"error": "Missing resource id"}, status=400)

        resource = Resource.objects.filter(tenant=request.tenant, id=pk).first()
        if not resource:
            return Response({"error": "Resource not found"}, status=404)

        with transaction.atomic():
            if "name" in request.data:
                resource.name = request.data.get("name") or resource.name
            if "email" in request.data:
                resource.email = request.data.get("email") or ""
            if "is_active" in request.data:
                parsed_active = _coerce_bool(request.data.get("is_active"))
                if parsed_active is not None:
                    resource.is_active = parsed_active
            resource.save()

            self._apply_profile_updates(resource, request.data)

        resource = (
            Resource.objects
            .select_related("profile")
            .prefetch_related("profile__medias")
            .get(id=resource.id)
        )
        return Response(_serialize_resource(resource), status=status.HTTP_200_OK)
