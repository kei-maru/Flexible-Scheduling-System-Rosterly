from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import Tenant, SaaSUser, StaffInvite

@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'contact_email', 'booking_window_days', 'enable_saas_dashboard')
    search_fields = ('name', 'contact_email', 'slug')
    list_filter = ('enable_saas_dashboard',)
    # 自动生成 slug
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ('api_key', 'api_secret')
    fieldsets = (
        ("基本信息", {"fields": ("name", "slug", "contact_email", "logo", "webhook_url", "enable_saas_dashboard")}),
        ("预约设置", {"fields": ("booking_window_days", "store_contract_label", "store_contract_url", "required_customer_fields", "custom_terms_label", "custom_terms_body")}),
        ("API 凭据", {"fields": ("api_key", "api_secret")}),
    )

@admin.register(SaaSUser)
class CustomSaaSUserAdmin(UserAdmin):
    list_display = UserAdmin.list_display + ('tenant', 'role', 'discord_id')
    fieldsets = UserAdmin.fieldsets + (
        ("SaaS属性", {'fields': ('tenant', 'role', 'discord_id')}),
    )


@admin.register(StaffInvite)
class StaffInviteAdmin(admin.ModelAdmin):
    list_display = ('id', 'tenant', 'role', 'used_count', 'max_uses', 'is_active', 'expires_at', 'created_at')
    list_filter = ('tenant', 'role', 'is_active')
    search_fields = ('token', 'tenant__name')
    readonly_fields = ('token', 'created_at')

# SaaS后台标题
admin.site.site_header = "Reservation SaaS 管理中心"
