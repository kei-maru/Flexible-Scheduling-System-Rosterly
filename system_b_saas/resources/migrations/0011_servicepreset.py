from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0001_initial'),
        ('resources', '0010_scheduletemplate'),
    ]

    operations = [
        migrations.CreateModel(
            name='ServicePreset',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=120, verbose_name='サービス名')),
                ('duration_minutes', models.PositiveIntegerField(default=60, verbose_name='所要時間(分)')),
                ('is_active', models.BooleanField(default=True, verbose_name='有効')),
                ('sort_order', models.IntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='service_presets', to='tenants.tenant')),
            ],
            options={
                'ordering': ['sort_order', 'id'],
                'unique_together': {('tenant', 'name')},
            },
        ),
    ]

