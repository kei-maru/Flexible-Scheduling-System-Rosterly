# system_b_saas/resources/views.py

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from datetime import datetime, timedelta
from tenants.permissions import IsTenantAuthorized
from .models import Resource, Availability
from bookings.models import Booking
import threading
from django.core.mail import send_mail
from django.conf import settings
from django.db import transaction
import requests
from uuid import UUID
import pytz
from zoneinfo import ZoneInfo


JST = ZoneInfo("Asia/Tokyo")
# =========================================================
# 核心算法 A: 针对 Guest 搜索 (纯净空闲时间)
# =========================================================
def calculate_free_slots(resource_id, start_dt, end_dt, tenant):
    """
    计算最终可用时间段 (带详细调试信息)
    """
    print(f"\n========== DEBUG: calculate_free_slots ==========")
    print(f"1. 原始请求 (Backend Recieved):")
    print(f"   Start: {start_dt} (tzinfo: {start_dt.tzinfo})")
    print(f"   End:   {end_dt}   (tzinfo: {end_dt.tzinfo})")

    # [关键修复] 如果传入的是 Naive 时间，强制设为 JST，防止被转成 UTC
    if timezone.is_naive(start_dt):
        start_dt = timezone.make_aware(start_dt, JST)
        print(f"   -> Fixed Start (JST): {start_dt}")
    if timezone.is_naive(end_dt):
        end_dt = timezone.make_aware(end_dt, JST)
        print(f"   -> Fixed End   (JST): {end_dt}")

    # 1. 扩大搜索范围 (查询排班)
    # 如果你要查 18号 00:30，这个属于 17号的排班（深夜班）
    # query_start 必须涵盖 17号
    query_start = start_dt - timedelta(days=1)
    query_end = end_dt + timedelta(days=1)
    
    print(f"2. DB 查询范围: {query_start} ~ {query_end}")

    raw_shifts = Availability.objects.filter(
        resource__id=resource_id, resource__tenant=tenant,
        start_time__lt=query_end, end_time__gt=query_start
    ).order_by('start_time')

    print(f"   -> 找到 {raw_shifts.count()} 个原始排班:")
    for s in raw_shifts:
        print(f"      [Shift] {s.start_time} ~ {s.end_time}")

    # 2. 获取预约 (用于扣减)
    all_bookings = Booking.objects.filter(
        resource__id=resource_id, resource__tenant=tenant,
        start_time__lt=query_end, end_time__gt=query_start
    ).exclude(status__iexact='CANCELLED').order_by('start_time')
    
    print(f"   -> 找到 {all_bookings.count()} 个预约 (用于扣减)")

    # 转换排班为字典列表
    free_segments = [{'start': s.start_time, 'end': s.end_time} for s in raw_shifts]
    buffer_time = timedelta(minutes=30)
    
    # 3. 执行几何减法 (Shift - Booking)
    for b in all_bookings:
        b_start = b.start_time - buffer_time
        b_end = b.end_time + buffer_time
        next_free_segments = []
        for seg in free_segments:
            s_start, s_end = seg['start'], seg['end']
            
            # Case A: 无交集
            if b_end <= s_start or b_start >= s_end:
                next_free_segments.append(seg)
            # Case B: 有交集，切割
            else:
                if s_start < b_start:
                    next_free_segments.append({'start': s_start, 'end': b_start})
                if s_end > b_end:
                    next_free_segments.append({'start': b_end, 'end': s_end})
        free_segments = next_free_segments

    # 4. 缝合逻辑 (Stitching)
    if not free_segments: 
        print("   -> No free segments left after subtraction.")
        return []

    free_segments.sort(key=lambda x: x['start'])
    merged = []
    curr = free_segments[0]
    for i in range(1, len(free_segments)):
        nxt = free_segments[i]
        # 如果首尾相接 (误差 < 1s)
        if abs((curr['end'] - nxt['start']).total_seconds()) <= 1:
            curr['end'] = nxt['end']
        else:
            merged.append(curr)
            curr = nxt
    merged.append(curr)

    # 5. 最终过滤：只返回与用户请求时间段有交集的
    final_results = []
    print(f"3. 最终过滤 (请求范围: {start_dt} ~ {end_dt}):")
    for s in merged:
        # 逻辑：只要有一点点交集就算 Available
        # 交集公式：not (EndA <= StartB or StartA >= EndB)
        if s['end'] <= start_dt or s['start'] >= end_dt:
            print(f"   [Skip] {s['start']} ~ {s['end']} (在请求范围之外)")
            continue
        
        print(f"   [MATCH] {s['start']} ~ {s['end']}")
        final_results.append(s)
        
    return final_results

# =========================================================
# 核心算法 B: 针对 Cast 后台日历 (可视化切割)
# =========================================================
def generate_admin_calendar_events(resource_id, start_dt, end_dt, tenant):
    """
    生成后台日历所需的 Event 列表。
    System B 必须在这里把 Available 的块切断，否则前端会显示一整块绿色。
    """
    print(f"\n=== DEBUG: Generating Calendar for {resource_id} ===")
    print(f"Time Range: {start_dt} ~ {end_dt}")

    raw_availabilities = Availability.objects.filter(
        resource__id=resource_id, resource__tenant=tenant,
        start_time__gte=start_dt, end_time__lte=end_dt
    )
    
    # [修正 2] 这里的查询逻辑放宽，防止大小写导致查不到
    # 只要不是 'CANCELLED' 的，都应该切掉排班
    bookings = Booking.objects.filter(
        resource__id=resource_id, resource__tenant=tenant,
        start_time__gte=start_dt, end_time__lte=end_dt
    ).exclude(status__iexact='CANCELLED') 

    print(f"Found {raw_availabilities.count()} shifts, {bookings.count()} bookings.")

    buffer_time = timedelta(minutes=30)
    
    # 1. 准备可用块
    available_slots = [{'start': i.start_time, 'end': i.end_time, 'id': str(i.id)} for i in raw_availabilities]

    # 2. 遍历预约进行视觉切割
    for booking in bookings:
        print(f"Cutting with booking: {booking.id} ({booking.status}) {booking.start_time} - {booking.end_time}")
        
        blocked_start = booking.start_time - buffer_time
        blocked_end = booking.end_time + buffer_time
        
        next_slots = []
        for slot in available_slots:
            s_start, s_end, orig_id = slot['start'], slot['end'], slot['id']
            
            # 无交集 -> 保留
            if blocked_end <= s_start or blocked_start >= s_end:
                next_slots.append(slot)
                continue
            
            # 有交集 -> 切割
            print(f"  -> Cutting slot {s_start}-{s_end}")
            if s_start < blocked_start:
                next_slots.append({'start': s_start, 'end': blocked_start, 'id': orig_id})
            if s_end > blocked_end:
                next_slots.append({'start': blocked_end, 'end': s_end, 'id': orig_id})
        available_slots = next_slots

    # 3. 组装结果
    events = []
    # A. 可用时间
    for slot in available_slots:
        events.append({
            'id': slot['id'], 
            'type': 'availability',
            'title': 'Available',
            'start': slot['start'],
            'end': slot['end']
        })
    # B. 预约时间 (只发 confirmed 的给前端显示，但切割是用所有有效订单切的)
    for b in bookings:
        # 只有 Confirmed 的才显示红块，其他的虽然切掉了绿块，但这里不一定要显示红块(看你需求)
        # 这里为了保险，只要没取消都显示
        events.append({
            'id': str(b.id),
            'type': 'booking',
            'title': f'Booked: {b.customer_name}',
            'start': b.start_time,
            'end': b.end_time
        })
        
    return events

# =========================================================
# 1. 排班管理接口 (Merged)
# =========================================================
class IntegrationAvailabilityView(APIView):
    permission_classes = [IsTenantAuthorized] 

    def get(self, request):
        mode = request.query_params.get('mode', 'raw')
        resource_id_raw = request.query_params.get('resource_id')
        start_str = request.query_params.get('start')
        end_str = request.query_params.get('end')

        # 1. 资源校验 (增强版)
        target_uuid = None
        if resource_id_raw:
            # 尝试情况 A: 直接是 UUID (Resource.id)
            try:
                uuid_obj = UUID(resource_id_raw)
                if Resource.objects.filter(id=uuid_obj, tenant=request.tenant).exists():
                    target_uuid = uuid_obj
            except ValueError:
                pass
            
            # 尝试情况 B: 是 External ID (User ID)
            if not target_uuid:
                try:
                    res = Resource.objects.get(tenant=request.tenant, external_id=resource_id_raw)
                    target_uuid = res.id
                except Resource.DoesNotExist:
                    # 只有当 ID 确实存在但找不到资源时才返回空，避免报错
                    print(f"System B: Resource not found for ID {resource_id_raw}")
                    return Response([], status=200)

        if not target_uuid:
             return Response({'error': 'resource_id required or invalid'}, status=400)

        # 2. 时间解析
        start_dt, end_dt = None, None
        if start_str and end_str:
            try:
                start_dt = parse_datetime(start_str)
                end_dt = parse_datetime(end_str)
                if timezone.is_naive(start_dt): start_dt = timezone.make_aware(start_dt, JST)
                if timezone.is_naive(end_dt): end_dt = timezone.make_aware(end_dt, JST)
            except: 
                pass

        # 3. 查询逻辑 (Availability + Booking)
        # 确保只查当前资源的数据
        avail_qs = Availability.objects.filter(
            resource__tenant=request.tenant, 
            resource__id=target_uuid, 
            is_booked=False
        )
        book_qs = Booking.objects.filter(
            resource__tenant=request.tenant, 
            resource__id=target_uuid, 
            status__in=['CONFIRMED', 'PENDING']
        )

        if start_dt:
            avail_qs = avail_qs.filter(start_time__gte=start_dt)
            # 扩大搜索范围以包含边界上的缓冲
            book_qs = book_qs.filter(start_time__gte=start_dt - timedelta(minutes=60)) 
        if end_dt:
            avail_qs = avail_qs.filter(end_time__lte=end_dt)
            book_qs = book_qs.filter(end_time__lte=end_dt + timedelta(minutes=60))

        avail_list = list(avail_qs)
        book_list = list(book_qs)
        final_events = []

        # --- 步骤 A: 添加预约 ---
        for b in book_list:
            # 获取客户名字
            client_name = getattr(b, 'guest_name', 'Guest')
            if not client_name and hasattr(b, 'user') and b.user:
                client_name = b.user.username

            final_events.append({
                'id': str(b.id),
                'resource_id': str(target_uuid),
                'start': b.start_time,
                'end': b.end_time,
                'is_booked': True,
                'is_recurring': False, 
                'type': 'booking',
                'title': 'Booking',
                'guest_name': client_name # 传递名字
            })

        # --- 步骤 B: 切割排班 ---
        buffer = timedelta(minutes=30)

        for avail in avail_list:
            current_segments = [(avail.start_time, avail.end_time)]
            
            # 找出相关预约
            relevant_bookings = [
                b for b in book_list 
                if (b.end_time + buffer) > avail.start_time and (b.start_time - buffer) < avail.end_time
            ]

            for b in relevant_bookings:
                cut_start = b.start_time - buffer
                cut_end = b.end_time + buffer
                
                next_segments = []
                for (seg_s, seg_e) in current_segments:
                    overlap_start = max(seg_s, cut_start)
                    overlap_end = min(seg_e, cut_end)

                    if overlap_start < overlap_end:
                        if seg_s < overlap_start:
                            next_segments.append((seg_s, overlap_start))
                        if overlap_end < seg_e:
                            next_segments.append((overlap_end, seg_e))
                    else:
                        next_segments.append((seg_s, seg_e))
                current_segments = next_segments

            for (fs_start, fs_end) in current_segments:
                if (fs_end - fs_start).total_seconds() < 60: continue

                final_events.append({
                    'id': str(avail.id),
                    'resource_id': str(target_uuid),
                    'start': fs_start,
                    'end': fs_end,
                    'is_booked': False,
                    'is_recurring': avail.is_recurring,
                    'type': 'availability',
                    'title': 'Available'
                })

        return Response(final_events)

    # ... (post, _handle_single, _handle_recurring, _check_conflict, delete 保持不变，不需要修改) ...
    # 为了完整性，下面我把 _check_conflict 和 delete 也贴在这里，您可以直接复制整个类

    def post(self, request):
        resource_uuid = request.data.get('resource_id')
        if 'week_config' in request.data:
            return self._handle_recurring(request, resource_uuid)
        else:
            return self._handle_single(request, resource_uuid)

    def _handle_single(self, request, resource_uuid):
        start_str = request.data.get('start')
        end_str = request.data.get('end')
        
        if not all([resource_uuid, start_str, end_str]): return Response({'error': 'Missing fields'}, status=400)
        
        start_dt = parse_datetime(start_str)
        end_dt = parse_datetime(end_str)
        if timezone.is_naive(start_dt): start_dt = timezone.make_aware(start_dt, JST)
        if timezone.is_naive(end_dt): end_dt = timezone.make_aware(end_dt, JST)

        if start_dt >= end_dt: return Response({'error': 'Invalid time range'}, status=400)

        try:
            resource = Resource.objects.get(tenant=request.tenant, id=resource_uuid)
        except Resource.DoesNotExist: return Response({'error': 'Resource not found'}, status=404)

        if self._check_conflict(resource, start_dt, end_dt):
             return Response({'error': 'Time slot conflict'}, status=409)

        avail = Availability.objects.create(resource=resource, start_time=start_dt, end_time=end_dt, is_recurring=False)
        return Response({'id': str(avail.id), 'status': 'created'}, status=201)

    def _handle_recurring(self, request, resource_uuid):
        range_start = request.data.get('range_start')
        range_end = request.data.get('range_end')
        week_config = request.data.get('week_config') or {} 
        
        with transaction.atomic():
            try:
                resource = Resource.objects.get(tenant=request.tenant, id=resource_uuid)
                def to_jst_date(dt_str):
                    dt = parse_datetime(dt_str)
                    if timezone.is_naive(dt): return dt.replace(tzinfo=JST).date()
                    return dt.astimezone(JST).date()
                curr_date = to_jst_date(range_start)
                end_date = to_jst_date(range_end)
            except Exception as e: 
                return Response({'error': f'Invalid data: {e}'}, status=400)

            stats = {'created': 0, 'skipped': 0, 'deleted': 0}
            
            while curr_date <= end_date:
                py_weekday = curr_date.weekday()
                js_day_key = '0' if py_weekday == 6 else str(py_weekday + 1)
                day_start_limit = datetime.combine(curr_date, datetime.min.time()).replace(tzinfo=JST)
                day_end_limit = day_start_limit + timedelta(hours=30) 

                del_count, _ = Availability.objects.filter(
                    resource=resource,
                    start_time__gte=day_start_limit,
                    start_time__lt=day_end_limit,
                    is_booked=False,
                    is_recurring=True 
                ).delete()
                stats['deleted'] += del_count

                if js_day_key in week_config and week_config[js_day_key].get('enabled'):
                    try:
                        cfg = week_config[js_day_key]
                        s_time = datetime.strptime(cfg['start'], '%H:%M').time()
                        e_time = datetime.strptime(cfg['end'], '%H:%M').time()
                        dt_s = datetime.combine(curr_date, s_time).replace(tzinfo=JST)
                        dt_e = datetime.combine(curr_date, e_time).replace(tzinfo=JST)
                        if dt_e <= dt_s: dt_e += timedelta(days=1)

                        if not self._check_conflict(resource, dt_s, dt_e):
                            Availability.objects.create(
                                resource=resource, start_time=dt_s, end_time=dt_e, is_booked=False, is_recurring=True
                            )
                            stats['created'] += 1
                        else:
                            stats['skipped'] += 1
                    except Exception as e:
                        print(f"Recurring Error: {e}")
                curr_date += timedelta(days=1)
            
        return Response(stats, status=201)

    def _check_conflict(self, resource, start, end):
        shift_c = Availability.objects.filter(resource=resource, start_time__lt=end, end_time__gt=start).exists()
        buff = timedelta(minutes=30)
        book_c = Booking.objects.filter(
            resource=resource, 
            start_time__lt=end + buff, 
            end_time__gt=start - buff, 
            status='CONFIRMED'
        ).exists()
        return shift_c or book_c

    def delete(self, request, pk=None):
        avail_id = pk or request.data.get('id')
        if not avail_id: return Response({'error': 'ID required'}, status=400)
        try:
            avail = Availability.objects.get(id=avail_id, resource__tenant=request.tenant)
        except Availability.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)
        if avail.is_booked: return Response({'error': 'Cannot delete booked slot'}, status=400)
        avail.delete()
        return Response(status=204)


# ---------------------------------------------------------
# 2. 辅助函数 (Email / Webhook)
# ---------------------------------------------------------
def send_booking_emails(booking):
    subject = f"【预约确认】您已成功预约: {booking.resource.name}"
    message = f"""
    尊敬的 {booking.customer_name} 和 {booking.resource.name}:
    
    预约已确认！
    --------------------------------
    Cast: {booking.resource.name}
    开始时间: {booking.start_time.strftime('%Y-%m-%d %H:%M')}
    结束时间: {booking.end_time.strftime('%Y-%m-%d %H:%M')}
    --------------------------------
    """
    recipient_list = [booking.customer_email]
    if hasattr(booking.resource, 'email') and booking.resource.email:
        recipient_list.append(booking.resource.email)

    try:
        send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, recipient_list, fail_silently=False)
    except Exception as e:
        print(f"ERROR: Failed to send confirmation email: {e}")

def trigger_webhook(booking):
    tenant = booking.tenant
    if not tenant.webhook_url: return
    
    payload = {
        "event": "booking.created",
        "booking_id": str(booking.id),
        "resource_name": booking.resource.name,
        "customer_name": booking.customer_name,
        "start_time": booking.start_time.isoformat(),
        "end_time": booking.end_time.isoformat(),
    }
    try:
        requests.post(tenant.webhook_url, json=payload, timeout=5)
    except Exception as e:
        print(f"ERROR: Webhook send failed: {e}")

def send_cancellation_email(resource_email, resource_name, customer_name, start_time, end_time):
    if not resource_email: return
    subject = f"【预约取消】{customer_name} 取消了预约"
    message = f"""
    通知 {resource_name}:
    有一笔预约已被取消。
    原定客人: {customer_name}
    """
    try:
        send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [resource_email], fail_silently=False)
    except Exception as e:
        print(f"ERROR: Failed to send cancellation email: {e}")


# ---------------------------------------------------------
# 3. 预约管理接口 (Bookings)
# ---------------------------------------------------------
class IntegrationBookingView(APIView):
    permission_classes = [IsTenantAuthorized]

    def post(self, request):
        resource_uuid = request.data.get('resource_id') # 这里拿到的是 UUID
        resource_name_from_a = request.data.get('resource_name')
        customer_email = request.data.get('customer_email')
        customer_name = request.data.get('customer_name')
        start_time_str = request.data.get('start_time')
        end_time_str = request.data.get('end_time')

        if not all([resource_uuid, start_time_str, end_time_str]):
            return Response({'error': 'Missing required fields'}, status=400)

        start_time = parse_datetime(start_time_str)
        end_time = parse_datetime(end_time_str)

        # ==========================================
        # 【核心修复】: 不要用 get_or_create(external_id=...)
        # ==========================================
        try:
            # 直接用 UUID (id) 查找资源
            # 如果找不到，说明 System A 和 System B 不同步，应该报错而不是瞎创建
            resource = Resource.objects.get(tenant=request.tenant, id=resource_uuid)
            
            # (可选) 如果传了名字，顺便更新一下名字
            if resource_name_from_a and resource.name != resource_name_from_a:
                resource.name = resource_name_from_a
                resource.save()
                
        except Resource.DoesNotExist:
            print(f"ERROR: 预约失败，找不到 UUID 为 {resource_uuid} 的资源")
            return Response({'error': 'Resource not found. Please resync Cast Profile.'}, status=404)

        # 2. 冲突检测
        BUFFER = timedelta(minutes=30)
        conflicting_booking = Booking.objects.filter(
            resource=resource,
            start_time__lt=end_time + BUFFER,
            end_time__gt=start_time - BUFFER,
            status='CONFIRMED'
        ).exists()

        if conflicting_booking:
            return Response({'error': 'Time slot unavailable (Buffer conflict)'}, status=status.HTTP_409_CONFLICT)

        try:
            with transaction.atomic():
                # 3. 创建预约 (默认 CONFIRMED，按你要求修改)
                booking = Booking.objects.create(
                    tenant=request.tenant,
                    resource=resource,
                    customer_email=customer_email,
                    customer_name=customer_name,
                    start_time=start_time,
                    end_time=end_time,
                    status='CONFIRMED' 
                )
                print(f"DEBUG: 预约已存入数据库 (ID: {booking.id})，准备发送通知...")

                # 4. 定义后台任务 (详细版)
                def run_background_tasks():
                    print(f"THREAD: 开始执行后台通知任务 (Booking {booking.id})...")
                    try:
                        send_booking_emails(booking)
                        print("THREAD: 邮件发送成功！")
                    except Exception as e:
                        print(f"THREAD ERROR: 邮件发送失败: {e}")
                    
                    try:
                        trigger_webhook(booking)
                        print("THREAD: Webhook 触发成功！")
                    except Exception as e:
                        print(f"THREAD ERROR: Webhook 失败: {e}")

                # 5. 事务提交后执行线程
                transaction.on_commit(lambda: threading.Thread(target=run_background_tasks).start())
                
        except Exception as e:
            print(f"CREATE ERROR: {e}")
            return Response({'error': str(e)}, status=500)

        return Response({
            'booking_id': str(booking.id),
            'status': booking.status
        }, status=201)

    def get(self, request):
        """
        [Debug模式] 查询预约
        """
        print("\n----- System B Debug: GET /bookings -----")
        
        email = request.query_params.get('customer_email')
        resource_id = request.query_params.get('resource_id')
        
        queryset = Booking.objects.filter(tenant=request.tenant)

        # 1. Email 过滤 (客人视角)
        if email:
            queryset = queryset.filter(customer_email=email)
        
        # 2. Resource ID 过滤 (Cast 视角) - 【核心修复】
        if resource_id:
            print(f"DEBUG: 收到 resource_id 参数: {resource_id}")
            try:
                # 尝试解析为 UUID
                uuid_obj = UUID(resource_id)
                # 如果是 UUID，查 System B 的主键
                queryset = queryset.filter(resource__id=uuid_obj)
                print(f"DEBUG: 使用 UUID 过滤预约: {uuid_obj}")
            except ValueError:
                # 如果不是 UUID，查 External ID (System A 的 ID)
                queryset = queryset.filter(resource__external_id=resource_id)
                print(f"DEBUG: 使用 External ID 过滤预约: {resource_id}")

        queryset = queryset.order_by('-start_time')

        # 3. 构造返回数据
        data = [{
            'id': str(b.id),
            'resource_name': b.resource.name,
            'customer_name': b.customer_name,
            'customer_email': b.customer_email,
            'start': b.start_time,
            'end': b.end_time,
            'status': b.status,
            'created_at': b.created_at
        } for b in queryset]
        
        print(f"DEBUG: 最终返回预约条数: {len(data)}")
        return Response(data)
    
    def delete(self, request, pk=None):
        booking_id = pk or request.query_params.get('id')
        booking = get_object_or_404(Booking, id=booking_id, tenant=request.tenant)
        
        # 记录数据用于发信
        r_email = booking.resource.email if hasattr(booking.resource, 'email') else None
        r_name = booking.resource.name
        c_name = booking.customer_name
        s_time = booking.start_time
        e_time = booking.end_time

        if (booking.start_time - timezone.now()) < timedelta(hours=2):
            return Response({'error': 'Cancellation allows only 2 hours in advance.'}, status=400)
        
        booking.delete() 
        threading.Thread(target=send_cancellation_email, args=(r_email, r_name, c_name, s_time, e_time)).start()
        return Response(status=204)

    def patch(self, request, pk=None):
        booking_id = pk or request.query_params.get('id')
        booking = get_object_or_404(Booking, id=booking_id, tenant=request.tenant)
        
        new_status = request.data.get('status')
        
        if new_status == 'COMPLETED':
            if booking.status != 'CONFIRMED':
                return Response({'error': 'Only CONFIRMED bookings can be completed.'}, status=400)
            
            booking.status = 'COMPLETED'
            booking.save()
            print(f"DEBUG: Booking {booking.id} marked as COMPLETED by Cast.")
            return Response({'status': 'COMPLETED'}, status=200)
            
        return Response({'error': 'Invalid status update'}, status=400)


# ---------------------------------------------------------
# 4. 资源同步接口 (Resources)
# ---------------------------------------------------------
class IntegrationResourceView(APIView):
    permission_classes = [IsTenantAuthorized]

    def post(self, request):
        a_user_id = request.data.get('external_id') 
        name = request.data.get('name')
        email = request.data.get('email')

        if not all([a_user_id, name]):
            return Response({'error': 'Missing external_id or name'}, status=400)

        resource, created = Resource.objects.update_or_create(
            tenant=request.tenant,
            external_id=a_user_id,
            defaults={'name': name, 'email': email or ''}
        )
        return Response({'saas_id': str(resource.id), 'status': "created" if created else "updated"}, status=201)