import hashlib
import json
import secrets
from datetime import timedelta
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

from django.conf import settings
from django.contrib.auth import logout
from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from allauth.socialaccount.models import SocialAccount
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .models import SSOAuthCode, Tenant, SaaSUser
from .permissions import IsTenantAuthorized
import logging


logger = logging.getLogger(__name__)


def _hash_code(raw_code: str) -> str:
    return hashlib.sha256(raw_code.encode('utf-8')).hexdigest()


def _append_query_params(url: str, extra_params: dict) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update(extra_params)
    return urlunparse(parsed._replace(query=urlencode(query)))


def _drop_query_params(url: str, keys: set) -> str:
    parsed = urlparse(url)
    query_items = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k not in keys]
    return urlunparse(parsed._replace(query=urlencode(query_items)))


def _get_client_conf(client_id: str):
    clients = getattr(settings, 'SYSTEM_B_SSO_CLIENTS', {}) or {}
    client_conf = clients.get(client_id)
    if not isinstance(client_conf, dict):
        return None
    redirect_uris = client_conf.get('redirect_uris') or []
    if not isinstance(redirect_uris, list):
        return None
    return {
        'client_secret': str(client_conf.get('client_secret') or ''),
        'redirect_uris': [str(uri) for uri in redirect_uris],
    }


def _should_preserve_staff_or_admin(user, role_hint: str) -> bool:
    if role_hint != 'CONSUMER' or user is None:
        return False
    if getattr(user, 'is_superuser', False):
        return True
    current_role = (getattr(user, 'role', '') or '').strip().upper()
    if current_role == 'ADMIN':
        return True
    if current_role != 'STAFF' or not getattr(user, 'tenant_id', None):
        return False
    try:
        from resources.models import Resource
    except Exception:
        return False
    return (
        Resource.objects.filter(tenant_id=user.tenant_id, linked_user=user).exists()
        or Resource.objects.filter(tenant_id=user.tenant_id, external_id=str(user.id)).exists()
    )


def _public_sso_tenant_slug() -> str:
    return str(getattr(settings, 'SYSTEM_B_PUBLIC_SSO_TENANT_SLUG', 'Veludo') or 'Veludo').strip()


def _public_sso_tenant_name() -> str:
    return str(
        getattr(settings, 'SYSTEM_B_PUBLIC_SSO_TENANT_NAME', 'VR ASMR Salon Veludo')
        or 'VR ASMR Salon Veludo'
    ).strip()


def _resolve_public_sso_tenant():
    slug = _public_sso_tenant_slug()
    tenant = Tenant.objects.filter(slug=slug).first() or Tenant.objects.filter(slug__iexact=slug).first()
    if tenant:
        return tenant

    tenant_name = _public_sso_tenant_name()
    return (
        Tenant.objects.filter(name=tenant_name).first()
        or Tenant.objects.filter(name__iexact=tenant_name).first()
    )


@require_GET
def sso_authorize(request):
    client_id = (request.GET.get('client_id') or '').strip()
    redirect_uri = (request.GET.get('redirect_uri') or '').strip()
    state = (request.GET.get('state') or '').strip()
    nonce = (request.GET.get('nonce') or '').strip()
    force_login = (request.GET.get('force_login') or '').strip() == '1'
    role_hint = (request.GET.get('a_role') or '').strip().upper()
    if role_hint not in {'ADMIN', 'STAFF', 'CONSUMER'}:
        role_hint = 'CONSUMER'

    if not client_id or not redirect_uri or not state or not nonce:
        return JsonResponse({'error': 'invalid_request'}, status=400)

    client_conf = _get_client_conf(client_id)
    if not client_conf:
        logger.warning('SSO authorize rejected: unknown client_id=%s', client_id)
        return JsonResponse({'error': 'unauthorized_client'}, status=400)

    if redirect_uri not in client_conf['redirect_uris']:
        logger.warning('SSO authorize rejected: redirect_uri mismatch for client_id=%s', client_id)
        return JsonResponse({'error': 'invalid_redirect_uri'}, status=400)

    if force_login and request.user.is_authenticated:
        logout(request)
        request.session['allow_public_sso_login'] = True
        request.session['sso_role_hint'] = role_hint
        next_path = _drop_query_params(request.get_full_path(), {'force_login'})
        return redirect(f"/accounts/discord/login/?process=login&next={quote(next_path, safe='')}")

    if not request.user.is_authenticated:
        request.session['allow_public_sso_login'] = True
        request.session['sso_role_hint'] = role_hint
        next_path = _drop_query_params(request.get_full_path(), {'force_login'})
        return redirect(f"/accounts/discord/login/?process=login&next={quote(next_path, safe='')}")

    if role_hint == 'CONSUMER' and not _should_preserve_staff_or_admin(request.user, role_hint):
        update_fields = []
        if request.user.role != 'CONSUMER':
            request.user.role = 'CONSUMER'
            update_fields.append('role')
        if request.user.is_staff:
            request.user.is_staff = False
            update_fields.append('is_staff')
        if getattr(request.user, 'tenant_id', None) is not None:
            request.user.tenant = None
            update_fields.append('tenant')
        if update_fields:
            request.user.save(update_fields=update_fields)
    elif role_hint == 'CONSUMER':
        current_role = (getattr(request.user, 'role', '') or '').strip().upper()
        if current_role in {'ADMIN', 'STAFF'} and not getattr(request.user, 'tenant_id', None):
            public_tenant = _resolve_public_sso_tenant()
            if public_tenant:
                request.user.tenant = public_tenant
                request.user.save(update_fields=['tenant'])
                logger.info(
                    'SSO authorize: repaired missing tenant for privileged user id=%s role=%s tenant_id=%s',
                    request.user.id,
                    current_role,
                    public_tenant.id,
                )
            else:
                logger.warning(
                    'SSO authorize: privileged user id=%s role=%s has no tenant and public tenant not found',
                    request.user.id,
                    current_role,
                )
        logger.info(
            'SSO authorize: preserving privileged role for user id=%s role=%s tenant_id=%s',
            request.user.id,
            getattr(request.user, 'role', ''),
            getattr(request.user, 'tenant_id', None),
        )

    public_sso_flow = bool(request.session.get('allow_public_sso_login'))
    request.session.pop('allow_public_sso_login', None)
    request.session.pop('sso_role_hint', None)

    raw_code = secrets.token_urlsafe(32)
    SSOAuthCode.objects.create(
        code_hash=_hash_code(raw_code),
        client_id=client_id,
        redirect_uri=redirect_uri,
        nonce=nonce,
        user=request.user,
        tenant=getattr(request.user, 'tenant', None),
        expires_at=timezone.now() + timedelta(seconds=getattr(settings, 'SYSTEM_B_SSO_CODE_TTL_SECONDS', 60)),
    )

    callback_url = _append_query_params(redirect_uri, {'code': raw_code, 'state': state})

    logout(request)

    return redirect(callback_url)


@require_POST
@csrf_exempt
def sso_exchange(request):
    payload = {}
    if request.content_type and 'application/json' in request.content_type:
        try:
            payload = json.loads(request.body.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse({'error': 'invalid_request'}, status=400)
    else:
        payload = request.POST

    code = (payload.get('code') or '').strip()
    client_id = (payload.get('client_id') or '').strip()
    client_secret = (payload.get('client_secret') or '').strip()
    redirect_uri = (payload.get('redirect_uri') or '').strip()

    if not code or not client_id or not client_secret or not redirect_uri:
        return JsonResponse({'error': 'invalid_request'}, status=400)

    client_conf = _get_client_conf(client_id)
    if not client_conf:
        logger.warning('SSO exchange rejected: unknown client_id=%s', client_id)
        return JsonResponse({'error': 'invalid_client'}, status=401)

    if client_secret != client_conf['client_secret']:
        logger.warning('SSO exchange rejected: bad secret for client_id=%s', client_id)
        return JsonResponse({'error': 'invalid_client'}, status=401)

    if redirect_uri not in client_conf['redirect_uris']:
        logger.warning('SSO exchange rejected: redirect_uri mismatch for client_id=%s', client_id)
        return JsonResponse({'error': 'invalid_grant'}, status=400)

    code_hash = _hash_code(code)
    auth_code = SSOAuthCode.objects.filter(
        code_hash=code_hash,
        client_id=client_id,
        redirect_uri=redirect_uri,
    ).select_related('user').first()

    if not auth_code:
        logger.warning('SSO exchange rejected: code not found')
        return JsonResponse({'error': 'invalid_grant'}, status=400)

    if auth_code.used_at is not None:
        logger.warning('SSO exchange rejected: code already used, id=%s', auth_code.id)
        return JsonResponse({'error': 'invalid_grant'}, status=400)

    if auth_code.is_expired:
        logger.warning('SSO exchange rejected: code expired, id=%s', auth_code.id)
        return JsonResponse({'error': 'invalid_grant'}, status=400)

    auth_code.used_at = timezone.now()
    auth_code.save(update_fields=['used_at'])

    user = auth_code.user
    discord_social = SocialAccount.objects.filter(user=user, provider='discord').only('uid').first()
    response_data = {
        'user_id': str(user.id),
        'discord_id': user.discord_id,
        'discord_uid': str(discord_social.uid) if discord_social and discord_social.uid else None,
        'username': user.username,
        'tenant_id': str(user.tenant_id) if user.tenant_id else None,
        'role': user.role,
        'nonce': auth_code.nonce,
        'exp': int(auth_code.expires_at.timestamp()),
    }
    return JsonResponse(response_data)


class IntegrationIdentityView(APIView):
    permission_classes = [IsTenantAuthorized]

    def _resolve_target_user(self, request, user_id: str):
        user = request.tenant.users.filter(id=user_id).first()
        if user is None:
            # Allow A-side CONSUMER users that may be tenant-less.
            user = SaaSUser.objects.filter(id=user_id, tenant__isnull=True).first()
        return user

    def _serialize_user(self, user):
        discord_social = SocialAccount.objects.filter(user=user, provider='discord').only('uid').first()
        return {
            'user_id': str(user.id),
            'username': user.username,
            'discord_id': user.discord_id,
            'discord_uid': str(discord_social.uid) if discord_social and discord_social.uid else None,
            'tenant_id': str(user.tenant_id) if user.tenant_id else None,
            'role': user.role,
            'is_staff': bool(user.is_staff),
            'is_superuser': bool(user.is_superuser),
        }

    def get(self, request):
        user_id = str(request.query_params.get('user_id') or '').strip()
        if not user_id:
            return Response({'error': 'user_id is required'}, status=status.HTTP_400_BAD_REQUEST)

        user = self._resolve_target_user(request, user_id)
        if user is None:
            return Response({'error': 'not_found'}, status=status.HTTP_404_NOT_FOUND)

        return Response(self._serialize_user(user), status=status.HTTP_200_OK)

    def patch(self, request):
        user_id = str(request.data.get('user_id') or '').strip()
        desired_role = str(request.data.get('role') or '').strip().upper()
        if not user_id or desired_role not in {'ADMIN', 'STAFF', 'CONSUMER'}:
            return Response({'error': 'invalid_request'}, status=status.HTTP_400_BAD_REQUEST)

        user = self._resolve_target_user(request, user_id)
        if user is None:
            return Response({'error': 'not_found'}, status=status.HTTP_404_NOT_FOUND)

        if user.tenant_id and user.tenant_id != request.tenant.id:
            return Response({'error': 'cross_tenant_forbidden'}, status=status.HTTP_403_FORBIDDEN)

        update_fields = []
        if user.role != desired_role:
            user.role = desired_role
            update_fields.append('role')

        if desired_role in {'ADMIN', 'STAFF'}:
            if user.tenant_id != request.tenant.id:
                user.tenant = request.tenant
                update_fields.append('tenant')
            if not user.is_staff:
                user.is_staff = True
                update_fields.append('is_staff')
            if not user.is_active:
                user.is_active = True
                update_fields.append('is_active')
            if desired_role == 'STAFF' and user.is_superuser:
                user.is_superuser = False
                update_fields.append('is_superuser')
        else:
            if user.tenant_id is not None:
                user.tenant = None
                update_fields.append('tenant')
            if user.is_staff:
                user.is_staff = False
                update_fields.append('is_staff')
            if user.is_superuser:
                user.is_superuser = False
                update_fields.append('is_superuser')

        if update_fields:
            user.save(update_fields=update_fields)

        return Response(self._serialize_user(user), status=status.HTTP_200_OK)
