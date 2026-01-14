# casts/signals.py

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from .models import CastProfile
from utils.saas_client import SaaSClient
import threading

User = get_user_model()

@receiver(post_save, sender=User)
def auto_register_cast_on_saas(sender, instance, created, **kwargs):
    # 1. 只处理被标记为 Cast 的用户
    if not getattr(instance, 'is_cast', False):
        return

    print(f"SIGNAL: User {instance.username} saved. Checking status...")

    # 2. 获取或创建 Profile
    profile, profile_created = CastProfile.objects.get_or_create(user=instance)

    # 【新增功能】自动填充本地 Cast 名字
    # 如果 profile 里的 cast_name 是空的，就自动填入 username
    # 这样 Admin 列表里就不会显示 "-" 了
    need_save_local = False
    if not profile.name:
        profile.name = instance.username
        need_save_local = True
        print(f"SIGNAL: Auto-filling local cast_name with {instance.username}")

    # 3. 检查是否需要同步到 SaaS
    if not profile.saas_resource_id:
        print(f"SIGNAL: Syncing {profile.name} to SaaS...")
        
        # 准备数据
        user_id = instance.id
        cast_name = profile.name
        email = instance.email

        def sync_task():
            client = SaaSClient()
            saas_uuid = client.sync_cast_to_saas(user_id, cast_name, email)
            
            if saas_uuid:
                # 同步成功：更新 saas_resource_id
                # 注意：这里我们只更新 ID，名字如果刚才填了，需要一起更新
                if need_save_local:
                    CastProfile.objects.filter(id=profile.id).update(
                        saas_resource_id=saas_uuid,
                        name=cast_name # 把名字也顺便存进去
                    )
                else:
                    CastProfile.objects.filter(id=profile.id).update(saas_resource_id=saas_uuid)
                    
                print(f"SIGNAL: >>> SUCCESS! Linked {cast_name} to SaaS ID {saas_uuid}")
            else:
                print("SIGNAL: >>> FAILED to get ID from SaaS.")
                # 如果同步失败，但本地名字刚才改了，也得保存一下本地名字
                if need_save_local:
                    profile.save()

        threading.Thread(target=sync_task).start()
    
    # 如果不需要同步（已经有了ID），但刚才自动填了名字，就单独保存一下
    elif need_save_local:
        profile.save()