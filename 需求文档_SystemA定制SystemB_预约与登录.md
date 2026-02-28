# 需求文档：System A 定制 System B（预约与登录）

## 1. 文档目标
- 明确 System A 与 System B 的主从关系与定制边界。
- 明确「客户预约免登录」与「员工/店长后台登录」的权限模型。
- 作为后续开发、验收、联调基线。

## 2. 系统关系定义
- System B：SaaS 核心能力（租户、资源、排班、预约、后台管理、通知）。
- System A：System B 的定制前台（面向终端客户的品牌化站点与预约入口）。
- 结论：System A 调用 System B 的 Integration API，业务规则以 System B 为准。

## 3. 角色与权限
- 客户（Customer）
  - 不需要登录。
  - 通过店铺提供的专属预约链接进入预约流程。
  - 仅可进行预约查询、下单、取消（受业务规则限制）。
- 员工（Staff）
  - 需要登录 System B Dashboard。
  - 可管理自己相关的排班/订单。
- 店长（Admin）
  - 需要登录 System B Dashboard。
  - 可管理店铺资源、订单、模板与配置。

## 4. 登录与预约核心需求
### 4.1 客户预约（免登录）
- 访问入口：店铺专属预约链接（由 System A 承载）。
- 鉴权方式：由 System A 在服务端调用 System B 时携带租户 Key（`X-Tenant-Key`）。
- 客户端不暴露员工后台登录入口。

### 4.2 员工/店长登录（需要登录）
- 访问入口：`/dashboard/login/`（System B）。
- 登录方式：Discord OAuth。
- 要求：System B 使用独立 Discord OAuth 应用，不可复用 System A Key。
- 安全要求：仅允许已授权员工/店长账号登录，不允许陌生 Discord 自动注册后台账号。

## 5. 当前实现状态（2026-03-01）
- 已满足：
  - System B 的 Dashboard 登录已切换为 Discord。
  - System B 使用独立环境变量：`SYSTEM_B_DISCORD_CLIENT_ID` / `SYSTEM_B_DISCORD_SECRET` / `SYSTEM_B_DISCORD_KEY`。
  - Dashboard 登录已限制为「预授权账号」，关闭自动注册。
  - System B Integration API 继续采用 `X-Tenant-Key`，客户流程不要求登录会话。
- 仍需在业务层确认：
  - 店铺专属预约链接的 URL 规范（如 `shop_slug` / `tenant_slug`）及分发规则。
  - 员工账号预授权流程（后台创建用户并维护 `discord_id` 对应关系）。

## 6. 数据与配置要求
### 6.1 环境变量（System B）
- `SYSTEM_B_DISCORD_CLIENT_ID`
- `SYSTEM_B_DISCORD_SECRET`
- `SYSTEM_B_DISCORD_KEY`（可为空）

### 6.2 Discord 回调地址
- 本地：`http://localhost:8001/accounts/discord/login/callback/`
- 生产：`https://<system-b-domain>/accounts/discord/login/callback/`

## 7. 验收标准
- 客户访问店铺预约链接，无需登录可完成预约流程。
- 未授权 Discord 账号访问 `/dashboard/login/` 后，无法进入后台并收到提示。
- 已授权员工/店长账号可登录并进入 Dashboard。
- System A 到 System B 的 API 调用在缺少/错误租户 Key 时返回拒绝。

## 8. 非目标（本阶段不做）
- 客户账号体系（注册/登录/密码找回）。
- 多因素认证（MFA）。
- 细粒度 RBAC（按菜单或按钮级别权限）。
