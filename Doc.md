# Veludo Booking System - Development & Maintenance Documentation

**Version:** 1.0.0 (Milestone 3 - Phase 1)
**Last Updated:** 2026-01-08
**Author:** Veludo Dev Team

---

## 1. 系统架构概览 (System Architecture)

Veludo 采用**前后端分离 (Decoupled) + SaaS 核心**的双系统架构。此设计确保了用户端（System A）的轻量化与核心业务逻辑（System B）的安全性与多租户扩展能力。

### 1.1 组件定义
* **System A (Veludo Client / Frontend App)**
    * **角色**: 面向最终用户（Guest & Cast）的 Web 门户。
    * **职责**: 页面渲染 (SSR)、用户认证 (Auth)、UI 交互、请求转发。
    * **数据存储**: 仅存储用户账户信息 (User, Profile)；**不存储**具体的排班与预约数据。
    * **技术栈**: Django 5.x, TailwindCSS, Vanilla JS。

* **System B (Veludo SaaS / Backend Core)**
    * **角色**: 核心业务中台 (Headless CMS / API Provider)。
    * **职责**: 排班管理、预约存储、冲突检测、邮件通知、租户鉴权。
    * **数据存储**: 核心业务数据 (Availability, Booking, Resource)。
    * **技术栈**: Django REST Framework (DRF), PostgreSQL.

### 1.2 通信协议
* **方式**: HTTP RESTful API (Synchronous).
* **鉴权**: Header `X-Tenant-Key`.
* **数据交换格式**: JSON.
* **网络拓扑**: System A 后端直接与 System B 后端通信 (Server-to-Server)，前端不直接连接 System B。

---

## 2. 核心业务流程 (Core Business Logic)

### 2.1 预约生命周期 (Booking State Machine)

| 状态 (Status) | 触发动作 | 描述 | 权限控制 |
| :--- | :--- | :--- | :--- |
| **CONFIRMED** | 用户创建预约 | 预约成立，占用排班槽位。 | Cast/Guest 均可见 |
| **LOCKED** | 距离开始 < 2小时 | 系统自动判定 (非数据库字段)。 | 禁止 Guest 取消 |
| **COMPLETED** | Cast 点击 "Complete" | 业务结束，用于结算。 | 仅 Cast 可操作 |

### 2.2 角色视图逻辑 (Role-Based Views)

系统根据 `request.user.is_cast` 属性在同一页面 (`MyBookingsPageView`) 渲染不同数据：

* **Guest (客人)**:
    * **查询**: `GET /bookings/?customer_email={user.email}`
    * **显示**: 预约对象为 **Cast 的名字**。
    * **操作**: 允许在开始时间 **2小时前** 取消。

* **Cast (角色)**:
    * **查询**: `GET /bookings/?resource_id={profile.saas_resource_id}`
    * **数据伪装 (Data Masking)**: System A 后端自动将 `resource_name` 字段替换为 `Guest: {customer_name}`，以便前端复用卡片 UI。
    * **操作**:
        * **禁止取消**: 取消按钮隐藏或显示锁定提示。
        * **业务完结**: 当状态为 `CONFIRMED` 时，显示金色 **COMPLETE WORK** 按钮。

---

## 3. 接口规范 (System B API Specification)

所有接口位于 `/api/v1/integration/` 下，需携带 Header `X-Tenant-Key: <SECRET>`.

### 3.1 预约管理 (Bookings)

#### `GET /bookings/`
查询预约列表。

* **Parameters**:
    * `customer_email` (Optional): 筛选特定客人的预约。
    * `resource_id` (Optional): 筛选特定 Cast (Resource) 的被预约记录。
* **Response**:
    ```json
    [
      {
        "id": "UUID-STRING",
        "resource_name": "Cast A",
        "customer_name": "Guest B",
        "customer_email": "guest@example.com",
        "start": "2026-01-10T15:00:00+09:00",
        "end": "2026-01-10T16:00:00+09:00",
        "status": "CONFIRMED"
      }
    ]
    ```

#### `POST /bookings/`
创建新预约。

* **Payload**:
    ```json
    {
      "resource_id": "external_id_123",
      "start_time": "ISO_8601_STRING",
      "end_time": "ISO_8601_STRING",
      "customer_email": "user@example.com",
      "customer_name": "User Name"
    }
    ```
* **Logic**:
    * 检查 `resource_id` 是否存在。
    * **冲突检测**: 检查 `(Start - 30min)` 至 `(End + 30min)` 范围内是否存在已确认预约。
    * **异步通知**: 事务提交后触发邮件与 Webhook。

#### `DELETE /bookings/{uuid}/`
取消预约。

* **Logic**:
    * 校验 `CurrentTime` 与 `StartTime` 的差值。
    * 如果 `< 2 hours`，返回 `400 Bad Request`。
    * 删除成功返回 `204 No Content`。

#### `PATCH /bookings/{uuid}/`
更新预约状态。

* **Payload**: `{"status": "COMPLETED"}`
* **Logic**:
    * 仅允许从 `CONFIRMED` -> `COMPLETED`。
    * 更新成功返回 `200 OK`。

---

## 4. System A (Client) 详细实现

### 4.1 通信层 (`utils/saas_client.py`)

封装了 `requests` 库，负责 URL 拼接与错误处理。

* **配置项**:
    * `api_base_url`: `http://127.0.0.1:8001/api/v1/integration` (注意不含尾部斜杠，避免双重拼接)。
    * `headers`: 包含 `Content-Type` 和 `X-Tenant-Key`。

* **关键方法**:
    * `get_cast_bookings(resource_id)`: 专门为 Cast 设计的查询方法，调用 `GET /bookings/?resource_id=...`。
    * `complete_booking(booking_id)`: 发送 PATCH 请求实现业务完结。

### 4.2 视图层 (`accounts/views.py`)

* **`MyBookingsPageView` (TemplateView)**
    * **Context 注入**:
        * `is_cast`: Boolean, 用于前端逻辑分支。
        * `bookings`: List, 处理过的数据列表。
    * **数据处理**:
        * 如果是 Cast 身份，遍历数据并将 `resource_name` 覆写为 `Guest: {customer_name}`。
        * 将 ISO 时间字符串转换为 Django `datetime` 对象。

* **`BookingCompleteAPI` (APIView)**
    * **Endpoint**: `/accounts/api/booking/complete/<str:pk>/`
    * **参数**: `pk` 必须定义为 `<str>` 或 `<uuid>` 以兼容 System B 的 UUID 格式。
    * **鉴权**: 强制检查 `request.user.is_cast`，防止 Guests 恶意调用接口完结订单。

### 4.3 前端交互 (`templates/my_bookings.html`)

* **CSS (Tailwind)**:
    * 使用 Jinja2 `{% if %}` 标签动态渲染状态标签的颜色（CONFIRMED 为绿色，COMPLETED 为金色）。
* **JavaScript Logic**:
    * **Global Constant**: `const IS_CAST = "{{ is_cast|yesno:'true,false' }}" === "true";`
    * **Button Visibility**:
        * `#btn-cancel`: 仅在 (Is Guest AND Time > 2h) 时显示。
        * `#btn-complete`: 仅在 (Is Cast AND Status == 'CONFIRMED') 时显示。
    * **Modal Logic**:
        * 点击卡片 -> `showDetail(this)` -> 从 `data-*` 属性读取数据 -> 填充 DOM -> 计算时间差/权限 -> 显示 Modal。

---

## 5. 安全性与性能 (Security & Performance)

### 5.1 安全措施
1.  **ID 隔离**: System A 的 User ID 与 System B 的 Resource ID 通过 `CastProfile.saas_resource_id` 映射，不直接暴露内部主键。
2.  **后端校验**: 即使前端隐藏了按钮，System A 的 `BookingCompleteAPI` 和 `BookingCancelAPI` 均在后端再次校验了用户权限 (`is_cast` check)。
3.  **时间锁**: 取消限制 (2小时) 在 System A 前端 (UX) 和 System B 后端 (Database Guard) 双重实施。

### 5.2 性能优化
1.  **异步任务**: System B 使用 `threading.Thread` 配合 `transaction.on_commit` 处理邮件发送，确保 HTTP 响应在 200ms 内完成，不受 SMTP 延迟影响。
2.  **按需查询**: 列表查询接口支持 `resource_id` 和 `email` 索引过滤，避免全表扫描。

---

## 6. 环境配置要求 (Configuration)

### System A (`settings.py`)
```python
SAAS_API_URL = "[http://127.0.0.1:8001/api/v1/integration](http://127.0.0.1:8001/api/v1/integration)"
SAAS_API_KEY = "veludo_secret_key_123"


### System B (settings.py)

# 邮件配置 (用于发送通知)
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
DEFAULT_FROM_EMAIL = 'no-reply@veludo.jp'