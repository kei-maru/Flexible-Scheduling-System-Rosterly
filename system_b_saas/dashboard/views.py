# system_b_saas/dashboard/views.py
import json
from django.core.serializers.json import DjangoJSONEncoder
from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.contrib import messages
from django.conf import settings
from bookings.models import Booking
from resources.models import Resource, EmailTemplate
from tenants.models import Tenant


class DashboardLoginView(TemplateView):
    template_name = "dashboard/login.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["discord_oauth_ready"] = bool(
            settings.SYSTEM_B_DISCORD_CLIENT_ID and settings.SYSTEM_B_DISCORD_SECRET
        )
        return context


class TenantDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard/tenant_dashboard.html"

    def post(self, request, *args, **kwargs):
        if request.POST.get('save_template') == 'true':
            try:
                # 获取租户 (兼容逻辑)
                tenant = getattr(request, 'tenant', None)
                if not tenant and hasattr(request.user, 'tenant'): tenant = request.user.tenant
                if not tenant: tenant = Tenant.objects.first()

                event_type = request.POST.get('event_type')
                send_to_customer = request.POST.get('send_to_customer') == 'on'
                send_to_cast = request.POST.get('send_to_cast') == 'on'
                
                # 构造更新数据
                defaults_data = {
                    'subject_template': request.POST.get('subject'),
                    'email_title': request.POST.get('email_title'),
                    'email_greeting': request.POST.get('email_greeting'),
                    'service_name': request.POST.get('service_name'), # 新增服务名
                    'button_text': request.POST.get('button_text'),
                    'button_link': request.POST.get('button_link'),
                    'footer_title': request.POST.get('footer_title'),
                    'footer_text': request.POST.get('footer_text'),
                    'send_to_customer': send_to_customer,
                    'send_to_cast': send_to_cast,
                    'is_active': True
                }

                # 处理图片上传 (如果有新图片传上来)
                if 'logo' in request.FILES:
                    defaults_data['logo'] = request.FILES['logo']

                EmailTemplate.objects.update_or_create(
                    tenant=tenant,
                    event_type=event_type,
                    defaults=defaults_data
                )
                
                messages.success(request, f"Template '{event_type}' saved successfully!")
            except Exception as e:
                messages.error(request, f"Error: {str(e)}")
            
            return redirect('tenant_dashboard')
            
        return self.get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # ... (获取 tenant, orders, resources 逻辑保持不变) ...
        tenant = getattr(self.request, 'tenant', None)
        if not tenant and hasattr(self.request.user, 'tenant'): tenant = self.request.user.tenant
        if not tenant: tenant = Tenant.objects.first()

        if tenant:
            context['orders'] = Booking.objects.filter(tenant=tenant).order_by('-created_at')[:50]
            context['resources'] = Resource.objects.filter(tenant=tenant)
            
            # 👇 【关键修改】一次性把所有类型的模板都取出来传给前端 JS
            templates_data = {}
            for event_type in ['BOOKING_CONFIRMED', 'BOOKING_CANCELLED']:
                try:
                    t = EmailTemplate.objects.get(tenant=tenant, event_type=event_type)
                    logo_url = t.logo.url if t.logo else ""
                    templates_data[event_type] = {
                        'subject': t.subject_template,
                        'email_title': t.email_title,
                        'email_greeting': t.email_greeting,
                        'service_name': t.service_name,
                        'button_text': t.button_text,
                        'button_link': t.button_link,
                        'footer_title': t.footer_title,
                        'footer_text': t.footer_text,
                        'logo_url': logo_url,
                        'send_to_customer': t.send_to_customer,
                        'send_to_cast': t.send_to_cast
                    }
                except EmailTemplate.DoesNotExist:
                    # 默认值
                    is_cancel = (event_type == 'BOOKING_CANCELLED')
                    templates_data[event_type] = {
                        'subject': "【予約キャンセル】" if is_cancel else "【予約確定】",
                        'email_title': "予約キャンセルのお知らせ" if is_cancel else "予約が確定しました。",
                        'email_greeting': "予約がキャンセルされました。" if is_cancel else "以下の内容で予約を承りました。",
                        'service_name': "60分VRASMR施術コース (PCVR)",
                        'button_text': "トップページへ" if is_cancel else "詳細を見る",
                        'button_link': "#",
                        'footer_title': "当社のキャンセルポリシー",
                        'footer_text': "...",
                        'logo_url': "",
                        'send_to_customer': True, # 默认都发
                        'send_to_cast': True
                    }
            
            # 把 Python 字典转成 JSON 字符串，方便 JS 读取
            context['templates_json'] = json.dumps(templates_data)
            
        return context
