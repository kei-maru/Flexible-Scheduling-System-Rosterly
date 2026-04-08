#!/usr/bin/env python3
import argparse
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

import requests


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def build_signature(secret: str, method: str, full_url: str, timestamp: str, body_bytes: bytes) -> str:
    parsed = urlsplit(full_url)
    path_with_query = parsed.path
    if parsed.query:
        path_with_query = f"{parsed.path}?{parsed.query}"
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    message = f"{method.upper()}\n{path_with_query}\n{timestamp}\n{body_hash}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


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
    sig_header = os.environ.get("SAAS_SIGNING_HEADER", "X-Tenant-Signature")
    ts_header = os.environ.get("SAAS_TIMESTAMP_HEADER", "X-Tenant-Timestamp")

    print("=== System B Connectivity Check ===")
    print(f"SYSTEM_B_ROOT={system_b_root}")
    print(f"SAAS_API_URL={saas_api_url}")
    print(f"SAAS_API_KEY_HEADER={saas_api_key_header}")
    print(f"SAAS_API_KEY_SET={'yes' if saas_api_key else 'no'}")

    start = args.start or iso(datetime.now(timezone.utc))
    end = args.end or iso(datetime.now(timezone.utc) + timedelta(days=1))

    url = f"{saas_api_url}/availability/"
    params = {
        "resource_id": args.resource_id,
        "start": start,
        "end": end,
    }

    query = urlencode(params)
    full_url = f"{url}?{query}"
    timestamp = str(int(time.time()))
    signature = build_signature(saas_api_key, "GET", full_url, timestamp, b"")
    headers = {
        saas_api_key_header: saas_api_key,
        ts_header: timestamp,
        sig_header: signature,
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
