from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0010_tenant_cancellation_window_hours"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="booking_detail_redirect_url",
            field=models.URLField(blank=True, null=True),
        ),
    ]
