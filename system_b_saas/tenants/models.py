from django.contrib.auth.models import AbstractUser
from django.conf import settings
from django.db import models
from django.utils import timezone
import uuid

class Tenant(models.Model):
    """
    租户表 (例如: Veludo)
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True) 
    contact_email = models.EmailField(blank=True, null=True)
    logo = models.ImageField(upload_to='tenant_logos/', blank=True, null=True)
    store_type = models.CharField(max_length=20, default='FLEX_SHIFT')
    booking_window_days = models.PositiveIntegerField(default=14)
    cancellation_window_hours = models.PositiveIntegerField(default=2)
    booking_detail_redirect_url = models.URLField(blank=True, null=True)
    store_contract_label = models.CharField(max_length=120, blank=True, default='')
    store_contract_url = models.URLField(blank=True, null=True)
    required_customer_fields = models.JSONField(default=list, blank=True)
    subscription_status = models.CharField(max_length=20, default='ACTIVE')
    subscription_plan_code = models.CharField(max_length=64, blank=True, default='')
    subscription_started_at = models.DateTimeField(blank=True, null=True)
    subscription_ends_at = models.DateTimeField(blank=True, null=True)
    stripe_customer_id = models.CharField(max_length=64, blank=True, default='')
    stripe_subscription_id = models.CharField(max_length=64, blank=True, default='')
    stripe_price_id = models.CharField(max_length=128, blank=True, default='')
    stripe_checkout_session_id = models.CharField(max_length=128, blank=True, default='')
    stripe_first_credit_applied_at = models.DateTimeField(blank=True, null=True)
    stripe_first_credit_amount = models.IntegerField(default=2000)
    stripe_synced_at = models.DateTimeField(blank=True, null=True)
    subscription_override_enabled = models.BooleanField(default=False)
    subscription_override_status = models.CharField(max_length=20, blank=True, default='')
    subscription_override_started_at = models.DateTimeField(blank=True, null=True)
    subscription_override_ends_at = models.DateTimeField(blank=True, null=True)
    subscription_override_note = models.TextField(blank=True, default='')
    core_time_week_config = models.JSONField(default=dict, blank=True)
    custom_terms_label = models.CharField(max_length=120, blank=True, default='')
    custom_terms_body = models.TextField(blank=True, default='')
    webhook_url = models.URLField(blank=True, null=True, help_text="预定成功后，系统会向此地址发送 POST 请求")
    
    # API 模式认证 (Phase 1 核心)
    api_key = models.CharField(max_length=64, unique=True, db_index=True)
    api_secret = models.CharField(max_length=64)
    is_api_enabled = models.BooleanField(default=True)
    api_ban_reason = models.CharField(max_length=32, blank=True, default='')
    api_ban_note = models.TextField(blank=True, default='')
    api_ban_media = models.FileField(upload_to='tenant_ban_evidences/', blank=True, null=True)
    api_banned_at = models.DateTimeField(blank=True, null=True)
    api_banned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='api_banned_tenants',
    )

    deleted_at = models.DateTimeField(blank=True, null=True)
    recoverable_until = models.DateTimeField(blank=True, null=True)
    deletion_requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='deleted_tenants',
    )
    
    enable_saas_dashboard = models.BooleanField(default=False)

    def __str__(self):
        return self.name

class SaaSUser(AbstractUser):
    """
    SaaS 内部的用户表
    Phase 2: Cast 将在这里拥有账号，登录后自行排班
    """
    ROLE_CHOICES = [
        ('ADMIN', '店铺管理员'),
        ('STAFF', '员工/Cast'),
        ('CONSUMER', 'A端用户'),
    ]
    tenant = models.ForeignKey(Tenant, related_name='users', on_delete=models.CASCADE, null=True, blank=True)
    role = models.CharField(choices=ROLE_CHOICES, default='STAFF')
    discord_id = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    
    class Meta:
        db_table = 'saas_users'


class SSOAuthCode(models.Model):
    code_hash = models.CharField(max_length=64, unique=True, db_index=True)
    client_id = models.CharField(max_length=128, db_index=True)
    redirect_uri = models.URLField(max_length=512)
    nonce = models.CharField(max_length=128, blank=True)
    user = models.ForeignKey(SaaSUser, on_delete=models.CASCADE, related_name='sso_codes')
    tenant = models.ForeignKey(Tenant, on_delete=models.SET_NULL, null=True, blank=True)
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'sso_auth_codes'
        ordering = ['-created_at']

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at


class StaffInvite(models.Model):
    ROLE_CHOICES = [
        ('STAFF', 'Staff'),
        ('ADMIN', 'Admin'),
    ]

    token = models.CharField(max_length=96, unique=True, db_index=True)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='staff_invites')
    role = models.CharField(max_length=16, choices=ROLE_CHOICES, default='STAFF')
    max_uses = models.PositiveIntegerField(default=1)
    used_count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        SaaSUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_staff_invites',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'staff_invites'
        ordering = ['-created_at']

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @property
    def is_available(self):
        return self.is_active and not self.is_expired and self.used_count < self.max_uses
