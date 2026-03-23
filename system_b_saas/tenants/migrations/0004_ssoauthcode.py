from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0003_saasuser_discord_id'),
    ]

    operations = [
        migrations.CreateModel(
            name='SSOAuthCode',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code_hash', models.CharField(db_index=True, max_length=64, unique=True)),
                ('client_id', models.CharField(db_index=True, max_length=128)),
                ('redirect_uri', models.URLField(max_length=512)),
                ('nonce', models.CharField(blank=True, max_length=128)),
                ('expires_at', models.DateTimeField(db_index=True)),
                ('used_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('tenant', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='tenants.tenant')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sso_codes', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'sso_auth_codes',
                'ordering': ['-created_at'],
            },
        ),
    ]
