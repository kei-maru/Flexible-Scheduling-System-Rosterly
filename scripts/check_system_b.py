#!/usr/bin/env python3
import argparse
import json
import os
from datetime import datetime, timedelta, timezone

import requests


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description="Check System B connectivity from System A container")
    parser.add_argument("--resource-id", required=True, help="SaaS resource UUID/external_id")
    parser.add_argument("--start", help="ISO start time (default: now)")
    parser.add_argument("--end", help="ISO end time (default: now+1 day)")
    args = parser.parse_args()

    system_b_root = os.environ.get("SYSTEM_B_ROOT", "http://system-b:8001")
    saas_api_url = os.environ.get("SAAS_API_URL", f"{system_b_root}/api/v1/integration")
    saas_api_key = os.environ.get("SAAS_API_KEY", "")
    saas_api_key_header = os.environ.get("SAAS_API_KEY_HEADER", "X-Tenant-Key")

    print("=== System B Connectivity Check ===")
    print(f"SYSTEM_B_ROOT={system_b_root}")
    print(f"SAAS_API_URL={saas_api_url}")
    print(f"SAAS_API_KEY_HEADER={saas_api_key_header}")
    print(f"SAAS_API_KEY_SET={'yes' if saas_api_key else 'no'}")

    headers = {saas_api_key_header: saas_api_key}
    start = args.start or iso(datetime.now(timezone.utc))
    end = args.end or iso(datetime.now(timezone.utc) + timedelta(days=1))

    url = f"{saas_api_url}/availability/"
    params = {
        "resource_id": args.resource_id,
        "start": start,
        "end": end,
    }

    print(f"\nGET {url}")
    print(f"params={json.dumps(params)}")

    try:
        res = requests.get(url, headers=headers, params=params, timeout=8)
        print(f"status={res.status_code}")
        ct = res.headers.get("content-type", "")
        if "application/json" in ct:
            print(json.dumps(res.json(), ensure_ascii=False, indent=2))
        else:
            print(res.text[:500])
        return 0 if res.ok else 1
    except Exception as exc:
        print(f"error={exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
