# core/adapters.py
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.core.files.base import ContentFile
import requests

class MySocialAccountAdapter(DefaultSocialAccountAdapter):
    def populate_user(self, request, sociallogin, data):
        """
        当用户通过 Discord 登录/注册时触发。
        """
        # 调用父类方法填充基本信息
        user = super().populate_user(request, sociallogin, data)

        user.email = ""
        sociallogin.email_addresses = []

        # 获取 Discord 返回的原始数据
        extra_data = sociallogin.account.extra_data
        
        # [修改] 1. 保存 Discord 稳定唯一 ID（长数字 uid）
        user.discord_id = extra_data.get('id')

        # 2. 自动下载并保存头像
        discord_id_num = extra_data.get('id') # 头像URL还是要用数字ID
        avatar_hash = extra_data.get('avatar')
        
        if discord_id_num and avatar_hash:
            avatar_url = f"https://cdn.discordapp.com/avatars/{discord_id_num}/{avatar_hash}.png"
            try:
                response = requests.get(avatar_url)
                if response.status_code == 200:
                    user.avatar.save(f"{user.username}_discord.png", ContentFile(response.content), save=False)
            except Exception as e:
                print(f"Failed to download discord avatar: {e}")

        return user