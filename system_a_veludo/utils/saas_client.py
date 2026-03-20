import requests
from django.conf import settings
import datetime

class SaaSClient:
    """
    System A (Veludo) -> System B (SaaS) 通信客户端
    基于架构文档 Milestone 1 & Phase 1 规范
    """
    def __init__(self):
        # [基础配置]
        # 默认端口假定为 8001，路径包含 /integration
        self.api_base_url = getattr(settings, 'SAAS_API_URL', 'http://127.0.0.1:8001/api/v1/integration')
        
        self.api_key = getattr(settings, 'veludo_secret_key_123', '') 
        
        self.headers = {
            "X-Tenant-Key": settings.SAAS_API_KEY,
            'Content-Type': 'application/json',
        }

    # ========================================================
    # Availability (排班管理)
    # ========================================================

    def get_availabilities(self, resource_id=None, start_date=None, end_date=None):
        """
        [基础查询] 获取原始排班 (Raw Data)
        API: GET /availability/
        """
        url = f"{self.api_base_url}/availability/" 
        params = {}

        # [新增] 如果没有传日期，默认查询 未来30天 的数据
        if not start_date and not end_date:
            today = datetime.date.today()
            next_month = today + datetime.timedelta(days=30)
            params['start'] = today.isoformat()
            params['end'] = next_month.isoformat()
        else:
            if start_date: params['start'] = start_date 
            if end_date: params['end'] = end_date

        if resource_id: params['resource_id'] = resource_id
        if start_date: params['start'] = start_date 
        if end_date: params['end'] = end_date
        
        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=5)
            if response.status_code == 400:
                print(f"❌ SaaS 400 Error Detail: {response.text}")
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"SaaS API GET Error: {e}")
            return []

    def create_availability(self, resource_id, start_time, end_time):
        """
        [单次排班]
        API: POST /availability/
        """
        url = f"{self.api_base_url}/availability/"
        data = {
            'resource_id': resource_id,
            'start': start_time, 
            'end': end_time      
        }
        try:
            response = requests.post(url, headers=self.headers, json=data, timeout=5)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"SaaS Create Error: {e}")
            if e.response is not None:
                print(f"SaaS Response: {e.response.text}")
            return None

    def get_recurring_config(self, resource_id):
        """
        [新增] 获取周期排班规则
        API: GET /integration/availability/recurring-config/
        """
        # 注意 URL 拼写，必须和 System B 的 urls.py 一致
        url = f"{self.api_base_url}/availability/recurring-config/"
        params = {'resource_id': resource_id}
        
        print(f"[SaaSClient] Requesting URL: {url}") # Debug
        
        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=5)
            
            if response.status_code == 404:
                print("[SaaSClient] 404 Not Found (Normal for new users)")
                return {}
                
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"[SaaSClient] Error: {e}")
            if e.response:
                print(f"[SaaSClient] Response Content: {e.response.text}")
            return {}

    def create_recurring_availability(self, resource_id, range_start, range_end, week_config):
        """
        [新增 - 周期排班]
        API: POST /availability/ (复用同一个接口，但 Payload 不同)
        """
        url = f"{self.api_base_url}/availability/"
        data = {
            'resource_id': resource_id,
            'range_start': range_start,
            'range_end': range_end,
            'week_config': week_config # JSON Object
        }
        try:
            response = requests.post(url, headers=self.headers, json=data, timeout=5)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"SaaS Recurring Error: {e}")
            if e.response is not None:
                print(f"SaaS Response: {e.response.text}")
            return None

    def delete_availability(self, availability_id):
        """
        [删除排班]
        API: DELETE /availability/{id}/
        """
        url = f"{self.api_base_url}/availability/{availability_id}/"
        try:
            response = requests.delete(url, headers=self.headers, timeout=5)
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            print(f"SaaS Delete Error: {e}")
            return False

    def check_availability(self, resource_id, start_dt, end_dt):
        """
        [智能搜索 - Guest端]
        调用 System B 的计算引擎 (mode='search')
        """
        if not resource_id: return []
            
        params = {
            'resource_id': resource_id,
            'start': start_dt.isoformat(),
            'end': end_dt.isoformat(),
            'mode': 'search'  # 【关键】告诉 System B 启用 search 计算逻辑
        }
        
        try:
            # [修正] 之前这里写错了，这里不需要再加 /api/v1/integration
            url = f"{self.api_base_url}/availability/"
            
            response = requests.get(url, params=params, headers=self.headers, timeout=5)
            if response.status_code == 200:
                return response.json() 
            return []
        except Exception as e:
            print(f"SaaS Search Error: {e}")
            return []

    def get_calendar_events(self, resource_id, start_date, end_date):
        """
        [智能日历 - Cast端]
        调用 System B 的日历渲染逻辑 (mode='calendar_admin')
        """
        url = f"{self.api_base_url}/availability/"
        params = {
            'resource_id': resource_id,
            'start': start_date,
            'end': end_date,
            'mode': 'calendar_admin' # 【关键】告诉 System B 启用 admin 计算逻辑
        }
        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=5)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Get Calendar Events Error: {e}")
            return []

    # ========================================================
    # Template (模版管理) - 新增
    # ========================================================

    def get_schedule_templates(self, resource_id):
        """
        [获取模版列表]
        API: GET /availability/templates/
        """
        # 注意: 这里不需要加 /api/v1/integration，因为 self.api_base_url 已经包含了
        url = f"{self.api_base_url}/availability/templates/"
        params = {'resource_id': resource_id}
        
        try:
            print(f"[SaaSClient] Getting Templates from: {url}")
            response = requests.get(url, headers=self.headers, params=params, timeout=5)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"[SaaSClient] Get Templates Error: {e}")
            if e.response: print(f"Detail: {e.response.text}")
            return []

    def save_schedule_template(self, resource_id, name, week_config):
        """
        [保存模版]
        API: POST /availability/templates/
        """
        url = f"{self.api_base_url}/availability/templates/"
        data = {
            'resource_id': resource_id,
            'name': name,
            'week_config': week_config
        }
        
        try:
            print(f"[SaaSClient] Saving Template to: {url}")
            response = requests.post(url, headers=self.headers, json=data, timeout=5)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"[SaaSClient] Save Template Error: {e}")
            if e.response: print(f"Detail: {e.response.text}")
            return None

    # ========================================================
    # Booking (预约管理)
    # ========================================================

    def create_booking(self, resource_id, resource_name, email, name, start, end, course_duration_minutes=None):
        """提交预约"""
        url = f"{self.api_base_url}/bookings/"
        data = {
            'resource_id': resource_id,
            'resource_name': resource_name,
            'customer_email': email,
            'customer_name': name,
            'start_time': start,
            'end_time': end
        }
        if course_duration_minutes is not None:
            data['course_duration_minutes'] = int(course_duration_minutes)
        try:
            response = requests.post(url, headers=self.headers, json=data, timeout=5)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"Booking Error: {e}")
            if e.response: print(f"Detail: {e.response.text}")
            return None

    def get_my_bookings(self, email=None, resource_id=None, customer_name=None, customer_id=None, admin_sync=False):
        """
        查询预约 (Guest/Cast)
        ✅ 修复：增加 customer_name 支持，且增加安全熔断，防止查出所有数据。
        """
        url = f"{self.api_base_url}/bookings/"
        params = {}

        # 1. 如果是 Cast，用 Resource ID 查
        if resource_id:
            params['resource_id'] = resource_id
        
        # 2. 如果是 Guest (普通用户)
        else:
            if customer_id:
                params['customer_id'] = str(customer_id)
                
            # 3. 同时也传入 VRCID (keimaru22)
            # 为什么？因为你 System B 里现存的老数据没有 ID，只有 Name。
            # 传入这个可以让 System B 在查不到 ID 的时候，回落去查 Name。
            if customer_name:
                params['customer_name'] = customer_name

            if email:
                params['customer_email'] = email
        
        if admin_sync:
            params['sync_all'] = 'true'
        
        if not params and not admin_sync:
            print("[SaaSClient] ⚠️ Security Warning: No parameters provided. Aborting.")
            return []

        try:
            print(f"[SaaSClient] Requesting: {url} with params {params}")
            response = requests.get(url, headers=self.headers, params=params, timeout=5)
            
            # print(f"[SaaSClient] Raw Response Status: {response.status_code}")
            
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"Get Bookings Error: {e}")
            return []

    def cancel_booking(self, booking_id):
        """取消预约"""
        url = f"{self.api_base_url}/bookings/{booking_id}/"
        try:
            response = requests.delete(url, headers=self.headers, timeout=5)
            if response.status_code == 204: return True
            return False
        except requests.RequestException as e:
            print(f"Cancel Booking Error: {e}")
            return False

    def complete_booking(self, booking_id):
        """完成预约 (改状态)"""
        url = f"{self.api_base_url}/bookings/{booking_id}/"
        data = {'status': 'COMPLETED'}
        try:
            response = requests.patch(url, headers=self.headers, json=data, timeout=5)
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            print(f"Complete Booking Error: {e}")
            return False

    # ========================================================
    # Resource (资源同步)
    # ========================================================

    def sync_cast_to_saas(self, user_id, name, email):
        """同步 Cast 资料"""
        url = f"{self.api_base_url}/resources/"
        data = {
            'external_id': str(user_id),
            'name': name,
            'email': email
        }
        try:
            response = requests.post(url, headers=self.headers, json=data, timeout=5)
            response.raise_for_status()
            result = response.json()
            return result.get('saas_id')
        except requests.RequestException as e:
            print(f"Sync Cast Error: {e}")
            return None
