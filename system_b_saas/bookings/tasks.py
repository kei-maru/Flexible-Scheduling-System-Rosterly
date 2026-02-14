# system_b_saas/bookings/tasks.py

from celery import shared_task
from django.core.mail import send_mail, EmailMultiAlternatives
from django.conf import settings
from django.template import Template, Context
from django.utils.html import strip_tags
from django.utils import timezone
import pytz
import requests
import os  
from email.mime.image import MIMEImage 

from resources.models import EmailTemplate
from bookings.models import Booking

# =========================================================
# 1. 发送预约确认邮件 (核心逻辑)
# =========================================================
@shared_task(bind=True, max_retries=3)
def process_new_booking(self, booking_id):
    """
    处理新预约的后台任务：
    1. 发送邮件给 顾客
    2. 发送邮件给 Cast
    3. 触发 Webhook
    """
    try:
        # 重新从数据库获取最新的 Booking 对象 (防止数据竞争)
        # select_related 优化查询，防止循环查库
        booking = Booking.objects.select_related('tenant', 'resource').get(id=booking_id)
    except Booking.DoesNotExist:
        print(f"[Task Error] Booking {booking_id} not found.")
        return

    # --- A. 发送邮件逻辑 ---
    try:
        _send_booking_emails_logic(booking)
    except Exception as e:
        print(f"[Task Error] Email sending failed: {e}")
        # 如果是网络错误，5秒后自动重试
        self.retry(exc=e, countdown=5)

    # --- B. 触发 Webhook 逻辑 ---
    try:
        _trigger_webhook_logic(booking)
    except Exception as e:
        print(f"[Task Error] Webhook failed: {e}")
        # Webhook 失败通常不重试，或者可以单独设置重试策略

# =========================================================
# 2. 取消预约通知任务
# =========================================================
@shared_task
def send_cancellation_email_task(resource_email, resource_name, customer_name, start_time_str):
    """
    发送取消通知给 Cast
    注意：接收简单参数，不要传递对象
    """
    if not resource_email: return

    subject = f"【予約キャンセル】{customer_name} 様の予約がキャンセルされました"
    message = f"""
    {resource_name} 様
    
    以下の予約がキャンセルされました。
    
    顧客名: {customer_name}
    予約日時: {start_time_str}
    
    システムより自動送信
    """
    try:
        send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [resource_email], fail_silently=False)
        print(f"[Email] Cancellation sent to {resource_email}")
    except Exception as e:
        print(f"[Email Error] Cancellation email failed: {e}")

# =========================================================
# 3. 内部辅助函数 (原 view 中的逻辑)
# =========================================================

def _send_booking_emails_logic(booking):
    """具体的邮件构建与发送逻辑 (CID 嵌入版)"""
    try:
        tpl = EmailTemplate.objects.get(tenant=booking.tenant, event_type='BOOKING_CONFIRMED')
        
        t_btn_text = tpl.button_text
        t_btn_link = tpl.button_link
        t_footer_title = tpl.footer_title
        t_footer_text = tpl.footer_text
        raw_subject = tpl.subject_template
        service_name = tpl.service_name

        # --- Logo 路径逻辑 ---
        # 1. 获取本地文件系统的绝对路径 (用于 open 读取)
        # 例如: /app/media/tenants/logos/shop.png
        logo_fs_path = tpl.logo.path if tpl.logo else None

    except EmailTemplate.DoesNotExist:
        print("[Email Warning] No EmailTemplate found.")
        return

    jst = pytz.timezone('Asia/Tokyo')
    start_jst = booking.start_time.astimezone(jst)
    end_jst = booking.end_time.astimezone(jst)
    date_str = start_jst.strftime('%Y年%m月%d日')
    time_range_str = f"{start_jst.strftime('%H:%M')} - {end_jst.strftime('%H:%M')}"

    def _send_single(recipient_email, recipient_name, email_title, email_greeting, is_cast=False):

        logo_src = "cid:shop_logo" if logo_fs_path else "https://via.placeholder.com/80x80/d4af37/ffffff?text=Veludo"

        ctx = {
            'customer_name': recipient_name,
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
            'logo_url': logo_src, # ✅ 关键：这里传的是 CID 字符串
        }
        
        # HTML 模板保持原样，不用动
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
        .info-block { margin-bottom: 30px; color: #000; font-size: 18px; line-height: 1.6; }
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
            
            <div style="margin-top: 15px;"></div>
            </div>

            <div class="footer">
            <div class="footer-head">{{ footer_title }}</div>
            <p style="margin:0;">{{ footer_text }}</p>
            </div>
        </div>
        </body>
        </html>
        """

        subject_prefix = "【キャスト通知】" if is_cast else ""
        final_subject = subject_prefix + Template(raw_subject).render(Context(ctx))
        final_html = Template(html_template).render(Context(ctx))
        text_content = strip_tags(final_html)

        msg = EmailMultiAlternatives(final_subject, text_content, settings.DEFAULT_FROM_EMAIL, [recipient_email])
        msg.attach_alternative(final_html, "text/html")
        
        # 3. 读取本地文件并作为附件嵌入
        if logo_fs_path:
            try:
                with open(logo_fs_path, 'rb') as f:
                    logo_data = f.read()
                
                logo_image = MIMEImage(logo_data)
                
                # 核心：给图片贴上身份证号，HTML里的 src="cid:shop_logo" 才能找到它
                logo_image.add_header('Content-ID', '<shop_logo>')
                logo_image.add_header('Content-Disposition', 'inline', filename='logo.png')
                
                msg.attach(logo_image)
                # print(f"[Email Debug] Attached local logo: {logo_fs_path}")
            
            except FileNotFoundError:
                print(f"[Email Error] Logo file not found: {logo_fs_path} (Check Docker volumes!)")
            except Exception as e:
                print(f"[Email Error] Failed to attach logo: {e}")

        msg.send()
        print(f"[Email] Sent to {recipient_name} ({recipient_email})")

    if tpl.send_to_customer:
        _send_single(booking.customer_email, booking.customer_name, tpl.email_title, tpl.email_greeting, is_cast=False)
    
    if tpl.send_to_cast and booking.resource.email:
        cast_greeting = f"お客様（{booking.customer_name} 様）より新しい予約が入りました。"
        _send_single(booking.resource.email, booking.resource.name, "新着予約通知", cast_greeting, is_cast=True)


def _trigger_webhook_logic(booking):
    """具体的 Webhook 逻辑"""
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
    requests.post(tenant.webhook_url, json=payload, timeout=5)
    print(f"[Webhook] Sent for booking {booking.id}")