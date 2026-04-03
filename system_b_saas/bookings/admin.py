from django.contrib import admin
from .models import Booking, BookingReport

@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = (
        'customer_name',
        'resource',
        'selected_service_name',
        'start_time',
        'status',
        'customer_report_count',
        'cast_report_count',
        'last_reported_at',
    )
    list_filter = ('status', 'tenant')
    search_fields = ('customer_email', 'customer_name', 'selected_service_name')


@admin.register(BookingReport)
class BookingReportAdmin(admin.ModelAdmin):
    list_display = (
        'booking',
        'tenant',
        'reporter_role',
        'reason',
        'reporter_name',
        'is_read_by_admin',
        'created_at',
    )
    list_filter = ('tenant', 'reporter_role', 'reason', 'is_read_by_admin')
    search_fields = ('booking__customer_name', 'booking__customer_email', 'detail', 'reporter_name')
