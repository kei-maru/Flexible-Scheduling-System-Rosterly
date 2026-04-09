from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("bookings", "0009_bookingreport_and_report_flags"),
        ("tenants", "0015_tenant_stripe_override_and_core_time"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UserBehaviorEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_type", models.CharField(choices=[("VIEW_PAGE", "View Page"), ("PAGE_DURATION", "Page Duration"), ("CLICK_CAST", "Click Cast"), ("CLICK_RESERVATION_INFO", "Click Reservation Info"), ("BOOKING_SUCCESS", "Booking Success")], max_length=40)),
                ("target", models.CharField(blank=True, default="", max_length=255)),
                ("page_url", models.CharField(blank=True, default="", max_length=500)),
                ("session_key", models.CharField(blank=True, default="", max_length=64)),
                ("meta_data", models.JSONField(blank=True, default=dict)),
                ("occurred_at", models.DateTimeField(auto_now_add=True)),
                ("booking", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="behavior_events", to="bookings.booking")),
                ("tenant", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="behavior_events", to="tenants.tenant")),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="behavior_events", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-occurred_at"]},
        ),
    ]
