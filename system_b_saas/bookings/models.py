from django.db import models
import uuid
from tenants.models import Tenant
from resources.models import Resource

class Booking(models.Model):
    """订单表"""
    STATUS_CHOICES = [('PENDING', '待确认'), ('CONFIRMED', '已确认'), ('CANCELLED', '已取消')]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    resource = models.ForeignKey(Resource, related_name='bookings', on_delete=models.CASCADE)
    
    customer_id = models.CharField(max_length=100, db_index=True, null=True, blank=True)
    customer_discord_id = models.CharField(max_length=100, db_index=True, null=True, blank=True)
    customer_email = models.EmailField()
    customer_name = models.CharField(max_length=100)
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
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Booking {self.id} - {self.customer_name}"
