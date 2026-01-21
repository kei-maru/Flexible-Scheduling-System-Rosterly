from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, UserActivity

# 安全注销 User 模型，防止 AlreadyRegistered 错误
if admin.site.is_registered(User):
    admin.site.unregister(User)

@admin.register(User)
class CustomUserAdmin(UserAdmin):
    """
    自定义用户模型的后台管理配置
    """
    # 1. 列表页显示的字段
    list_display = ('username', 'vrc_id', 'email', 'discord_id', 'twitter_id', 'is_staff', 'is_cast')
    
    # 2. 详情编辑页的表单布局 (Fieldsets)
    # [修正] 我把你漏掉的 vrc_id 加进去了，方便你在后台编辑
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Veludo ユーザー情報', {'fields': ('avatar', 'vrc_id', 'discord_id', 'twitter_id', 'email')}),
        ('権限', {
            'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions'),
        }),
        ('日付', {'fields': ('last_login', 'date_joined')}),
        ('Cast 設定', {'fields': ('is_cast',)}),
    )
    
    # 3. 搜索功能支持的字段
    search_fields = ('username', 'vrc_id', 'email', 'discord_id', 'twitter_id')
    
    # 4. 排序
    ordering = ('username',)

# ==========================================
# [新增] 埋点数据后台查看
# ==========================================
@admin.register(UserActivity)
class UserActivityAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'user', 'action', 'target', 'get_ip')
    list_filter = ('action', 'timestamp')
    search_fields = ('user__username', 'target')
    readonly_fields = ('user', 'action', 'target', 'meta_data', 'timestamp') # 日志只读
    
    def get_ip(self, obj):
        return obj.meta_data.get('ip', '-')
    get_ip.short_description = "IP Address"