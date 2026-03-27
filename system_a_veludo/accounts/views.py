# accounts/views.py

from django.contrib.auth import logout, login
# [关键修复] 添加 get_user_model
from django.contrib.auth import get_user_model 
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render, get_object_or_404
from django.urls import reverse, reverse_lazy
from django.views.generic import TemplateView, CreateView, UpdateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ObjectDoesNotExist
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_POST
from django import forms
from django.contrib import messages
from django.db.models import Q, Count, Avg, FloatField
from django.db.models.functions import Cast, TruncDate, ExtractHour
from django.db import transaction
import requests
import json
import pytz
import traceback
import logging
import secrets
from django.http import JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.contrib.auth.decorators import login_required, user_passes_test
from datetime import datetime, timedelta
from django.utils import timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from django.conf import settings

# DRF 引用
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework import status

# 本地模块引用
from .forms import VeludoLoginForm, VeludoRegisterForm, ProfileEditForm
from .forms import UserRoleForm, CastCMSForm
from utils.saas_client import SaaSClient
from casts.models import CastProfile, CastMedia
from casts.source import (
    get_public_casts,
    use_remote_cast_source,
    build_cast_profile_payload,
    build_cast_medias_payload,
    sync_cast_profile_to_system_b,
)
from core.models import UserActivity
from .forms import CastProfileForm, CastMediaFormSet

# [关键修复] 获取 User 模型并赋值给全局变量
User = get_user_model()
logger = logging.getLogger(__name__)

SYSTEM_B_ROOT = getattr(settings, "SYSTEM_B_ROOT", "http://system-b:8001")
SYSTEM_B_API_KEY = getattr(settings, "SAAS_API_KEY", "veludo_secret_key_123")
SYSTEM_B_SSO_AUTHORIZE_URL = getattr(settings, "SYSTEM_B_SSO_AUTHORIZE_URL", f"{SYSTEM_B_ROOT}/sso/authorize")
SYSTEM_B_SSO_EXCHANGE_URL = getattr(settings, "SYSTEM_B_SSO_EXCHANGE_URL", f"{SYSTEM_B_ROOT}/api/v1/auth/sso/exchange")
SYSTEM_B_SSO_CLIENT_ID = getattr(settings, "SYSTEM_B_SSO_CLIENT_ID", "")
SYSTEM_B_SSO_CLIENT_SECRET = getattr(settings, "SYSTEM_B_SSO_CLIENT_SECRET", "")
SYSTEM_A_BASE_URL = getattr(settings, "SYSTEM_A_BASE_URL", "")


def _current_login_mode() -> str:
    return (getattr(settings, 'A_LOGIN_MODE', 'hybrid') or 'hybrid').lower().strip()


def _is_sso_ready() -> bool:
    return bool(
        SYSTEM_B_SSO_CLIENT_ID
        and SYSTEM_B_SSO_CLIENT_SECRET
        and SYSTEM_B_SSO_AUTHORIZE_URL
        and SYSTEM_B_SSO_EXCHANGE_URL
    )


def _build_sso_callback_url(request) -> str:
    callback_path = reverse('sso_callback')
    if SYSTEM_A_BASE_URL:
        return f"{SYSTEM_A_BASE_URL}{callback_path}"
    return request.build_absolute_uri(callback_path)


def _build_unique_username(seed_name: str) -> str:
    normalized = (seed_name or 'user').strip()[:120] or 'user'
    candidate = normalized
    suffix = 0
    while User.objects.filter(username=candidate).exists():
        suffix += 1
        candidate = f"{normalized[:110]}_{suffix}"
    return candidate


def _upsert_shadow_user(identity_payload: dict):
    saas_user_id = (identity_payload.get('user_id') or '').strip()
    discord_id = (identity_payload.get('discord_id') or '').strip()
    discord_uid = (identity_payload.get('discord_uid') or '').strip()
    username_from_b = (identity_payload.get('username') or '').strip()
    saas_tenant_id = identity_payload.get('tenant_id')
    saas_role = identity_payload.get('role')

    if not saas_user_id:
        raise ValueError('Missing user_id from SSO payload')

    user = User.objects.filter(saas_user_id=saas_user_id).first()
    if user is None and discord_uid:
        user = User.objects.filter(discord_uid=discord_uid).first()
    if user is None and discord_id:
        user = User.objects.filter(discord_id=discord_id).first()

    if user is None:
        base_name = username_from_b or (discord_id.split('#')[0] if discord_id else 'user')
        user = User(username=_build_unique_username(base_name))
        user.set_unusable_password()

    if username_from_b and user.username != username_from_b and not User.objects.filter(username=username_from_b).exclude(pk=user.pk).exists():
        user.username = username_from_b

    if discord_uid:
        user.discord_uid = discord_uid

    if discord_id:
        user.discord_id = discord_id
    elif discord_uid and not user.discord_id:
        user.discord_id = discord_uid

    user.saas_user_id = saas_user_id
    user.saas_tenant_id = str(saas_tenant_id) if saas_tenant_id else None
    user.saas_role = str(saas_role) if saas_role else None
    user.is_staff = user.is_staff or (user.saas_role == 'ADMIN')
    user.save()
    return user

# --- 1. 登录视图 ---
class CustomLoginView(LoginView):
    template_name = 'login.html'
    authentication_form = VeludoLoginForm
    redirect_authenticated_user = True

    def dispatch(self, request, *args, **kwargs):
        login_mode = _current_login_mode()
        if login_mode == 'sso' and _is_sso_ready():
            return redirect('sso_login')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['login_mode'] = _current_login_mode()
        context['sso_enabled'] = _is_sso_ready()
        return context
    
    def get_success_url(self):
        return reverse_lazy('index')


@require_GET
def sso_login(request):
    if not _is_sso_ready():
        messages.error(request, 'SSO is not configured. Please contact administrator.')
        return redirect('login')

    role_hint = 'CONSUMER'
    if request.user.is_authenticated:
        if request.user.is_superuser or (request.user.is_staff and not getattr(request.user, 'is_cast', False)):
            role_hint = 'ADMIN'
        elif getattr(request.user, 'is_cast', False):
            role_hint = 'STAFF'

    if request.user.is_authenticated:
        logout(request)

    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    request.session['sso_state'] = state
    request.session['sso_nonce'] = nonce
    request.session['sso_started_at'] = timezone.now().isoformat()

    query_params = {
        'client_id': SYSTEM_B_SSO_CLIENT_ID,
        'redirect_uri': _build_sso_callback_url(request),
        'state': state,
        'nonce': nonce,
        'force_login': '1',
        'a_role': role_hint,
    }
    return redirect(f"{SYSTEM_B_SSO_AUTHORIZE_URL}?{requests.compat.urlencode(query_params)}")


@require_GET
def sso_callback(request):
    state = (request.GET.get('state') or '').strip()
    code = (request.GET.get('code') or '').strip()

    session_state = request.session.pop('sso_state', None)
    session_nonce = request.session.pop('sso_nonce', None)
    session_started_at = request.session.pop('sso_started_at', None)

    if not code or not state or not session_state or state != session_state:
        logger.warning('SSO callback state validation failed')
        messages.error(request, 'SSO validation failed. Please try again.')
        return redirect('login')

    if session_started_at:
        started_at = parse_datetime(session_started_at)
        if started_at and timezone.now() - started_at > timedelta(minutes=5):
            messages.error(request, 'SSO request expired. Please retry login.')
            return redirect('login')

    exchange_payload = {
        'code': code,
        'client_id': SYSTEM_B_SSO_CLIENT_ID,
        'client_secret': SYSTEM_B_SSO_CLIENT_SECRET,
        'redirect_uri': _build_sso_callback_url(request),
    }

    try:
        exchange_resp = requests.post(SYSTEM_B_SSO_EXCHANGE_URL, json=exchange_payload, timeout=10)
    except requests.RequestException as exc:
        logger.exception('SSO exchange request failed: %s', exc)
        messages.error(request, 'SSO service unavailable. Please try again later.')
        return redirect('login')

    if exchange_resp.status_code != 200:
        logger.warning('SSO exchange failed with status=%s', exchange_resp.status_code)
        messages.error(request, 'SSO login failed. Please retry.')
        return redirect('login')

    try:
        identity_payload = exchange_resp.json()
    except ValueError:
        logger.warning('SSO exchange returned invalid JSON')
        messages.error(request, 'SSO response invalid. Please retry.')
        return redirect('login')

    if session_nonce and identity_payload.get('nonce') != session_nonce:
        logger.warning('SSO callback nonce mismatch')
        messages.error(request, 'SSO nonce check failed. Please retry.')
        return redirect('login')

    try:
        local_user = _upsert_shadow_user(identity_payload)
    except Exception as exc:
        logger.exception('SSO shadow user sync failed: %s', exc)
        messages.error(request, 'Login succeeded but account mapping failed. Please contact support.')
        return redirect('login')

    login(request, local_user, backend='django.contrib.auth.backends.ModelBackend')
    return redirect('index')

# --- 2. 登出视图 ---
def logout_view(request):
    logout(request)
    return redirect('index')

# --- 3. 注册视图 ---
class RegisterView(CreateView):
    template_name = 'register.html'
    form_class = VeludoRegisterForm
    success_url = reverse_lazy('index')

    def form_valid(self, form):
        response = super().form_valid(form)
        login(self.request, self.object)
        return response


class TermsView(TemplateView):
    template_name = "terms.html"

# --- 4. 个人中心视图 ---
class ProfileView(LoginRequiredMixin, UpdateView):
    template_name = 'profile.html'
    form_class = ProfileEditForm
    success_url = reverse_lazy('profile')

    def get_object(self):
        return self.request.user

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        
        # [修正] user.vrc_id が空っぽなら警告を出す
        if not user.vrc_id:
             messages.warning(self.request, "登録を完了するために、VRCHAT IDを入力してください。")

        context['is_cast'] = getattr(user, 'is_cast', False) 
        context['is_admin'] = user.is_staff or user.is_superuser
        
        if context['is_cast']:
            try:
                context['cast_profile'] = user.cast_profile
            except ObjectDoesNotExist:
                context['cast_profile'] = None
                
        return context
    
    def form_valid(self, form):
        messages.success(self.request, "プロフィールを更新しました。")
        response = super().form_valid(form)

        if getattr(self.request.user, "is_cast", False):
            cast_profile = CastProfile.objects.filter(user=self.request.user).first()
            if cast_profile:
                cast_profile_id = cast_profile.id

                def _sync_latest_cast_profile():
                    latest_profile = CastProfile.objects.filter(id=cast_profile_id).select_related("user").first()
                    if not latest_profile:
                        return
                    try:
                        sync_cast_profile_to_system_b(latest_profile)
                    except Exception as exc:
                        logger.warning("Profile forced sync failed user_id=%s err=%s", self.request.user.id, exc)

                transaction.on_commit(_sync_latest_cast_profile)

        return response

# --- 5. 排班页面容器视图 (Cast后台用) ---
class ScheduleView(LoginRequiredMixin, TemplateView):
    template_name = 'schedule.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_cast'] = getattr(self.request.user, 'is_cast', False)
        return context

# --- 6. 排班数据 API (已重构 - 纯代理) ---
class AvailabilityAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.request.method == 'GET':
            return [AllowAny()]
        return [IsAuthenticated()]

    def _get_my_remote_id(self, user):
        if hasattr(user, 'cast_profile') and user.cast_profile.saas_resource_id:
            return user.cast_profile.saas_resource_id
        return None

    def get(self, request):
        target_resource_id = request.query_params.get('resource_id')
        start = request.query_params.get('start')
        end = request.query_params.get('end')

        if not target_resource_id:
            return Response([])

        # 权限判断
        is_owner = False
        if getattr(request.user, 'is_cast', False):
            try:
                my_remote_id = request.user.cast_profile.saas_resource_id
                if str(my_remote_id) == str(target_resource_id):
                    is_owner = True
            except:
                pass

        client = SaaSClient()
        
        try:
            # 获取 System B 数据
            events = client.get_calendar_events(target_resource_id, start, end)
            
            formatted_events = []
            for e in events:
                # [关键修复] 兼容性判断：
                # 优先读取 is_booked (System B Raw模式返回的是布尔值)
                # 如果没有，再尝试读取 type (System B Calendar模式)
                is_booked = e.get('is_booked') 
                if is_booked is None:
                    is_booked = (e.get('type') == 'booking')

                # 隐私保护：如果是已预约的块，且当前用户不是主人 -> 隐藏
                if is_booked and not is_owner:
                    continue 

                # 颜色逻辑
                is_recurring = e.get('is_recurring', False) 

                guest_name = e.get('guest_name', 'Unknown')

                if is_booked:
                    title = f"{guest_name} 様の予約"       
                    bg_color = 'rgba(139, 0, 0, 0.6)' # 红色
                    border_color = '#ff4444'
                    # [新增] 强制白色文字，确保在深红背景下可见
                    text_color = '#ffffff' 
                else:
                    title = '空きシフト'     
                    if is_recurring:
                        # 周期任务 -> 金色
                        bg_color = 'rgba(212, 175, 55, 0.2)' 
                        border_color = '#d4af37'
                        text_color = '#d4af37'
                    else:
                        # 单次任务 -> 绿色
                        bg_color = 'rgba(16, 185, 129, 0.2)' 
                        border_color = '#10b981'
                        text_color = '#4ade80'

                formatted_events.append({
                    'id': e['id'],
                    'title': title, 
                    'start': e['start'],
                    'end': e['end'],
                    'backgroundColor': bg_color,
                    'borderColor': border_color,
                    'textColor': text_color, 
                    'className': 'cursor-pointer hover:opacity-80 font-bold',
                    'is_recurring': is_recurring, 
                    'is_booked': is_booked,
                    'extendedProps': {
                        'is_booked': is_booked,
                        'is_recurring': is_recurring,
                        'guest_name': guest_name
                    },
                    # 只有未预约的才允许拖拽
                    'editable': not is_booked 
                })
            
            return Response(formatted_events)

        except Exception as e:
            import traceback
            print(f"❌ SaaS Error in GET: {e}")
            traceback.print_exc()
            return Response([])

    def post(self, request):
        """
        创建排班 (自动支持 单次 和 周期，透传给 System B)
        """
        user = request.user
        if not getattr(user, 'is_cast', False):
             return Response({'error': 'Not a cast'}, status=status.HTTP_403_FORBIDDEN)
             
        remote_id = self._get_my_remote_id(user)
        if not remote_id:
            return Response({'error': 'Cast ID not synced'}, status=400)

        # 构造 Payload
        payload = request.data.copy()
        payload['resource_id'] = remote_id # 强制覆盖为当前用户 ID
        
        client = SaaSClient()
        try:
            url = f"{client.api_base_url}/availability/"
            resp = requests.post(url, headers=client.headers, json=payload, timeout=10)
            
            if resp.status_code == 201:
                return Response(resp.json(), status=201)
            else:
                try: err = resp.json()
                except: err = resp.text
                return Response(err, status=resp.status_code)
                
        except Exception as e:
            return Response({'error': str(e)}, status=500)

    def delete(self, request, pk=None):
        if not getattr(request.user, 'is_cast', False):
             return Response({'error': 'Not a cast'}, status=403)

        if not pk: pk = request.data.get('id') or request.query_params.get('id')
        if not pk: return Response({'error': 'Missing ID'}, status=400)

        client = SaaSClient()
        if client.delete_availability(pk):
            return Response(status=204)
        return Response({'error': 'Failed to delete'}, status=500)

def recurring_config_proxy(request):
    print("\n[System A] Proxy: recurring_config_proxy called") # Debug 1

    # 1. 权限与参数校验
    if not request.user.is_authenticated:
        print("[System A] User not authenticated")
        return JsonResponse({'error': 'Unauthorized'}, status=401)
        
    resource_id = getattr(request.user.cast_profile, 'saas_resource_id', None)
    if not resource_id:
        print("[System A] No resource_id found")
        return JsonResponse({})
        
    # 2. 调用 Client
    try:
        client = SaaSClient()
        print(f"[System A] Calling SaaSClient for {resource_id}...") # Debug 2
        config_data = client.get_recurring_config(resource_id)
        
        print(f"[System A] Received from System B: {config_data}") # Debug 3: 关键！看这里是否为空
        
        # 3. 返回结果
        return JsonResponse(config_data)
        
    except Exception as e:
        print(f"[System A] Proxy Exception: {e}") # Debug 4: 捕获报错
        return JsonResponse({})

class IntegrationAvailabilityProxyView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """获取模版列表"""
        requested_resource_id = request.GET.get('resource_id')
        
        # 【安全检查】确保当前登录用户就是这个 resource 的主人
        # 假设 request.user.cast_profile.saas_resource_id 存了当前用户的 ID
        current_user_resource_id = getattr(request.user.cast_profile, 'saas_resource_id', None)

        if str(requested_resource_id) != str(current_user_resource_id):
            return Response({'error': 'Permission Denied: You cannot view other people\'s templates'}, status=403)

        # 检查通过，才放行
        client = SaaSClient()
        resource_id = request.GET.get('resource_id')
        
        if not resource_id:
            return Response({'error': 'resource_id required'}, status=400)

        # 调用 Client 方法
        templates = client.get_schedule_templates(resource_id)
        
        # 无论结果如何都返回 200 (空列表也是一种结果)
        # 如果 client 内部报错返回 None/空列表，前端也只会看到空
        return Response(templates, status=200)

    def post(self, request):
        """保存模版"""
        client = SaaSClient()
        resource_id = request.data.get('resource_id')
        name = request.data.get('name')
        week_config = request.data.get('week_config')
        
        if not all([resource_id, name, week_config]):
            return Response({'error': 'Missing fields'}, status=400)

        # 调用 Client 方法
        result = client.save_schedule_template(resource_id, name, week_config)
        
        if result:
            return Response(result, status=201)
        else:
            return Response({'error': 'Failed to save template'}, status=500)
# --- 7. 预约页面视图 ---
class BookingPageView(TemplateView):
    template_name = 'booking.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['casts'] = get_public_casts()
        return context

# --- 8. 预约提交 API ---
class BookingActionAPI(APIView):
    permission_classes = [IsAuthenticated]
    def post(self, request):
        user = request.user
        if not user.vrc_id: return Response({'error': 'VRCID Missing'}, status=400)
        
        # 24小时校验
        start_str = request.data.get('start')
        if start_str:
            booking_start = parse_datetime(start_str)
            if timezone.is_naive(booking_start): booking_start = timezone.make_aware(booking_start)
            if booking_start < timezone.now() + timedelta(hours=24):
                return Response({'error': 'Must book 24h in advance'}, status=400)

        client = SaaSClient()
        try:
            course_duration_minutes = None
            duration_from_client_raw = request.data.get('duration_minutes')
            if duration_from_client_raw is not None:
                try:
                    parsed_duration = int(duration_from_client_raw)
                    if parsed_duration > 0:
                        course_duration_minutes = parsed_duration
                except (TypeError, ValueError):
                    course_duration_minutes = None

            start_dt = parse_datetime(request.data.get('start')) if request.data.get('start') else None
            end_dt = parse_datetime(request.data.get('end')) if request.data.get('end') else None
            if course_duration_minutes is None and start_dt and end_dt:
                if timezone.is_naive(start_dt):
                    start_dt = timezone.make_aware(start_dt)
                if timezone.is_naive(end_dt):
                    end_dt = timezone.make_aware(end_dt)
                delta_minutes = int((end_dt - start_dt).total_seconds() // 60)
                if delta_minutes > 0:
                    course_duration_minutes = delta_minutes

            result = client.create_booking(
                resource_id=request.data.get('resource_id'),
                resource_name=request.data.get('resource_name'),
                email=user.email,
                name=user.vrc_id,
                start=request.data.get('start'),
                end=request.data.get('end'),
                course_duration_minutes=course_duration_minutes
            )
            if result: return Response(result, status=201)
            else: return Response({'error': 'SaaS Booking Failed'}, status=500)
        except Exception as e: return Response({'error': str(e)}, status=500)

class CastSearchAPI(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        print("\n========== DEBUG: CastSearchAPI START ==========")
        start_str = request.GET.get('start') 
        try:
            duration = int(request.GET.get('duration', 60))
        except (TypeError, ValueError):
            duration = 60
        
        if not start_str:
            return Response({'error': 'Start time is required'}, status=400)

        try:
            # 1. Parse Request Time
            search_start = parse_datetime(start_str)
            if search_start is None: raise ValueError("Invalid format")
            
            # [CRITICAL FIX] Ensure JST timezone for REQUEST
            jst = pytz.timezone('Asia/Tokyo')
            if timezone.is_naive(search_start):
                # Use localize to prevent +09:19 LMT issue
                search_start = jst.localize(search_start)
            else:
                search_start = search_start.astimezone(jst)
                
            search_end = search_start + timedelta(minutes=duration)
            
            print(f"1. Request Range: {search_start} ~ {search_end} ({duration}min)")
        except Exception as e:
            print(f"ERROR Parsing Date: {e}")
            return Response({'error': 'Invalid date format'}, status=400)

        source_casts = get_public_casts()
        active_casts = []
        for cast in source_casts:
            remote_id = getattr(cast, 'saas_resource_id', None)
            if remote_id:
                active_casts.append(cast)
        available_casts = []
        client = SaaSClient()

        def check_cast(cast):
            # Duration Check
            if duration == 30 and not cast.allow_30_min: return None
            if duration == 60 and not cast.allow_60_min: return None
            if duration == 120 and not cast.allow_120_min: return None

            try:
                # 2. Call System B
                valid_slots = client.check_availability(cast.saas_resource_id, search_start, search_end)
                
                for slot in valid_slots:
                    s = parse_datetime(slot['start'])
                    e = parse_datetime(slot['end'])
                    if s is None or e is None:
                        continue
                    
                    # [CRITICAL FIX] Ensure JST timezone for RESPONSE (System B slots)
                    # Previously it might default to UTC or Server Time, causing mismatch
                    if timezone.is_naive(s):
                        s = jst.localize(s)
                    else:
                        s = s.astimezone(jst)
                    if timezone.is_naive(e):
                        e = jst.localize(e)
                    else:
                        e = e.astimezone(jst)
                    
                    # 3. Matching Logic
                    if s <= search_start and e >= search_end:
                        return cast 
            except Exception as e:
                print(f"Error checking {cast.name}: {e}")
            return None

        print(f"2. Concurrently checking {len(active_casts)} casts...")
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_cast = {executor.submit(check_cast, cast): cast for cast in active_casts}
            
            for future in as_completed(future_to_cast):
                try:
                    result_cast = future.result()
                    if result_cast:
                        # [BUG FIX] Rank Handling
                        rank_val = getattr(result_cast, 'rank', 'REGULAR')
                        
                        available_casts.append({
                            'id': result_cast.id,
                            'name': result_cast.name,
                            'avatar_url': result_cast.avatar.url if result_cast.avatar else None,
                            'rank': rank_val, 
                            'saas_id': result_cast.saas_resource_id
                        })
                except Exception as exc:
                    print(f'Exception: {exc}')

        print(f"========== DEBUG END: Found {len(available_casts)} casts ==========\n")
        return Response({'casts': available_casts})

@login_required
@require_GET
def my_bookings_api(request):
    """
    专门为前端 JS 提供的 API，用于检查订单冲突。
    逻辑完全复刻 MyBookingsPageView.get_context_data
    """
    user = request.user
    client = SaaSClient()
    
    # 判断是否为 Cast (保持逻辑一致性)
    is_cast = getattr(user, 'is_cast', False)
    
    bookings = []

    # 1. 获取 System B 的数据 (完全复刻 View 的逻辑)
    if is_cast:
        if hasattr(user, 'cast_profile') and user.cast_profile.saas_resource_id:
            bookings = client.get_my_bookings(resource_id=user.cast_profile.saas_resource_id)
    else:
        # ✅ 关键点：这里必须和 MyBookingsPageView 一模一样
        # 否则会出现“主页能看到订单，但预约时却检测不到冲突”的 Bug
        bookings = client.get_my_bookings(
            customer_id=user.username,       # System A 的 ID (查新数据)
            customer_name=user.vrc_id,       # VRCID (查旧数据)
            email=user.email                 # Email (兜底)
        )

    # 2. 提取前端仅需的字段 (时间与状态)
    data = []
    for b in bookings:
        # 兼容 System B 可能返回的不同键名 ('start' 或 'start_time')
        start_str = b.get('start') or b.get('start_time')
        end_str = b.get('end') or b.get('end_time')
        
        if start_str and end_str:
            data.append({
                'start': start_str, # ISO 格式字符串
                'end': end_str,
                'status': b.get('status')
            })

    return JsonResponse(data, safe=False)

class MyBookingsPageView(LoginRequiredMixin, TemplateView):
    template_name = 'my_bookings.html'

    def get_context_data(self, **kwargs):
        print(f"\n========== DEBUG START: User {self.request.user.username} (Class View) ==========")
        
        
        context = super().get_context_data(**kwargs)
        user = self.request.user
        
        print(f"DEBUG Check User: {user.id}")
        print(f" - username: {user.username}")     # <--- 看看这里是不是 Discord ID (数字)
        print(f" - discord_id: {user.discord_id}") # <--- 看看这里是不是空的？
        print(f" - email: {user.email}")
        client = SaaSClient()
        is_cast = getattr(user, 'is_cast', False)
        context['is_cast'] = is_cast
        
        # 1. 获取 System B 的数据
        if is_cast:
            if hasattr(user, 'cast_profile') and user.cast_profile.saas_resource_id:
                bookings = client.get_my_bookings(resource_id=user.cast_profile.saas_resource_id)
            else:
                bookings = []
                print("DEBUG: Current user is Cast but has no saas_resource_id")
        else:
            # SaaSClient 会根据这些参数智能判断，且如果所有参数为空会自动熔断
            bookings = client.get_my_bookings(
                customer_id=user.username,       # usamaru6090 (用来查新数据)
                customer_name=user.vrc_id,       # keimaru22 (用来查旧数据)
                email=user.email
            )

        # 2. [核心修复] 补充 Discord ID 并转换时间格式
        for booking in bookings:
            # -------------------------------------------------------
            # [修复时间消失] 将字符串转换为 datetime 对象
            # -------------------------------------------------------
            if booking.get('start'):
                booking['start'] = parse_datetime(booking['start'])
            if booking.get('end'):
                booking['end'] = parse_datetime(booking['end'])
            # -------------------------------------------------------

            booking['discord_id'] = None 
            
            # 仅 Cast 端需要显示客人的 Discord
            if is_cast:
                guest_email = booking.get('customer_email') 
                guest_name = booking.get('customer_name') or booking.get('guest_name')
                
                target_user = None
                match_method = "None"

                # 策略 A: Email 匹配
                if guest_email:
                    target_user = User.objects.filter(email__iexact=guest_email).first()
                    match_method = "Email"
                
                # 策略 B: Name 匹配
                if not target_user and guest_name:
                    target_user = User.objects.filter(username__iexact=guest_name).first()
                    match_method = "Username"
                    if not target_user:
                        # 智能筛选 VRCID
                        candidates = User.objects.filter(vrc_id__iexact=guest_name)
                        if candidates.exists():
                            match_method = "VRC_ID"
                            best_match = None
                            for u in candidates:
                                d_id = getattr(u, 'discord_id', '')
                                if d_id and d_id != '-' and d_id != '':
                                    best_match = u
                                    break
                            target_user = best_match if best_match else candidates.first()

                # 3. 提取 Discord ID
                if target_user:
                    d_id = getattr(target_user, 'discord_id', None)
                    if d_id and d_id != '-':
                        booking['discord_id'] = d_id
                        print(f"  [SUCCESS] Match: {match_method} -> Discord: {d_id}")
                    else:
                        booking['discord_id'] = "未設定"
                else:
                    booking['discord_id'] = "未登録"

        print("========== DEBUG END ==========\n")
        
        context['bookings'] = bookings
        return context

class BookingCancelAPI(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        if getattr(request.user, 'is_cast', False):
            print(f"SECURITY: Cast用户 {request.user.username} 尝试删除预约 {pk} 被拦截")
            return Response({'error': 'Permission Denied: Cast cannot cancel bookings.'}, status=403)

        client = SaaSClient()
        success = client.cancel_booking(pk) 
        if success:
            return Response(status=204)
        return Response(status=400)

@login_required
def availability_proxy(request):
    print("----- System A Proxy Start -----")
    print(f"DEBUG:收到请求: {request.method} {request.path}")
    
    url = f"{SYSTEM_B_ROOT}/api/v1/integration/availability/"
    headers = {
        "X-Tenant-Key": SYSTEM_B_API_KEY,
        "Content-Type": "application/json"
    }

    try:
        if request.method == 'GET':
            print("DEBUG: 正在转发 GET 请求...")
            params = request.GET.dict()
            response = requests.get(url, params=params, headers=headers, timeout=5)
            
        elif request.method == 'POST':
            print("DEBUG: 正在处理 POST 请求...")
            try:
                raw_body = request.body.decode('utf-8')
                print(f"DEBUG: 原始 Request Body: {raw_body}")
                payload = json.loads(raw_body)
                print(f"DEBUG: 解析后的 Payload: {payload}")
                print("DEBUG: 正在向 System B 发送 POST...")
                response = requests.post(url, json=payload, headers=headers, timeout=5)
            except json.JSONDecodeError as e:
                print(f"ERROR: System A JSON 解析失败: {e}")
                return JsonResponse({'error': 'Invalid JSON format from Frontend'}, status=400)

        elif request.method == 'DELETE':
            print("DEBUG: 正在转发 DELETE 请求...")
            payload = json.loads(request.body)
            response = requests.delete(url, json=payload, headers=headers, timeout=5)
            
        else:
            return JsonResponse({'error': 'Method not allowed'}, status=405)

        print(f"DEBUG: System B 响应状态码: {response.status_code}")
        print(f"DEBUG: System B 响应内容: {response.text}")

        if response.status_code == 204:
            return JsonResponse({}, status=204)

        try:
            return JsonResponse(response.json(), status=response.status_code, safe=False)
        except:
            return JsonResponse({'error': 'System B returned invalid data', 'details': response.text[:200]}, status=502)

    except requests.exceptions.RequestException as e:
        print(f"CRITICAL ERROR: 连接 System B 失败: {e}")
        traceback.print_exc()
        return JsonResponse({'error': f'Failed to connect to SaaS: {str(e)}'}, status=503)
    except Exception as e:
        print(f"CRITICAL ERROR: System A 内部未知错误: {e}")
        traceback.print_exc()
        return JsonResponse({'error': str(e)}, status=500)

class BookingCompleteAPI(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        if not getattr(request.user, 'is_cast', False):
             return Response({'error': 'Permission denied'}, status=403)

        client = SaaSClient()
        success = client.complete_booking(pk)
        
        if success:
            return Response({'status': 'success'}, status=200)
        return Response({'error': 'Failed to update status'}, status=500)


# ==========================================
# 管理员面板视图 (Admin Dashboard)
# ==========================================

@login_required
@user_passes_test(lambda u: u.is_staff or u.is_superuser)
def admin_dashboard(request):
    # --------------------------------------------------------
    # 1. 处理 AJAX 拖拽排序请求 (CMS)
    # --------------------------------------------------------
    if request.method == 'POST' and request.headers.get('x-requested-with') == 'XMLHttpRequest':
        try:
            data = json.loads(request.body)
            if data.get('action') == 'update_sort_order':
                ordered_ids = data.get('order', [])
                has_uuid_like_id = any('-' in str(cast_id) for cast_id in ordered_ids)
                if use_remote_cast_source() and has_uuid_like_id:
                    client = SaaSClient()
                    failed_ids = []
                    for index, cast_id in enumerate(ordered_ids):
                        result = client.update_resource(
                            resource_id=cast_id,
                            profile={"display_order": index},
                        )
                        if result is None:
                            failed_ids.append(cast_id)
                    if failed_ids:
                        return JsonResponse(
                            {'status': 'error', 'message': f'Failed to update: {failed_ids}'},
                            status=502
                        )
                else:
                    for index, cast_id in enumerate(ordered_ids):
                        CastProfile.objects.filter(id=cast_id).update(display_order=index)
                return JsonResponse({'status': 'success'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)

    # --------------------------------------------------------
    # 2. 获取本地数据 (Users & Casts)
    # --------------------------------------------------------
    users = User.objects.all().order_by('-date_joined')
    casts = list(get_public_casts())

    # --------------------------------------------------------
    # 3. 获取 SaaS 数据 (Shifts & Orders)
    # --------------------------------------------------------
    client = SaaSClient()
    
    # === [Debug] 打印 Cast 信息，方便在控制台查看 ===
    print(f"--- [Dashboard Casts Debug] ---")
    for c in casts:
        print(
            f"RID: {getattr(c, 'saas_resource_id', '')} | "
            f"External: {getattr(c, 'external_id', '')} | "
            f"Name: {getattr(c, 'name', '')}"
        )

    cast_map_by_external_id = {}
    cast_map_by_saas_resource_id = {}
    local_casts_list = list(casts)
    for cast in local_casts_list:
        external_id = str(getattr(cast, 'external_id', '')).strip()
        if not external_id:
            user_id = getattr(getattr(cast, 'user', None), 'id', '')
            external_id = str(user_id).strip() if user_id else ''
        saas_resource_id = str(getattr(cast, 'saas_resource_id', '')).strip()
        if external_id:
            cast_map_by_external_id[external_id] = cast
        if saas_resource_id:
            cast_map_by_saas_resource_id[saas_resource_id] = cast

    def find_local_cast(saas_item):
        """
        匹配函数：优先外部ID -> 资源UUID -> 名称匹配
        """
        # 1. 优先按 external_id 匹配（bookings API 当前返回 resource.external_id）
        r_external_id = str(saas_item.get('resource_id', '')).strip()
        if r_external_id and r_external_id in cast_map_by_external_id:
            return cast_map_by_external_id[r_external_id]

        # 2. 兼容按资源 UUID 匹配（如果未来 API 返回此字段）
        r_uuid = str(saas_item.get('resource_uuid', '')).strip()
        if r_uuid and r_uuid in cast_map_by_saas_resource_id:
            return cast_map_by_saas_resource_id[r_uuid]
        
        # 获取 SaaS 端返回的名字 (去除空格，转小写)
        r_name_raw = str(saas_item.get('resource_name', ''))
        r_name = r_name_raw.strip().lower()
        
        if not r_name:
            return None

        # 2. 尝试 VRCID 匹配 (这是你最需要的)
        for cast in local_casts_list:
            vrcid_val = getattr(getattr(cast, 'user', None), 'vrc_id', '')
            vrcid = str(vrcid_val).strip().lower() if vrcid_val else ""
            
            # A. 精确匹配 (忽略大小写)
            if vrcid == r_name:
                return cast
            
            # B. 包含匹配 (解决 keimaru00 vs Keimaru 的问题)
            # 如果 SaaS名字(Keimaru) 包含在 VRCID(keimaru00) 里，或者反过来
            if vrcid and (r_name in vrcid or vrcid in r_name):
                print(f"🔥 Fuzzy Match Found: Local '{vrcid}' <-> SaaS '{r_name}'")
                return cast

        # 3. 尝试 Username 匹配 (备用)
        for cast in local_casts_list:
            uname_val = getattr(getattr(cast, 'user', None), 'username', '')
            uname = str(uname_val).strip().lower()
            if uname == r_name or uname in r_name or r_name in uname:
                return cast
                
        print(f"⚠️ No Match for SaaS Resource: {r_name_raw} (external_id: {r_external_id})")
        return None

    # --- A. 处理排班数据 (Shifts) ---
    # [修正] 必须确保 SaaSClient 默认查询未来一段时间，否则可能返回空
    print("--- DEBUG: Fetching Shifts ---")
    processed_shifts = []

    for cast in casts:
        r_id = str(getattr(cast, 'saas_resource_id', '') or getattr(cast, 'external_id', '')).strip()
        if not r_id:
            continue
        raw_shifts = client.get_availabilities(resource_id=r_id)
        
        if raw_shifts and isinstance(raw_shifts, list):
            for s in raw_shifts:
                # [新增需求 1] 过滤掉已预订 (BOOKED) 的排班
                is_booked = s.get('is_booked', False) or s.get('status') == 'BOOKED'
                if is_booked:
                    continue # 跳过，不在 Upcoming Shifts 里显示

                # 解析时间
                start_dt = parse_datetime(s.get('start')) if s.get('start') else None
                end_dt = parse_datetime(s.get('end')) if s.get('end') else None

                # [新增需求 2] 判断排班类型 (单次 vs 周期)
                # 逻辑：如果 API 返回了 recurrence_rule (RRULE字符串) 或者是 is_recurring=True，则是周期排班
                is_recurring = bool(s.get('recurrence_rule') or s.get('is_recurring'))
                shift_type = 'RECURRING' if is_recurring else 'SINGLE'

                if start_dt:
                    display_name = (
                        getattr(getattr(cast, 'user', None), 'vrc_id', '')
                        or getattr(getattr(cast, 'user', None), 'username', '')
                        or getattr(cast, 'name', 'Unknown')
                    )
                    processed_shifts.append({
                        'cast': cast,
                        'cast_id': str(cast.id),
                        'cast_name': display_name,
                        'date': start_dt,
                        'start_time': start_dt,
                        'end_time': end_dt,
                        'type': shift_type, # 传递给模板使用
                        'raw': s 
                    })
    
    print(f"--- [Dashboard] Total Shifts Loaded: {len(processed_shifts)}")
    
    # --- B. 处理订单数据 (Orders) ---
    raw_orders = client.get_my_bookings(admin_sync=True)
    processed_orders = []

    print(f"--- [SaaS Orders Debug] Count: {len(raw_orders) if raw_orders else 0}")

    if raw_orders and isinstance(raw_orders, list):
        for o in raw_orders:
            local_cast = find_local_cast(o)

            created_at = parse_datetime(o.get('created_at')) if o.get('created_at') else None
            
            # 👇 [修复] 优先取 'start' (SaaS返回的key)，取不到再取 'start_time'
            s_time_str = o.get('start') or o.get('start_time')
            start_dt = parse_datetime(s_time_str) if s_time_str else None
            
            processed_orders.append({
                'id': o.get('id'),
                'created_at': created_at,
                'status': o.get('status', 'pending').lower(),
                'customer_name': o.get('customer_name', 'Unknown'),
                'cast': local_cast,
                'cast_id': str(local_cast.id) if local_cast else 'unknown',
                'cast_name': (
                    getattr(getattr(local_cast, 'user', None), 'vrc_id', '')
                    or getattr(getattr(local_cast, 'user', None), 'username', '')
                    or getattr(local_cast, 'name', '')
                    or o.get('resource_name', 'Unknown')
                ),
                'booking_date': start_dt,
                'start_time': start_dt,
            })

    # --------------------------------------------------------
    # 4. Analytics 数据 (访问数统计)
    # --------------------------------------------------------
    now = timezone.now()
    start_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_7d = now - timedelta(days=7)

    def format_duration(seconds):
        if not seconds or seconds < 0:
            return "0:00"
        total_seconds = int(round(seconds))
        minutes, sec = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{sec:02d}"
        return f"{minutes:02d}:{sec:02d}"

    ip_activity_qs = UserActivity.objects.exclude(meta_data__ip__isnull=True).exclude(meta_data__ip='')
    eligible_ips = (
        ip_activity_qs
        .values('meta_data__ip')
        .annotate(c=Count('id'))
        .filter(c__gte=3)
        .values_list('meta_data__ip', flat=True)
    )

    visit_qs = UserActivity.objects.filter(action='VIEW_PAGE', meta_data__ip__in=eligible_ips)
    analytics_visits_total = visit_qs.count()
    analytics_visits_today = visit_qs.filter(timestamp__gte=start_today).count()
    analytics_visits_7d = visit_qs.filter(timestamp__gte=start_7d).count()

    analytics_unique_total = visit_qs.values('meta_data__ip').exclude(meta_data__ip__isnull=True).exclude(meta_data__ip='').distinct().count()
    analytics_unique_today = visit_qs.filter(timestamp__gte=start_today).values('meta_data__ip').exclude(meta_data__ip__isnull=True).exclude(meta_data__ip='').distinct().count()
    analytics_unique_7d = visit_qs.filter(timestamp__gte=start_7d).values('meta_data__ip').exclude(meta_data__ip__isnull=True).exclude(meta_data__ip='').distinct().count()

    trend_days = 14
    trend_start = start_today - timedelta(days=trend_days - 1)
    trend_qs = UserActivity.objects.filter(
        action='VIEW_PAGE',
        meta_data__ip__in=eligible_ips,
        timestamp__gte=trend_start,
        timestamp__lt=start_today + timedelta(days=1)
    )
    trend_data = (
        trend_qs
        .annotate(d=TruncDate('timestamp'))
        .values('d')
        .annotate(c=Count('id'))
        .order_by('d')
    )
    trend_map = {row['d']: row['c'] for row in trend_data}
    analytics_trend_labels = []
    analytics_trend_values = []
    for i in range(trend_days):
        day = trend_start + timedelta(days=i)
        day_label = day.strftime("%m/%d")
        analytics_trend_labels.append(day_label)
        analytics_trend_values.append(trend_map.get(day.date(), 0))

    def parse_date(value):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except Exception:
            return None

    default_end_date = now.date()
    default_start_date = default_end_date - timedelta(days=6)
    range_start_date = parse_date(request.GET.get("analytics_start")) or default_start_date
    range_end_date = parse_date(request.GET.get("analytics_end")) or default_end_date
    if range_start_date > range_end_date:
        range_start_date, range_end_date = range_end_date, range_start_date

    tz = timezone.get_current_timezone()
    range_start_dt = timezone.make_aware(datetime.combine(range_start_date, datetime.min.time()), tz)
    range_end_dt = timezone.make_aware(datetime.combine(range_end_date + timedelta(days=1), datetime.min.time()), tz)

    hourly_qs = UserActivity.objects.filter(
        action='VIEW_PAGE',
        meta_data__ip__in=eligible_ips,
        timestamp__gte=range_start_dt,
        timestamp__lt=range_end_dt
    )
    hourly_rows = (
        hourly_qs
        .annotate(hour=ExtractHour('timestamp'))
        .values('hour')
        .annotate(c=Count('id'))
        .order_by('hour')
    )
    hourly_map = {row['hour']: row['c'] for row in hourly_rows}
    analytics_hour_labels = [f"{h:02d}:00" for h in range(24)]
    analytics_hour_values = [hourly_map.get(h, 0) for h in range(24)]

    mobile_regex = r"(Mobile|Android|iPhone|iPad|iPod|Windows Phone)"
    ua_ip_qs = UserActivity.objects.filter(
        action='VIEW_PAGE'
    ).exclude(
        meta_data__ip__isnull=True
    ).exclude(
        meta_data__ip=''
    ).exclude(
        meta_data__user_agent__isnull=True
    ).exclude(
        meta_data__user_agent=''
    )
    mobile_ips = ua_ip_qs.filter(meta_data__user_agent__iregex=mobile_regex).values('meta_data__ip').distinct().count()
    desktop_ips = ua_ip_qs.exclude(meta_data__user_agent__iregex=mobile_regex).values('meta_data__ip').distinct().count()
    total_ips_for_ratio = mobile_ips + desktop_ips
    mobile_ratio = round((mobile_ips / total_ips_for_ratio) * 100, 1) if total_ips_for_ratio else 0

    duration_ms_field = Cast('meta_data__duration_ms', FloatField())
    duration_qs = (
        UserActivity.objects
        .filter(action='PAGE_DURATION', meta_data__ip__in=eligible_ips)
        .annotate(duration_ms=duration_ms_field)
        .filter(duration_ms__gte=3000)
    )
    avg_duration_ms = duration_qs.aggregate(avg=Avg('duration_ms')).get('avg') or 0
    avg_duration_str = format_duration(avg_duration_ms / 1000)

    analytics_modules = [
        {
            "key": "visits",
            "title": "Website Visits",
            "subtitle": "UserActivity: VIEW_PAGE (IP >= 3)",
            "value": analytics_visits_total,
            "stats": [
                {"label": "Today", "value": analytics_visits_today},
                {"label": "Last 7 Days", "value": analytics_visits_7d},
                {"label": "Unique IPs", "value": analytics_unique_total},
                {"label": "Unique Today", "value": analytics_unique_today},
                {"label": "Unique 7 Days", "value": analytics_unique_7d},
            ],
        },
        {
            "key": "mobile_ratio",
            "title": "Mobile Ratio",
            "subtitle": "UserAgent (Unique IPs)",
            "value": f"{mobile_ratio}%",
            "stats": [
                {"label": "Mobile IPs", "value": mobile_ips},
                {"label": "Desktop IPs", "value": desktop_ips},
            ],
        },
        {
            "key": "engagement",
            "title": "Engagement",
            "subtitle": "PAGE_DURATION (IP >= 3)",
            "value": avg_duration_str,
            "stats": [
                {"label": "Avg Duration", "value": avg_duration_str},
            ],
        },
    ]

    # --------------------------------------------------------
    # 5. 处理 POST 请求 (User Role & CMS)
    # --------------------------------------------------------
    role_form = UserRoleForm()
    cms_form = CastCMSForm()

    if request.method == 'POST':
        # --- 情况 A: 修改用户权限 ---
        if 'update_role' in request.POST:
            form = UserRoleForm(request.POST)
            if form.is_valid():
                target_user_id = form.cleaned_data.get('user_id') 
                target_user = get_object_or_404(User, id=target_user_id)
                
                is_cast = form.cleaned_data['is_cast']
                is_staff = form.cleaned_data['is_staff']
                
                target_user.is_staff = is_staff
                target_user.is_cast = is_cast
                
                if is_cast:
                    # [本地逻辑] 创建 CastProfile
                    display_name = target_user.vrc_id if target_user.vrc_id else target_user.username
                    cast_profile, _ = CastProfile.objects.get_or_create(
                        user=target_user, 
                        defaults={'name': display_name}
                    )
                    if not cast_profile.name:
                        cast_profile.name = display_name
                        cast_profile.save(update_fields=['name'])

                    # [SaaS逻辑] 同步到 System B
                    try:
                        client.sync_cast_to_saas(
                            user_id=target_user.id,
                            name=display_name,
                            email=target_user.email,
                            profile=build_cast_profile_payload(cast_profile),
                            medias=build_cast_medias_payload(cast_profile),
                        )
                    except Exception as e:
                        print(f"SaaS Sync Warning: {e}")
                
                target_user.save()
                return redirect('admin_dashboard')

        # --- 情况 B: 修改 Cast 内容 ---
        elif 'update_cms' in request.POST:
            cast_id = request.POST.get('cast_id')
            target_cast = get_object_or_404(CastProfile, id=cast_id)
            form = CastCMSForm(request.POST, request.FILES, instance=target_cast)
            if form.is_valid():
                saved_cast = form.save()
                sync_cast_profile_to_system_b(saved_cast)
                return redirect('admin_dashboard')

    # --------------------------------------------------------
    # 6. 渲染页面
    # --------------------------------------------------------
    context = {
        'users': users,
        'casts': casts,
        'role_form': role_form,
        'cms_form': cms_form,
        # 新增列表，包含了 cast_id 供前端 JS 筛选使用
        'shifts': processed_shifts,
        'orders': processed_orders,
        'analytics_modules': analytics_modules,
        'analytics_updated_at': now,
        'analytics_trend_labels': analytics_trend_labels,
        'analytics_trend_values': analytics_trend_values,
        'analytics_hour_labels': analytics_hour_labels,
        'analytics_hour_values': analytics_hour_values,
        'analytics_range_start': range_start_date.strftime("%Y-%m-%d"),
        'analytics_range_end': range_end_date.strftime("%Y-%m-%d"),
    }
    return render(request, 'admin_dashboard.html', context)
