from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Max, Min
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from bookings.models import Booking
from resources.models import Availability, RecurringPattern, Resource, ScheduleTemplate

JST = ZoneInfo("Asia/Tokyo")
BOOKING_BUFFER = timedelta(minutes=30)
MIN_SLOT_SECONDS = 30 * 60


class ScheduleServiceError(Exception):
    pass


class ScheduleValidationError(ScheduleServiceError):
    pass


class SchedulePermissionError(ScheduleServiceError):
    pass


class ScheduleNotFoundError(ScheduleServiceError):
    pass


def _ensure_aware(dt: datetime) -> datetime:
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, JST)
    return dt.astimezone(JST)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = parse_datetime(value)
    if dt is None:
        return None
    return _ensure_aware(dt)


def _parse_date_or_datetime(value: str) -> datetime:
    dt = parse_datetime(value)
    if dt is not None:
        return _ensure_aware(dt)

    d = parse_date(value)
    if d is not None:
        return _ensure_aware(datetime.combine(d, datetime.min.time()))

    raise ScheduleValidationError(f"Invalid date format: {value}")


def _normalize_week_config(week_config: dict | None) -> dict[str, list[dict[str, str]]]:
    normalized: dict[str, list[dict[str, str]]] = {}
    if not week_config:
        return normalized

    for day_key, config in week_config.items():
        if not config or not config.get("enabled"):
            continue

        slots: list[dict[str, str]] = []
        raw_slots = config.get("slots")
        if isinstance(raw_slots, list):
            for slot in raw_slots:
                if slot and slot.get("start") and slot.get("end"):
                    slots.append({"start": slot["start"], "end": slot["end"]})
        elif config.get("start") and config.get("end"):
            slots.append({"start": config["start"], "end": config["end"]})

        if slots:
            normalized[str(day_key)] = slots

    return normalized


def resolve_resource(tenant, resource_id_raw: str | None) -> Resource:
    if not resource_id_raw:
        raise ScheduleValidationError("resource_id required")

    try:
        uuid_obj = UUID(resource_id_raw)
        resource = Resource.objects.filter(id=uuid_obj, tenant=tenant).first()
        if resource:
            return resource
    except (ValueError, TypeError):
        pass

    resource = Resource.objects.filter(tenant=tenant, external_id=resource_id_raw).first()
    if not resource:
        raise ScheduleNotFoundError("Resource not found")
    return resource


def _check_conflict(resource: Resource, start_dt: datetime, end_dt: datetime) -> bool:
    shift_conflict = Availability.objects.filter(
        resource=resource,
        start_time__lt=end_dt,
        end_time__gt=start_dt,
    ).exists()

    booking_conflict = Booking.objects.filter(
        resource=resource,
        start_time__lt=end_dt + BOOKING_BUFFER,
        end_time__gt=start_dt - BOOKING_BUFFER,
        status="CONFIRMED",
    ).exists()

    return shift_conflict or booking_conflict


def list_events(tenant, resource_id_raw: str | None, start_str: str | None, end_str: str | None, mode: str = "raw"):
    resource = resolve_resource(tenant, resource_id_raw)

    start_dt = _parse_datetime(start_str)
    end_dt = _parse_datetime(end_str)

    booking_deadline = timezone.now() + timedelta(hours=24)

    if mode == "search":
        if not start_dt or not end_dt:
            raise ScheduleValidationError("Time range required")

        if start_dt < booking_deadline:
            return []

        shift = Availability.objects.filter(
            resource=resource,
            is_booked=False,
            start_time__lte=start_dt,
            end_time__gte=end_dt,
        ).first()
        if not shift:
            return []

        has_conflict = Booking.objects.filter(
            resource=resource,
            status__in=["CONFIRMED", "PENDING"],
            start_time__lt=end_dt + BOOKING_BUFFER,
            end_time__gt=start_dt - BOOKING_BUFFER,
        ).exists()

        if has_conflict:
            return []

        return [{"start": start_dt, "end": end_dt, "status": "AVAILABLE"}]

    avail_qs = Availability.objects.filter(resource=resource, is_booked=False)
    book_qs = Booking.objects.filter(resource=resource, status__in=["CONFIRMED", "PENDING"])

    if start_dt and end_dt:
        avail_qs = avail_qs.filter(start_time__lt=end_dt, end_time__gt=start_dt)
        book_qs = book_qs.filter(
            start_time__lt=end_dt + timedelta(minutes=60),
            end_time__gt=start_dt - timedelta(minutes=60),
        )

    avail_qs = avail_qs.filter(end_time__gt=booking_deadline)

    avail_list = list(avail_qs)
    book_list = list(book_qs)
    events = []

    for booking in book_list:
        client_name = getattr(booking, "guest_name", "Guest")
        if not client_name and hasattr(booking, "user") and booking.user:
            client_name = booking.user.username

        events.append(
            {
                "id": str(booking.id),
                "resource_id": str(resource.id),
                "start": booking.start_time,
                "end": booking.end_time,
                "is_booked": True,
                "is_recurring": False,
                "type": "booking",
                "title": f"{client_name} 様",
                "guest_name": client_name,
            }
        )

    for availability in avail_list:
        segments = [(availability.start_time, availability.end_time)]
        relevant_bookings = [
            booking
            for booking in book_list
            if (booking.end_time + BOOKING_BUFFER) > availability.start_time
            and (booking.start_time - BOOKING_BUFFER) < availability.end_time
        ]

        for booking in relevant_bookings:
            cut_start = booking.start_time - BOOKING_BUFFER
            cut_end = booking.end_time + BOOKING_BUFFER
            next_segments = []

            for seg_start, seg_end in segments:
                overlap_start = max(seg_start, cut_start)
                overlap_end = min(seg_end, cut_end)
                if overlap_start < overlap_end:
                    if seg_start < overlap_start:
                        next_segments.append((seg_start, overlap_start))
                    if overlap_end < seg_end:
                        next_segments.append((overlap_end, seg_end))
                else:
                    next_segments.append((seg_start, seg_end))

            segments = next_segments

        for seg_start, seg_end in segments:
            if (seg_end - seg_start).total_seconds() < 60:
                continue

            valid_start = max(seg_start, booking_deadline)
            if valid_start >= seg_end:
                continue

            if (seg_end - valid_start).total_seconds() < MIN_SLOT_SECONDS:
                continue

            events.append(
                {
                    "id": str(availability.id),
                    "resource_id": str(resource.id),
                    "start": valid_start,
                    "end": seg_end,
                    "is_booked": False,
                    "is_recurring": availability.is_recurring,
                    "type": "availability",
                    "title": "Available",
                }
            )

    return events


def create_single_availability(tenant, resource_id_raw: str, start_str: str, end_str: str):
    resource = resolve_resource(tenant, resource_id_raw)

    if not all([start_str, end_str]):
        raise ScheduleValidationError("Missing fields")

    start_dt = _parse_datetime(start_str)
    end_dt = _parse_datetime(end_str)
    if not start_dt or not end_dt:
        raise ScheduleValidationError("Invalid time range")

    if start_dt >= end_dt:
        raise ScheduleValidationError("Invalid time range")

    if start_dt < timezone.now() + timedelta(hours=24):
        raise ScheduleValidationError("新規シフトは24時間後から設定可能です。")

    if _check_conflict(resource, start_dt, end_dt):
        raise ScheduleValidationError("Time slot conflict")

    availability = Availability.objects.create(
        resource=resource,
        start_time=start_dt,
        end_time=end_dt,
        is_recurring=False,
    )

    return {"id": str(availability.id), "status": "created"}


def create_recurring_availability(
    tenant,
    resource_id_raw: str,
    range_start: str,
    range_end: str,
    week_config: dict,
):
    resource = resolve_resource(tenant, resource_id_raw)

    if not all([range_start, range_end, week_config]):
        raise ScheduleValidationError("Missing fields")

    normalized_week = _normalize_week_config(week_config)

    start_dt = _parse_date_or_datetime(range_start)
    end_dt = _parse_date_or_datetime(range_end)
    curr_date = start_dt.date()
    end_date = end_dt.date()

    if curr_date > end_date:
        raise ScheduleValidationError("Invalid range")

    generation_min_time = timezone.now() + timedelta(hours=24)
    stats = {"created": 0, "skipped_conflict": 0, "skipped_24h": 0, "deleted": 0}

    with transaction.atomic():
        RecurringPattern.objects.filter(resource=resource).delete()

        for day_key, slots in normalized_week.items():
            for slot in slots:
                RecurringPattern.objects.create(
                    resource=resource,
                    day_of_week=int(day_key),
                    start_time=slot["start"],
                    end_time=slot["end"],
                    valid_from=curr_date,
                    valid_until=end_date,
                )

        loop_date = curr_date
        while loop_date <= end_date:
            py_weekday = loop_date.weekday()
            js_day_key = "0" if py_weekday == 6 else str(py_weekday + 1)

            day_start = _ensure_aware(datetime.combine(loop_date, datetime.min.time()))
            day_end = day_start + timedelta(hours=30)

            deleted_count, _ = Availability.objects.filter(
                resource=resource,
                start_time__gte=day_start,
                start_time__lt=day_end,
                is_booked=False,
                is_recurring=True,
            ).delete()
            stats["deleted"] += deleted_count

            day_slots = normalized_week.get(js_day_key, [])
            for slot in day_slots:
                start_time = datetime.strptime(slot["start"], "%H:%M").time()
                end_time = datetime.strptime(slot["end"], "%H:%M").time()

                seg_start = _ensure_aware(datetime.combine(loop_date, start_time))
                seg_end = _ensure_aware(datetime.combine(loop_date, end_time))
                if seg_end <= seg_start:
                    seg_end += timedelta(days=1)

                if seg_start < generation_min_time:
                    stats["skipped_24h"] += 1
                elif _check_conflict(resource, seg_start, seg_end):
                    stats["skipped_conflict"] += 1
                else:
                    Availability.objects.create(
                        resource=resource,
                        start_time=seg_start,
                        end_time=seg_end,
                        is_booked=False,
                        is_recurring=True,
                    )
                    stats["created"] += 1

            loop_date += timedelta(days=1)

    return stats


def delete_availability(tenant, availability_id: str):
    try:
        availability = Availability.objects.get(id=availability_id, resource__tenant=tenant)
    except Availability.DoesNotExist as exc:
        raise ScheduleNotFoundError("Not found") from exc

    if availability.is_booked:
        raise ScheduleValidationError("Cannot delete booked slot")

    availability.delete()


def get_recurring_config(tenant, resource_id_raw: str | None):
    if not resource_id_raw:
        return {}

    resource = resolve_resource(tenant, resource_id_raw)
    patterns = RecurringPattern.objects.filter(resource=resource)

    config = {}
    range_info = {"start": None, "end": None}

    if patterns.exists():
        dates = patterns.aggregate(min_start=Min("valid_from"), max_end=Max("valid_until"))
        range_info["start"] = dates["min_start"]
        range_info["end"] = dates["max_end"]

        for pattern in patterns.order_by("day_of_week", "start_time"):
            day_key = str(pattern.day_of_week)
            day_cfg = config.setdefault(day_key, {"enabled": True, "slots": []})
            day_cfg["slots"].append(
                {
                    "start": pattern.start_time.strftime("%H:%M"),
                    "end": pattern.end_time.strftime("%H:%M"),
                }
            )

        for day_cfg in config.values():
            if day_cfg.get("slots"):
                day_cfg["start"] = day_cfg["slots"][0]["start"]
                day_cfg["end"] = day_cfg["slots"][0]["end"]

    return {"range": range_info, "week_config": config}


def list_templates(tenant, resource_id_raw: str):
    resource = resolve_resource(tenant, resource_id_raw)
    templates = ScheduleTemplate.objects.filter(resource=resource)

    return [
        {"id": str(template.id), "name": template.name, "week_config": template.week_config}
        for template in templates
    ]


def save_template(tenant, resource_id_raw: str, name: str, week_config: dict):
    if not all([resource_id_raw, name, week_config]):
        raise ScheduleValidationError("Missing fields")

    resource = resolve_resource(tenant, resource_id_raw)
    template, _ = ScheduleTemplate.objects.update_or_create(
        resource=resource,
        name=name,
        defaults={"week_config": week_config},
    )

    return {"id": str(template.id), "status": "saved"}


def delete_template(tenant, template_id: str):
    if not template_id:
        raise ScheduleValidationError("id required")

    ScheduleTemplate.objects.filter(id=template_id, resource__tenant=tenant).delete()
