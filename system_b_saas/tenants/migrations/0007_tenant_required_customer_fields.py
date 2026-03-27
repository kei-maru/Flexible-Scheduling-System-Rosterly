from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0006_tenant_booking_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="required_customer_fields",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
