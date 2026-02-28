from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.exceptions import ImmediateHttpResponse
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import redirect


class SaaSDiscordSocialAdapter(DefaultSocialAccountAdapter):
    def _build_unique_username(self, base_username: str, discord_numeric_id: str) -> str:
        User = get_user_model()
        candidate = (base_username or "discord_user").strip().lower().replace(" ", "_")
        if not candidate:
            candidate = "discord_user"
        candidate = candidate[:120]

        if not User.objects.filter(username=candidate).exists():
            return candidate

        suffix = (discord_numeric_id or "0000")[-6:]
        fallback = f"{candidate}_{suffix}"[:150]
        if not User.objects.filter(username=fallback).exists():
            return fallback

        i = 1
        while True:
            alt = f"{fallback}_{i}"[:150]
            if not User.objects.filter(username=alt).exists():
                return alt
            i += 1

    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        extra_data = sociallogin.account.extra_data

        discord_username = extra_data.get("username") or data.get("username")
        discord_id_numeric = extra_data.get("id", "")
        discriminator = extra_data.get("discriminator")

        user.username = self._build_unique_username(discord_username, discord_id_numeric)
        user.email = ""

        if discord_username and discriminator and discriminator != "0":
            user.discord_id = f"{discord_username}#{discriminator}"
        elif discord_username:
            user.discord_id = discord_username
        else:
            user.discord_id = discord_id_numeric

        return user

    def pre_social_login(self, request, sociallogin):
        # Existing social account: standard allauth flow.
        if sociallogin.is_existing:
            return

        extra_data = sociallogin.account.extra_data or {}
        discord_username = extra_data.get("username")
        discord_id_numeric = extra_data.get("id", "")
        discriminator = extra_data.get("discriminator")

        candidates = []
        if discord_username and discriminator and discriminator != "0":
            candidates.append(f"{discord_username}#{discriminator}")
        if discord_username:
            candidates.append(discord_username)
        if discord_id_numeric:
            candidates.append(discord_id_numeric)

        User = get_user_model()
        authorized_user = User.objects.filter(discord_id__in=candidates).first()
        if authorized_user:
            sociallogin.connect(request, authorized_user)
            return

        messages.error(request, "仅已授权员工/店长可登录，请联系店铺管理员开通账号。")
        raise ImmediateHttpResponse(redirect("dashboard_login"))

    def is_open_for_signup(self, request, sociallogin):
        # Dashboard login is restricted to pre-authorized staff/owners.
        return False
