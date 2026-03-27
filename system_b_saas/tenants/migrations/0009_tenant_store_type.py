from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0008_tenant_subscription_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="store_type",
            field=models.CharField(default="FLEX_SHIFT", max_length=20),
        ),
    ]
