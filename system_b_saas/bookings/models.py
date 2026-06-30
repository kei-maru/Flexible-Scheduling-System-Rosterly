from django.db import models
import uuid
from tenants.models import Tenant
from resources.models import Resource


REPORT_REASON_CHOICES = [
    ("NO_SHOW", "無断欠席"),
    ("HARASSMENT", "ハラスメント・暴言"),
    ("LATE", "遅刻・時間不履行"),
    ("FRAUD", "虚偽申告・なりすまし"),
    ("UNSAFE", "危険行為・不適切行為"),
    ("PAYMENT", "支払い・返金トラブル"),
    ("OTHER", "その他"),
]

class Booking(models.Model):
    """订单表"""
    STATUS_CHOICES = [
        ('PENDING', '待确认'),
        ('CONFIRMED', '已确认'),
        ('CANCELLED', '已取消'),
        ('CANCELLED_BY_ADMIN', '管理者キャンセル'),
        ('COMPLETED', '完了'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    resource = models.ForeignKey(Resource, related_name='bookings', on_delete=models.CASCADE)
    
    customer_id = models.CharField("名前ID", max_length=100, db_index=True, null=True, blank=True)
    customer_discord_id = models.CharField("DiscordID", max_length=100, db_index=True, null=True, blank=True)
    customer_email = models.EmailField("メールアドレス")
    customer_name = models.CharField("名前", max_length=100)
    selected_service = models.ForeignKey(
        'resources.ServicePreset',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='bookings'
    )
    selected_service_name = models.CharField(max_length=120, null=True, blank=True)

    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    booking_type = models.CharField(max_length=20, default='PUBLIC')
    
    status = models.CharField(choices=STATUS_CHOICES, default='CONFIRMED', max_length=20)
    public_access_token = models.CharField(max_length=64, unique=True, null=True, blank=True, db_index=True)
    public_detail_url = models.URLField(max_length=500, null=True, blank=True)
    customer_report_count = models.PositiveIntegerField(default=0)
    cast_report_count = models.PositiveIntegerField(default=0)
    last_reported_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Booking {self.id} - {self.customer_name}"


class BookingReport(models.Model):
    REPORTER_ROLE_CHOICES = [
        ("CUSTOMER", "Customer"),
        ("CAST", "Cast"),
    ]

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="reports")
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="booking_reports")
    reporter_role = models.CharField(max_length=16, choices=REPORTER_ROLE_CHOICES)
    reason = models.CharField(max_length=32, choices=REPORT_REASON_CHOICES)
    detail = models.TextField(blank=True, default="")
    media = models.FileField(upload_to="booking_reports/%Y/%m/%d/", null=True, blank=True)
    reporter_name = models.CharField(max_length=120, blank=True, default="")
    reporter_email = models.EmailField(blank=True, default="")
    is_read_by_admin = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Report<{self.booking_id}:{self.reporter_role}:{self.reason}>"
