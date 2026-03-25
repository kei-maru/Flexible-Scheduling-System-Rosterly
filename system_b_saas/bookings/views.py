# system_b_saas/bookings/views.py

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from django.db import transaction
from uuid import UUID
from datetime import timedelta
import zoneinfo
from django.db.models import Q

# 👇 导入我们刚才写好的 Tasks
from bookings.tasks import process_new_booking, send_cancellation_email_task
from tenants.permissions import IsTenantAuthorized
from resources.models import Resource, ServicePreset
from resources.services.service_mapping import resolve_booking_service_name, resolve_service_by_duration
from bookings.models import Booking


class IntegrationBookingView(APIView):
    permission_classes = [IsTenantAuthorized]

    def post(self, request):
        resource_uuid = request.data.get('resource_id')
        resource_name_from_a = request.data.get('resource_name')
        customer_email = request.data.get('customer_email')
        customer_name = request.data.get('customer_name')
        start_time_str = request.data.get('start_time')
        end_time_str = request.data.get('end_time')
        service_id = request.data.get('service_id')
        service_name_raw = (request.data.get('service_name') or '').strip()
        course_duration_raw = request.data.get("course_duration_minutes")

        if not all([resource_uuid, start_time_str, end_time_str]):
            return Response({'error': 'Missing required fields'}, status=400)

        start_time = parse_datetime(start_time_str)
        end_time = parse_datetime(end_time_str)
        if not start_time or not end_time:
            return Response({'error': 'Invalid datetime format'}, status=400)

        try:
            resource = Resource.objects.get(tenant=request.tenant, id=resource_uuid)
            if resource_name_from_a and resource.name != resource_name_from_a:
                resource.name = resource_name_from_a
                resource.save()
        except Resource.DoesNotExist:
            return Response({'error': 'Resource not found.'}, status=404)

        BUFFER = timedelta(minutes=30)
        conflicting_booking = Booking.objects.filter(
            resource=resource,
            start_time__lt=end_time + BUFFER,
            end_time__gt=start_time - BUFFER,
            status='CONFIRMED'
        ).exists()

        if conflicting_booking:
            return Response({'error': 'Time slot unavailable'}, status=status.HTTP_409_CONFLICT)

        selected_service = None
        selected_service_name = None
        duration_minutes = 0
        try:
            duration_minutes = int(course_duration_raw) if course_duration_raw is not None else 0
        except (TypeError, ValueError):
            duration_minutes = 0
        if duration_minutes <= 0:
            duration_minutes = max(0, int((end_time - start_time).total_seconds() // 60))

        resource_profile = getattr(resource, "profile", None)
        resource_metadata = resource_profile.metadata if resource_profile and isinstance(resource_profile.metadata, dict) else {}
        preferred_service_ids = resource_metadata.get("service_preset_ids") or []

        if service_id:
            selected_service = ServicePreset.objects.filter(
                tenant=request.tenant,
                id=service_id,
                is_active=True
            ).first()
            if not selected_service:
                return Response({'error': 'Invalid service_id'}, status=400)
            selected_service_name = selected_service.name
        elif service_name_raw:
            selected_service = ServicePreset.objects.filter(
                tenant=request.tenant,
                name=service_name_raw
            ).first()
            selected_service_name = selected_service.name if selected_service else service_name_raw

        if selected_service is None:
            selected_service = resolve_service_by_duration(
                request.tenant,
                duration_minutes,
                preferred_service_ids=preferred_service_ids,
            )
            if selected_service and not selected_service_name:
                selected_service_name = selected_service.name
        elif not selected_service_name:
            selected_service_name = selected_service.name

        if not selected_service_name and duration_minutes > 0:
            selected_service_name = f"{duration_minutes}分"

        try:
            with transaction.atomic():
                booking = Booking.objects.create(
                    tenant=request.tenant,
                    resource=resource,
                    customer_email=customer_email,
                    customer_name=customer_name,
                    selected_service=selected_service,
                    selected_service_name=selected_service_name,
                    start_time=start_time,
                    end_time=end_time,
                    status='CONFIRMED'
                )

                # ✅【关键修改】不再使用 threading，而是调用 Celery Task
                # on_commit 确保事务提交后，Worker 才能从数据库里查到这个 booking
                transaction.on_commit(
                    lambda: process_new_booking.delay(booking.id)
                )

        except Exception as e:
            return Response({'error': str(e)}, status=500)

        return Response({
            'booking_id': str(booking.id),
            'status': booking.status,
            'service_name': resolve_booking_service_name(booking, request.tenant)
        }, status=201)

    def get(self, request):
        """
        最终修复版：
        1. 兼容旧数据 (ID OR Name)
        2. 支持资源查询 (Resource ID)
        3. 支持管理员全量同步 (sync_all)
        """
        # 1. 获取参数
        query_id = request.query_params.get('customer_id') 
        query_name = request.query_params.get('customer_name')
        email = request.query_params.get('customer_email')
        resource_id = request.query_params.get('resource_id')
        sync_all = request.query_params.get('sync_all') # ✅ 关键：获取管理员通行证

        print(f"\n========== SYSTEM B DEBUG ==========")
        print(f"Params: ID={query_id}, Name={query_name}, SyncAll={sync_all}")

        # 2. 初始化 Queryset
        queryset = Booking.objects.filter(tenant=request.tenant).select_related('resource').order_by('-start_time')
        
        # 3. 构建混合查询条件 (User Identity: ID OR Name)
        # 我们希望：(customer_id == query_id) OR (customer_name == query_name)
        filter_condition = Q() # 初始化一个空的查询对象
        
        if query_id:
            # 如果有 ID，添加到条件中
            filter_condition |= Q(customer_id=query_id)
            
        if query_name:
            # 如果有 Name，也添加到条件中 (用 | 代表 OR)
            filter_condition |= Q(customer_name=query_name)
            
        # 4. 应用 User 筛选
        if filter_condition:
            print(f"Applying Q Filter: {filter_condition}")
            queryset = queryset.filter(filter_condition)
        
        # 5. 如果没有查 User，则检查其他条件 (互斥逻辑)
        else:
            # --- 分支 A: 查资源 (Cast) ---
            if resource_id:
                 try:
                    uuid_obj = UUID(resource_id)
                    queryset = queryset.filter(resource__id=uuid_obj)
                 except ValueError:
                    queryset = queryset.filter(resource__external_id=resource_id)
            
            # --- 分支 B: 查邮箱 (降级) ---
            elif email:
                 queryset = queryset.filter(customer_email=email)
            
            # --- 分支 C: 管理员全量同步 (关键修复) ---
            elif sync_all == 'true':
                 print("Applying Admin Sync (All Data)")
                 # 不做任何过滤，直接返回全量数据
                 pass
                 
            # --- 分支 D: 没有任何有效条件 -> 拒绝返回 ---
            else:
                 print("No valid filter params -> returning empty list.")
                 return Response([])

        print(f"Result Count: {queryset.count()}")
        print(f"========== SYSTEM B DEBUG END ==========\n")
        return self._serialize_response(queryset)

    def _serialize_response(self, queryset):
        """辅助方法：序列化数据"""
        data = [{
            'id': str(b.id),
            'resource_id': b.resource.external_id if hasattr(b.resource, 'external_id') else None,
            'resource_name': b.resource.name,
            'customer_name': b.customer_name,
            'customer_email': b.customer_email,
            'service_name': resolve_booking_service_name(b, b.tenant),
            'start': b.start_time,
            'end': b.end_time,
            'status': b.status,
            'created_at': b.created_at
        } for b in queryset]
        return Response(data)

    def delete(self, request, pk=None):
        booking_id = pk or request.query_params.get('id')
        if not booking_id and pk: booking_id = pk
            
        booking = get_object_or_404(Booking, id=booking_id, tenant=request.tenant)
        
        # 记录数据用于发信
        r_email = booking.resource.email
        r_name = booking.resource.name
        c_name = booking.customer_name
        tokyo_tz = zoneinfo.ZoneInfo("Asia/Tokyo")
        # 格式化时间字符串，因为 Celery 最好传基本数据类型
        s_time_str = booking.start_time.astimezone(tokyo_tz).strftime('%Y-%m-%d %H:%M')

        if (booking.start_time - timezone.now()) < timedelta(hours=2):
            return Response({'error': 'Cancellation allows only 2 hours in advance.'}, status=400)
        
        booking.delete() 
        
        # ✅【关键修改】调用 Celery Task 发送取消邮件
        transaction.on_commit(
            lambda: send_cancellation_email_task.delay(r_email, r_name, c_name, s_time_str)
        )
        
        return Response(status=204)

    def patch(self, request, pk=None):
        booking_id = pk or request.query_params.get('id')
        if not booking_id and pk: booking_id = pk

        booking = get_object_or_404(Booking, id=booking_id, tenant=request.tenant)
        new_status = request.data.get('status')
        
        if new_status == 'COMPLETED':
            if booking.status != 'CONFIRMED':
                return Response({'error': 'Only CONFIRMED bookings can be completed.'}, status=400)
            booking.status = 'COMPLETED'
            booking.save()
            return Response({'status': 'COMPLETED'}, status=200)
            
        return Response({'error': 'Invalid status update'}, status=400)


class IntegrationServiceListView(APIView):
    permission_classes = [IsTenantAuthorized]

    def get(self, request):
        services = ServicePreset.objects.filter(tenant=request.tenant, is_active=True).order_by('sort_order', 'id')
        data = [
            {
                'id': s.id,
                'name': s.name,
                'description': s.description,
                'price': int(s.price),
                'duration_minutes': s.duration_minutes,
            }
            for s in services
        ]
        return Response(data)
