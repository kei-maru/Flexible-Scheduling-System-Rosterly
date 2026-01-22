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
    发送预约确认邮件（已区分顾客和Cast的发送内容）
    """
    # --- 1. 获取模板配置 ---
    try:
        tpl = EmailTemplate.objects.get(tenant=booking.tenant, event_type='BOOKING_CONFIRMED')
        
        # 基础配置
        t_btn_text = tpl.button_text
        t_btn_link = tpl.button_link
        t_footer_title = tpl.footer_title
        t_footer_text = tpl.footer_text
        raw_subject = tpl.subject_template
        service_name = tpl.service_name

        # 处理 Logo (绝对路径)
        BASE_DOMAIN = "http://161.33.129.157" 
        if tpl.logo:
            logo_url = f"{BASE_DOMAIN}{tpl.logo.url}"
        else:
            logo_url = "https://via.placeholder.com/80x80/d4af37/ffffff?text=Veludo"

    except EmailTemplate.DoesNotExist:
        # 兜底默认值
        return # 如果连模板都没有，建议直接返回或记录日志

    # --- 2. 时区处理 (UTC -> JST) ---
    jst = pytz.timezone('Asia/Tokyo')
    start_jst = booking.start_time.astimezone(jst)
    end_jst = booking.end_time.astimezone(jst)
    date_str = start_jst.strftime('%Y年%m月%d日')
    time_range_str = f"{start_jst.strftime('%H:%M')} - {end_jst.strftime('%H:%M')}"

    # --- 3. 定义发送帮助函数 (避免代码重复) ---
    def _send_single_email(recipient_email, recipient_name, email_title, email_greeting, is_cast=False):
        """
        内部函数：发送单封邮件
        """
        ctx = {
            'customer_name': recipient_name, # 这里复用变量名，但在HTML里显示为"XX 様"
            'resource_name': booking.resource.name,
            'tenant_name': booking.tenant.name,
            'service_name': service_name,
            'start_date': date_str,
            'time_range': time_range_str,
            'email_title': email_title,
            'email_greeting': email_greeting,
            'button_text': t_btn_text,
            'button_link': t_btn_link,
            'footer_title': t_footer_title,
            'footer_text': t_footer_text,
            'logo_url': logo_url,
        }
        
        # HTML 模板 (保持不变)
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
        
        /* 👇 【修改了这里】字体变大(18px)、加粗(bold)、颜色变深(#000) */
        .info-block { 
            margin-bottom: 30px; 
            color: #000; 
            font-size: 18px; 
            font-weight: bold; 
            line-height: 1.6; 
        }

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
                <p style="margin: 5px 0;">{{ service_name }}</p>
                <p style="margin: 5px 0;">担当: {{ resource_name }}</p>
                <p style="margin: 5px 0;">{{ tenant_name }}</p>
            </div>

            <a href="{{ button_link }}" class="btn">{{ button_text }}</a>
            
            <div style="margin-top: 15px;">
                <a href="#" style="color: #d6c698; font-size: 12px; text-decoration: none;">予約確認・変更</a>
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

        try:
            # Cast 邮件可以使用不同的标题
            subject_prefix = "【キャスト通知】" if is_cast else ""
            final_subject = subject_prefix + Template(raw_subject).render(Context(ctx))
            
            final_html = Template(html_template).render(Context(ctx))
            text_content = strip_tags(final_html)

            msg = EmailMultiAlternatives(final_subject, text_content, settings.DEFAULT_FROM_EMAIL, [recipient_email])
            msg.attach_alternative(final_html, "text/html")
            msg.send()
            print(f"[Email] Sent to {recipient_name} ({recipient_email})")
        except Exception as e:
            print(f"[Email Error] Failed to send to {recipient_email}: {e}")

    # --- 4. 执行发送逻辑 ---
    
    # A. 发送给 顾客 (使用模板里的标准欢迎语)
    if tpl.send_to_customer:
        _send_single_email(
            recipient_email=booking.customer_email,
            recipient_name=booking.customer_name, # 称呼：顾客名
            email_title=tpl.email_title,
            email_greeting=tpl.email_greeting,     # 问候：模板里的“感谢预约...”
            is_cast=False
        )
    
    # B. 发送给 Cast (使用定制的通知语)
    if tpl.send_to_cast and booking.resource.email:
        # Cast 的问候语需要告知是谁预约了
        cast_greeting_text = f"お客様（{booking.customer_name} 様）より新しい予約が入りました。"
        cast_title_text = "新着予約通知"
        
        _send_single_email(
            recipient_email=booking.resource.email,
            recipient_name=booking.resource.name,  # 称呼：Cast名
            email_title=cast_title_text,
            email_greeting=cast_greeting_text,     # 问候：通知有新预约
            is_cast=True
        )

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