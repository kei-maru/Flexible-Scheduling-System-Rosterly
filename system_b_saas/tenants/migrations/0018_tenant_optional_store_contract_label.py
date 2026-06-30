from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0017_tenant_soft_delete_window"),
    ]

    operations = [
        migrations.AlterField(
            model_name="tenant",
            name="store_contract_label",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
    ]
