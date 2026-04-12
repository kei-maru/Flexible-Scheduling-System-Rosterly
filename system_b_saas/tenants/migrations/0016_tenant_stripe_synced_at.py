from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0015_tenant_stripe_override_and_core_time"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="stripe_synced_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
