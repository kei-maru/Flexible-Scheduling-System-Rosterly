from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.exceptions import ImmediateHttpResponse
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import redirect
from django.utils.text import slugify
from allauth.socialaccount.models import SocialAccount
import secrets
import logging

from tenants.models import Tenant


logger = logging.getLogger(__name__)


class SaaSDiscordSocialAdapter(DefaultSocialAccountAdapter):
    def _is_public_sso_flow(self, request) -> bool:
        if request is None:
            return False
        return bool(request.session.get("allow_public_sso_login"))

    def _is_first_owner_bootstrap(self) -> bool:
        User = get_user_model()
        bootstrap = not User.objects.filter(role="ADMIN", tenant__isnull=False).exists()
        logger.info("social-login bootstrap mode=%s", bootstrap)
        return bootstrap

    def _build_unique_tenant_slug(self, base_name: str) -> str:
        candidate = slugify(base_name)[:45] or "tenant"
        if not Tenant.objects.filter(slug=candidate).exists():
            return candidate

        suffix = 1
        while True:
            alt = f"{candidate[:40]}-{suffix}"
            if not Tenant.objects.filter(slug=alt).exists():
                return alt
            suffix += 1

    def _ensure_bootstrap_tenant(self):
        tenant = Tenant.objects.first()
        if tenant:
            return tenant

        tenant_name = getattr(settings, "DEFAULT_BOOTSTRAP_TENANT_NAME", "Veludo")
        tenant_slug = self._build_unique_tenant_slug(tenant_name)
        return Tenant.objects.create(
            name=tenant_name,
            slug=tenant_slug,
            api_key=secrets.token_urlsafe(24)[:32],
            api_secret=secrets.token_urlsafe(32),
            enable_saas_dashboard=True,
        )

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
        public_sso_flow = self._is_public_sso_flow(request)
        logger.info("pre_social_login: existing=%s", sociallogin.is_existing)
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
        authorized_user = None
        if discord_id_numeric:
            linked_social = SocialAccount.objects.filter(provider="discord", uid=str(discord_id_numeric)).select_related("user").first()
            if linked_social:
                authorized_user = linked_social.user

        if authorized_user is None:
            authorized_user = User.objects.filter(discord_id__in=candidates).first()
        if authorized_user:
            logger.info("pre_social_login: matched authorized user id=%s", authorized_user.id)
            sociallogin.connect(request, authorized_user)
            return

        if public_sso_flow:
            logger.info("pre_social_login: allowing public SSO user provisioning")
            return

        if self._is_first_owner_bootstrap():
            logger.info("pre_social_login: allowing bootstrap signup")
            return

        logger.warning("pre_social_login: denied, no authorized account candidates=%s", candidates)
        messages.error(request, "仅已授权员工/店长可登录，请联系店铺管理员开通账号。")
        raise ImmediateHttpResponse(redirect("dashboard_login"))

    def is_open_for_signup(self, request, sociallogin):
        # Allow signup in two cases:
        # 1) Public OAuth flow triggered by /sso/authorize (for System A end-users)
        # 2) First owner bootstrap for dashboard admin initialization
        allowed = self._is_public_sso_flow(request) or self._is_first_owner_bootstrap()
        logger.info("is_open_for_signup: allowed=%s", allowed)
        return allowed

    def save_user(self, request, sociallogin, form=None):
        public_sso_flow = self._is_public_sso_flow(request)
        is_first_bootstrap = self._is_first_owner_bootstrap()
        logger.info("save_user: first_bootstrap=%s public_sso_flow=%s", is_first_bootstrap, public_sso_flow)
        user = super().save_user(request, sociallogin, form=form)

        if is_first_bootstrap and not public_sso_flow:
            tenant = self._ensure_bootstrap_tenant()
            update_fields = []
            if user.tenant_id != tenant.id:
                user.tenant = tenant
                update_fields.append("tenant")
            if user.role != "ADMIN":
                user.role = "ADMIN"
                update_fields.append("role")
            if not user.is_staff:
                user.is_staff = True
                update_fields.append("is_staff")
            if user.is_superuser:
                user.is_superuser = False
                update_fields.append("is_superuser")

            if update_fields:
                user.save(update_fields=update_fields)
                logger.info("save_user: bootstrap user updated id=%s fields=%s", user.id, update_fields)
            messages.success(request, "首位管理员账号创建成功，已自动开通店铺。")

        if request is not None and request.session.get("allow_public_sso_login"):
            request.session.pop("allow_public_sso_login", None)

        return user
