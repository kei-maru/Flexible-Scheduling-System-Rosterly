from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User

# 安全注销 User 模型，防止 AlreadyRegistered 错误
if admin.site.is_registered(User):
    admin.site.unregister(User)

@admin.register(User)
class CustomUserAdmin(UserAdmin):
    """
    自定义用户模型的后台管理配置
    """
    # 1. 列表页显示的字段
    list_display = ('username', 'vrc_id','email', 'discord_id', 'twitter_id', 'is_staff', 'is_cast')
    
    # 2. 详情编辑页的表单布局 (Fieldsets)
    # 我们移除了默认的 First name / Last name，替换为 Veludo 的自定义字段
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Veludo ユーザー情報', {'fields': ('avatar', 'discord_id', 'twitter_id', 'email')}),
        ('権限', {
            'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions'),
        }),
        ('日付', {'fields': ('last_login', 'date_joined')}),
        ('Cast 設定', {'fields': ('is_cast',)}),
    )
    
    # 3. 搜索功能支持的字段
    search_fields = ('username', 'vrc_id','email', 'discord_id', 'twitter_id')
    
    # 4. 排序
    ordering = ('username',)