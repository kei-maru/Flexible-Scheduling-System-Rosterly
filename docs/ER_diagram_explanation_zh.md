# Rosterly — 完整 ER 图说明

## 📊 图表位置
- **完整ER图:** `docs/diagrams/system_b_er_complete.mmd`
- **简化ER图:** `docs/diagrams/system_b_er.mmd`（已有）

---

## 🏗️ 数据模型分层（4 大逻辑区）

### 1️⃣ 租户与权限管理层（Tenant & Auth Layer）
这一层负责多租户隔离、用户身份认证、SSO 和员工邀请。

**核心表：**
- `Tenant`（店铺）
  - 主键：`id` (UUID)
  - 唯一键：`slug`
  - 关键字段：`store_type`（店铺类型：CORE_TIME / FLEX_SHIFT）、`subscription_status`（订阅状态）、`api_key` / `api_secret`（API认证）、`deleted_at`（软删除）

- `SaaSUser`（用户，继承 Django AbstractUser）
  - 主键：`id` (int)
  - 外键：`tenant_id`（所属店铺）
  - 关键字段：`username`（唯一）、`role`（ADMIN / STAFF / CONSUMER）、`discord_id`

- `SSOAuthCode`（SSO授权码）
  - 主键：`id` (int)
  - 外键：`user_id`、`tenant_id`
  - 关键字段：`code_hash`（一次性授权码）、`expires_at`（过期时间）、`used_at`（消费时间）

- `StaffInvite`（员工邀请）
  - 主键：`id` (int)
  - 外键：`tenant_id`、`created_by_id` (SaaSUser)
  - 关键字段：`token`（邀请链接）、`role`（邀请角色）、`max_uses` / `used_count`（次数限制）

**关键约束：**
- `Tenant.slug`：全局唯一
- `Tenant.api_key`：全局唯一（用于 Integration API 认证）
- `SaaSUser.username`：全局唯一
- `SaaSUser.tenant` + `role`：结合决定权限边界

---

### 2️⃣ 资源（员工）与档案层（Resource & Profile Layer）
这一层管理被预约的对象（Cast / 员工）及其展示档案、媒体、排班。

**核心表：**
- `Resource`（资源 / 员工）
  - 主键：`id` (UUID)
  - 外键：`tenant_id`、`linked_user_id`（可选，关联 SaaSUser）
  - 关键字段：`name`（展示名）、`external_id`（System A 侧 ID，用于同步）
  - 约束：`(tenant_id, external_id)` 联合唯一

- `ResourceProfile`（资源档案）
  - 主键：`id` (int)
  - 外键：`resource_id` (1:1 关系)
  - 关键字段：`intro`（自我介绍）、`avatar_url`、`tags` (JSON)、`allow_30/60/120_min`（支持的时长）

- `ResourceMedia`（资源媒体）
  - 主键：`id` (int)
  - 外键：`profile_id` (多对一)
  - 关键字段：`media_type`（IMAGE / VIDEO）、`image_url` / `video_url`、`order`（排序）

**关键约束：**
- `Resource.linked_user` 是可选的 1:1 关联，表示员工是否有自己的 SaaSUser 账号（若有，可直接登录排班）
- `ResourceProfile` 与 `Resource` 是 1:1 关系（每个资源有一个档案）
- `display_order` 字段控制在预约页面的展示顺序

---

### 3️⃣ 排班与时间管理层（Availability & Schedule Layer）
这一层管理员工的可预约时间段、周期排班、模板等。

**核心表：**
- `Availability`（可用时段 / 排班）
  - 主键：`id` (UUID)
  - 外键：`resource_id`
  - 关键字段：`start_time` / `end_time`、`is_booked`（是否已被预约）、`is_recurring`（是否周期）

- `RecurringPattern`（周期排班模式）
  - 主键：`id` (UUID)
  - 外键：`resource_id`
  - 关键字段：`day_of_week`（0=Sun~6=Sat）、`start_time` / `end_time`、`valid_from` / `valid_until`（有效期）

- `ScheduleTemplate`（排班模板）
  - 主键：`id` (UUID)
  - 外键：`resource_id`
  - 关键字段：`name`（模板名，如"平日深夜"）、`week_config` (JSON，包含每周的时间配置)

**关键业务规则：**
- 员工可以创建多个排班模板，快速套用常用时段
- `RecurringPattern` 定义的周期规则会自动生成 `Availability` 实例
- `Availability` 中的 `is_booked` 字段防止重复预约（需配合 `select_for_update()` 悲观锁使用）

---

### 4️⃣ 服务、预约与报告层（Service, Booking & Reporting Layer）
这一层管理店铺提供的服务、客户预约、问题报告等。

**核心表：**
- `ServicePreset`（服务预设）
  - 主键：`id` (int)
  - 外键：`tenant_id`
  - 关键字段：`name`（服务名，如"60分 ASMR 施術コース"）、`duration_minutes`（时长）、`price`（价格）
  - 约束：`(tenant_id, name)` 联合唯一

- `Booking`（预约单）
  - 主键：`id` (UUID)
  - 外键：`tenant_id`、`resource_id`、`selected_service_id`（可选）
  - 关键字段：`customer_name` / `customer_email` / `customer_id`（顾客标识）、`start_time` / `end_time`（预约时段）、`status`（PENDING / CONFIRMED / CANCELLED）、`booking_type`（PUBLIC / API）

- `BookingReport`（预约报告 / 申告）
  - 主键：`id` (int)
  - 外键：`booking_id`、`tenant_id`
  - 关键字段：`reporter_role`（CUSTOMER / CAST）、`reason`（申告理由，枚举）、`detail`（说明文本）

- `EmailTemplate`（邮件模板）
  - 主键：`id` (int)
  - 外键：`tenant_id`
  - 关键字段：`event_type`（BOOKING_CONFIRMED / BOOKING_CANCELLED）、`email_title`、`service_name`（动态变量）

**关键业务规则：**
- `Booking` 必须同时拥有 `tenant_id` 和 `resource_id` 以确保数据隔离
- `selected_service` 可为 null（兼容旧数据或 System A 同步），此时由时长动态匹配
- `public_access_token` 用于顾客查看预约详情页（邮件中的链接）
- `BookingReport.reason` 枚举包括：NO_SHOW（无故缺席）、HARASSMENT（骚扰）、LATE（迟到）等

---

## 🔐 多租户隔离设计

**强制隔离点（必须在代码层验证）：**
1. **主表 tenant_id 必填**
   - `Tenant`、`SaaSUser`、`Resource`、`ServicePreset`、`Booking`、`BookingReport`、`EmailTemplate` 等都有 `tenant_id`
   - 所有查询必须在 WHERE 条件中加 `tenant_id=<current_tenant>`

2. **ORM 层强制（Custom Manager）**
   - 应用层使用 Custom Manager 和 QuerySet，确保每次查询自动注入 `tenant_id` 过滤
   - 参考代码：`system_b_saas` 的各 models.py 中的 `Manager` 定义

3. **数据库级约束**
   - 外键关系中，`Resource` 到 `ServicePreset` 必须校验两者的 `tenant_id` 一致
   - 复合唯一键如 `(tenant_id, external_id)` 防止跨租户冲突

---

## 🔄 关键关系图解

```
Tenant (中心枢纽)
├── SaaSUser (多对一)
│   ├── 可链接到 Resource (一对一，可选)
│   └── 创建 SSOAuthCode、StaffInvite
├── Resource (多对一)
│   ├── 关联 ResourceProfile (一对一)
│   │   └── ResourceMedia (一对多)
│   ├── 关联 Availability (一对多)
│   ├── 关联 RecurringPattern (一对多)
│   ├── 关联 ScheduleTemplate (一对多)
│   └── 关联 Booking (一对多，作为 resource_id)
├── ServicePreset (多对一)
│   └── 关联 Booking (一对多，作为 selected_service_id)
├── Booking (多对一)
│   └── 关联 BookingReport (一对多)
└── EmailTemplate (多对一)
```

---

## 📋 表清单（共 13 张表）

| 表名 | 主键类型 | 外键数 | 用途 | 软删除? |
|------|---------|--------|------|--------|
| Tenant | UUID | 0 | 店铺主体 | ✅ |
| SaaSUser | Int | 1 (tenant_id) | 用户身份 | ❌ |
| SSOAuthCode | Int | 2 (user, tenant) | SSO 授权 | ❌ |
| StaffInvite | Int | 2 (tenant, created_by) | 员工邀请 | ❌ |
| Resource | UUID | 2 (tenant, linked_user) | 员工资源 | ❌ |
| ResourceProfile | Int | 1 (resource) | 档案详情 | ❌ |
| ResourceMedia | Int | 1 (profile) | 媒体文件 | ❌ |
| Availability | UUID | 1 (resource) | 时段管理 | ❌ |
| RecurringPattern | UUID | 1 (resource) | 周期规则 | ❌ |
| ScheduleTemplate | UUID | 1 (resource) | 排班模板 | ❌ |
| ServicePreset | Int | 1 (tenant) | 服务预设 | ❌ |
| Booking | UUID | 3 (tenant, resource, service) | 预约单 | ❌ |
| BookingReport | Int | 2 (booking, tenant) | 申告报告 | ❌ |
| EmailTemplate | Int | 1 (tenant) | 邮件模板 | ❌ |

---

## 🔗 参考代码文件

- 模型定义：
  - `system_b_saas/tenants/models.py` (Tenant, SaaSUser, SSOAuthCode, StaffInvite)
  - `system_b_saas/resources/models.py` (Resource, ResourceProfile, ResourceMedia, Availability, RecurringPattern, ScheduleTemplate, ServicePreset, EmailTemplate)
  - `system_b_saas/bookings/models.py` (Booking, BookingReport)

- ORM 查询强制隔离：
  - 应检查各 models 是否定义了 Custom Manager（常见模式：`TenantManager` 或 `TenantQuerySet`）
  - 若无，请在 `views.py` / `serializers.py` 中手工添加 `tenant_id` 过滤

- 并发控制：
  - 预约冲突防护使用 `select_for_update()` 悲观锁（参考 `system_b_saas/bookings/services.py` 或相同逻辑）

---

## 💡 使用建议

1. **学习顺序**
   - 从 `Tenant` 和 `SaaSUser` 理解多租户基础
   - 再从 `Resource` 和 `Availability` 理解业务对象
   - 最后从 `Booking` 和 `BookingReport` 理解交易流

2. **面试讲解** 
   - 强调"所有表都带 `tenant_id`"的隔离原则
   - 指出"预约时使用 `select_for_update()` 防止超卖"
   - 提及"SSO 和邀请流程支持快速员工授权"

3. **导出与演示**
   - 使用 Mermaid 在线编辑器（mermaid.live）预览
   - 截图或导出 SVG 用于博客/演示
   - 对比简化版 (`system_b_er.mmd`) 和完整版 (`system_b_er_complete.mmd`)
