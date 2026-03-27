from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bookings", "0006_booking_customer_discord_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="booking",
            name="booking_type",
            field=models.CharField(default="PUBLIC", max_length=20),
        ),
    ]
