from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0004_ssoauthcode'),
    ]

    operations = [
        migrations.AddField(
            model_name='tenant',
            name='contact_email',
            field=models.EmailField(blank=True, null=True, max_length=254),
        ),
        migrations.AddField(
            model_name='tenant',
            name='logo',
            field=models.ImageField(blank=True, null=True, upload_to='tenant_logos/'),
        ),
        migrations.CreateModel(
            name='StaffInvite',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token', models.CharField(db_index=True, max_length=96, unique=True)),
                ('role', models.CharField(choices=[('STAFF', 'Staff'), ('ADMIN', 'Admin')], default='STAFF', max_length=16)),
                ('max_uses', models.PositiveIntegerField(default=1)),
                ('used_count', models.PositiveIntegerField(default=0)),
                ('expires_at', models.DateTimeField(db_index=True)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_staff_invites', to=settings.AUTH_USER_MODEL)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='staff_invites', to='tenants.tenant')),
            ],
            options={
                'db_table': 'staff_invites',
                'ordering': ['-created_at'],
            },
        ),
    ]
