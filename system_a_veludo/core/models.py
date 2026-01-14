from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    """
    自定义用户模型
    """
    is_cast = models.BooleanField("Castフラグ", default=False, help_text="このユーザーがキャストかどうか")
    
    # [新增] 社交账号 ID
    vrc_id = models.CharField("VRCID", max_length=100, blank=True, null=True, help_text="VRChat上の表示名")
    discord_id = models.CharField("Discord ID", max_length=50, blank=True, null=True, help_text="例: user#1234")
    twitter_id = models.CharField("X (Twitter) ID", max_length=50, blank=True, null=True, help_text="例: @username")
    
    # [新增] 用户头像
    avatar = models.ImageField("アイコン", upload_to="users/avatars/", blank=True, null=True)

    class Meta:
        verbose_name = "ユーザー"
        verbose_name_plural = "ユーザー一覧"

    def __str__(self):
        return self.username

