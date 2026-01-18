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

class RecurringPattern(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    resource = models.ForeignKey(Resource, on_delete=models.CASCADE, related_name='recurring_patterns')
    
    # 星期几 (0=Sunday, 1=Monday... 6=Saturday) 
    # 注意：Python weekday是0=Mon，但为了配合前端习惯，建议 0=Sun 或明确字段名
    day_of_week = models.IntegerField() 
    
    start_time = models.TimeField()
    end_time = models.TimeField()
    
    # 有效期范围 (可选，但这符合你之前的 range_start/end 逻辑)
    valid_from = models.DateField()
    valid_until = models.DateField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # 确保同一资源、同一天不会有重复的规则（或者根据业务需求允许）
        unique_together = ('resource', 'day_of_week', 'start_time', 'end_time')