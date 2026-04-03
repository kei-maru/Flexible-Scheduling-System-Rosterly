from django.db import migrations, models
from django.conf import settings
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0012_tenant_is_api_enabled"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="api_ban_reason",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="tenant",
            name="api_ban_note",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="tenant",
            name="api_ban_media",
            field=models.FileField(blank=True, null=True, upload_to="tenant_ban_evidences/"),
        ),
        migrations.AddField(
            model_name="tenant",
            name="api_banned_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="tenant",
            name="api_banned_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="api_banned_tenants", to=settings.AUTH_USER_MODEL),
        ),
    ]
