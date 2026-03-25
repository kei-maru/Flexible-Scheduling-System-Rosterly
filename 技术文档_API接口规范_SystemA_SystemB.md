# Veludo API 文档（System A + System B）

**最后更新**: 2026-03-25  
**说明**: 本文档基于当前代码实现整理，优先用于前后端联调与接口排障。本文档是当前项目中最权威的接口基线说明。

## 1. 认证与约定

### 1.3 Discord 身份字段术语（重要）

- `discord_uid`：Discord 平台稳定唯一 ID（通常为 15~22 位长数字串）。
- `discord_id`：历史兼容展示字段（可能是用户名、`name#1234` 或其他显示形态）。
- 若你口中的“Discord ID 是用户名下面那串长数字”，在本文统一称为 `discord_uid`。
- 跨系统映射必须优先使用 `saas_user_id` / `discord_uid`，不要把展示名当唯一键。

### 1.1 System A API（给前端页面调用）

- 认证方式：Django Session（登录态）
- 大多数接口需要登录（`IsAuthenticated` 或 `@login_required`）
- 站内请求需携带 CSRF（浏览器同源场景通常自动处理）

### 1.2 System B API（给 System A 或服务端调用）

- 基础路径：`/api/v1/integration/`
- 认证 Header：`X-Tenant-Key: <tenant_api_key>`
- 数据格式：JSON
- 说明：除 Integration API 外，System B 还提供 SSO 统一登录相关接口（见第 4 节）。

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

- 周期排班示例（支持同一天多个时间段）：

```json
{
  "range_start": "2026-03-01",
  "range_end": "2026-03-31",
  "week_config": {
    "1": {
      "enabled": true,
      "slots": [
        {"start": "21:00", "end": "23:00"},
        {"start": "01:00", "end": "03:00"}
      ],
      "start": "21:00",
      "end": "23:00"
    }
  }
}
```
说明：
- `slots` 是新格式，可为每个星期几设置多个时段。
- `start/end` 仍保留（兼容旧前端），表示该日第一个时段。

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
    "1": {
      "enabled": true,
      "slots": [
        {"start": "21:00", "end": "23:00"},
        {"start": "01:00", "end": "03:00"}
      ],
      "start": "21:00",
      "end": "23:00"
    }
  }
}
```
说明：`slots` 为新字段，`start/end` 为兼容字段（第一个时段）。

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
    "1": {
      "enabled": true,
      "slots": [
        {"start": "21:00", "end": "23:00"},
        {"start": "01:00", "end": "03:00"}
      ],
      "start": "21:00",
      "end": "23:00"
    }
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
备注（前端行为）：若单次排班跨天，预约界面会按 **24:00** 拆分展示，点击凌晨时间只会使用当日区间。

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

- 功能：创建/更新 Resource（Cast 同步，幂等 upsert）
- 线上用途：
  - `sync_casts_to_system_b` 批量迁移命令调用该接口
  - 管理员在 System A 编辑 CastProfile 后也调用该接口做实时同步
- 请求体：

```json
{
  "external_id": "123",
  "name": "CastName",
  "email": "cast@example.com",
  "profile": {
    "intro": "自己紹介",
    "tags": ["癒し", "ロールプレイ"],
    "avatar_url": "https://...",
    "youtube_url": "https://youtu.be/...",
    "display_order": 10,
    "allow_30_min": true,
    "allow_60_min": true,
    "allow_120_min": false
  },
  "medias": [
    {
      "title": "宣材写真A",
      "media_type": "IMAGE",
      "image_url": "https://...",
      "order": 0
    }
  ]
}
```

- 响应：

```json
{"saas_id": "<uuid>", "status": "created"}
```

#### `GET /api/v1/integration/resources/`

- 功能：读取租户下 Resource 列表（用于 System A 改为远端主数据读取）
- Query：
  - `active_only=true|false`（可选）
  - `external_id=<system_a_user_id>`（可选）

#### `GET /api/v1/integration/resources/<resource_uuid>/`

- 功能：读取单个 Resource + Profile + Media

#### `PATCH /api/v1/integration/resources/<resource_uuid>/`

- 功能：局部更新 Resource / Profile / Media
- 说明：当传入 `medias` 时，按当前 payload 全量替换该 Resource 的媒体列表。

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
  "service_id": 3,
  "service_name": "90分 ASMR コース",
  "customer_email": "guest@example.com",
  "customer_name": "guest_vrc",
  "start_time": "2026-03-05T21:00:00+09:00",
  "end_time": "2026-03-05T22:00:00+09:00",
  "course_duration_minutes": 60
}
```

- 关键规则：
  - 冲突检测含前后 30 分钟缓冲
  - 冲突返回 `409 Time slot unavailable`
  - `service_id`（推荐）可指定管理员预设服务；`service_name` 作为兼容兜底
  - 订单会落库 `selected_service_name`，邮件优先显示用户所选服务名
  - `course_duration_minutes` 为可选透传字段（System A 可用 `end-start` 动态计算）
  - 若未选择服务，System B 邮件回退为按数据库 `Booking.start_time/end_time` 计算时长并渲染

#### `GET /api/v1/integration/services/`

- 功能：获取租户下可预约的服务预设（给预约页下拉框使用）
- 返回示例：

```json
[
  {"id": 1, "name": "60分 ASMR コース", "duration_minutes": 60},
  {"id": 2, "name": "90分 ASMR コース", "duration_minutes": 90}
]
```

#### 邮件模板动态变量（System B）

`BOOKING_CONFIRMED` 邮件模板支持以下变量：

- `{{ customer_name }}`
- `{{ resource_name }}`
- `{{ tenant_name }}`
- `{{ start_date }}`
- `{{ time_range }}`
- `{{ duration_minutes }}`
- `{{ duration_hours }}`
- `{{ service_name }}`

兼容性说明：

- 旧模板若写死 `60分...`，系统会在发送时自动替换为实际预约分钟数（基于 `start_time/end_time`）。

#### `GET /api/v1/integration/bookings/`

- 功能：查询订单
- Query 支持：
  - `customer_id`
  - `customer_name`
  - `customer_email`
  - `resource_id`
  - `sync_all=true`（管理员全量同步）
- 返回补充字段：
  - `service_name`（用户下单时选择的服务名；无则为空字符串）

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

## 4. SSO 接口（System B 作为 IdP）

### 4.1 `GET /sso/authorize`

- 功能：SSO 授权入口（浏览器跳转）。
- Query：
  - `client_id`（必填）
  - `redirect_uri`（必填，必须命中白名单）
  - `state`（必填）
  - `nonce`（必填）
  - `a_role`（可选，System A 透传角色提示，`ADMIN|STAFF|CONSUMER`，默认 `CONSUMER`）
- 行为：
  - 未登录：跳转到 B 的 Discord 登录。
  - 已登录：签发一次性 `code` 并 302 到 `redirect_uri?code=...&state=...`。
  - 角色同步：若为 A 发起的 public SSO，B 会按 `a_role` 同步当前用户 `role/is_staff`（并在缺失时绑定 public tenant）。

### 4.2 `POST /api/v1/auth/sso/exchange`

- 功能：服务端交换授权码（只能后端调用）。
- 请求体：

```json
{
  "code": "one_time_code",
  "client_id": "veludo-system-a",
  "client_secret": "***",
  "redirect_uri": "https://xxx/accounts/sso/callback"
}
```

- 成功响应：

```json
{
  "user_id": "3",
  "discord_uid": "812928114665324544",
  "discord_id": "usamaru6090",
  "username": "usamaru6090",
  "tenant_id": null,
  "role": "STAFF",
  "nonce": "...",
  "exp": 1770000000
}
```

- 字段约定：
  - `user_id`：System B 用户主键（稳定主身份键）。
  - `discord_uid`：Discord 不可变 UID（强烈建议作为跨系统回填键）。
  - `discord_id`：显示名/兼容字段，可能随用户改名变化。
  - `role`：可能为 `ADMIN` / `STAFF` / `CONSUMER`，其中 `CONSUMER` 表示 A 端用户（无租户后台权限）。

### 4.4 A/B 角色映射（当前生产口径）

- A 发起 SSO 时角色提示：
  - A 管理员（`is_superuser` 或 `is_staff 且非 cast`）→ `a_role=ADMIN`
  - A 员工（`is_cast=True`）→ `a_role=STAFF`
  - 其余用户 → `a_role=CONSUMER`
- B 在 authorize/social login 流程中按该提示同步 `SaaSUser.role` 与 `SaaSUser.is_staff`。

### 4.3 System A 映射优先级（落地口径）

- 必须按以下顺序匹配本地影子用户：
  1. `saas_user_id == user_id`
  2. `discord_uid`（若本地已存或可回填）
  3. `discord_id`（仅历史兼容兜底）
- 禁止仅按显示名做长期唯一映射。

---

## 5. 常见状态码

- `200`: 查询/更新成功
- `201`: 创建成功
- `204`: 删除成功（无响应体）
- `400`: 参数错误、时间规则不满足、状态非法
- `403`: 权限不足（如 Guest 调用 Cast 专属接口）
- `404`: 资源不存在
- `409`: 时间冲突
- `500/503`: 上游系统异常或网络错误

---

## 6. 联调建议

1. 先验证 `resources/` 同步成功（拿到 `saas_id`）。
2. 再验证 `availability/`（能查到可预约窗口）。
3. 最后打通 `bookings/` 创建、取消、完结全链路。
4. 若出现“前端显示与后端不一致”，优先检查时区和 `resource_id` 映射。
