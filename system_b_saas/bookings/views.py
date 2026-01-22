# system_b_saas/bookings/views.py

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime
from django.utils import timezone  # 👈 必须导入
from django.db import transaction
from django.core.mail import send_mail, EmailMultiAlternatives
from django.conf import settings
from django.template import Template, Context
from django.utils.html import strip_tags

import threading
import requests
import pytz  # 👈 必须导入 pytz 处理时区
from datetime import timedelta
from uuid import UUID

from tenants.permissions import IsTenantAuthorized
from resources.models import Resource, EmailTemplate
from bookings.models import Booking

# =========================================================
# 1. 核心修复：发送邮件函数
# =========================================================

def send_booking_emails(booking):
    """
    发送预约确认邮件（已修复时区和图片路径问题）
    """
    # --- 1. 获取模板配置 ---
    try:
        # 获取确认邮件模板
        tpl = EmailTemplate.objects.get(tenant=booking.tenant, event_type='BOOKING_CONFIRMED')
        
        # 检查是否需要发送给客户 (复选框逻辑)
        if not tpl.send_to_customer:
            print(f"[Email] Skipped sending to customer (Configured in Dashboard)")
            return 

        t_title = tpl.email_title
        t_greeting = tpl.email_greeting
        t_btn_text = tpl.button_text
        t_btn_link = tpl.button_link
        t_footer_title = tpl.footer_title
        t_footer_text = tpl.footer_text
        raw_subject = tpl.subject_template
        
        # 处理 Logo 的绝对路径 (修复邮件裂图)
        # ⚠️ 注意：这里请替换成你真实的公网 IP 或域名
        BASE_DOMAIN = "http://161.33.129.157:8001" # 或者 "https://vr-veludo.com"
        
        if tpl.logo:
            # 拼接完整 URL: http://.../media/...
            logo_url = f"{BASE_DOMAIN}{tpl.logo.url}"
        else:
            logo_url = "https://via.placeholder.com/80x80/d4af37/ffffff?text=Veludo"

        service_name = tpl.service_name

    except EmailTemplate.DoesNotExist:
        # 兜底默认值
        t_title = "予約が確定しました。"
        t_greeting = "以下の内容で予約を承りました。"
        t_btn_text = "詳細を見る"
        t_btn_link = "#"
        t_footer_title = "当社のキャンセルポリシー"
        t_footer_text = "ご予約の変更やキャンセルは 1日 前までにお願いいたします。"
        raw_subject = "【予約確定】{{ resource_name }} との予約が確定しました"
        logo_url = "https://via.placeholder.com/80x80/d4af37/ffffff?text=Veludo"
        service_name = "60分VRASMR施術コース (PCVR)"

    # --- 2. 修复时区问题 (UTC -> JST) ---
    # 定义日本时区
    jst = pytz.timezone('Asia/Tokyo')
    
    # 将数据库的 UTC 时间转换为 JST
    start_jst = booking.start_time.astimezone(jst)
    end_jst = booking.end_time.astimezone(jst)

    # 格式化时间字符串
    date_str = start_jst.strftime('%Y年%m月%d日')  # 2025年4月28日
    time_range_str = f"{start_jst.strftime('%H:%M')} - {end_jst.strftime('%H:%M')}" # 23:30 - 00:30

    # --- 3. 准备上下文变量 ---
    ctx = {
        'customer_name': booking.customer_name,
        'resource_name': booking.resource.name,
        'tenant_name': booking.tenant.name,
        'service_name': service_name,
        
        # 使用转换后的 JST 时间
        'start_date': date_str,
        'time_range': time_range_str,
        
        'email_title': t_title,
        'email_greeting': t_greeting,
        'button_text': t_btn_text,
        'button_link': t_btn_link,
        'footer_title': t_footer_title,
        'footer_text': t_footer_text,
        'logo_url': logo_url, # ✅ 绝对路径
    }

    # --- 4. HTML 邮件骨架 (样式内联) ---
    html_template = """
    <!DOCTYPE html>
    <html>
    <head>
    <style>
      body { font-family: "Helvetica Neue", Arial, sans-serif; background-color: #ffffff; color: #333; margin: 0; padding: 0; }
      .container { max-width: 600px; margin: 0 auto; text-align: center; padding: 40px 20px; }
      .logo { width: 80px; height: auto; margin-bottom: 20px; border-radius: 50%; }
      .title { font-size: 24px; font-weight: bold; margin-bottom: 30px; letter-spacing: 0.05em; color: #000; }
      .card { border: 1px solid #e5e5e5; padding: 40px 30px; border-radius: 8px; background: #fff; margin-bottom: 30px; text-align: center; }
      .date-block { font-weight: bold; font-size: 18px; margin-bottom: 20px; line-height: 1.6; color: #000; }
      .info-block { margin-bottom: 30px; color: #555; font-size: 14px; line-height: 1.8; }
      .highlight { background-color: #fceea7; padding: 0 4px; }
      .btn { display: inline-block; background-color: #f0e6cc; color: #5d5340; padding: 14px 50px; text-decoration: none; border-radius: 4px; font-weight: bold; font-size: 14px; margin-top: 10px; }
      .footer { border: 1px solid #e5e5e5; padding: 20px; border-radius: 8px; font-size: 12px; text-align: center; color: #666; }
      .footer-head { font-weight: bold; margin-bottom: 5px; font-size: 13px; color: #333; }
    </style>
    </head>
    <body>
      <div class="container">
        <img src="{{ logo_url }}" class="logo" alt="Logo">
        
        <div class="title">{{ email_title }}</div>
        
        <p style="text-align: left; margin-bottom: 20px;">
            {{ customer_name }} 様<br>
            {{ email_greeting }}
        </p>

        <div class="card">
          <div class="date-block">
            {{ start_date }}<br>
            {{ time_range }} 日本時間
          </div>
          
          <div class="info-block">
            <p>{{ service_name }}</p>
            <p>担当: {{ resource_name }}</p>
            <p><span class="highlight">{{ tenant_name }}</span></p>
          </div>

          <a href="{{ button_link }}" class="btn">{{ button_text }}</a>
          
          <div style="margin-top: 15px;">
             <a href="#" style="color: #d6c698; font-size: 12px; text-decoration: none;">予約変更またはキャンセル</a>
          </div>
        </div>

        <div class="footer">
          <div class="footer-head">{{ footer_title }}</div>
          <p style="margin:0;">{{ footer_text }}</p>
        </div>
      </div>
    </body>
    </html>
    """

    # --- 5. 渲染并发送 ---
    try:
        final_subject = Template(raw_subject).render(Context(ctx))
        final_html = Template(html_template).render(Context(ctx))
        text_content = strip_tags(final_html)

        recipient_list = [booking.customer_email]
        
        # 检查是否也要发给 Cast (资源)
        try:
            # 重新获取 tpl 以确保逻辑严谨 (如果上面用了默认值则跳过)
            tpl = EmailTemplate.objects.get(tenant=booking.tenant, event_type='BOOKING_CONFIRMED')
            if tpl.send_to_cast and booking.resource.email:
                recipient_list.append(booking.resource.email)
        except:
            # 如果没配置模板，默认也发给资源
            if booking.resource.email:
                recipient_list.append(booking.resource.email)

        msg = EmailMultiAlternatives(final_subject, text_content, settings.DEFAULT_FROM_EMAIL, recipient_list)
        msg.attach_alternative(final_html, "text/html")
        msg.send()
        print(f"[Email] Sent successfully to {recipient_list}")
        
    except Exception as e:
        print(f"[Email Error] {e}")

# =========================================================
# 其他 Webhook 和 View 逻辑保持不变...
# =========================================================

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
        "status": booking.status
    }
    try:
        requests.post(tenant.webhook_url, json=payload, timeout=5)
    except Exception as e:
        print(f"[Webhook Error] {e}")

def send_cancellation_email(resource_email, resource_name, customer_name, start_time, end_time):
    if not resource_email: return
    subject = f"【予約キャンセル】{customer_name} 様の予約がキャンセルされました"
    message = f"""
    {resource_name} 様
    
    以下の予約がキャンセルされました。
    
    顧客名: {customer_name}
    予約日時: {start_time}
    
    システムより自動送信
    """
    try:
        send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [resource_email], fail_silently=False)
    except Exception as e:
        print(f"[Email Error] Cancellation email failed: {e}")

# =========================================================
# 2. 预约视图 (IntegrationBookingView)
# =========================================================
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
            # 同步更新名字
            if resource_name_from_a and resource.name != resource_name_from_a:
                resource.name = resource_name_from_a
                resource.save()
        except Resource.DoesNotExist:
            return Response({'error': 'Resource not found.'}, status=404)

        # 冲突检测
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

                # 后台任务：发送邮件和 Webhook
                def run_background_tasks():
                    send_booking_emails(booking)
                    trigger_webhook(booking)

                transaction.on_commit(lambda: threading.Thread(target=run_background_tasks).start())
                
        except Exception as e:
            return Response({'error': str(e)}, status=500)

        return Response({'booking_id': str(booking.id), 'status': booking.status}, status=201)

    def get(self, request):
        """Debug查询预约"""
        email = request.query_params.get('customer_email')
        resource_id = request.query_params.get('resource_id')
        
        queryset = Booking.objects.filter(tenant=request.tenant).order_by('-start_time')

        if email:
            queryset = queryset.filter(customer_email=email)
        if resource_id:
            try:
                uuid_obj = UUID(resource_id)
                queryset = queryset.filter(resource__id=uuid_obj)
            except ValueError:
                queryset = queryset.filter(resource__external_id=resource_id)

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
        s_time = booking.start_time
        e_time = booking.end_time

        if (booking.start_time - timezone.now()) < timedelta(hours=2):
            return Response({'error': 'Cancellation allows only 2 hours in advance.'}, status=400)
        
        booking.delete() 
        threading.Thread(target=send_cancellation_email, args=(r_email, r_name, c_name, s_time, e_time)).start()
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