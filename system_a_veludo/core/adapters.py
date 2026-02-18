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
        
        # [修改] 1. 保存 Discord 真实 ID (用户名) 而不是数字 ID
        # 优先获取 username (例如: keimaru)
        discord_handle = extra_data.get('username')
        discriminator = extra_data.get('discriminator')

        # 如果存在旧版后缀 (且不是 '0')，则拼接 (例如: keimaru#1234)
        if discord_handle and discriminator and discriminator != '0':
            discord_handle = f"{discord_handle}#{discriminator}"
        
        if discord_handle:
            user.discord_id = discord_handle
        else:
            #以此为备用，万一取不到用户名才存数字ID
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