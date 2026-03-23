from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0007_blockedip'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='saas_role',
            field=models.CharField(blank=True, max_length=32, null=True, verbose_name='SaaS Role'),
        ),
        migrations.AddField(
            model_name='user',
            name='saas_tenant_id',
            field=models.CharField(blank=True, max_length=64, null=True, verbose_name='SaaS Tenant ID'),
        ),
        migrations.AddField(
            model_name='user',
            name='saas_user_id',
            field=models.CharField(blank=True, db_index=True, max_length=64, null=True, unique=True, verbose_name='SaaS User ID'),
        ),
    ]
