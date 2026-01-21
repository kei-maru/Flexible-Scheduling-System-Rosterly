from django.contrib.auth.models import AbstractUser
from django.db import models
from django.conf import settings

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


class UserActivity(models.Model):
    ACTION_CHOICES = [
        ('VIEW_PAGE', 'View Page'),       # 访问页面
        ('CLICK_CAST', 'Click Cast'),     # 点击 Cast 头像
        ('FILTER_ROLE', 'Filter Role'),   # 筛选角色
        ('VIEW_TAB', 'Switch Tab'),       # 切换标签页
        ('VIEW_SHIFT', 'Check Schedule'), # 查看排班
        ('LOGIN', 'User Login'),          # 登录
        ('BOOKING_SUCCESS', 'Booking Success'), 
        ('SWITCH_MODE', 'Switch Mode'),         
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        verbose_name="ユーザー"
    )
    action = models.CharField("操作タイプ", max_length=50, choices=ACTION_CHOICES)
    target = models.CharField("ターゲット", max_length=100, blank=True, null=True, help_text="例: Cast名, ページ名")
    meta_data = models.JSONField("メタデータ", default=dict, blank=True) # 存 IP, 浏览器信息等
    timestamp = models.DateTimeField("日時", auto_now_add=True)
    
    class Meta:
        verbose_name = "ユーザーアクティビティ"
        verbose_name_plural = "アクティビティログ"
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.user} - {self.action} - {self.timestamp}"
