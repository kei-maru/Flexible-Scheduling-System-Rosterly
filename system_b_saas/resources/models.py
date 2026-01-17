from django.db import models
import uuid
from tenants.models import Tenant, SaaSUser

class Resource(models.Model):
    """
    予約可能なリソース（キャストなど）
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='resources')
    
    # System A (Veludo) 側のIDと紐付けるためのフィールド
    # これにより、System AのキャストとSystem Bのリソースを同期させる
    external_id = models.CharField("外部システムID", max_length=100, blank=True, null=True, help_text="System A側のCastProfile IDなど")
    
    name = models.CharField("リソース名", max_length=100)
    email = models.EmailField("メールアドレス", blank=True, null=True, help_text="予約・キャンセル通知の送信先")
    
    # 将来的にSaaSユーザー（キャスト本人）が直接ログインする場合用
    linked_user = models.OneToOneField(SaaSUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='resource_profile')
    
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ('tenant', 'external_id')

    def __str__(self):
        # 显示名字和邮箱，方便调试
        email_display = f" <{self.email}>" if self.email else ""
        return f"{self.name}{email_display} ({self.tenant.name})"

class Availability(models.Model):
    """
    リソースの空き状況（シフト）
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    resource = models.ForeignKey(Resource, on_delete=models.CASCADE, related_name='availabilities')
    
    start_time = models.DateTimeField("開始時間")
    end_time = models.DateTimeField("終了時間")
    
    is_booked = models.BooleanField("予約済み", default=False)

    is_recurring = models.BooleanField("周期フラグ", default=False)
    
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['start_time']
        verbose_name = "シフト・空き枠"
        verbose_name_plural = "シフト・空き枠一覧"

    def __str__(self):
        return f"{self.resource.name}: {self.start_time} - {self.end_time}"