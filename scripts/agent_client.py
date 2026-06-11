#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request


def request_json(base_url: str, token: str, method: str, path: str, payload: dict | None = None) -> dict:
    data = None
    headers = {"Authorization": f"Bearer {token}"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(f"{base_url.rstrip('/')}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent API client for GEO audit.")
    parser.add_argument("--base-url", default=os.environ.get("GEO_AUDIT_BASE_URL", "http://127.0.0.1:8765"))
    parser.add_argument("--token", default=os.environ.get("AGENT_API_TOKEN", ""))
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-batch")
    create.add_argument("--project-id", type=int, required=True)
    create.add_argument("--model-id", action="append", type=int, required=True)
    create.add_argument("--csv-file", default="")
    create.add_argument("--repeat-count", type=int, default=1)
    create.add_argument("--max-workers", type=int, default=8)

    status = sub.add_parser("status")
    status.add_argument("batch_id")

    export = sub.add_parser("export")
    export.add_argument("batch_id")

    rerun = sub.add_parser("rerun-failed")
    rerun.add_argument("batch_id")

    args = parser.parse_args()
    if not args.token:
        raise SystemExit("缺少 AGENT_API_TOKEN")

    if args.command == "create-batch":
        csv_text = ""
        if args.csv_file:
            with open(args.csv_file, "r", encoding="utf-8-sig") as file:
                csv_text = file.read()
        payload = {
            "project_id": args.project_id,
            "model_ids": args.model_id,
            "csv_text": csv_text,
            "options": {
                "repeat_count": args.repeat_count,
                "max_workers": args.max_workers,
            },
        }
        result = request_json(args.base_url, args.token, "POST", "/api/agent/batches", payload)
    elif args.command == "status":
        result = request_json(args.base_url, args.token, "GET", f"/api/agent/batches/{args.batch_id}")
    elif args.command == "export":
        result = request_json(args.base_url, args.token, "GET", f"/api/agent/batches/{args.batch_id}/export")
    elif args.command == "rerun-failed":
        result = request_json(args.base_url, args.token, "POST", f"/api/agent/batches/{args.batch_id}/rerun_failed", {})
    else:
        raise SystemExit("未知命令")

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Agent client failed: {exc}", file=sys.stderr)
        raise
