from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0011_tenant_booking_detail_redirect_url"),
        ("bookings", "0008_booking_public_link_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="booking",
            name="cast_report_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="booking",
            name="customer_report_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="booking",
            name="last_reported_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="BookingReport",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "reporter_role",
                    models.CharField(
                        choices=[("CUSTOMER", "Customer"), ("CAST", "Cast")],
                        max_length=16,
                    ),
                ),
                (
                    "reason",
                    models.CharField(
                        choices=[
                            ("NO_SHOW", "無断欠席"),
                            ("HARASSMENT", "ハラスメント・暴言"),
                            ("LATE", "遅刻・時間不履行"),
                            ("FRAUD", "虚偽申告・なりすまし"),
                            ("UNSAFE", "危険行為・不適切行為"),
                            ("PAYMENT", "支払い・返金トラブル"),
                            ("OTHER", "その他"),
                        ],
                        max_length=32,
                    ),
                ),
                ("detail", models.TextField(blank=True, default="")),
                ("media", models.FileField(blank=True, null=True, upload_to="booking_reports/%Y/%m/%d/")),
                ("reporter_name", models.CharField(blank=True, default="", max_length=120)),
                ("reporter_email", models.EmailField(blank=True, default="", max_length=254)),
                ("is_read_by_admin", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "booking",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="reports", to="bookings.booking"),
                ),
                (
                    "tenant",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="booking_reports", to="tenants.tenant"),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
