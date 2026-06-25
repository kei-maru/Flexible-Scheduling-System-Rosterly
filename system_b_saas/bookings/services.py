from datetime import timedelta

from django.db import transaction

from bookings.models import Booking
from resources.models import Availability, Resource


class BookingCreateError(Exception):
    def __init__(self, message, *, status_code=400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def create_confirmed_booking_with_lock(
    *,
    tenant,
    resource,
    start_time,
    end_time,
    buffer_minutes=30,
    after_create=None,
    **booking_fields,
):
    """
    Serialize booking creation for a resource, then validate availability and
    conflicts inside the same transaction.
    """
    if end_time <= start_time:
        raise BookingCreateError("Invalid booking time range", status_code=400)

    buffer_delta = timedelta(minutes=max(0, int(buffer_minutes or 0)))

    with transaction.atomic():
        locked_resource = (
            Resource.objects.select_for_update()
            .select_related("tenant")
            .get(id=resource.id, tenant=tenant)
        )

        availability = (
            Availability.objects.select_for_update()
            .filter(
                resource=locked_resource,
                is_booked=False,
                start_time__lte=start_time,
                end_time__gte=end_time,
            )
            .order_by("start_time", "id")
            .first()
        )
        if not availability:
            raise BookingCreateError("Selected start time is outside available slots", status_code=400)

        conflict = Booking.objects.filter(
            tenant=tenant,
            resource=locked_resource,
            start_time__lt=end_time + buffer_delta,
            end_time__gt=start_time - buffer_delta,
            status="CONFIRMED",
        ).exists()
        if conflict:
            raise BookingCreateError("Time slot unavailable", status_code=409)

        booking = Booking.objects.create(
            tenant=tenant,
            resource=locked_resource,
            start_time=start_time,
            end_time=end_time,
            status="CONFIRMED",
            **booking_fields,
        )
        if after_create:
            after_create(booking)
        return booking
