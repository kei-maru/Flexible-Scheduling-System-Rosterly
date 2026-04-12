from django.conf import settings
from django.db import models


class UserBehaviorEvent(models.Model):
    EVENT_CHOICES = [
        ("VIEW_PAGE", "View Page"),
        ("PAGE_DURATION", "Page Duration"),
        ("CLICK_CAST", "Click Cast"),
        ("CLICK_RESERVATION_INFO", "Click Reservation Info"),
        ("BOOKING_SUCCESS", "Booking Success"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="behavior_events",
    )
    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="behavior_events",
    )
    booking = models.ForeignKey(
        "bookings.Booking",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="behavior_events",
    )
    event_type = models.CharField(max_length=40, choices=EVENT_CHOICES)
    target = models.CharField(max_length=255, blank=True, default="")
    page_url = models.CharField(max_length=500, blank=True, default="")
    session_key = models.CharField(max_length=64, blank=True, default="")
    meta_data = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at"]

    def __str__(self):
        return f"{self.event_type}<{self.target or self.page_url}>"


class GlobalAnnouncement(models.Model):
    title = models.CharField(max_length=160)
    body = models.TextField(blank=True, default="")
    link_url = models.URLField(blank=True, default="")
    image = models.ImageField(upload_to="announcements/%Y/%m/%d/", null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_pinned = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_global_announcements",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_pinned", "-created_at"]

    def __str__(self):
        return self.title
