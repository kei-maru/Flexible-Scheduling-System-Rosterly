# 施工指南：SSO 统一登录（System B 主身份源）

**版本**: v1.0  
**日期**: 2026-03-23  
**适用项目**: `Veludo_Saas`  
**目标**: 将登录系统统一到 System B（Rosterly），System A 不再直连 Discord OAuth。

---

## 1. 施工目标

- 统一身份源：`System B` 成为唯一登录入口与用户主档来源。
- System A 角色转变：`System A` 只做 SSO 消费方（SP），不再自己做 Discord OAuth。
- 用户资料一致性：身份字段以 `SaaSUser` 为准，System A 保留业务影子用户。

---

## 2. 范围与非范围

### 2.1 本次范围

- SSO 登录流建设（Authorize + Code Exchange + Callback）。
- A/B 两端会话打通（统一身份，独立 Session）。
- 旧账号映射与灰度切换方案。
- 上线、验证、回滚手册。

### 2.2 本次不做

- 不做完整 OIDC Provider 产品化（例如通用 Discovery/JWKS 公网能力）。
- 不做跨系统“强一致单点登出”强制闭环（可后续迭代）。
- 不一次性删除 A 现有用户表与登录代码（先灰度下线）。

---

## 3. 目标架构

- IdP: `System B`
  - 负责 Discord OAuth、用户识别、签发一次性授权码。
  - 负责 `code -> 用户身份` 交换接口。
- SP: `System A`
  - 负责发起跳转、接收 callback、服务端换取身份、创建本地 Session。
- 用户模型
  - `System B`: `tenants.SaaSUser` 为身份主档。
  - `System A`: `core.User` 保留为影子用户，新增并维护 `saas_user_id` 映射字段。

---

## 4. 施工阶段

## 4.1 Phase 0：冻结与准备

- 冻结登录相关改动分支。
- 确认 A/B 都可稳定访问 HTTPS 域名。
- 在 B 准备 SSO 客户端配置（允许的 `redirect_uri` 白名单、`client_id`、`client_secret`）。
- 明确 Feature Flag：
  - `A_LOGIN_MODE=legacy|hybrid|sso`
  - 初始建议 `hybrid`。

验收：
- A/B 当前功能稳定，无新增 500。
- 登录相关日志可追踪（request id / user id / tenant id）。

---

## 4.2 Phase 1：System B 增加 SSO 身份能力

建议接口：

1. `GET /sso/authorize`
- 入参：`client_id`, `redirect_uri`, `state`, `nonce`
- 逻辑：用户未登录则走 B 登录；登录后生成一次性 `code` 并 302 回调。

2. `POST /api/v1/auth/sso/exchange`
- 入参：`code`, `client_id`, `client_secret`, `redirect_uri`
- 返回：`user_id`, `discord_id`, `username`, `tenant_id`, `role`, `exp`（或签名 token）

数据要求：
- `code` 一次性使用。
- `code` 有效期建议 60 秒。
- `redirect_uri` 强白名单校验。
- 失败场景记录审计日志（不返回敏感细节）。

验收：
- 同一 `code` 第二次使用必须失败。
- 过期 `code` 必须失败。
- 非白名单 `redirect_uri` 必须拒绝。

---

## 4.3 Phase 2：System A 改为 SSO 消费方

建议入口：

1. `GET /accounts/sso/login`
- 生成 `state/nonce` 写入 A Session。
- 跳转到 B `/sso/authorize`。

2. `GET /accounts/sso/callback`
- 校验 `state`。
- 服务端调用 B `/sso/exchange`。
- 以 `saas_user_id` 或 `discord_id` 幂等 `update_or_create` A 影子用户。
- 调用 `login(request, user)` 建立 A Session。

3. `GET /accounts/login/`
- 在 `hybrid` 阶段保留旧登录按钮。
- 默认引导使用 SSO 按钮。

验收：
- 新用户首次登录可正常落库并进入 A 页面。
- 老用户登录不会重复建人。
- callback 异常有明确错误页与日志。

---

## 4.4 Phase 3：账号映射与灰度切流

映射策略：
- 优先按 `saas_user_id`。
- 无 `saas_user_id` 时按 `discord_id` 回填。
- 禁止按可变显示名做主键匹配。

灰度策略：
- 第 1 阶段：`A_LOGIN_MODE=hybrid`，仅内部账号试用。
- 第 2 阶段：扩大到全量用户 20%/50%/100%。
- 第 3 阶段：切 `A_LOGIN_MODE=sso`。

验收：
- 登录成功率 >= 当前基线。
- 登录平均耗时在可接受范围内。
- 客服反馈中无明显“登录循环跳转”问题。

---

## 4.5 Phase 4：下线 A 直连 Discord

- 下线 A 的 Discord 直连入口与文案。
- A 中与直连 Discord 强绑定的逻辑改为从 B 身份结果读取。
- 保留紧急回退开关 1 个版本周期。

验收：
- A 不再触发 Discord 直连流程。
- B 成为唯一 OAuth 实际入口。

---

## 5. 安全基线

- 全链路 HTTPS。
- `state` 必检，防 CSRF。
- `nonce` 必检，防重放。
- `code` 一次性 + 短 TTL。
- `client_secret` 仅服务端保存，不进前端。
- SSO 交换接口仅服务端访问，严禁浏览器直接调用。
- 日志脱敏，禁止输出 `secret` 和完整 token。

---

## 6. 测试清单

### 6.1 功能测试

- 未登录用户从 A 跳转到 B 并登录成功回 A。
- 已登录用户从 A 发起登录可秒回调。
- A Session 过期后可重新 SSO 登录。

### 6.2 异常测试

- 篡改 `state`。
- 重放 `code`。
- 过期 `code`。
- 非法 `redirect_uri`。

### 6.3 回归测试

- A 预约、排班、cast 展示、admin-dashboard 不受登录改造影响。
- B 后台角色权限（STAFF/ADMIN）不回退。

---

## 7. 上线步骤（建议顺序）

1. 先部署 B（SSO 能力上线但未对外切流）。
2. 再部署 A（增加 callback 和 feature flag）。
3. 设置 `A_LOGIN_MODE=hybrid` 做小流量验证。
4. 验证通过后切 `A_LOGIN_MODE=sso`。
5. 稳定观察后下线 A 直连 Discord。

---

## 8. 回滚方案

一级回滚（最快）：
- 将 `A_LOGIN_MODE` 从 `sso` 调回 `hybrid` 或 `legacy`。
- 重启 A 服务。

二级回滚：
- 临时禁用 B SSO authorize 路由暴露。
- 保持 B 原有 dashboard 登录能力。

回滚验收：
- 用户可恢复登录。
- 关键业务页面可访问。
- 无持续 500。

---

## 9. 交付物清单

- 技术方案文档（本文件）。
- API 文档增补（SSO authorize/exchange）。
- 运维文档增补（上线与回滚步骤）。
- 测试报告（功能、异常、回归）。

---

## 10. 施工完成定义（DoD）

- B 成为唯一身份源。
- A 不再直连 Discord OAuth。
- A/B 身份映射稳定无重复账户增长。
- 线上运行 7 天无 P1 登录事故。

