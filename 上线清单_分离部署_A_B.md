# 上线清单（分离部署：System A / System B）

**适用场景**
- Server A 使用 `compose.veludo.yml`（仅 System A）
- Server B 使用 `compose.rosterly.yml`（System B + DB + Redis + Worker）
- 本清单不使用本地联调用的 `docker-compose.yml`

---

## 0. 版本与回滚基线（上线前必须）

- 在两台机器记录当前版本：
  - `git rev-parse --short HEAD`
  - `docker images | head`
- 备份数据库（Server B）：
  - `pg_dump -h 127.0.0.1 -U <DB_USER> -d saas_db > saas_db_$(date +%F_%H%M).sql`
  - `pg_dump -h 127.0.0.1 -U <DB_USER> -d veludo_db > veludo_db_$(date +%F_%H%M).sql`
- 备份配置：
  - `cp .env .env.backup.$(date +%F_%H%M)`

---

## 1. 生产配置检查（两端）

## 1.1 Server B（Rosterly）

- 使用 `compose.rosterly.yml`
- `.env` 关键项：
  - `SYSTEM_B_DISCORD_CLIENT_ID`
  - `SYSTEM_B_DISCORD_SECRET`
  - `SYSTEM_B_SSO_CLIENT_ID`
  - `SYSTEM_B_SSO_CLIENT_SECRET`
  - `SYSTEM_B_SSO_REDIRECT_URIS=https://<A域名>/accounts/sso/callback`
  - `ALLOWED_HOSTS` 包含 B 域名/IP
- Discord Developer Portal 回调必须是：
  - `https://<B域名>/accounts/discord/login/callback/`

## 1.2 Server A（Veludo）

- 使用 `compose.veludo.yml`
- `.env` 关键项：
  - `A_LOGIN_MODE=hybrid`（建议先灰度）
  - `SYSTEM_A_BASE_URL=https://<A域名>`
  - `SYSTEM_B_SSO_CLIENT_ID` 与 B 保持一致
  - `SYSTEM_B_SSO_CLIENT_SECRET` 与 B 保持一致
  - `SYSTEM_B_SSO_AUTHORIZE_URL=https://<B域名>/sso/authorize`
  - `SYSTEM_B_SSO_EXCHANGE_URL=http://<B内网地址或可达地址>:8001/api/v1/auth/sso/exchange`
  - `DB_HOST=<ServerB_IP>`（按 `compose.veludo.yml` 设计）
  - `CELERY_BROKER_URL=redis://<ServerB_IP>:6379/0`

---

## 2. 发布顺序（必须按顺序）

## 2.1 先发布 Server B

```bash
docker compose -f compose.rosterly.yml pull
docker compose -f compose.rosterly.yml up -d --build
docker compose -f compose.rosterly.yml exec -T rosterly-core python /app/system_b_saas/manage.py migrate
docker compose -f compose.rosterly.yml exec -T rosterly-core python /app/system_b_saas/manage.py check
docker compose -f compose.rosterly.yml ps
```

健康检查：
- `https://<B域名>/sso/authorize?...` 能返回 302/参数校验响应
- `rosterly-worker` 正常运行，日志无持续报错

## 2.2 再发布 Server A

```bash
docker compose -f compose.veludo.yml pull
docker compose -f compose.veludo.yml up -d --build
docker compose -f compose.veludo.yml exec -T system_a python /app/system_a_veludo/manage.py migrate
docker compose -f compose.veludo.yml exec -T system_a python /app/system_a_veludo/manage.py check
docker compose -f compose.veludo.yml ps
```

---

## 3. 冒烟测试（上线后立刻）

## 3.1 用户链路（A -> B -> A）

- 浏览器打开 `https://<A域名>/accounts/login/`
- 点击 Discord 登录
- 期望：跳转 B 授权 -> 回到 A 成功登录
- 期望：A 数据不出现重复账号（`saas_user_id` 不重复）

## 3.2 后台权限链路（B）

- 普通用户访问 `https://<B域名>/dashboard/login/`
  - 期望：提示无后台权限，不进入员工页
- 管理员访问 `https://<B域名>/dashboard/login/`
  - 期望：正常进入后台

## 3.3 映射风险检查（A）

```bash
docker compose -f compose.veludo.yml exec -T system_a python /app/system_a_veludo/manage.py shell -c "from django.contrib.auth import get_user_model; from django.db.models import Count; U=get_user_model(); print('dup_saas', list(U.objects.exclude(saas_user_id__isnull=True).exclude(saas_user_id='').values('saas_user_id').annotate(c=Count('id')).filter(c__gt=1))); print('non_uid', list(U.objects.exclude(discord_id__isnull=True).exclude(discord_id='').exclude(discord_id__regex=r'^[0-9]{15,22}$').values('id','username','discord_id','is_superuser','saas_user_id')));"
```

通过标准：
- `dup_saas` 为空
- `non_uid` 仅允许历史管理员账号（如有）

---

## 4. 切流步骤（推荐）

1. 先 `A_LOGIN_MODE=hybrid` 跑 24 小时观察
2. 登录成功率稳定、无“同人新号”投诉后
3. 切换 `A_LOGIN_MODE=sso`
4. 重建 A 容器使环境变量生效：

```bash
docker compose -f compose.veludo.yml up -d --no-deps --force-recreate system_a
```

---

## 5. 回滚方案

## 5.1 快速回滚（首选）

- 将 A 改回：`A_LOGIN_MODE=hybrid`（或 `legacy`）
- 执行：

```bash
docker compose -f compose.veludo.yml up -d --no-deps --force-recreate system_a
```

## 5.2 完整回滚

- 回退代码到上一 tag/commit
- 重建 B -> A
- 必要时恢复数据库备份

---

## 6. 常见误区（你这个项目高发）

- 在生产误用 `docker-compose.yml`（本地一体化文件）
- 修改 `.env` 后只 `restart` 不 `force-recreate`
- `SYSTEM_B_SSO_AUTHORIZE_URL` 和 `SYSTEM_B_SSO_EXCHANGE_URL` 都写成外网或都写成内网导致一端不可达
- A/B 使用不同 `SYSTEM_B_SSO_CLIENT_SECRET`
- Discord 回调 URL 没改成生产域名

---

## 7. 单行速查命令

- Server B 状态：
  - `docker compose -f compose.rosterly.yml ps`
- Server A 状态：
  - `docker compose -f compose.veludo.yml ps`
- B 日志：
  - `docker compose -f compose.rosterly.yml logs -f rosterly-core rosterly-worker`
- A 日志：
  - `docker compose -f compose.veludo.yml logs -f system_a`
