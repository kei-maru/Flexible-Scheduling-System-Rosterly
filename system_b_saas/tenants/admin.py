from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import Tenant, SaaSUser

@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'api_key', 'enable_saas_dashboard')
    search_fields = ('name',)
    # 自动生成 slug
    prepopulated_fields = {"slug": ("name",)}

@admin.register(SaaSUser)
class CustomSaaSUserAdmin(UserAdmin):
    list_display = UserAdmin.list_display + ('tenant', 'role')
    fieldsets = UserAdmin.fieldsets + (
        ("SaaS属性", {'fields': ('tenant', 'role')}),
    )

# SaaS后台标题
admin.site.site_header = "Reservation SaaS 管理中心"