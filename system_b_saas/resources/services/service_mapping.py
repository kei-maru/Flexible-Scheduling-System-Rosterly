from resources.models import ServicePreset


def _normalize_preset_ids(raw_ids):
    result = []
    if not isinstance(raw_ids, (list, tuple)):
        return result
    for item in raw_ids:
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def _duration_from_booking(booking):
    if not booking or not getattr(booking, "start_time", None) or not getattr(booking, "end_time", None):
        return 0
    delta = booking.end_time - booking.start_time
    minutes = int(delta.total_seconds() // 60)
    return minutes if minutes > 0 else 0


def course_flags_to_service_preset_ids(tenant, allow_30=False, allow_60=True, allow_120=False):
    if not tenant:
        return []

    duration_flags = [(30, bool(allow_30)), (60, bool(allow_60)), (120, bool(allow_120))]
    selected_ids = []
    for duration, enabled in duration_flags:
        if not enabled:
            continue
        preset = (
            ServicePreset.objects.filter(
                tenant=tenant,
                is_active=True,
                duration_minutes=duration,
            )
            .order_by("sort_order", "id")
            .first()
        )
        if preset:
            selected_ids.append(str(preset.id))
    return selected_ids


def resolve_service_by_duration(tenant, duration_minutes, preferred_service_ids=None):
    if not tenant:
        return None

    try:
        duration = int(duration_minutes)
    except (TypeError, ValueError):
        return None

    if duration <= 0:
        return None

    preferred_ids = _normalize_preset_ids(preferred_service_ids)
    if preferred_ids:
        preferred = (
            ServicePreset.objects.filter(
                tenant=tenant,
                id__in=preferred_ids,
                is_active=True,
                duration_minutes=duration,
            )
            .order_by("sort_order", "id")
            .first()
        )
        if preferred:
            return preferred

    return (
        ServicePreset.objects.filter(
            tenant=tenant,
            is_active=True,
            duration_minutes=duration,
        )
        .order_by("sort_order", "id")
        .first()
    )


def resolve_booking_service_name(booking, tenant):
    if not booking:
        return ""

    service_name = (getattr(booking, "selected_service_name", "") or "").strip()
    if service_name:
        return service_name

    selected_service = getattr(booking, "selected_service", None)
    if selected_service and selected_service.name:
        return selected_service.name

    preferred_ids = []
    resource = getattr(booking, "resource", None)
    profile = getattr(resource, "profile", None) if resource else None
    metadata = profile.metadata if profile and isinstance(profile.metadata, dict) else {}
    preferred_ids = metadata.get("service_preset_ids") or []

    duration_minutes = _duration_from_booking(booking)
    preset = resolve_service_by_duration(tenant, duration_minutes, preferred_service_ids=preferred_ids)
    if preset:
        return preset.name

    if duration_minutes > 0:
        return f"{duration_minutes}分"

    return ""
