# 迭代履历（Case Study，全量版）

**项目**: Veludo / Rosterly（System A 定制前台 + System B SaaS 核心）  
**作者**: Yukimikei（作者归并见第 2 节）  
**统计区间**: 2026-01-15 ~ 2026-03-25  
**数据来源**: `git log --all`（当前仓库全量历史）  

---

## 1. 全量统计快照

- 总提交数：`134`
- 时间跨度：`2026-01-15` 到 `2026-03-23`
- 月度分布：
  - `2026-01`: 74 commits
  - `2026-02`: 36 commits
  - `2026-03`: 24 commits
- 最早提交：`3021a2a Re-init clean project`
- 最新提交：`ffd669e feat: Update API documentation with latest changes...`

---

## 2. 作者归并说明（同一作者）

以下 Git identity 需归并为同一人（你本人）：
- `Keimaru <m17621752319@gmail.com>`：88 commits
- `kei-maru <2894758587@qq.com>`：40 commits
- `kei-maru <m17621752319@gmail.com>`：6 commits

结论：本仓库历史中的 134 提交均归属同一作者主体。

---

## 3. 项目演进主线

项目经历了从「快速试错构建」到「架构收敛」的完整路径：

1. 基础搭建与跑通（1 月中旬）
2. 体验和业务逻辑密集迭代（1 月下旬到 2 月中旬）
3. 双系统边界清晰化与运维安全强化（2 月下旬到 3 月上旬）
4. 数据能力与合规完善（3 月中旬）
5. 核心去耦（Cast 主数据迁移到 B，3 月下旬）
6. 身份去耦（统一 SSO，上线收口，3 月下旬）

---

## 4. 分阶段履历（全量整理）

## 4.1 Phase 0：初始化与工程骨架（2026-01-15）

关键内容：
- 项目重建、Docker 服务、依赖、Python 版本、数据库连接。
- 基础配置反复修正（host、static、api key、signals 调试）。

代表提交：
- `3021a2a` Re-init clean project
- `293cbc8` Add Docker services
- `0077999` Add requirements
- `ca39c1b` update_database_setting

阶段结论：
- 工程从空仓到可运行状态，完成最小可用开发基座。

---

## 4.2 Phase 1：业务可用性冲刺（2026-01-17 ~ 2026-01-25）

关键内容：
- 日历、排班、搜索、递归任务等核心流程修复。
- 邮件模板和发送内容连续调优。
- 移动端布局、首页与 cast 图片交互快速迭代。
- 埋点功能上线并调整开关。

代表提交：
- `5ca9442` fix_calendar
- `e24595a` fix_recurciveTask & signals
- `2c52d7f` fix search bug
- `21f12b5` Update_email_to_both client and Cast
- `760c4e2` tracking_user

阶段结论：
- 核心业务从“能跑”提升到“可连续使用”。

---

## 4.3 Phase 2：部署与环境治理（2026-02-04 ~ 2026-02-15）

关键内容：
- 容器命名、yml 结构、服务拆分与路径修复。
- System B 命名和 API URL 对齐。
- 开发/部署 compose 体系逐步稳定。

代表提交：
- `2bf2977` change_system_b name
- `ced73a9` fix saas_api_url
- `a199ee4` twoyml
- `e2fee3e` setup dev compse

阶段结论：
- 多环境部署方式成形，为后续双服务器运行打基础。

---

## 4.4 Phase 3：预约/排班细节完善（2026-02-08 ~ 2026-02-20）

关键内容：
- 排班 pattern 与周开关能力完善。
- 预约时段、冲突、展示细节多轮修复。
- 邮件与时间逻辑问题修补。

代表提交：
- `dc585c5` update_schedule_pattern
- `496a514` add_week switch
- `7a05f39` fix_bookingview
- `636f493` update_reserve time
- `1ce2028` fix_email&& time search bug

阶段结论：
- 预约链路从粗粒度逻辑进入“边界条件优化”阶段。

---

## 4.5 Phase 4：System B 运营后台与安全强化（2026-03-01 ~ 2026-03-13）

关键内容：
- System B 登录和后台界面进入稳定迭代。
- A 侧对外暴露面收敛（仅 127.0.0.1，交由反代处理）。
- 排班与 API 整体能力增强。

代表提交：
- `d3c5d2c` systemB login
- `343db80` update systemb interface
- `9172d57` security: restrict system_a port to 127.0.0.1
- `e87f8a4` Enhance scheduling features and API integration

阶段结论：
- 系统从“功能开发期”进入“可运维上线期”。

---

## 4.6 Phase 5：合规、体验、数据化运营（2026-03-14 ~ 2026-03-17）

关键内容：
- 利用条款与同意逻辑接入。
- 预约登录拦截点和交互体验优化。
- 管理员 Analytics 模块化落地，补趋势/分布/停留等指标。
- 自动化 IP 防护与清理命令上线。

代表提交：
- `fb504a4` terms + consent checkbox
- `eb85b1c` login required modal
- `572d92b` analytics dashboard
- `83e97a4` page duration + mobile ratio
- `6657412` trend + hourly charts
- `b360738` IP blocking + cleanup command

阶段结论：
- 产品从业务可用升级到“可观测、可运营、可防护”。

---

## 4.7 Phase 6：SaaS 一般化能力（2026-03-19 ~ 2026-03-20）

关键内容：
- 服务预设进入主流程，邮件服务名支持动态与回退策略。
- 文档体系统一到“API 权威、需求主导、架构补充”的协作模型。

代表提交：
- `9291f48` service presets + duration tracking
- `d44dc72` docs alignment for system relationship/login requirement

阶段结论：
- 从定制业务逻辑转向可复用 SaaS 能力沉淀。

---

## 4.8 Phase 7：核心去耦里程碑（2026-03-23）

关键内容：
- B 新增 `ResourceProfile / ResourceMedia` 承接 Cast 主数据。
- A 改为远端读取 Cast（带本地回退开关）。
- 增加幂等迁移命令 `sync_casts_to_system_b`。
- 管理员编辑 CastProfile 后实时同步到 B。

代表提交：
- `f2f4c14` resource profile/media + API
- `6d418dc` remote cast sync and management
- `ffd669e` API doc sync and migration details

线上结果：
- `sync_casts_to_system_b --only-active`：17/17 成功，0 失败。
- 线上配置确认：`CAST_SOURCE=remote`、`CAST_SOURCE_FALLBACK_LOCAL=True`。

阶段结论：
- 完成首个核心域去耦，为下一步统一登录（SSO）奠定基础。

---

## 4.9 Phase 8：统一 SSO 与上线收口（2026-03-24）

关键内容：
- System B 上线 `GET /sso/authorize` 与 `POST /api/v1/auth/sso/exchange`。
- System A 改为 SSO 消费方，完成 state/nonce 校验、服务端 exchange、影子用户幂等映射。
- 账号映射口径收敛：`saas_user_id -> discord_uid -> discord_id`。
- 用户体验收口：A 用户链路不暴露 B；切换账号可重新触发认证。
- 角色收口：B 端无租户 A 用户标记为 `CONSUMER`，与员工体系区分。

阶段结论：
- 身份域去耦完成，A/B 登录耦合从“共享登录入口”收敛为“统一身份源 + 分角色后台”。

---

## 4.10 Phase 9：角色映射闭环与历史修正（2026-03-25）

关键内容：
- A 发起 SSO 增加 `a_role` 透传，形成三态映射：`ADMIN / STAFF / CONSUMER`。
- B 侧 public SSO 角色同步完善：已绑定 `Veludo` tenant 的 A 来源账号仍会按 `a_role` 更新角色。
- 新增历史数据修正命令 `backfill_a_sso_users`，支持 dry-run / apply。
- 生产实操补充：当 B 侧不可见 A `core_user` 时进入 fallback，只批量修 tenant，角色需手动 `--admin-ids/--staff-ids`。
- 员工端前台统一改造：`/home|/profile|/schedule|/bookings` 对齐同一 Header 设计语言。
- 员工资料页接入 `ResourceProfile` 扩展字段保存，`Discord ID` 改为只读并在后端禁改。
- 修复头像菜单 hover 丢失导致的 logout 下拉消失问题，改为点击展开模式。
- `STAFF` 账号注册/登录时自动绑定 `Resource`，确保员工可直接被预约与管理排班。
- 管理员面板重构为白色主基调，并收敛为 `Users & Roles / Cast CMS / Shifts & Orders / Service Presets / Email Templates` 模块架构。
- 店员资料与 Cast CMS 支持按 `ServicePreset` 复选并同步兼容时长字段（`allow_30/60/120_min`）。
- 邮件模板服务名支持 `{{ selected_service_name }}` 变量，并基于历史预约服务名做预填建议。
- `ServicePreset` 增加 `description` 与 `price` 字段，管理端录入与接口返回同步升级，为后续记账/运营扩展预留数据能力。
- 服务预设展示口径调整为整数日元（不显示小数），说明字段允许 `...` 占位以支持快速运营录入。

阶段结论：
- 角色语义从“仅区分消费者/员工”升级为“与 A 组织角色一致映射”，并形成可回放的历史数据修正路径。
- 员工端交互与资料维护闭环完成，管理员运营面板与服务配置链路同步收敛。

---

## 4.11 Phase 10：映射修复与员工视角收口（2026-03-26）

关键内容：
- 修复员工端排班页“预约红块点击误判为可删除”问题，改为跳转预约一览高亮对应订单。
- 修复 Cast CMS 介绍文中的 `\u000d\u000a` 等转义残留显示问题（前后端双向清洗）。

---

## 4.12 Phase 11：预约与 Cast CMS 体验细化（2026-03-28）

关键内容：
- 管理员 Cast CMS 增加拖拽排序，保存顺序同步为预约页面 Cast 显示顺序（基于 `ResourceProfile.display_order`）。
- 公开预约页 Cast/时间切换 Tab 增加语义图标（指名预约 / 时间预约）。
- Cast 详情卡片收起动画改为过渡结束后再收起布局，消除“瞬间消失 + 延迟回位”的割裂感。

阶段结论：
- 预约入口与后台运营联动更顺滑，前台展示顺序可控，关键交互更稳定。
- 新增 A->B `course(30/60/120)` 与 B 侧 `ServicePreset` 自动映射，回填 `service_preset_ids`。
- 新增 System A 个人资料保存后的强制同步（`on_commit` 触发），确保 course 变更实时同步到 System B。
- 新增订单服务名自动补齐：当未传 `service_id/service_name` 时按时长自动匹配服务预设。
- 历史订单短服务名（`30分/60分/120分`）展示时自动补全为完整课程名（`XX分VRASMR施術コース (PCVR)`）。
- 管理员 `Shifts & Orders` 筛选粒度下调到“按天”，订单排序改为按预约时间。
- 账号口径收口：`CONSUMER` 强制 `tenant=null`；public SSO 增加 STAFF 识别兜底，减少误判为 CONSUMER。
- 回填命令 `backfill_a_sso_users` 增强：`CONSUMER` 自动清空 tenant，fallback 模式可基于资源证据识别 STAFF。

阶段结论：
- 解决“数据已同步但员工端不可见”的尾部问题，完成身份/服务映射与员工端展示的一致性闭环。

---

## 4.12 Phase 11：Resource 串号事故修复（2026-03-27）

关键内容：
- 定位并修复 Veludo 员工资源串号事故：`nemuifia` 历史资源被 `orikasayom(id=14)` 误占用。
- 根因确认：`resources_resource.external_id` 承接的是 A 侧外部ID，但 B 侧历史绑定逻辑同时将 `SaaSUser.id` 作为匹配键，发生 numeric id 重用碰撞。
- 线上数据修复：
  - 资源 `41b3a02c-88b2-4879-a264-bbaf815d11bc` 解除错误 `linked_user_id=14`；
  - 资源名恢复为 `常眠フィア`；
  - 保留 `external_id=14` 以便 A 侧正确回填。
- 代码修复（防复发）：
  - `resources/services/binding_service.py` 移除 `SaaSUser.id` 参与 `external_id` 匹配；
  - 绑定仅保留 Discord 稳定身份键（`SocialAccount.uid` / `discord_id`）。
- 交付数据审计表：
  - 全量同步表、异常表、审计建议表与身份对照表写入 `reports/`，支撑后续批量清洗。
- 文档同步：
  - `技术文档_架构部署与运维.md`、`技术文档_API接口规范_SystemA_SystemB.md` 已补充此次事故与规则变更。

阶段结论：
- 彻底阻断“跨系统数字ID碰撞导致资源错绑”的高风险路径。
- 员工资源映射规则从“可能误命中”收敛为“稳定身份键优先”。

---

## 4.13 Phase 12：登录身份解耦与脏对象收口（2026-03-27）

关键内容：
- 明确双轨口径：
  - 登录身份使用 Discord `uid`；
  - `Resource.external_id` 仅保留 A 侧用户ID（数字口径）。
- B 端绑定逻辑升级：
  - `ensure_staff_resource_binding` 支持 `allow_create`；
  - public SSO 员工流程仅绑定已有资源，不再自动创建新资源。
- 历史脏数据清理：
  - 清理 `external_id = Discord uid` 的记录，恢复 `external_id` 语义一致性。
- A 端展示收口：
  - 远端 cast 拉取仅展示数字 `external_id` 对象，降低预约页脏可预约对象暴露。
- 运营结果：
  - 2tail / agi7171 恢复 canonical 映射（`external_id=15/16`）；
  - 对重复历史行做 legacy 归档处理，避免影响线上可预约列表。

阶段结论：
- 实现“认证身份域”与“业务映射域”彻底解耦，显著降低后续串号与垃圾资源复发概率。

---

## 4.14 Phase 13：资料展示口径收口与预约页媒体修复（2026-03-28）

关键内容：
- 修复个人资料页 `POST /profile/` 间歇 500：
  - 对用户名冲突/约束错误增加显式校验与错误提示，避免直接抛 500。
- 显示名持久化策略落地：
  - 显示名使用 `SaaSUser.first_name` 承载；
  - 资源绑定优先用显示名同步 `Resource.name`，解决“改名后刷新被改回”。
- 公开预约页媒体展示修复：
  - 修复介绍卡 `poster` 图片 URL 转义问题，恢复正常显示；
  - 增加图片加载失败自动回退占位图逻辑。
- 公开预约页 Cast 介绍卡增强：
  - 新增 `tags` 渲染，位置在展示名与介绍文之间，支持多标签 chip 展示。
- 网关侧稳定性修复（Nginx）：
  - `client_max_body_size` 提升到 `100M`；
  - 新增域名：`rosterlyreverse.com`（含 `www` 与 `api`）；
  - 适度放宽限流与超时参数，降低偶发拒绝率。

阶段结论：
- 完成“认证用户名”和“业务显示名”解耦，资料保存与预约展示口径一致。
- 预约页从“头像可见但海报缺失”恢复为完整可视化展示，用户理解成本显著下降。

---

## 4.15 Phase 14：用户名单一口径与 Discord 登录恢复（2026-03-28）

关键内容：
- 名称口径最终收敛为单一来源：
  - `SaaSUser.username` 作为唯一名称基准。
  - 对齐关系：profile username = cast cms 显示名 = 预约页显示名 = `Resource.name` = 管理员面板 username。
- 代码调整：
  - `resources/services/binding_service.py` 中资源名同步改为仅使用 `username`。
  - `dashboard/schedule_views.py` 与 `shared_profile.html` 改为直接读写 `username`，冲突时直接报错阻止保存。
- 登录故障热修：
  - 处理 Discord OAuth 回调后落到 `/accounts/inactive/` 的问题。
  - 在 `tenants/adapters.py` 登录前置流程增加 active 兜底：匹配到用户时若 `is_active=False` 自动恢复为 `True`。
- 运行验证：
  - 服务重启后登录链路恢复；
  - 现场核验 `inactive_users_total=0`，Discord 外部登录可正常完成。

阶段结论：
- 解决“名称多源导致回退/不一致”与“外部登录被 inactive 拦截”两类高频故障。
- 用户身份展示与登录可用性进入稳定状态。

---

## 4.16 Phase 15：公开预约链接收口与防刷增强（2026-03-29）

关键内容：
- 店铺级“订单详情跳转”能力上线：
  - `Tenant` 新增 `booking_detail_redirect_url`；
  - 邮件“詳細を見る”按钮支持跳转到店铺自有前台承接页（如 Veludo homepage）。
- 公开链接生成健壮性修复：
  - 修复极端场景下 `http:///...` 无效链接问题；
  - URL 构建改为多级回退（配置 base -> request host -> 相对路径）。
- 顾客订单详情页增强：
  - 新增日语取消期限明文（到“几日几点”）；
  - 取消能力继续受店铺 `cancellation_window_hours` 控制。
- 邮件内容增强：
  - 预约确认邮件新增“キャンセル期限：YYYY年MM月DD日 HH:MM まで”。
- 公开预约 anti-bot 加固：
  - 前端新增 honeypot 隐藏字段 `website`；
  - 后端新增按租户维度的 IP 限流（10 分钟/1 小时）与提交指纹短时限流；
  - 超限统一返回 `429`。
- 预约确认弹窗信息重构：
  - 分组显示为“担当者+服务 / 输入信息 / 完整时间段”；
  - 担当者展示头像，时间显示“开始-结束 + 时长”。

阶段结论：
- 从“链接可用”升级为“可品牌化承接 + 可解释取消规则 + 抗滥用”的可运营状态。
- 前台确认信息结构更清晰，减少下单误解与客服沟通成本。

---

## 5. 关键架构结论（供讲解）

1. 主体系统定位清晰：`System B` 为核心能力沉淀层，`System A` 为定制前台。  
2. 先稳态再去耦：先解决体验、安全、可观测，再做核心数据域迁移。  
3. 保留灰度与回滚：每次关键改造都保留开关和 fallback，降低线上风险。  
4. 文档治理先行：接口、需求、架构三文档分工明确后，迭代效率明显提升。  

---

## 6. 当前技术债与下一里程碑

当前主要技术债：
- 历史数据中仍可能存在少量展示型 `discord_id`（需持续收敛到 `discord_uid`）。

下一目标：
- 完成分离部署环境下的自动化健康检查（A/B 会话、回调、映射一致性）。

现有准备：
- 统一文档已并入主文档体系（API / 架构运维 / 需求文档）。

---

## 7. 对外 Case Study 建议讲法（10 分钟框架）

1. 起点（1 分钟）：双系统并行导致复杂度累积。  
2. 过程（5 分钟）：从可用性修复到运营能力建设再到去耦。  
3. 证据（2 分钟）：134 commits，全量演进；17/17 线上迁移成功。  
4. 方法（1 分钟）：灰度、幂等、回滚优先。  
5. 下一步（1 分钟）：登录统一 SSO，完成身份域去耦。  

---

## 8. 技术深潜：系统拓扑与边界

## 8.1 逻辑拓扑

- `System B (Rosterly)`：多租户核心层  
  - 职责：Tenant、Resource、Availability、Booking、EmailTemplate、ServicePreset、后台权限。
- `System A (Veludo)`：品牌化前台与编排层  
  - 职责：品牌页面、预约交互、埋点、部分运营后台视图。

调用方向：
- 用户前台流量：`Browser -> System A`
- 业务能力调用：`System A -> System B Integration API`
- 异步任务：`System B -> Celery Worker -> SMTP/Webhook`

## 8.2 部署拓扑（双服务器演进）

- 低资源机器（A）：仅运行 `system_a`（Gunicorn），端口绑定 `127.0.0.1:8000`，由 Nginx 反代。
- 主服务机器（B）：运行 `rosterly-core + rosterly-worker + postgres + redis`。
- 演进目标：A 逐步变轻，核心数据与身份都向 B 收敛。

---

## 9. 技术深潜：数据模型与一致性策略

## 9.1 关键模型（当前）

- A 侧：`core.User`, `casts.CastProfile`, `casts.CastMedia`, `core.UserActivity`
- B 侧：`tenants.SaaSUser`, `resources.Resource`, `resources.ResourceProfile`, `resources.ResourceMedia`, `bookings.Booking`

## 9.2 Cast 主数据迁移后的边界

- 主数据：B 的 `Resource + ResourceProfile + ResourceMedia`
- A 的角色：读取远端 cast 数据并做前台适配
- 回退策略：`CAST_SOURCE_FALLBACK_LOCAL=True` 时本地可降级

## 9.3 幂等同步策略（核心）

- 关键键：`tenant + external_id(system_a_user_id)`
- 写入方式：B 侧 `update_or_create` upsert
- 结果：重复执行 `sync_casts_to_system_b` 不会产生重复资源

---

## 10. 技术深潜：关键算法与业务规则

## 10.1 排班可约切分算法（B）

在 `resources/services/schedule_service.py` 中，`list_events` 采用以下规则：
- `24h` 预约提前量（`booking_deadline = now + 24h`）
- `30min` 预约缓冲带（`BOOKING_BUFFER`）
- 对每个可用 Availability：
  - 从原始区间开始
  - 逐个扣除和已确认订单重叠的缓冲区间
  - 小于最小可约时长（30 分钟）的碎片丢弃

效果：
- 前端看到的是“已避让缓冲冲突”的可约窗口，减少下单时冲突失败。

## 10.2 预约冲突规则

- 下单冲突：`start < existing_end + buffer && end > existing_start - buffer`
- 取消规则：距离开场不足 2 小时不可取消（B API 侧）。

## 10.3 跨天展示与前端切分

- 日历可视化按日切分展示，保证跨午夜时段在 UI 上可理解。
- 但实际预约时保留原始连续时间窗口，避免“只能按切片下单”的体验问题。

---

## 11. 技术深潜：API 契约与异步链路

## 11.1 关键 Integration API

- `POST /api/v1/integration/resources/`  
  - Cast 资料 upsert（支持 profile + medias）
- `GET /api/v1/integration/resources/`  
  - A 侧远端读取 cast 列表
- `PATCH /api/v1/integration/resources/<uuid>/`  
  - 管理端排序与档案局部更新
- `POST /api/v1/integration/bookings/`  
  - 创建预约，支持 `service_id/service_name`

## 11.2 邮件异步链路

链路：
- 下单成功 -> `process_new_booking.delay(booking_id)` -> Worker 读取最新 Booking -> 渲染模板变量 -> 发送客户/cast 邮件

邮件服务名优先级：
1. `booking.selected_service_name`
2. `tpl.service_name`（支持模板变量）
3. 动态时长回退（`duration_minutes`）

设计价值：
- 事务与邮件发送解耦，失败可重试，不阻塞主交易链路。

---

## 12. 技术深潜：可观测性、安全与稳定性

## 12.1 观测

- 埋点表：`UserActivity`
- 指标模块：访问总量、去重 IP、移动端占比、平均停留时长、趋势图、小时分布
- 过滤策略：短时误触（<3s）剔除，统计口径更稳定

## 12.2 安全

- A 服务端口收敛到回环地址（Nginx 统一对外）
- 自动封禁高频可疑 IP（缓存窗口计数 + 持久化 BlockedIP）
- 清理命令支持 `--dry-run` 与按条件定向清理

## 12.3 稳定发布策略

- 低内存机器优先 `restart`，避免不必要 `--build`
- 关键迁移都设计为幂等命令，可重复执行
- Feature Flag 灰度（如 `CAST_SOURCE`）+ 回滚开关

---

## 13. 附录：关键命令（复盘用）

```bash
git rev-list --count --all
git shortlog -sne --all
git log --reverse --pretty=format:'%ad|%h|%s' --date=short --all
```

---

## 14. 一句话总结

这 134 次提交的核心价值，不是“功能数量”，而是完成了从快速试错到架构收敛的转变：在不中断业务的前提下，把系统逐步推向可运营、可扩展、可讲述的产品化状态。

---

## 15. Phase 12：管理台结构收口（2026-03-27）

关键内容：
- 将订阅配置从 `Store Settings` 完全拆分为独立 `Subscription` 模块。
- 在管理台顶部“ログイン中”左侧增加“サブスクリプション管理”按钮，统一订阅编辑入口。
- 修复“保存后总跳回店铺设置”的交互问题：按操作回到当前 tab（`?tab=...`）。
- Core-Time 注文编辑字段收敛为 4 项（担当者/顧客名(vrcID)/服务预设/開始時間），并由后端按服务时长自动计算结束时间。

阶段结论：
- 管理后台从“功能可用”进入“流程可运营”状态，编辑路径与模块职责边界显著清晰。
