# Veludo API 文档（System A + System B）

**最后更新**: 2026-02-28  
**说明**: 本文档基于当前代码实现整理，优先用于前后端联调与接口排障。

## 1. 认证与约定

### 1.1 System A API（给前端页面调用）

- 认证方式：Django Session（登录态）
- 大多数接口需要登录（`IsAuthenticated` 或 `@login_required`）
- 站内请求需携带 CSRF（浏览器同源场景通常自动处理）

### 1.2 System B API（给 System A 或服务端调用）

- 基础路径：`/api/v1/integration/`
- 认证 Header：`X-Tenant-Key: <tenant_api_key>`
- 数据格式：JSON

---

## 2. System A API

前缀：`/accounts/api/`（除埋点接口）

### 2.1 埋点接口

#### `POST /core/api/track/`

- 功能：记录用户行为日志
- 请求体：

```json
{
  "action": "VIEW_PAGE",
  "target": "booking",
  "meta": {"from": "home"}
}
```

- 成功响应：

```json
{"status": "success"}
```

### 2.2 排班日历 API

#### `GET /accounts/api/availability/`

- 功能：读取指定 Cast 的排班+预约事件（已做隐私过滤）
- Query:
  - `resource_id`（必填）
  - `start`（可选，ISO）
  - `end`（可选，ISO）
- 返回：FullCalendar 事件数组

#### `POST /accounts/api/availability/`

- 功能：创建单次/周期排班（仅 Cast）
- 说明：后端会强制把 `resource_id` 覆盖为当前登录 Cast 的 `saas_resource_id`
- 单次排班示例：

```json
{
  "start": "2026-03-03T21:00:00+09:00",
  "end": "2026-03-03T22:00:00+09:00"
}
```

- 周期排班示例：

```json
{
  "range_start": "2026-03-01",
  "range_end": "2026-03-31",
  "week_config": {
    "1": {"enabled": true, "start": "21:00", "end": "23:00"}
  }
}
```

#### `DELETE /accounts/api/availability/`

- 功能：删除排班（仅 Cast）
- 入参：`id`（body/query 都可）

### 2.3 周期配置与模板

#### `GET /accounts/api/availability/recurring-config/`

- 功能：读取当前 Cast 的周期排班配置
- 返回示例：

```json
{
  "range": {"start": "2026-03-01", "end": "2026-03-31"},
  "week_config": {
    "1": {"enabled": true, "start": "21:00", "end": "23:00"}
  }
}
```

#### `GET /accounts/api/availability/templates/?resource_id=<id>`

- 功能：获取排班模板列表
- 权限：只能查看自己的 `resource_id`

#### `POST /accounts/api/availability/templates/`

- 功能：保存/更新模板
- 请求体：

```json
{
  "resource_id": "<my_resource_id>",
  "name": "平日夜班",
  "week_config": {
    "1": {"enabled": true, "start": "21:00", "end": "23:00"}
  }
}
```

### 2.4 预约流程 API

#### `POST /accounts/api/booking/submit/`

- 功能：Guest 提交预约
- 关键校验：预约时间必须大于当前时间 24 小时
- 请求体：

```json
{
  "resource_id": "<cast_resource_id>",
  "resource_name": "CastName",
  "start": "2026-03-05T21:00:00+09:00",
  "end": "2026-03-05T22:00:00+09:00"
}
```

#### `DELETE /accounts/api/booking/cancel/<booking_id>/`

- 功能：取消预约（Guest）
- 限制：Cast 调用会返回 403

#### `POST /accounts/api/booking/complete/<booking_id>/`

- 功能：完结预约（Cast）
- 限制：非 Cast 返回 403

#### `GET /accounts/api/my-bookings/`

- 功能：返回当前用户相关订单（用于前端冲突检测）
- 返回字段（简化）：`start`, `end`, `status`

### 2.5 可预约 Cast 搜索

#### `GET /accounts/api/cast/search/?start=<ISO>&duration=<30|60|120>`

- 功能：按目标时段筛选可预约 Cast
- 返回：

```json
{
  "casts": [
    {
      "id": 12,
      "name": "Keimaru",
      "avatar_url": "/media/casts/avatars/x.png",
      "rank": "REGULAR",
      "saas_id": "..."
    }
  ]
}
```

---

## 3. System B Integration API

前缀：`/api/v1/integration/`  
统一 Header：`X-Tenant-Key`

### 3.1 资源同步

#### `POST /api/v1/integration/resources/`

- 功能：创建/更新 Resource（Cast 同步）
- 请求体：

```json
{
  "external_id": "123",
  "name": "CastName",
  "email": "cast@example.com"
}
```

- 响应：

```json
{"saas_id": "<uuid>", "status": "created"}
```

### 3.2 排班管理

#### `GET /api/v1/integration/availability/`

- Query:
  - `resource_id`（必填，UUID 或 external_id）
  - `mode=raw|search`（默认 `raw`）
  - `start`、`end`（ISO，可选）

- `mode=search`：检查某时间段是否可约（返回可约窗口数组或空数组）
- `mode=raw`：返回排班与预约混合事件（包含切分逻辑、24h 过滤）

#### `POST /api/v1/integration/availability/`

- 单次排班请求体：

```json
{
  "resource_id": "<uuid_or_external_id>",
  "start": "2026-03-03T21:00:00+09:00",
  "end": "2026-03-03T22:00:00+09:00"
}
```

- 周期排班请求体：

```json
{
  "resource_id": "<uuid_or_external_id>",
  "range_start": "2026-03-01",
  "range_end": "2026-03-31",
  "week_config": {
    "1": {"enabled": true, "start": "21:00", "end": "23:00"}
  }
}
```

- 关键规则：创建排班需满足 24h 限制

#### `DELETE /api/v1/integration/availability/<availability_id>/`

- 功能：删除排班
- 限制：`is_booked=True` 的排班不可删

### 3.3 周期配置与模板

#### `GET /api/v1/integration/availability/recurring-config/?resource_id=<id>`

- 功能：读取周期排班规则

#### `GET /api/v1/integration/availability/templates/?resource_id=<id>`

- 功能：模板列表

#### `POST /api/v1/integration/availability/templates/`

- 功能：保存/更新模板

#### `DELETE /api/v1/integration/availability/templates/`

- 请求体：`{"id": "<template_id>"}`

### 3.4 预约管理

#### `POST /api/v1/integration/bookings/`

- 功能：创建预约
- 请求体：

```json
{
  "resource_id": "<uuid_or_external_id>",
  "resource_name": "CastName",
  "customer_email": "guest@example.com",
  "customer_name": "guest_vrc",
  "start_time": "2026-03-05T21:00:00+09:00",
  "end_time": "2026-03-05T22:00:00+09:00"
}
```

- 关键规则：
  - 冲突检测含前后 30 分钟缓冲
  - 冲突返回 `409 Time slot unavailable`

#### `GET /api/v1/integration/bookings/`

- 功能：查询订单
- Query 支持：
  - `customer_id`
  - `customer_name`
  - `customer_email`
  - `resource_id`
  - `sync_all=true`（管理员全量同步）

#### `DELETE /api/v1/integration/bookings/<booking_id>/`

- 功能：取消预约
- 限制：距离开始小于 2 小时返回 `400`

#### `PATCH /api/v1/integration/bookings/<booking_id>/`

- 功能：状态更新（当前仅支持完结）
- 请求体：

```json
{"status": "COMPLETED"}
```

- 限制：仅 `CONFIRMED` 可变更为 `COMPLETED`

---

## 4. 常见状态码

- `200`: 查询/更新成功
- `201`: 创建成功
- `204`: 删除成功（无响应体）
- `400`: 参数错误、时间规则不满足、状态非法
- `403`: 权限不足（如 Guest 调用 Cast 专属接口）
- `404`: 资源不存在
- `409`: 时间冲突
- `500/503`: 上游系统异常或网络错误

---

## 5. 联调建议

1. 先验证 `resources/` 同步成功（拿到 `saas_id`）。
2. 再验证 `availability/`（能查到可预约窗口）。
3. 最后打通 `bookings/` 创建、取消、完结全链路。
4. 若出现“前端显示与后端不一致”，优先检查时区和 `resource_id` 映射。

