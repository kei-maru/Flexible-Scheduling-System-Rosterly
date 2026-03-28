from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.exceptions import ImmediateHttpResponse
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import redirect
from django.utils.text import slugify
from django.db import transaction
from allauth.socialaccount.models import SocialAccount
import secrets
import hashlib
import logging

from tenants.models import Tenant, StaffInvite
from resources.services.binding_service import ensure_staff_resource_binding


logger = logging.getLogger(__name__)


class SaaSDiscordSocialAdapter(DefaultSocialAccountAdapter):
    def _ensure_staff_resource_binding(self, user, allow_create=True):
        ensure_staff_resource_binding(user, allow_create=allow_create)

    def _is_public_sso_flow(self, request) -> bool:
        if request is None:
            return False
        return bool(request.session.get("allow_public_sso_login"))

    def _sso_role_hint(self, request) -> str:
        if request is None:
            return "CONSUMER"
        role_hint = str(request.session.get("sso_role_hint") or "").strip().upper()
        if role_hint not in {"ADMIN", "STAFF", "CONSUMER"}:
            return "CONSUMER"
        return role_hint

    def _is_shop_signup_flow(self, request) -> bool:
        if request is None:
            return False
        return bool(request.session.get("allow_shop_signup"))

    def _is_shop_signup_intent(self, request) -> bool:
        if request is None:
            return False
        next_hint = str(request.GET.get("next") or request.POST.get("next") or "").strip()
        if not next_hint:
            return False
        return next_hint.startswith("/dashboard/register-shop/form/")

    def _invite_token(self, request) -> str:
        if request is None:
            return ""
        return str(request.session.get("allow_staff_invite_token") or "").strip()

    def _resolve_staff_invite(self, request):
        token = self._invite_token(request)
        if not token:
            return None
        invite = StaffInvite.objects.filter(token=token).select_related("tenant").first()
        if not invite or not invite.is_available:
            if request is not None:
                request.session.pop("allow_staff_invite_token", None)
                request.session.pop("allow_public_sso_login", None)
                request.session.pop("sso_role_hint", None)
            return None
        return invite

    def _public_sso_tenant_slug(self) -> str:
        return str(getattr(settings, "SYSTEM_B_PUBLIC_SSO_TENANT_SLUG", "Veludo") or "Veludo").strip()

    def _public_sso_tenant_name(self) -> str:
        return str(
            getattr(settings, "SYSTEM_B_PUBLIC_SSO_TENANT_NAME", "VR ASMR Salon Veludo")
            or "VR ASMR Salon Veludo"
        ).strip()

    def _resolve_public_sso_tenant(self):
        slug = self._public_sso_tenant_slug()
        tenant = Tenant.objects.filter(slug=slug).first()
        if tenant:
            return tenant
        tenant = Tenant.objects.filter(slug__iexact=slug).first()
        if tenant:
            return tenant

        tenant_name = self._public_sso_tenant_name()
        tenant = Tenant.objects.filter(name=tenant_name).first()
        if tenant:
            return tenant
        return Tenant.objects.filter(name__iexact=tenant_name).first()

    def _sync_public_sso_role(self, user, role_hint: str):
        if user is None or user.is_superuser:
            return

        public_tenant = self._resolve_public_sso_tenant()
        desired_role = role_hint if role_hint in {"ADMIN", "STAFF"} else "CONSUMER"

        desired_is_staff = desired_role in {"ADMIN", "STAFF"}
        update_fields = []

        if desired_role == "CONSUMER":
            if user.tenant_id is not None:
                user.tenant = None
                update_fields.append("tenant")
        elif not user.tenant_id and public_tenant:
            user.tenant = public_tenant
            update_fields.append("tenant")
        elif not user.tenant_id and not public_tenant:
            logger.warning(
                "public sso role sync: desired role=%s but no public tenant resolved (slug=%s, name=%s)",
                desired_role,
                self._public_sso_tenant_slug(),
                self._public_sso_tenant_name(),
            )

        if user.role != desired_role:
            user.role = desired_role
            update_fields.append("role")
        if user.is_staff != desired_is_staff:
            user.is_staff = desired_is_staff
            update_fields.append("is_staff")
        if desired_is_staff and not user.is_active:
            user.is_active = True
            update_fields.append("is_active")

        if update_fields:
            user.save(update_fields=update_fields)
            logger.info("public sso role sync user id=%s fields=%s", user.id, update_fields)
        if desired_role in {"STAFF", "ADMIN"}:
            # Public SSO should not create new resources automatically.
            # It may only bind existing canonical resources to avoid garbage rows.
            self._ensure_staff_resource_binding(user, allow_create=False)

    def _is_first_owner_bootstrap(self) -> bool:
        User = get_user_model()
        bootstrap = not User.objects.filter(role="ADMIN", tenant__isnull=False).exists()
        logger.info("social-login bootstrap mode=%s", bootstrap)
        return bootstrap

    def _build_unique_tenant_slug(self, base_name: str) -> str:
        base_text = str(base_name or "").strip()
        candidate = slugify(base_text)[:45]
        if not candidate:
            digest_seed = base_text or secrets.token_urlsafe(8)
            digest = hashlib.sha1(digest_seed.encode("utf-8")).hexdigest()[:8]
            candidate = f"tenant-{digest}"

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

    def _create_tenant_for_shop_signup(self, user):
        base_name = (getattr(user, "username", "") or "").strip()
        if not base_name:
            base_name = (getattr(user, "discord_id", "") or "").strip() or "Rosterly"
        tenant_name = f"{base_name[:50]} Shop"
        tenant_slug = self._build_unique_tenant_slug(tenant_name)
        return Tenant.objects.create(
            name=tenant_name,
            slug=tenant_slug,
            api_key=secrets.token_urlsafe(24)[:32],
            api_secret=secrets.token_urlsafe(32),
            enable_saas_dashboard=True,
        )

    def _promote_user_to_shop_owner(self, request, user):
        if user is None:
            return

        public_tenant = self._resolve_public_sso_tenant()
        is_public_consumer = (
            public_tenant is not None
            and user.tenant_id == public_tenant.id
            and getattr(user, "role", "CONSUMER") == "CONSUMER"
        )

        if user.tenant_id and not is_public_consumer:
            if request is not None:
                messages.info(request, "このアカウントは既に店舗に紐付いています。再登録は不要です。")
            return

        tenant = self._create_tenant_for_shop_signup(user)
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
        if request is not None:
            messages.success(request, "店舗登録が完了しました。ADMIN 権限を付与しました。")

    def _apply_staff_invite(self, request, user) -> bool:
        invite = self._resolve_staff_invite(request)
        if not invite or user is None:
            return False

        with transaction.atomic():
            locked_invite = StaffInvite.objects.select_for_update().select_related("tenant").filter(id=invite.id).first()
            if not locked_invite or not locked_invite.is_available:
                if request is not None:
                    request.session.pop("allow_staff_invite_token", None)
                    request.session.pop("allow_public_sso_login", None)
                    request.session.pop("sso_role_hint", None)
                return False

            if user.tenant_id and user.tenant_id != locked_invite.tenant_id and getattr(user, "role", "") in {"ADMIN", "STAFF"}:
                messages.error(request, "このアカウントは別店舗に所属しています。別アカウントで招待リンクをご利用ください。")
                raise ImmediateHttpResponse(redirect("dashboard_login"))

            desired_role = locked_invite.role if locked_invite.role in {"ADMIN", "STAFF"} else "STAFF"
            update_fields = []

            if user.tenant_id != locked_invite.tenant_id:
                user.tenant = locked_invite.tenant
                update_fields.append("tenant")
            if user.role != desired_role:
                user.role = desired_role
                update_fields.append("role")
            if user.is_staff != (desired_role in {"ADMIN", "STAFF"}):
                user.is_staff = desired_role in {"ADMIN", "STAFF"}
                update_fields.append("is_staff")
            if desired_role in {"ADMIN", "STAFF"} and not user.is_active:
                user.is_active = True
                update_fields.append("is_active")

            if update_fields:
                user.save(update_fields=update_fields)

            locked_invite.used_count += 1
            if locked_invite.used_count >= locked_invite.max_uses:
                locked_invite.is_active = False
            locked_invite.save(update_fields=["used_count", "is_active"])

        if request is not None:
            request.session.pop("allow_staff_invite_token", None)
            request.session.pop("allow_public_sso_login", None)
            request.session.pop("sso_role_hint", None)

        if desired_role in {"STAFF", "ADMIN"}:
            self._ensure_staff_resource_binding(user, allow_create=True)

        return True

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
        role_hint = self._sso_role_hint(request)
        shop_signup_flow = self._is_shop_signup_flow(request)
        invite = self._resolve_staff_invite(request)
        logger.info("pre_social_login: existing=%s", sociallogin.is_existing)
        # Existing social account: standard allauth flow.
        if sociallogin.is_existing:
            if invite and self._apply_staff_invite(request, sociallogin.user):
                return
            if public_sso_flow:
                self._sync_public_sso_role(sociallogin.user, role_hint)
            return

        extra_data = sociallogin.account.extra_data or {}
        discord_id_numeric = extra_data.get("id", "")

        authorized_user = None
        if discord_id_numeric:
            linked_social = SocialAccount.objects.filter(provider="discord", uid=str(discord_id_numeric)).select_related("user").first()
            if linked_social:
                authorized_user = linked_social.user
        if authorized_user:
            logger.info("pre_social_login: matched authorized user id=%s", authorized_user.id)
            if invite and self._apply_staff_invite(request, authorized_user):
                sociallogin.connect(request, authorized_user)
                return
            if public_sso_flow:
                self._sync_public_sso_role(authorized_user, role_hint)
            sociallogin.connect(request, authorized_user)
            return

        if public_sso_flow:
            logger.info("pre_social_login: allowing public SSO user provisioning")
            return

        if invite:
            logger.info("pre_social_login: allowing invite signup provisioning")
            return

        if shop_signup_flow:
            logger.info("pre_social_login: allowing shop signup provisioning")
            return

        if self._is_first_owner_bootstrap():
            logger.info("pre_social_login: allowing bootstrap signup")
            return

        logger.warning("pre_social_login: denied, no authorized account")
        if request is not None:
            try:
                request.session["dashboard_auth_blocked_message"] = "このアカウントにはスタッフ/管理者権限がありません。"
            except Exception:
                pass
        raise ImmediateHttpResponse(redirect("dashboard_login"))

    def is_open_for_signup(self, request, sociallogin):
        # Allow signup in two cases:
        # 1) Public OAuth flow triggered by /sso/authorize (for System A end-users)
        # 2) Explicit shop-signup flow from dashboard register entry
        # 3) First owner bootstrap for dashboard admin initialization
        allowed = (
            self._is_public_sso_flow(request)
            or self._is_shop_signup_flow(request)
            or self._is_shop_signup_intent(request)
            or bool(self._resolve_staff_invite(request))
            or self._is_first_owner_bootstrap()
        )
        logger.info("is_open_for_signup: allowed=%s", allowed)
        return allowed

    def save_user(self, request, sociallogin, form=None):
        public_sso_flow = self._is_public_sso_flow(request)
        shop_signup_flow = self._is_shop_signup_flow(request)
        role_hint = self._sso_role_hint(request)
        is_first_bootstrap = self._is_first_owner_bootstrap()
        logger.info(
            "save_user: first_bootstrap=%s public_sso_flow=%s shop_signup_flow=%s",
            is_first_bootstrap,
            public_sso_flow,
            shop_signup_flow,
        )
        user = super().save_user(request, sociallogin, form=form)

        if public_sso_flow:
            self._sync_public_sso_role(user, role_hint)

        invite_applied = self._apply_staff_invite(request, user)

        if shop_signup_flow and not public_sso_flow:
            if request is not None:
                request.session["shop_signup_provisional_user_id"] = user.id
        elif is_first_bootstrap and not public_sso_flow:
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
            messages.success(request, "初回管理者アカウントの作成が完了し、店舗を自動開設しました。")

        if user.role in {"STAFF", "ADMIN"}:
            self._ensure_staff_resource_binding(user, allow_create=not public_sso_flow)

        if request is not None and request.session.get("allow_public_sso_login"):
            request.session.pop("allow_public_sso_login", None)
        if request is not None and request.session.get("allow_shop_signup") and not shop_signup_flow:
            request.session.pop("allow_shop_signup", None)

        if invite_applied and request is not None:
            request.session.pop("allow_staff_invite_token", None)

        return user
