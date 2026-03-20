from django.contrib import admin
from .models import Booking

@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ('customer_name', 'resource', 'selected_service_name', 'start_time', 'status')
    list_filter = ('status', 'tenant')
    search_fields = ('customer_email', 'customer_name', 'selected_service_name')
