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

from .models import SSOAuthCode
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


@require_GET
def sso_authorize(request):
    client_id = (request.GET.get('client_id') or '').strip()
    redirect_uri = (request.GET.get('redirect_uri') or '').strip()
    state = (request.GET.get('state') or '').strip()
    nonce = (request.GET.get('nonce') or '').strip()
    force_login = (request.GET.get('force_login') or '').strip() == '1'

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
        next_path = _drop_query_params(request.get_full_path(), {'force_login'})
        return redirect(f"/accounts/discord/login/?process=login&next={quote(next_path, safe='')}")

    if not request.user.is_authenticated:
        request.session['allow_public_sso_login'] = True
        next_path = _drop_query_params(request.get_full_path(), {'force_login'})
        return redirect(f"/accounts/discord/login/?process=login&next={quote(next_path, safe='')}")

    if not getattr(request.user, 'tenant_id', None) and getattr(request.user, 'role', None) == 'STAFF' and not request.user.is_superuser:
        request.user.role = 'CONSUMER'
        request.user.is_staff = False
        request.user.save(update_fields=['role', 'is_staff'])

    public_sso_flow = bool(request.session.get('allow_public_sso_login'))
    request.session.pop('allow_public_sso_login', None)

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
