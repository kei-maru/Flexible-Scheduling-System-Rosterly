# casts/admin.py

from django.contrib import admin
from .models import CastProfile, CastMedia

# 1. CastMedia 只负责图片，所以 Inline 要简化
class CastMediaInline(admin.TabularInline):
    model = CastMedia
    extra = 1
    # [修正] 移除了 youtube_url，只保留图片和排序
    fields = ('image_file', 'order') 
    # image_file 是必填的，order 有默认值

@admin.register(CastProfile)
class CastProfileAdmin(admin.ModelAdmin):
    # 列表页显示的字段
    list_display = ('id', 'display_order', 'name', 'user', 'is_active')
    
    # [关键功能] 允许管理员在列表页直接修改顺序，不用点进去
    list_editable = ('display_order', 'is_active')
    
    # 点击 ID 或 Name 进入编辑
    list_display_links = ('id', 'name')
    
    ordering = ('display_order', 'id')
    search_fields = ('name', 'user__username')
    
    # 编辑详情页的字段布局
    # [修正] 加上了 youtube_url
    fields = ('user', 'name', 'intro', 'tags', 'youtube_url', 'avatar', 'display_order', 'saas_resource_id', 'is_active')
    
    inlines = [CastMediaInline]