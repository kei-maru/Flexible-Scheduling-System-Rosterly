from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bookings", "0009_bookingreport_and_report_flags"),
    ]

    operations = [
        migrations.AlterField(
            model_name="booking",
            name="customer_id",
            field=models.CharField("名前ID", blank=True, db_index=True, max_length=100, null=True),
        ),
        migrations.AlterField(
            model_name="booking",
            name="customer_discord_id",
            field=models.CharField("DiscordID", blank=True, db_index=True, max_length=100, null=True),
        ),
        migrations.AlterField(
            model_name="booking",
            name="customer_email",
            field=models.EmailField("メールアドレス", max_length=254),
        ),
        migrations.AlterField(
            model_name="booking",
            name="customer_name",
            field=models.CharField("名前", max_length=100),
        ),
    ]
