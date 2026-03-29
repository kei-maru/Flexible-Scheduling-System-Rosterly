from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0009_tenant_store_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="cancellation_window_hours",
            field=models.PositiveIntegerField(default=2),
        ),
    ]
