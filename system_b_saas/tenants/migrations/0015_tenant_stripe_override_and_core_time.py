from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0014_alter_saasuser_role"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="core_time_week_config",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="tenant",
            name="stripe_checkout_session_id",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="tenant",
            name="stripe_customer_id",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="tenant",
            name="stripe_first_credit_amount",
            field=models.IntegerField(default=2000),
        ),
        migrations.AddField(
            model_name="tenant",
            name="stripe_first_credit_applied_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="tenant",
            name="stripe_price_id",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="tenant",
            name="stripe_subscription_id",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="tenant",
            name="subscription_override_enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="tenant",
            name="subscription_override_ends_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="tenant",
            name="subscription_override_note",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="tenant",
            name="subscription_override_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="tenant",
            name="subscription_override_status",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
    ]
