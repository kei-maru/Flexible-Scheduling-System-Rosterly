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
from resources.models import Resource
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

        if not all([resource_uuid, start_time_str, end_time_str]):
            return Response({'error': 'Missing required fields'}, status=400)

        start_time = parse_datetime(start_time_str)
        end_time = parse_datetime(end_time_str)

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

        try:
            with transaction.atomic():
                booking = Booking.objects.create(
                    tenant=request.tenant,
                    resource=resource,
                    customer_email=customer_email,
                    customer_name=customer_name,
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

        return Response({'booking_id': str(booking.id), 'status': booking.status}, status=201)

    def get(self, request):
        """
        最终修复版：兼容旧数据 (OR 逻辑)
        """
        query_id = request.query_params.get('customer_id') 
        query_name = request.query_params.get('customer_name')
        # ... 其他参数 ...

        print(f"\n========== SYSTEM B DEBUG ==========")
        print(f"Params: ID={query_id}, Name={query_name}")

        queryset = Booking.objects.filter(tenant=request.tenant).order_by('-start_time')
        
        # 1. 构建混合查询条件 (OR Logic)
        # 我们希望：(customer_id == query_id) OR (customer_name == query_name)
        
        filter_condition = Q() # 初始化一个空的查询对象
        
        if query_id:
            # 如果有 ID，添加到条件中
            filter_condition |= Q(customer_id=query_id)
            
        if query_name:
            # 如果有 Name，也添加到条件中 (用 | 代表 OR)
            filter_condition |= Q(customer_name=query_name)
            
        # 2. 应用筛选
        if filter_condition:
            print(f"Applying Q Filter: {filter_condition}")
            queryset = queryset.filter(filter_condition)
        
        # 3. 处理其他筛选 (Resource / Email)
        # 注意：这里的逻辑是 AND 关系 (上面的 OR 结果 AND 下面的条件)
        # 但通常 resource_id 和 user 是互斥的，所以可以用 if/else 分开
        if not filter_condition:
             # 如果上面没查用户，再看是不是查资源
             resource_id = request.query_params.get('resource_id')
             if resource_id:
                 # ... 资源查询逻辑 ...
                 pass
             elif request.query_params.get('customer_email'):
                 # ... 邮箱查询逻辑 ...
                 queryset = queryset.filter(customer_email=request.query_params.get('customer_email'))
             else:
                 # 没有任何有效条件
                 return Response([])

        print(f"Result Count: {queryset.count()}")
        return self._serialize_response(queryset)

    def _serialize_response(self, queryset):
        """辅助方法：序列化数据"""
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