from django.contrib.auth.models import AbstractUser
from django.db import models
import uuid

class Tenant(models.Model):
    """
    租户表 (例如: Veludo)
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True) 
    webhook_url = models.URLField(blank=True, null=True, help_text="预定成功后，系统会向此地址发送 POST 请求")
    
    # API 模式认证 (Phase 1 核心)
    api_key = models.CharField(max_length=64, unique=True, db_index=True)
    api_secret = models.CharField(max_length=64)
    
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
    ]
    tenant = models.ForeignKey(Tenant, related_name='users', on_delete=models.CASCADE, null=True, blank=True)
    role = models.CharField(choices=ROLE_CHOICES, default='STAFF')
    
    class Meta:
        db_table = 'saas_users'