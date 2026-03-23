from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from django.db import transaction
from .models import CastProfile
from .source import sync_cast_profile_to_system_b
import traceback

User = get_user_model()

@receiver(post_save, sender=User)
def auto_register_cast_on_saas(sender, instance, created, **kwargs):
    """
    当 User 保存时触发。
    如果是 Cast，自动创建/更新 Profile，并同步到 SaaS (System B)。
    """
    # 1. 检查是否为 Cast
    if not getattr(instance, 'is_cast', False):
        return

    print(f"SIGNAL: User {instance.username} is a CAST. Processing sync...")

    # 2. 定义同步任务 (核心逻辑)
    def do_sync():
        try:
            # A. 获取或创建本地 Profile
            profile, _ = CastProfile.objects.get_or_create(user=instance)
            
            # 如果本地名字为空，先填上
            if not profile.name:
                profile.name = instance.username
                profile.save(update_fields=['name'])

            # B. 准备数据
            user_id = instance.id
            cast_name = profile.name

            print(f"SIGNAL: Syncing {cast_name} (ID: {user_id}) to SaaS...")

            # C. 调用 System B 接口
            # 注意：这里我们总是尝试同步，确保 System B 那边的数据是最新的
            saas_uuid = sync_cast_profile_to_system_b(profile)

            if saas_uuid:
                print(f"SIGNAL: >>> SUCCESS! SaaS ID: {saas_uuid}")
            else:
                print("SIGNAL: >>> FAILED. No ID returned from SaaS.")

        except Exception as e:
            print(f"SIGNAL ERROR: {e}")
            traceback.print_exc()

    # 3. [关键修复] 确保在数据库事务提交后再执行同步
    # 这样可以避免 "在新线程里查不到刚刚创建的 Profile" 的问题
    transaction.on_commit(do_sync)
