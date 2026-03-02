# 需求文档：System A 定制 System B（预约、登录、门户与排班）

## 1. 目标
- 明确 System A（前台定制层）与 System B（SaaS 核心层）的职责边界。
- 明确客户免登录预约、员工/管理员登录后的访问权限。
- 明确登录后公共主页面与各模块互联规则。
- 明确排班页面与管理员 Dashboard 的拆分规则。

## 2. 系统关系
- System B：多租户核心（资源、排班、预约、订单、邮件模板、后台管理）。
- System A：面向客户的品牌化前台，调用 System B Integration API 完成预约链路。
- 结论：System A 是 System B 的定制前端层，核心业务规则以 System B 为准。

## 3. 角色与访问矩阵
- Customer（客户）
  - 不登录。
  - 通过店铺链接直接进入预约页面（System A）。
- Staff（员工）
  - 需要登录。
  - 可访问公共主页面 `/home/`。
  - 可访问共同排班页面 `/schedule/`。
  - 可访问个人信息页面 `/profile/`。
  - 可访问预约一览页面 `/bookings/`。
  - 不可访问管理员 Dashboard `/dashboard/`。
- Admin（店长/管理员）
  - 需要登录。
  - 可访问公共主页面 `/home/`。
  - 可访问共同排班页面 `/schedule/`。
  - 可访问个人信息页面 `/profile/`。
  - 可访问预约一览页面 `/bookings/`。
  - 可访问管理员 Dashboard `/dashboard/`，可编辑员工资料、查看排班与订单、编辑邮件模板。

## 4. 登录需求
- 登录入口：`/dashboard/login/`（Discord OAuth）。
- System B 必须使用独立 Discord 应用：
  - `SYSTEM_B_DISCORD_CLIENT_ID`
  - `SYSTEM_B_DISCORD_SECRET`
  - `SYSTEM_B_DISCORD_KEY`（可空）
- 禁止陌生 Discord 自动注册后台账号；仅预授权员工/管理员可登录。

## 5. 页面与功能需求
### 5.0 公共主页面（员工 + 管理员）
- 路径：`/home/`
- 登录后默认跳转到 `/home/`。
- 页面包含 3 个功能模块按钮（互相可达）：
  - 排班管理（`/schedule/`）
  - 个人信息管理（`/profile/`）
  - 预约一览（`/bookings/`）
- 右上角显示当前用户头像（无头像时显示用户名首字母）。
- 鼠标悬浮头像显示登出入口（Logout）。

### 5.1 共同排班页面（员工 + 管理员）
- 路径：`/schedule/`
- 交互与逻辑：对齐 System A `schedule` 页面
  - 周视图排班展示
  - 拖拽新增排班
  - 点击删除排班
  - 周期排班配置
  - 排班模板保存/加载
- 权限：
  - Staff 只能管理自己的 Resource 排班
  - Admin 可切换并管理租户内资源排班
  - 页面导航保留模块互链按钮（主页/排班/个人信息/预约一览），管理员额外可见 Dashboard 按钮

### 5.2 个人信息页面（员工 + 管理员）
- 路径：`/profile/`
- 交互与逻辑：参考 System A 个人信息管理
  - 可编辑用户名、邮箱、Discord ID
  - 提交后更新当前登录用户资料
- 页面导航保留模块互链按钮，管理员额外可见 Dashboard 按钮

### 5.3 预约一览页面（员工 + 管理员）
- 路径：`/bookings/`
- 交互与逻辑：参考 System A 预约一览
  - Staff：只看自己绑定 Resource 的预约
  - Admin：可看租户全部预约
  - 支持状态操作（完成；管理员可取消）
- 页面导航保留模块互链按钮，管理员额外可见 Dashboard 按钮

### 5.4 管理员 Dashboard（仅管理员）
- 路径：`/dashboard/`
- 功能：
  - 员工资料编辑（用户名、邮箱、Discord、角色、启用状态、资源绑定）
  - 订单列表查看
  - 即将到来的排班查看
  - 邮件模板编辑
- 非管理员访问 `/dashboard/`：自动重定向至 `/schedule/`

## 6. API 与后端结构约束
- 排班业务逻辑不得继续与 API View 混写。
- 统一由服务层承载核心排班逻辑（例如 `resources/services/schedule_service.py`）。
- Integration API 与 Dashboard API 均复用服务层。

## 7. 验收标准
- 客户链路：客户无需登录即可完成预约流程。
- 登录后：用户跳转到 `/home/`。
- Staff 账号：可访问 `/home/`、`/schedule/`、`/profile/`、`/bookings/`；访问 `/dashboard/` 被拒绝并跳转。
- Admin 账号：可访问 `/home/`、`/schedule/`、`/profile/`、`/bookings/` 与 `/dashboard/`。
- Dashboard 可成功修改员工资料并生效。
- 共同排班页面交互与 System A 一致，且规则正确（24h、冲突、模板、周期）。
- 各模块页面存在互链按钮；右上角头像悬浮可登出。

## 8. 本地测试口径
- 未配置 Discord 时可走 `/accounts/login/` 本地账号测试登录态。
- 配置 Discord 后走 `/dashboard/login/` 验证 OAuth 登录。
