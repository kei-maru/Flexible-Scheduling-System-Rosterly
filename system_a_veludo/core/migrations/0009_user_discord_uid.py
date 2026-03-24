from django.db import migrations, models


def backfill_discord_uid_and_display(apps, schema_editor):
    User = apps.get_model('core', 'User')
    for user in User.objects.all().iterator():
        discord_id = (user.discord_id or '').strip()
        if not discord_id:
            continue

        is_numeric_uid = discord_id.isdigit() and 15 <= len(discord_id) <= 22
        if not is_numeric_uid:
            continue

        update_fields = []
        if not user.discord_uid:
            user.discord_uid = discord_id
            update_fields.append('discord_uid')

        if user.username and user.username != discord_id:
            user.discord_id = user.username
            update_fields.append('discord_id')

        if update_fields:
            user.save(update_fields=update_fields)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0008_user_sso_mapping_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='discord_uid',
            field=models.CharField(blank=True, db_index=True, help_text='Discord stable numeric uid', max_length=64, null=True, verbose_name='Discord UID'),
        ),
        migrations.RunPython(backfill_discord_uid_and_display, migrations.RunPython.noop),
    ]
