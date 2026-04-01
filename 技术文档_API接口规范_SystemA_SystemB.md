# Veludo API 文档（System A + System B）

**最后更新**: 2026-04-01  
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
  - cast 用户在 System A 个人资料页保存时，后端会在事务提交后强制调用同步（`transaction.on_commit(sync_cast_profile_to_system_b)`）
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

- 同步补充规则（2026-03-26）：
  - 当 payload 仅携带 `allow_30_min / allow_60_min / allow_120_min` 时，System B 会按时长自动映射到 `ServicePreset`，并写入 `profile.metadata.service_preset_ids`（每个时长取排序最前的有效预设）。

#### `GET /api/v1/integration/resources/`

- 功能：读取租户下 Resource 列表（用于 System A 改为远端主数据读取）
- Query：
  - `active_only=true|false`（可选）
  - `external_id=<system_a_user_id>`（可选）
- 公开预约页展示约定（2026-03-28 补充）：
  - `profile.avatar_url`：作为 Cast 卡片头像与介绍卡 `poster` 图片来源。
  - `profile.tags`：用于介绍卡标签区展示（位于展示名与介绍文之间）。
  - 若 `avatar_url` 无效/加载失败，前端需回退到占位图（`NO IMAGE`）。

#### 员工显示名同步规则（2026-03-28）

- 统一规则：`SaaSUser.username` 为唯一显示名来源（仍保留全局唯一约束）。
- 名称对齐：profile username / Cast CMS 显示名 / 预约页显示名 / `Resource.name` / 管理端 username 必须一致。
- 资源绑定同步时，`Resource.name` 仅对齐 `username`，不再读取 `first_name`。

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
  - 当未传 `service_id/service_name` 时，System B 会基于 `course_duration_minutes`（或 `end-start`）自动匹配同租户服务预设并补齐服务名
  - 若历史订单服务名仅存为 `30分/60分/120分`，B 侧返回与管理端展示时会自动补全为 `XX分VRASMR施術コース (PCVR)`
  - 若未选择服务，System B 邮件回退为按数据库 `Booking.start_time/end_time` 计算时长并渲染

#### `GET /api/v1/integration/services/`

- 功能：获取租户下可预约的服务预设（给预约页下拉框使用）
- 返回示例：

```json
[
  {
    "id": 1,
    "name": "60分 ASMR コース",
    "description": "...",
    "price": 8000,
    "duration_minutes": 60
  },
  {
    "id": 2,
    "name": "90分 ASMR コース",
    "description": "...",
    "price": 12000,
    "duration_minutes": 90
  }
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

### 3.5 身份同步（A/B Role Sync）

#### `GET /api/v1/integration/identity`

- 功能：按 `user_id` 或 `discord_uid` 查询 B 端身份快照（用于 A 端回拉同步）。
- Query：
  - `user_id`（可选）
  - `discord_uid`（可选）
  - 至少传一个
- 返回字段：
  - `user_id`, `username`, `discord_id`, `discord_uid`
  - `tenant_id`, `role`, `is_staff`, `is_superuser`
- 关键规则（2026-04-01）：
  - 当 `user_id` 与 `discord_uid` 同时传入且不一致时，优先按 `discord_uid` 解析，返回真实账号。
  - 用途：修复 A 端历史 `saas_user_id` 漂移造成的错人同步。

#### `PATCH /api/v1/integration/identity`

- 功能：A 发起修改 B 端角色（B 仍为 source of truth）。
- 请求体：
  - `role`（必填）：`ADMIN|STAFF|CONSUMER`
  - `user_id`（可选）
  - `discord_uid`（可选）
  - 至少传一个身份键
- 关键规则：
  - `ADMIN/STAFF`：确保 `tenant` 归属为请求租户，`is_staff=True`，`is_active=True`
  - `CONSUMER`：强制 `tenant=null`，`is_staff=False`，并清理 `is_superuser`

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
  - System A 现行入口：`/accounts/sso/consent/`（先同意协议）-> `/accounts/sso/login` -> 本接口。
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
  - `CONSUMER` 口径：`tenant_id` 必须为 `null`。

### 4.3 System A 映射优先级（落地口径）

- 必须按以下顺序匹配本地影子用户：
  1. `saas_user_id == user_id`
  2. `discord_uid`（若本地已存或可回填）
  3. `discord_id`（仅历史兼容兜底）
- 禁止仅按显示名做长期唯一映射。

### 4.4 A/B 角色映射（当前生产口径）

- A 发起 SSO 时角色提示：
  - A 管理员（`is_superuser` 或 `is_staff 且非 cast`）→ `a_role=ADMIN`
  - A 员工（`is_cast=True`）→ `a_role=STAFF`
  - 其余用户 → `a_role=CONSUMER`
- B 在 authorize/social login 流程中按该提示同步 `SaaSUser.role` 与 `SaaSUser.is_staff`。

---

## 5. 管理员面板补充（2026-03-27）

以下为当前后台实际行为补充（TemplateView POST 分支）：

- `save_tenant_settings=true`
  - 仅保存店铺基础信息、店铺类型、预约公开窗口、客户必填字段配置与条款模块。
  - 不再更新订阅状态字段。
- `save_subscription_settings=true`
  - 专用于保存 `subscription_status / subscription_plan_code / subscription_started_at / subscription_ends_at`。
- `save_core_time_order=true`
  - 仅接受 Core-Time 精简字段：`core_resource_id / core_customer_name / core_customer_vrcid / core_service_preset_id / core_start_time`。
  - `end_time` 由后端按 `ServicePreset.duration_minutes` 自动推导。
  - `status` 统一写入 `CONFIRMED`。
  - `selected_service` 与 `selected_service_name` 由 `core_service_preset_id` 对应预设自动写入。

重定向行为：

- 管理台各 POST 操作会携带 `?tab=<module>` 返回当前模块。
- 示例：
  - 保存订阅后返回 `?tab=subscription`
  - 保存 Core-Time 后返回 `?tab=shifts`
  - 保存服务预设后返回 `?tab=services`

### 4.5 角色与租户同步修复（2026-03-26）

- Public SSO 不再根据 `Resource.external_id=user.id` 做二次“员工推断”。
- `a_role=CONSUMER` 时，B 侧强制写入：
  - `role=CONSUMER`
  - `is_staff=False`
  - `tenant=null`
- Public SSO 的历史兼容“按 `discord_id` 展示名匹配账号”已收紧；优先使用 Discord SocialAccount 的 `uid` 绑定，避免展示名碰撞造成误映射。
- A 侧普通用户被误授予 `STAFF/Tenant` 的问题按上述规则修复。

### 4.6 Resource external_id 绑定规则收紧（2026-03-27）

- 规则更新：
  - `Resource.external_id` 仅用于承接 A 侧外部标识，不再允许以 B 侧 `SaaSUser.id` 作为匹配键参与自动绑定。
  - 绑定与复用优先顺序：Discord `SocialAccount.uid` -> `discord_id`（历史兼容）。
- 原因：
  - A/B 两端数值ID并非同一命名空间；历史迁移后可能发生 numeric id 重用。
  - 若将 B `user.id` 参与 external_id 匹配，会出现“资源被错误用户认领”。
- 事故样例（已修复）：
  - `resource_id=41b3a02c-88b2-4879-a264-bbaf815d11bc` 曾被 `orikasayom(id=14)` 占用，实际历史归属为 `nemuifia`。
  - 修复后已解除错误 `linked_user` 并恢复资源名，等待 A 侧正确同步回填。

### 4.7 external_id / Discord 身份双轨规则（2026-03-27）

- 身份双轨：
  - 认证与登录识别：使用 Discord `SocialAccount.uid`。
  - 跨系统资源映射：使用 `Resource.external_id`（A 侧用户ID口径）。
- 禁止事项：
  - 不允许将 Discord `uid` 或 `discord_id` 写入 `Resource.external_id`。
- Public SSO 员工绑定：
  - 只允许绑定已有 canonical 资源。
  - 禁止在 public SSO 过程中自动创建新 `Resource`（避免垃圾可预约对象）。
- A 端展示约束：
  - 远端 cast 列表仅展示 `external_id` 为数字的资源，过滤历史脏数据。

### 4.8 Discord 回调 inactive 防护（2026-03-28）

- 适用范围：`/accounts/discord/login/callback/` 社交登录回调。
- 规则：
  - 若回调匹配到既有用户且 `is_active=False`，B 侧先恢复 `is_active=True`，再继续 allauth 登录流程。
  - 覆盖两条路径：
    - 已绑定社交账号（`sociallogin.is_existing=True`）
    - 通过 Discord `uid` 关联到既有账号（`authorized_user`）
- 目的：避免被重定向到 `/accounts/inactive/` 造成外部登录不可用。

### 4.9 Public SSO 角色保留与新用户防误判（2026-04-01）

- 目标：
  - 解决“员工重新登录被降级为普通用户”。
  - 同时避免“新用户首次登录被误判为员工”。
- 规则：
  - `a_role=CONSUMER` 时，仅当存在“真实特权证据”才保留 `STAFF/ADMIN`：
    - `is_staff=True` 或 `tenant_id` 非空。
  - 无证据的新用户按 `CONSUMER` 落库。

---

## 附录 A. Dashboard 入口补充（System B 内部）

以下为 System B 管理后台内部路由（非 Integration API）：

- `GET /dashboard/register-shop/`
  - 功能：触发初次店铺注册 OAuth 导流（Discord）。
- `GET|POST /dashboard/register-shop/form/`
  - 功能：OAuth 后填写店铺表单并完成落库。
  - 表单字段：`shop_name`（必填）、`owner_email`（必填）、`logo`（可选）、`preset_services_json`（可选）。
  - 成功后写入：`Tenant.name/logo/contact_email`，并将当前账号提升为该店 `ADMIN`。
  - 取消时支持清理本次临时 Discord 注册记录（仅限本次 provisional 账号）。
- `GET /dashboard/invite/<token>/`
  - 功能：员工邀请链接入口。
  - 行为：携带店铺上下文进入 OAuth，登录成功后直接绑定邀请目标租户与角色（默认 `STAFF`，可选 `ADMIN`）。

- `GET /dashboard/book/<tenant_slug>/`
  - 功能：店铺公开预约页面（System B 原生页面，Rosterly 设计语言）。
  - 说明：Store Settings 中“预约链接（自动生成）”已切换为该路由，不再依赖 `localhost:8000` 的 System A 页面。
  - 数据隔离：仅展示当前 `tenant_slug` 对应店铺的 Resource 与 ServicePreset。
  - 反刷保护：页面包含 honeypot 字段 `website`（正常用户不可见）。

- `GET /dashboard/book/<tenant_slug>/api/availability/?resource_id=<uuid>`
  - 功能：拉取该店指定担当者未来 14 天可预约空档（仅返回当前店铺数据）。

- `POST /dashboard/book/<tenant_slug>/api/create/`
  - 功能：提交公开预约。
  - 必填：`resource_id`、`start_time`
  - 条件必填：`customer_vrcid` / `customer_discord_id` / `customer_email` 由店铺 `required_customer_fields` 决定。
  - 可选：`service_id`
  - 校验：
    - 预约开始时间需大于当前时间 24 小时
    - 保持前后 30 分钟冲突检测规则
    - 反刷限流：同店铺同 IP 有 10 分钟与 1 小时窗口限制；同指纹短时重复提交会被限制
    - 蜜罐拦截：若 `website` 有值则判定为机器人请求并拒绝
  - 失败码补充：`429`（访问频率过高）

- `GET /dashboard/book/detail/<access_token>/`
  - 功能：顾客订单详情页（邮件“详细を見る”按钮目标）。
  - 展示：预约明细 + 日语取消期限（例：`2026年03月31日 20:00 まで`）。

- Tenant 店铺设置字段补充（Store Settings）
  - `booking_detail_redirect_url`（可选）：
    - 配置后，邮件按钮优先跳转该 URL；
    - 系统自动追加 `booking_token` 与 `tenant` 查询参数，便于 Veludo 首页承接并自行路由。

邀请模型：`tenants.StaffInvite`
- 字段：`token`、`tenant`、`role`、`expires_at`、`max_uses`、`used_count`、`is_active`。
- 规则：过期、超次数或手动失效后不可再用。

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
