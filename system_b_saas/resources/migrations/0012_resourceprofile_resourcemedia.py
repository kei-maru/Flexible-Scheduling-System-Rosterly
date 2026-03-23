from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('resources', '0011_servicepreset'),
    ]

    operations = [
        migrations.CreateModel(
            name='ResourceProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('intro', models.TextField(blank=True, default='', verbose_name='自己紹介文')),
                ('tags', models.JSONField(blank=True, default=list, verbose_name='特徴タグ')),
                ('avatar_url', models.URLField(blank=True, null=True, verbose_name='プロフィール画像URL')),
                ('youtube_url', models.URLField(blank=True, null=True, verbose_name='YouTube URL')),
                ('display_order', models.IntegerField(default=0, verbose_name='表示順序')),
                ('allow_30_min', models.BooleanField(default=False, verbose_name='Allow 30 min')),
                ('allow_60_min', models.BooleanField(default=True, verbose_name='Allow 60 min')),
                ('allow_120_min', models.BooleanField(default=False, verbose_name='Allow 120 min')),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('resource', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='profile', to='resources.resource')),
            ],
            options={
                'ordering': ['display_order', 'resource__name'],
            },
        ),
        migrations.CreateModel(
            name='ResourceMedia',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(blank=True, default='', max_length=120, verbose_name='タイトル')),
                ('media_type', models.CharField(choices=[('IMAGE', '画像'), ('VIDEO', '動画')], default='IMAGE', max_length=10, verbose_name='メディアタイプ')),
                ('image_url', models.URLField(blank=True, null=True, verbose_name='画像URL')),
                ('video_url', models.URLField(blank=True, null=True, verbose_name='動画URL')),
                ('cover_url', models.URLField(blank=True, null=True, verbose_name='サムネイルURL')),
                ('order', models.IntegerField(default=0, verbose_name='表示順序')),
                ('is_active', models.BooleanField(default=True, verbose_name='有効')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('profile', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='medias', to='resources.resourceprofile')),
            ],
            options={
                'ordering': ['order', 'id'],
            },
        ),
    ]
