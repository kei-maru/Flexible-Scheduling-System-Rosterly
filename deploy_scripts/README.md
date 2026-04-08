# One-Click Release Scripts

## Files
- `deploy_b.sh`: Release Server B (System B + DB + Redis + Worker)
- `deploy_a.sh`: Release Server A (1G machine, System A only)
- `verify_release.sh`: Post-release verification

## Default container names (already set)
- `veludo_system_a`
- `veludo-system-b`
- `veludo_worker`
- `saas_postgres`

Fallback names are also built-in:
- `rosterly_api`, `rosterly_worker`, `rosterly_postgres`

## Usage

### 1) On Server B
```bash
cd /home/ubuntu/Veludo_Saas
bash deploy_scripts/deploy_b.sh
```

### 2) On Server A (1G)
```bash
cd /home/ubuntu/Veludo_Saas
bash deploy_scripts/deploy_a.sh
```

### 3) Verification (after both are done)
```bash
cd /home/ubuntu/Veludo_Saas
bash deploy_scripts/verify_release.sh
```

## Optional overrides
If your path/container/health URL differs, override at runtime:

```bash
PROJECT_DIR=/your/path COMPOSE_FILE=compose.rosterly.yml bash deploy_scripts/deploy_b.sh
A_HEALTH_URL=https://your-a-domain/accounts/login/ B_HEALTH_URL=https://your-b-domain/sso/authorize bash deploy_scripts/verify_release.sh
```

## Notes
- Do not use `docker compose down -v` in production.
- After `.env` changes, use recreate (`up -d --build` or `up -d --force-recreate`), not only `restart`.
