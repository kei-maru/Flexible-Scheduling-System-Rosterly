# System A 和 B 的接口文档

## 目录
1. [系统概述](#系统概述)
2. [System A 接口](#system-a-接口)
3. [System B 接口](#system-b-インターフェース)

## 系统概述
- **System A** 是前端系统。
- **System B** 是后端 API。前端 System A 必须通过调用 System B 的 API 来获取数据。

## System A 接口

### 路由
- `admin/` - Django 后台管理界面
- ```` - 首页及静态页面
  - `/` - 主页 (`core.views.index`)
  - `/service/` - 服务页面 (静态模板)
  - `/access/` - 访问页面 (静态模板)
  - `/core/` - 核心功能路由 (包含于 `core.urls`)
- `/accounts/` - 用户账户管理相关路由（包含于 `accounts.urls`）
- `/booking/` - 预约页面 (`accounts.views.BookingPageView`)
- `/casts/` - 播客路由（包含于 `casts.urls`）

### 视图函数
#### 1. 埋点数据接收接口 (`track_activity`)
- **URL**: `/core/api/track/`
- **Method**: POST
- **输入**:
  - `action`: 动作名称 (字符串)
  - `target`: 目标对象 (字符串)
  - `meta`: 元数据 (JSON 对象，可选)
- **返回**:
  ```json
  {
    "status": "success"
  }
  ```
- **错误返回**:
  ```json
  {
    "status": "error",
    "message": "Error message"
  }
  ```

## System B 接口

### 路由
- `admin/` - Django 后台管理界面
- ```` - API v1 集成相关接口 (包含于 `resources.urls`)
  - `/api/v1/integration/availability/`
    - **GET** - 获取可用性列表 (`IntegrationAvailabilityView.get`)
      - **输入**: 
        - `mode`: 查询模式 (`raw` 或 `search`)
        - `resource_id`: 资源 ID
        - `start`: 开始时间 (ISO 8601 格式)
        - `end`: 结束时间 (ISO 8601 格式)
      - **返回**: 
        - 可用性列表（模式为 `raw`）
        - 搜索结果（模式为 `search`）
    - **POST** - 添加或更新排班 (`IntegrationAvailabilityView.post`)
      - **输入**: 
        - `resource_id`: 资源 ID
        - `start_time`: 开始时间 (ISO 8601 格式)
        - `end_time`: 结束时间 (ISO 8601 格式)
        - `week_config`: 周配置（用于周期性排班）
      - **返回**: 
        - 排班创建/更新状态
    - **DELETE** - 删除排班 (`IntegrationAvailabilityView.delete`)
      - **输入**: 
        - `id`: 排班 ID
      - **返回**: 
        - 状态码 204 (无内容)
- ```` - API v1 预约相关接口 (包含于 `bookings.urls`)
  - `/api/v1/integration/bookings/`
    - **POST** - 创建预约 (`IntegrationBookingView.post`)
      - **输入**: 
        - `resource_id`: 资源 ID
        - `resource_name`: 资源名称
        - `customer_email`: 客户邮箱
        - `customer_name`: 客户姓名
        - `start_time`: 开始时间 (ISO 8601 格式)
        - `end_time`: 结束时间 (ISO 8601 格式)
      - **返回**: 
        - 预约创建状态
    - **GET** - 查询预约列表 (`IntegrationBookingView.get`)
      - **输入**: 
        - `customer_id`: 客户 ID
        - `customer_name`: 客户姓名
        - `customer_email`: 客户邮箱
        - `resource_id`: 资源 ID
        - `sync_all`: 是否全量同步 (true 或 false)
      - **返回**: 
        - 预约列表
    - **DELETE** - 取消预约 (`IntegrationBookingView.delete`)
      - **输入**: 
        - `id`: 预约 ID
      - **返回**: 
        - 状态码 204 (无内容)
    - **PATCH** - 更新预约状态 (`IntegrationBookingView.patch`)
      - **输入**: 
        - `id`: 预约 ID
        - `status`: 新状态 (`COMPLETED`)
      - **返回**: 
        - 更新状态