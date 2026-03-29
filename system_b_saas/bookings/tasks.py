# system_b_saas/bookings/tasks.py

from celery import shared_task
from django.core.mail import send_mail, EmailMultiAlternatives
from django.conf import settings
from django.template import Template, Context
from django.utils.html import strip_tags
from django.utils import timezone
from django.urls import reverse
import pytz
import requests
import os  
import secrets
from email.mime.image import MIMEImage 

from resources.models import EmailTemplate
from bookings.models import Booking


def _build_public_booking_detail_url(booking):
    token = (booking.public_access_token or "").strip() or secrets.token_urlsafe(24)
    path = reverse("dashboard_public_booking_detail", kwargs={"access_token": token})
    base = (getattr(settings, "SYSTEM_B_PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
    if base:
        url = f"{base}{path}"
    else:
        url = path

    update_fields = []
    if booking.public_access_token != token:
        booking.public_access_token = token
        update_fields.append("public_access_token")
    if booking.public_detail_url != url:
        booking.public_detail_url = url
        update_fields.append("public_detail_url")
    if update_fields:
        booking.save(update_fields=update_fields)
    return url

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
        booking_public_url = _build_public_booking_detail_url(booking)
        t_btn_link = (tpl.button_link or "").strip() or booking_public_url
        t_footer_title = tpl.footer_title
        t_footer_text = tpl.footer_text or "キャンセルは予定時刻の二十四時間前までにDisocordまたはEmailにて連絡"
        raw_subject = tpl.subject_template

        # Logo source priority: tenant logo > template logo
        logo_fs_path = None
        tenant_logo = getattr(booking.tenant, "logo", None)
        if tenant_logo:
            try:
                logo_fs_path = tenant_logo.path
            except Exception:
                logo_fs_path = None
        if not logo_fs_path and tpl.logo:
            try:
                logo_fs_path = tpl.logo.path
            except Exception:
                logo_fs_path = None

    except EmailTemplate.DoesNotExist:
        print("[Email Warning] No EmailTemplate found.")
        return

    jst = pytz.timezone('Asia/Tokyo')
    start_jst = booking.start_time.astimezone(jst)
    end_jst = booking.end_time.astimezone(jst)
    duration_minutes = int((booking.end_time - booking.start_time).total_seconds() // 60)
    if duration_minutes <= 0:
        duration_minutes = 60
    duration_hours = round(duration_minutes / 60, 2)
    date_str = start_jst.strftime('%Y年%m月%d日')
    time_range_str = f"{start_jst.strftime('%H:%M')} - {end_jst.strftime('%H:%M')}"

    # Baseline context from booking DB values.
    base_ctx = {
        'resource_name': booking.resource.name,
        'tenant_name': booking.tenant.name,
        'start_date': date_str,
        'time_range': time_range_str,
        'duration_minutes': duration_minutes,
        'duration_hours': duration_hours,
        'selected_service_name': (booking.selected_service_name or '').strip(),
    }

    def _render_text_with_duration(text, ctx):
        """Render template vars; keep backward compatibility for old fixed '60分' strings."""
        if text is None:
            return ""
        text = str(text)
        try:
            rendered = Template(text).render(Context(ctx))
        except Exception:
            rendered = text
        if "{{" not in text and "60分" in rendered:
            rendered = rendered.replace("60分", f"{duration_minutes}分")
        return rendered

    booking_selected_service = (booking.selected_service_name or "").strip()
    dynamic_service_name_default = f"{duration_minutes}分VRASMR施術コース (PCVR)"
    if booking_selected_service:
        service_name = booking_selected_service
    elif tpl.service_name and tpl.service_name.strip():
        rendered_service_name = _render_text_with_duration(tpl.service_name, base_ctx)
        service_name = rendered_service_name or dynamic_service_name_default
    else:
        service_name = dynamic_service_name_default

    def _send_single(recipient_email, recipient_name, email_title, email_greeting, is_cast=False):

        logo_src = "cid:shop_logo" if logo_fs_path else "https://via.placeholder.com/80x80/d4af37/ffffff?text=Veludo"

        ctx = {
            'customer_name': recipient_name,
            'resource_name': booking.resource.name,
            'tenant_name': booking.tenant.name,
            'service_name': service_name,
            'selected_service_name': (booking.selected_service_name or '').strip(),
            'start_date': date_str,
            'time_range': time_range_str,
            'duration_minutes': duration_minutes,
            'duration_hours': duration_hours,
            'email_title': email_title,
            'email_greeting': email_greeting,
            'button_text': t_btn_text,
            'button_link': t_btn_link,
            'booking_public_url': booking_public_url,
            'footer_title': t_footer_title,
            'footer_text': t_footer_text,
            'logo_url': logo_src, # ✅ 关键：这里传的是 CID 字符串
        }
        raw_subject_or_default = raw_subject or "【{{ tenant_name }}：ご予約日時のお知らせ】"
        resolved_subject = _render_text_with_duration(raw_subject_or_default, ctx)
        resolved_email_title = _render_text_with_duration(email_title, ctx)
        resolved_email_greeting = _render_text_with_duration(email_greeting, ctx)
        ctx['email_title'] = resolved_email_title
        ctx['email_greeting'] = resolved_email_greeting
        ctx['button_link'] = _render_text_with_duration(t_btn_link, ctx)
        
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
        final_subject = subject_prefix + resolved_subject
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

    if tpl.send_to_customer and booking.customer_email:
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
