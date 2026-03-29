from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bookings", "0007_booking_booking_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="booking",
            name="public_access_token",
            field=models.CharField(blank=True, db_index=True, max_length=64, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="booking",
            name="public_detail_url",
            field=models.URLField(blank=True, max_length=500, null=True),
        ),
    ]
