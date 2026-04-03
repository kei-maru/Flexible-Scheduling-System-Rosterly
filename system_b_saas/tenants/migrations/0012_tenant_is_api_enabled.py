from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0011_tenant_booking_detail_redirect_url"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="is_api_enabled",
            field=models.BooleanField(default=True),
        ),
    ]
