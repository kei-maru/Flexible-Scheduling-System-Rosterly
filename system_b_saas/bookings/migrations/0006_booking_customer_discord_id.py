from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bookings", "0005_booking_service_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="booking",
            name="customer_discord_id",
            field=models.CharField(blank=True, db_index=True, max_length=100, null=True),
        ),
    ]
