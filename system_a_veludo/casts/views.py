# casts/views.py

from django.shortcuts import render, redirect, get_object_or_404
from django.views.generic import ListView
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.contrib.auth import get_user_model
from django.contrib import messages

# 引入模型
from .models import CastProfile, CastMedia
from .source import get_public_casts, sync_cast_profile_to_system_b
# 引入表单 (注意：确保你的 forms.py 在正确的位置，通常在 accounts 或 casts 下)
# 假设你的 CastProfileForm 和 CastMediaFormSet 定义在 accounts.forms 或 casts.forms
# 如果在 accounts.forms:
from accounts.forms import CastProfileForm, CastMediaFormSet 

User = get_user_model()

# --- 1. 前台列表视图 ---
class CastListView(ListView):
    model = CastProfile
    template_name = 'cast_list.html' 
    context_object_name = 'casts'

    def get_queryset(self):
        return get_public_casts()

# --- 2. 编辑个人资料视图 (从 accounts 移过来的) ---
@login_required
def edit_cast_profile(request, user_id):
    # 1. 获取目标用户
    target_user = get_object_or_404(User, id=user_id)
    
    # 2. 权限检查
    if not request.user.is_staff and request.user.id != target_user.id:
        raise PermissionDenied("You do not have permission to edit this profile.")

    # 3. 获取或创建 Profile
    profile, created = CastProfile.objects.get_or_create(user=target_user)

    if request.method == 'POST':
        form = CastProfileForm(request.POST, request.FILES, instance=profile)
        formset = CastMediaFormSet(request.POST, request.FILES, instance=profile)

        if form.is_valid() and formset.is_valid():
            profile = form.save()
            
            # 处理 Media 数据
            instances = formset.save(commit=False)
            for obj in instances:
                obj.media_type = 'IMAGE'
                obj.save()
            for obj in formset.deleted_objects:
                obj.delete()

            # 保存后立刻同步到 System B
            saas_id = sync_cast_profile_to_system_b(profile)
            if not saas_id:
                messages.warning(request, 'プロフィールは保存済みですが、System B 同期に失敗しました。')
                
            messages.success(request, f'{target_user.username} のプロフィールを更新しました！')
            return redirect('edit_cast_profile', user_id=user_id)
        else:
            # [调试关键] 打印错误到控制台，方便你修复 Bug
            print("Form Errors:", form.errors)
            print("Formset Errors:", formset.errors)
            # [修改] 不再发送前端错误提示，或者发送更具体的
            # messages.error(request, '入力内容を確認してください。') 
            pass
    else:
        form = CastProfileForm(instance=profile)
        # 按 order 排序显示图片
        formset = CastMediaFormSet(instance=profile, queryset=profile.medias.all().order_by('order'))

    # 获取预览用的图片列表
    slider_images = profile.medias.all().order_by('order')
    
    # [新逻辑] 直接从 Profile 模型获取 YouTube 链接
    first_youtube_url = profile.youtube_url

    context = {
        'form': form,
        'formset': formset,
        'profile': profile,
        'slider_images': slider_images,
        'first_youtube_url': first_youtube_url, 
        'target_user': target_user 
    }
    return render(request, 'cast/edit_profile.html', context)
