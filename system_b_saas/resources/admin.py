from django.contrib import admin
from .models import Resource, Availability, RecurringPattern, EmailTemplate, ScheduleTemplate

class AvailabilityInline(admin.TabularInline):
    model = Availability
    extra = 3

@admin.register(Resource)
class ResourceAdmin(admin.ModelAdmin):
    list_display = ('name', 'tenant', 'external_id')
    list_filter = ('tenant',)
    inlines = [AvailabilityInline]

@admin.register(Availability)
class AvailabilityAdmin(admin.ModelAdmin):
    list_display = ('resource', 'start_time', 'end_time')
    list_filter = ('resource__tenant', 'resource')

@admin.register(EmailTemplate)
class EmailTemplateAdmin(admin.ModelAdmin):
    list_display = ('event_type', 'tenant', 'subject_template', 'is_active', 'updated_at')
    list_filter = ('tenant', 'event_type', 'is_active')
    search_fields = ('subject_template', 'tenant__name')
    
    # 这是一个小技巧：在后台显示只读的预览
    readonly_fields = ('preview_variables',)

    def preview_variables(self, obj):
        if obj.event_type == 'BOOKING_CONFIRMED':
            return "{{ customer_name }}, {{ resource_name }}, {{ start_time }}, {{ end_time }}"
        return "N/A"
    preview_variables.short_description = "可用变量参考"

@admin.register(RecurringPattern)
class RecurringPatternAdmin(admin.ModelAdmin):
    list_display = ('id', 'resource', 'day_of_week', 'start_time', 'end_time', 'valid_from', 'valid_until')
    list_filter = ('day_of_week', 'resource')
    ordering = ('resource', 'day_of_week', 'start_time')

@admin.register(ScheduleTemplate)
class ScheduleTemplateAdmin(admin.ModelAdmin):
    list_display = ('name', 'resource', 'created_at') # 列表页显示的字段
    list_filter = ('created_at',) # 侧边栏筛选
    search_fields = ('name', 'resource__name') # 搜索框
    ordering = ('-created_at',)