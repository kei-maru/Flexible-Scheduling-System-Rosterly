import requests
from django.conf import settings

class SaaSClient:
    """
    System A (Veludo) -> System B (SaaS) 通信客户端
    基于架构文档 Milestone 1 & Phase 1 规范
    """
    def __init__(self):
        # [基础配置]
        # 默认端口假定为 8001，路径包含 /integration
        self.api_base_url = getattr(settings, 'SAAS_API_URL', 'http://127.0.0.1:8001/api/v1/integration')
        
        self.api_key = getattr(settings, 'SAAS_API_KEY', '') 
        
        self.headers = {
            'Content-Type': 'application/json',
            'X-Tenant-Key': self.api_key, 
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
        if resource_id: params['resource_id'] = resource_id
        if start_date: params['start'] = start_date 
        if end_date: params['end'] = end_date
        
        try:
            response = requests.get(url, headers=self.headers, params=params)
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
            response = requests.post(url, headers=self.headers, json=data)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"SaaS Create Error: {e}")
            if e.response is not None:
                print(f"SaaS Response: {e.response.text}")
            return None

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
            response = requests.post(url, headers=self.headers, json=data)
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
            response = requests.delete(url, headers=self.headers)
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
    # Booking (预约管理)
    # ========================================================

    def create_booking(self, resource_id, resource_name, email, name, start, end):
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
        try:
            response = requests.post(url, headers=self.headers, json=data)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"Booking Error: {e}")
            if e.response: print(f"Detail: {e.response.text}")
            return None

    def get_my_bookings(self, email=None, resource_id=None):
        """查询预约 (Guest/Cast)"""
        url = f"{self.api_base_url}/bookings/"
        params = {}
        if resource_id: params['resource_id'] = resource_id
        elif email: params['customer_email'] = email
            
        try:
            response = requests.get(url, headers=self.headers, params=params)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"Get Bookings Error: {e}")
            return []

    def cancel_booking(self, booking_id):
        """取消预约"""
        url = f"{self.api_base_url}/bookings/{booking_id}/"
        try:
            response = requests.delete(url, headers=self.headers)
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
            response = requests.patch(url, headers=self.headers, json=data)
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
            response = requests.post(url, headers=self.headers, json=data)
            response.raise_for_status()
            result = response.json()
            return result.get('saas_id')
        except requests.RequestException as e:
            print(f"Sync Cast Error: {e}")
            return None