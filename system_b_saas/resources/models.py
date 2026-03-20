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

class ScheduleTemplate(models.Model):
    """
    排班模版（Pattern）：用于保存用户的常用排班配置，供快速调用
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # 关联到 Resource (每个 Cast 可以有自己的模版)
    resource = models.ForeignKey(Resource, on_delete=models.CASCADE, related_name='schedule_templates')
    
    # 模版名称 (例如: "平日深夜", "週末全天", "考试周")
    name = models.CharField("テンプレート名", max_length=100)
    
    # 直接存储前端传来的 week_config JSON 对象
    # 格式示例: { "1": {"enabled": true, "start": "22:00", "end": "01:00"}, ... }
    week_config = models.JSONField("設定データ") 
    
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # 防止同一个 Cast 起重名的模版
        unique_together = ('resource', 'name')
        ordering = ['-created_at']
        verbose_name = "シフトテンプレート"
        verbose_name_plural = "シフトテンプレート一覧"

    def __str__(self):
        return f"{self.name} ({self.resource.name})"

class ServicePreset(models.Model):
    """
    Tenant 级别的可预约服务预设
    """
    tenant = models.ForeignKey('tenants.Tenant', on_delete=models.CASCADE, related_name='service_presets')
    name = models.CharField("サービス名", max_length=120)
    duration_minutes = models.PositiveIntegerField("所要時間(分)", default=60)
    is_active = models.BooleanField("有効", default=True)
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('tenant', 'name')
        ordering = ['sort_order', 'id']

    def __str__(self):
        return f"{self.name} ({self.duration_minutes}min)"

class EmailTemplate(models.Model):
    EVENT_CHOICES = [
        ('BOOKING_CONFIRMED', 'Booking Confirmation'),
        ('BOOKING_CANCELLED', 'Booking Cancellation'),
    ]

    tenant = models.ForeignKey('tenants.Tenant', on_delete=models.CASCADE, related_name='email_templates')
    event_type = models.CharField(max_length=50, choices=EVENT_CHOICES)

    logo = models.ImageField(upload_to='tenant_logos/', blank=True, null=True, help_text="品牌Logo")
    
    # --- 新增：结构化字段 ---
    email_title = models.CharField(max_length=200, default="予約が確定しました。", help_text="邮件的大标题")
    email_greeting = models.TextField(default="以下の内容で予約を承りました。", help_text="开场问候语")

    service_name = models.CharField(max_length=200, default="60分VRASMR施術コース (PCVR)", help_text="サービス名")

    send_to_customer = models.BooleanField(default=True, help_text="お客様にメール送る")
    send_to_cast = models.BooleanField(default=True, help_text="担当キャストにメール送る")
    
    # 按钮相关
    button_text = models.CharField(max_length=50, default="詳細を見る", help_text="按钮上的文字")
    button_link = models.CharField(max_length=255, default="https://vr-veludo.com/my-page", help_text="按钮跳转链接")
    
    # 页脚
    footer_title = models.CharField(max_length=100, default="当社のキャンセルポリシー", help_text="页脚标题")
    footer_text = models.TextField(default="ご予約の変更やキャンセルは 1日 前までにお願いいたします。", help_text="页脚正文")

    # 保留 subject_template 用于邮件标题
    subject_template = models.CharField(max_length=255, default="【予約確定】{{ resource_name }} との予約が確定しました")
    
    # body_html 现在作为只读的缓存，或者干脆不再使用，我们动态生成
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('tenant', 'event_type')

    def __str__(self):
        return f"{self.tenant.name} - {self.event_type}"
