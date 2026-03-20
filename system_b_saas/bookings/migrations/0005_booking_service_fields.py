from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('resources', '0011_servicepreset'),
        ('bookings', '0004_booking_customer_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='booking',
            name='selected_service',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='bookings', to='resources.servicepreset'),
        ),
        migrations.AddField(
            model_name='booking',
            name='selected_service_name',
            field=models.CharField(blank=True, max_length=120, null=True),
        ),
    ]

