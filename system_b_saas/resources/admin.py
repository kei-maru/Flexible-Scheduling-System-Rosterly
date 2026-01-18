from django.contrib import admin
from .models import Resource, Availability, RecurringPattern

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

@admin.register(RecurringPattern)
class RecurringPatternAdmin(admin.ModelAdmin):
    list_display = ('id', 'resource', 'day_of_week', 'start_time', 'end_time', 'valid_from', 'valid_until')
    list_filter = ('day_of_week', 'resource')
    ordering = ('resource', 'day_of_week', 'start_time')