from django.contrib.auth.models import AbstractUser
from django.db import models
from django.conf import settings
from django.utils import timezone

class User(AbstractUser):
    """
    自定义用户模型
    """
    is_cast = models.BooleanField("Castフラグ", default=False, help_text="このユーザーがキャストかどうか")
    
    # [新增] 社交账号 ID
    vrc_id = models.CharField("VRCID", max_length=100, blank=True, null=True, help_text="VRChat上の表示名")
    discord_id = models.CharField("Discord ID", max_length=50, blank=True, null=True, help_text="例: user#1234")
    discord_uid = models.CharField("Discord UID", max_length=64, blank=True, null=True, db_index=True, help_text="Discord stable numeric uid")
    twitter_id = models.CharField("X (Twitter) ID", max_length=50, blank=True, null=True, help_text="例: @username")
    saas_user_id = models.CharField("SaaS User ID", max_length=64, blank=True, null=True, unique=True, db_index=True)
    saas_tenant_id = models.CharField("SaaS Tenant ID", max_length=64, blank=True, null=True)
    saas_role = models.CharField("SaaS Role", max_length=32, blank=True, null=True)
    
    # [新增] 用户头像
    avatar = models.ImageField("アイコン", upload_to="users/avatars/", blank=True, null=True)

    class Meta:
        verbose_name = "ユーザー"
        verbose_name_plural = "ユーザー一覧"

    def __str__(self):
        return self.username


class UserActivity(models.Model):
    # [关键修复] 补充所有前端用到的 Action 类型
    ACTION_CHOICES = [
        ('VIEW_PAGE', 'View Page'),           # 访问页面
        ('CLICK_CAST', 'Click Cast'),         # 点击 Cast 头像
        ('CLICK_SOCIAL', 'Click SNS/Video'),  # [新增] 点击社交链接/视频
        ('CLICK_RESERVATION', 'Click Reserve'), # [新增] 点击预约按钮
        ('CLICK_LINK', 'Click Link'),         # [新增] 点击普通链接 (如查看全部)
        ('OPEN_MENU', 'Open Mobile Menu'),    # [新增] 打开手机菜单
        ('FILTER_ROLE', 'Filter Role'),       
        ('VIEW_TAB', 'Switch Tab'),           
        ('VIEW_SHIFT', 'Check Schedule'),     
        ('LOGIN', 'User Login'),              
        ('BOOKING_SUCCESS', 'Booking Success'), 
        ('SWITCH_MODE', 'Switch Mode'),       
        ('PAGE_DURATION', 'Page Duration'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        verbose_name="ユーザー"
    )
    
    # 字段定义
    action = models.CharField("操作タイプ", max_length=50, choices=ACTION_CHOICES)
    
    # [建议] 将 target 长度增加到 255，防止 URL 过长导致报错
    target = models.CharField("ターゲット", max_length=255, blank=True, null=True, help_text="例: Cast名, ページ名")
    
    meta_data = models.JSONField("メタデータ", default=dict, blank=True) 
    timestamp = models.DateTimeField("日時", auto_now_add=True)
    
    class Meta:
        verbose_name = "ユーザー履歴"
        verbose_name_plural = "アクティビティログ"
        ordering = ['-timestamp']

    def __str__(self):
        user_str = self.user.username if self.user else "Anonymous"
        # 使用 get_action_display() 可以在后台显示可读的标签
        return f"{user_str} - {self.get_action_display()} - {self.timestamp}"


class BlockedIP(models.Model):
    ip = models.GenericIPAddressField("IP", unique=True, db_index=True)
    reason = models.CharField("理由", max_length=255, default="High-frequency bot traffic")
    is_active = models.BooleanField("有効", default=True)
    hit_count = models.PositiveIntegerField("検知回数", default=0)
    first_detected_at = models.DateTimeField("初回検知", auto_now_add=True)
    last_detected_at = models.DateTimeField("最終検知", auto_now=True)
    banned_until = models.DateTimeField("解除日時", null=True, blank=True)

    class Meta:
        verbose_name = "Blocked IP"
        verbose_name_plural = "Blocked IPs"
        ordering = ["-last_detected_at"]

    def __str__(self):
        return f"{self.ip} ({'active' if self.is_currently_blocked else 'inactive'})"

    @property
    def is_currently_blocked(self):
        if not self.is_active:
            return False
        if self.banned_until is None:
            return True
        return self.banned_until > timezone.now()
