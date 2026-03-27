from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0005_tenant_profile_and_staffinvite"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="booking_window_days",
            field=models.PositiveIntegerField(default=14),
        ),
        migrations.AddField(
            model_name="tenant",
            name="store_contract_label",
            field=models.CharField(default="店舗利用規約", max_length=120),
        ),
        migrations.AddField(
            model_name="tenant",
            name="store_contract_url",
            field=models.URLField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="tenant",
            name="custom_terms_label",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="tenant",
            name="custom_terms_body",
            field=models.TextField(blank=True, default=""),
        ),
    ]
