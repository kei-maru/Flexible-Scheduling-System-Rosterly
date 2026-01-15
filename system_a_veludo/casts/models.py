from django.db import models
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

class CastProfile(models.Model):
    """
    Cast 的公开档案
    """
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='cast_profile')
    
    name = models.CharField("キャスト名", max_length=100)
    
    # [修正] JSON标准必须使用双引号。修改提示文案以防止报错。
    tags = models.JSONField("特徴タグ", default=list, blank=True, help_text='必ずダブルクォート(")を使用してください。例: ["癒し", "イケボ", "ロールプレイ"]')
    
    # TextField 完美支持换行和特殊符号
    intro = models.TextField("自己紹介文", blank=True, help_text="改行や特殊文字もそのまま保存されます。")
    
    avatar = models.ImageField("プロフィール画像", upload_to="casts/avatars/")

    youtube_url = models.URLField("YouTube URL", blank=True, null=True, help_text="プロフィールのメイン動画 (例: https://youtu.be/...)")

    display_order = models.IntegerField("表示順序", default=0, help_text="一覧ページでの表示順 (数字が小さい方が先頭)")
    
    saas_resource_id = models.CharField("SaaSリソースID", max_length=64, blank=True, unique=True)

    allow_30_min = models.BooleanField(default=False, verbose_name="Allow 30 min")
    allow_60_min = models.BooleanField(default=True, verbose_name="Allow 60 min")
    allow_120_min = models.BooleanField(default=False, verbose_name="Allow 120 min")
    
    is_active = models.BooleanField("在籍中", default=True)

    class Meta:
        verbose_name = "キャストプロフィール"
        verbose_name_plural = "キャストプロフィール一覧"
        ordering = ['display_order', 'id'] # 默认按顺序排列

        

    def __str__(self):
        return self.name

class CastMedia(models.Model):
    """
    用于前端横向滚动窗口(Carousel)的媒体资源
    """
    # 区分是图片还是 YouTube 视频
    MEDIA_TYPES = [('VIDEO', '動画 (YouTube)'), ('IMAGE', '画像')]
    
    cast = models.ForeignKey(CastProfile, related_name='medias', on_delete=models.CASCADE)
    title = models.CharField("タイトル", max_length=100)
    
    # [优化点] 区分图片上传和视频链接
    # 如果是图片，上传到这里
    image_file = models.ImageField("画像ファイル", upload_to="casts/works/")
    
    # 如果是视频，直接存 YouTube 链接
    #youtube_url = models.URLField("YouTube URL", null=True, blank=True, help_text="タイプが『動画』の場合にURLを入力 (例: https://youtu.be/...)")
    
    media_type = models.CharField("メディアタイプ", max_length=10, choices=MEDIA_TYPES, default='IMAGE')
    
    # 视频依然可以上传一个自定义封面，如果没传，前端可以用默认图或调用 YouTube API 获取封面
    cover_image = models.ImageField("サムネイル画像", upload_to="casts/covers/", null=True, blank=True, help_text="動画の表紙画像（未設定可）")
    
    order = models.IntegerField("表示順序", default=0, help_text="数字が小さいほど先に表示されます")

    class Meta:
        ordering = ['order']
        verbose_name = "キャスト作品（メディア）"
        verbose_name_plural = "キャスト作品一覧"
