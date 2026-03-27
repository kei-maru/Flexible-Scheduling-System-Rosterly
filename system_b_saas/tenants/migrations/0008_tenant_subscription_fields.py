from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0007_tenant_required_customer_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="subscription_status",
            field=models.CharField(default="ACTIVE", max_length=20),
        ),
        migrations.AddField(
            model_name="tenant",
            name="subscription_plan_code",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="tenant",
            name="subscription_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="tenant",
            name="subscription_ends_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
