# system_b_saas/resources/views.py

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from datetime import datetime, timedelta
from tenants.permissions import IsTenantAuthorized
from .models import Resource, Availability, RecurringPattern
from bookings.models import Booking
import threading
from django.core.mail import send_mail
from django.conf import settings
from django.db import transaction
import requests
from uuid import UUID
import pytz
from zoneinfo import ZoneInfo
from django.db.models import Min, Max


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

        print(f"\n[System B] GET Request: mode={mode}, resource_id={resource_id_raw}")

        # 1. 资源校验
        target_uuid = None
        if resource_id_raw:
            try:
                # A. 尝试直接 UUID
                uuid_obj = UUID(resource_id_raw)
                if Resource.objects.filter(id=uuid_obj, tenant=request.tenant).exists():
                    target_uuid = uuid_obj
            except ValueError:
                pass
            
            if not target_uuid:
                try:
                    # B. 尝试 External ID (User ID)
                    res = Resource.objects.get(tenant=request.tenant, external_id=resource_id_raw)
                    target_uuid = res.id
                except Resource.DoesNotExist:
                    print(f"[System B] Resource Not Found: {resource_id_raw}")
                    return Response([], status=200)

        if not target_uuid:
             return Response({'error': 'resource_id required'}, status=400)

        # 2. 时间解析
        start_dt, end_dt = None, None
        if start_str and end_str:
            try:
                start_dt = parse_datetime(start_str)
                end_dt = parse_datetime(end_str)
                # 强制时区
                if timezone.is_naive(start_dt): start_dt = timezone.make_aware(start_dt, JST)
                if timezone.is_naive(end_dt): end_dt = timezone.make_aware(end_dt, JST)
            except: 
                pass

        # [需求 1] 24小时限制线 (Booking Deadline)
        booking_deadline = timezone.now() + timedelta(hours=24)
        print(f"[DEBUG] Deadline(JST): {booking_deadline.astimezone(JST).strftime('%d日 %H:%M')}")
        
        # =========================================================
        # 模式 A: Search (Guest查询: 能否预约某时刻?)
        # =========================================================
        if mode == 'search':
            if not start_dt or not end_dt: return Response({'error': 'Time range required'}, status=400)

            # 24h 硬性拦截
            if start_dt < booking_deadline:
                return Response([]) # 24小时内不可预约

            # [需求 2] 包含查询逻辑: Shift 必须包裹住 Start~End
            shift = Availability.objects.filter(
                resource__tenant=request.tenant,
                resource__id=target_uuid,
                is_booked=False,
                start_time__lte=start_dt, # 排班开始 <= 预约开始
                end_time__gte=end_dt      # 排班结束 >= 预约结束
            ).first()

            if not shift:
                return Response([]) 

            # 预约冲突检查 (Overlap)
            buffer = timedelta(minutes=30)
            has_conflict = Booking.objects.filter(
                resource__tenant=request.tenant,
                resource__id=target_uuid,
                status__in=['CONFIRMED', 'PENDING'],
                start_time__lt=end_dt + buffer,
                end_time__gt=start_dt - buffer
            ).exists()

            if has_conflict: return Response([])

            return Response([{'start': start_dt, 'end': end_dt, 'status': 'AVAILABLE'}])

        # =========================================================
        # 模式 B: Raw (日历视图: 显示所有排班)
        # =========================================================
        else:
            avail_qs = Availability.objects.filter(resource__tenant=request.tenant, resource__id=target_uuid, is_booked=False)
            book_qs = Booking.objects.filter(resource__tenant=request.tenant, resource__id=target_uuid, status__in=['CONFIRMED', 'PENDING'])

            # 时间范围 (Overlap)
            if start_dt and end_dt:
                avail_qs = avail_qs.filter(start_time__lt=end_dt, end_time__gt=start_dt)
                book_qs = book_qs.filter(start_time__lt=end_dt + timedelta(minutes=60), end_time__gt=start_dt - timedelta(minutes=60))

            # [需求 1] 24小时过滤 (仅针对 Availability，不隐藏已存在的 Booking)
            # 数据库层面先过滤掉完全过期的
            avail_qs = avail_qs.filter(end_time__gt=booking_deadline)

            avail_list = list(avail_qs)
            book_list = list(book_qs)
            final_events = []

            print(f"[System B] Found {len(avail_list)} raw shifts, {len(book_list)} bookings")

            # 1. 放入 Booking (红色)
            for b in book_list:
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
                    'title': f"{client_name} 様",
                    'guest_name': client_name
                })

            # 2. 切分 Availability (绿色/金色)
            buffer = timedelta(minutes=30)
            min_duration_seconds = 30 * 60  # 30分钟限制

            for avail in avail_list:
                # print(f"  > Processing Shift: {avail.start_time.astimezone(JST).strftime('%H:%M')} - {avail.end_time.astimezone(JST).strftime('%H:%M')}")
                current_segments = [(avail.start_time, avail.end_time)]
                
                # 找出切割刀片 (Bookings)
                relevant_bookings = [
                    b for b in book_list 
                    if (b.end_time + buffer) > avail.start_time and (b.start_time - buffer) < avail.end_time
                ]

                for b in relevant_bookings:
                    cut_start = b.start_time - buffer
                    cut_end = b.end_time + buffer
                    next_segments = []
                    for (s_start, s_end) in current_segments:
                        overlap_start = max(s_start, cut_start)
                        overlap_end = min(s_end, cut_end)
                        if overlap_start < overlap_end:
                            if s_start < overlap_start: next_segments.append((s_start, overlap_start))
                            if overlap_end < s_end: next_segments.append((overlap_end, s_end))
                        else:
                            next_segments.append((s_start, s_end))
                    current_segments = next_segments

                # 3. 输出并执行 24h 过滤
                for (fs_start, fs_end) in current_segments:
                    if (fs_end - fs_start).total_seconds() < 60: continue
                    
                    # [修复点] 先计算修剪后的开始时间
                    valid_start = max(fs_start, booking_deadline)

                    # [修复点] 提前定义 v_str，防止报错
                    v_str = valid_start.astimezone(JST).strftime('%H:%M')

                    # 只有当 有效开始 < 结束 时才处理
                    if valid_start < fs_end:
                        remaining_duration = (fs_end - valid_start).total_seconds()
                        remaining_min = remaining_duration / 60

                        # 检查剩余时长是否满足 30 分钟
                        if remaining_duration < min_duration_seconds:
                            print(f"    x Hidden (Too Short): {remaining_min:.1f} mins < 30 mins")
                            continue

                        print(f"    o KEEP: New Range {v_str} ~ {fs_end.astimezone(JST).strftime('%H:%M')} ({remaining_min:.1f} mins)")

                        final_events.append({
                            'id': str(avail.id),
                            'resource_id': str(target_uuid),
                            'start': valid_start, # 使用修剪后的时间
                            'end': fs_end,
                            'is_booked': False,
                            'is_recurring': avail.is_recurring,
                            'type': 'availability',
                            'title': 'Available'
                        })
                    else:
                        print(f"    x Hidden (Fully Expired)")

            return Response(final_events)

    # -------------------------------------------------------------
    # POST: 增加 24h 创建限制
    # -------------------------------------------------------------
    def post(self, request):
        resource_uuid = request.data.get('resource_id')
        
        # 检查开始时间 (针对 Cast 新建排班)
        start_check = request.data.get('range_start') or request.data.get('start')
        if start_check:
            try:
                dt = parse_datetime(start_check)
                if not dt: dt = datetime.strptime(start_check, '%Y-%m-%d') # 处理仅日期
                if timezone.is_naive(dt): dt = timezone.make_aware(dt, JST)
                
                # 如果是单次排班，或者周期排班的开始日期
                # Cast 界面只能添加 24h 之后的
                deadline = timezone.now() + timedelta(hours=24)
                
                # 只有单次排班做严格报错，周期排班如果选了今天，我们只跳过生成，不报错
                if 'week_config' not in request.data:
                    if dt < deadline:
                        return Response({'error': '新規シフトは24時間後から設定可能です。'}, status=400)
            except Exception as e:
                print(f"Date check warning: {e}")

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
        
        generation_min_time = timezone.now() + timedelta(hours=24) # 24h 限制线

        with transaction.atomic():
            try:
                resource = Resource.objects.get(tenant=request.tenant, id=resource_uuid)
                
                # 日期解析函数
                def to_jst_date(dt_str):
                    dt = parse_datetime(dt_str)
                    if dt:
                        if timezone.is_naive(dt): return dt.replace(tzinfo=JST).date()
                        return dt.astimezone(JST).date()
                    d = parse_date(dt_str)
                    if d: return d
                    raise ValueError(f"Invalid date format: {dt_str}")

                curr_date = to_jst_date(range_start)
                end_date = to_jst_date(range_end)
                
            except Exception as e: 
                print(f"Date Parse Error: {e}")
                return Response({'error': f'Invalid date data: {e}'}, status=400)

            # =========================================================
            # PART 1: 保存规则到 RecurringPattern 表 (Source of Truth)
            # =========================================================
            # 先清除该资源在 [curr_date, end_date] 范围内有重叠的旧规则
            # 策略：简单覆盖。只要有效期有重叠就删掉旧的，或者您可以选择更复杂的逻辑。
            # 这里为了简单稳健，直接删除该资源所有的旧规则，保存最新的（假设用户总是全量更新规则）
            # 或者：只删除 valid_until >= curr_date 的规则
            RecurringPattern.objects.filter(resource=resource).delete() 

            for day_key, config in week_config.items():
                if config.get('enabled'):
                    try:
                        RecurringPattern.objects.create(
                            resource=resource,
                            day_of_week=int(day_key),
                            start_time=config['start'], # 前端传 "HH:MM"
                            end_time=config['end'],
                            valid_from=curr_date,
                            valid_until=end_date
                        )
                    except Exception as e:
                        print(f"Error saving pattern: {e}")

            # =========================================================
            # PART 2: 生成具体 Availability (保持原有逻辑不变)
            # =========================================================
            stats = {'created': 0, 'skipped_conflict': 0, 'skipped_24h': 0, 'deleted': 0}
            
            loop_date = curr_date
            while loop_date <= end_date:
                py_weekday = loop_date.weekday()
                # Python weekday: 0=Mon, 6=Sun
                # JS day_key: 0=Sun, 1=Mon ... 6=Sat
                # 转换: Python 6 -> JS 0, 其他 Python + 1 -> JS
                js_day_key = '0' if py_weekday == 6 else str(py_weekday + 1)
                
                day_start_limit = datetime.combine(loop_date, datetime.min.time()).replace(tzinfo=JST)
                day_end_limit = day_start_limit + timedelta(hours=30) 

                # 1. 删除旧的具体的 Availability
                del_count, _ = Availability.objects.filter(
                    resource=resource,
                    start_time__gte=day_start_limit,
                    start_time__lt=day_end_limit,
                    is_booked=False,
                    is_recurring=True 
                ).delete()
                stats['deleted'] += del_count

                # 2. 根据规则创建新的
                if js_day_key in week_config and week_config[js_day_key].get('enabled'):
                    try:
                        cfg = week_config[js_day_key]
                        s_time = datetime.strptime(cfg['start'], '%H:%M').time()
                        e_time = datetime.strptime(cfg['end'], '%H:%M').time()
                        
                        dt_s = datetime.combine(loop_date, s_time).replace(tzinfo=JST)
                        dt_e = datetime.combine(loop_date, e_time).replace(tzinfo=JST)
                        if dt_e <= dt_s: dt_e += timedelta(days=1)

                        if dt_s < generation_min_time:
                            stats['skipped_24h'] += 1
                        elif not self._check_conflict(resource, dt_s, dt_e):
                            Availability.objects.create(
                                resource=resource, start_time=dt_s, end_time=dt_e, is_booked=False, is_recurring=True
                            )
                            stats['created'] += 1
                        else:
                            stats['skipped_conflict'] += 1
                    except Exception as e:
                        print(f"Recurring Gen Error: {e}")
                
                loop_date += timedelta(days=1)
            
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

class RecurringConfigView(APIView):
    permission_classes = [IsTenantAuthorized]
    def get(self, request):
        resource_id_raw = request.query_params.get('resource_id')
        print(f"\n[System B] GET Recurring Config: resource_id={resource_id_raw}")

        if not resource_id_raw: 
            return Response({})
        
        try:
            # 兼容 UUID 和 External ID
            try:
                uuid_obj = UUID(resource_id_raw)
                res = Resource.objects.get(id=uuid_obj, tenant=request.tenant)
            except ValueError:
                res = Resource.objects.get(external_id=resource_id_raw, tenant=request.tenant)
                
            patterns = RecurringPattern.objects.filter(resource=res)
            count = patterns.count()
            print(f"[System B] Found {count} recurring patterns for this resource.")
            
            config = {}
            range_info = {'start': None, 'end': None}
            
            if patterns.exists():
                # [修复 Bug 1] 自动计算整个周期任务的 [最早开始] 和 [最晚结束] 时间
                # 这样前端就能显示正确的日期范围，而不仅仅是默认的一周
                dates = patterns.aggregate(
                    min_start=Min('valid_from'),
                    max_end=Max('valid_until')
                )
                range_info['start'] = dates['min_start']
                range_info['end'] = dates['max_end']
                
                print(f"[System B] Calculated Range: {range_info['start']} ~ {range_info['end']}")

                for p in patterns:
                    # 注意：时间字段转字符串，去掉秒数 (HH:MM:SS -> HH:MM)
                    s_str = p.start_time.strftime('%H:%M')
                    e_str = p.end_time.strftime('%H:%M')
                    
                    config[str(p.day_of_week)] = {
                        'enabled': True,
                        'start': s_str,
                        'end': e_str
                    }
                    print(f"  - Day {p.day_of_week}: {s_str} - {e_str}")
            else:
                print("[System B] No patterns found (New setup).")
            
            return Response({
                'range': range_info,
                'week_config': config
            })
            
        except Resource.DoesNotExist:
            print("[System B] Resource not found.")
            return Response({})
        except Exception as e:
            print(f"[System B] Error in RecurringConfigView: {e}")
            return Response({'error': str(e)}, status=500)

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

        try:
            # 直接用 UUID (id) 查找资源
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
                # 3. 创建预约
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
        print("\n----- System B Debug: GET /bookings (Deep Debug) -----")
        
        email = request.query_params.get('customer_email')
        resource_id = request.query_params.get('resource_id')
        
        queryset = Booking.objects.filter(tenant=request.tenant)

        # 1. Email 过滤
        if email:
            queryset = queryset.filter(customer_email=email)
        
        # 2. Resource ID 过滤
        if resource_id:
            try:
                uuid_obj = UUID(resource_id)
                queryset = queryset.filter(resource__id=uuid_obj)
            except ValueError:
                queryset = queryset.filter(resource__external_id=resource_id)

        queryset = queryset.order_by('-start_time')
        
        # [重点] 强制展开循环，打印每一行数据，确保字段被正确读取
        data = []
        print(f"DEBUG: Queryset count: {queryset.count()}")
        
        for b in queryset:
            # 打印关键信息到控制台，确认数据库里读出来的 model 对象里到底有没有 email
            # 注意：如果这里打印出来是 None，说明 Model 定义或者数据库有大问题
            # 如果这里打印出来有值，但前端没收到，说明是下面的 data.append 没写对
            if str(b.id).startswith('26d8e381'): # 只针对那个有问题的订单打印，防止日志刷屏
                print(f"  >>> TARGET HIT: ID={b.id} | DB_Email={b.customer_email} | DB_Name={b.customer_name}")
            
            data.append({
                'id': str(b.id),
                'resource_name': b.resource.name,
                'customer_name': b.customer_name,
                'customer_email': b.customer_email, # 确保这一行存在
                'start': b.start_time,
                'end': b.end_time,
                'status': b.status,
                'created_at': b.created_at
            })
        
        print(f"DEBUG: Returning {len(data)} items to Client")
        return Response(data)
    
    def delete(self, request, pk=None):
        booking_id = pk or request.query_params.get('id')
        # [兼容] 支持 path 参数或 query param
        if not booking_id and pk: booking_id = pk
            
        booking = get_object_or_404(Booking, id=booking_id, tenant=request.tenant)
        
        # 记录数据用于发信
        r_email = booking.resource.email if hasattr(booking.resource, 'email') else None
        r_name = booking.resource.name
        c_name = booking.customer_name
        s_time = booking.start_time
        e_time = booking.end_time

        # 检查是否过期
        if (booking.start_time - timezone.now()) < timedelta(hours=2):
            return Response({'error': 'Cancellation allows only 2 hours in advance.'}, status=400)
        
        booking.delete() 
        threading.Thread(target=send_cancellation_email, args=(r_email, r_name, c_name, s_time, e_time)).start()
        return Response(status=204)

    def patch(self, request, pk=None):
        booking_id = pk or request.query_params.get('id')
        # [兼容] 支持 path 参数或 query param
        if not booking_id and pk: booking_id = pk

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