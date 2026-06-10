#!/usr/bin/env python3
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import sys
import time
import urllib.request


def request_json(opener, base_url: str, method: str, path: str, payload: dict | None = None) -> dict:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(f"{base_url}{path}", data=data, headers=headers, method=method)
    with opener.open(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def request_bytes(opener, base_url: str, path: str) -> bytes:
    with opener.open(f"{base_url}{path}", timeout=20) as response:
        if response.status != 200:
            raise RuntimeError(f"{path} 返回状态异常：{response.status}")
        return response.read()


def main() -> int:
    parser = argparse.ArgumentParser(description="本地冒烟测试。需要先启动 app.py。")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--password", default=os.environ.get("APP_PASSWORD", ""))
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    health = request_json(opener, base_url, "GET", "/api/health")
    assert health.get("ok") is True, health
    auth_status = request_json(opener, base_url, "GET", "/api/auth/status")
    if auth_status.get("auth_enabled") and not auth_status.get("authenticated"):
        if not args.password:
            raise RuntimeError("服务已启用应用密码，请通过 --password 或 APP_PASSWORD 提供密码")
        login = request_json(opener, base_url, "POST", "/api/auth/login", {"password": args.password})
        assert login.get("authenticated") is True, login

    project = request_json(
        opener,
        base_url,
        "POST",
        "/api/projects",
        {
            "client_name": "Smoke 测试客户",
            "brand_name": "Smoke 测试品牌",
            "product_category": "Smoke 测试品类",
            "competitors": "竞品A;竞品B",
        },
    )
    model = request_json(
        opener,
        base_url,
        "POST",
        "/api/models",
        {
            "provider": "mock",
            "label": "Smoke Mock",
            "model": "mock-smoke",
            "api_key": "SMOKE-SECRET-KEY",
            "supports_pure": True,
            "supports_search": True,
            "active": True,
        },
    )
    models_raw = json.dumps(request_json(opener, base_url, "GET", "/api/models"), ensure_ascii=False)
    assert "SMOKE-SECRET-KEY" not in models_raw, "模型配置接口泄露了明文 api_key"
    assert '"api_key"' not in models_raw, "模型配置接口返回了 api_key 字段"

    rows = [
        {
            "question_id": f"S{idx:03d}",
            "question": f"Smoke 第 {idx} 个问题，Smoke 测试品牌表现如何？",
            "question_type": "smoke",
            "target_brand": "Smoke 测试品牌",
            "competitor_brands": "竞品A;竞品B",
        }
        for idx in range(1, 4)
    ]
    imported = request_json(opener, base_url, "POST", "/api/questions/import_rows", {"project_id": project["id"], "rows": rows})
    assert imported["count"] == 3, imported

    started = request_json(
        opener,
        base_url,
        "POST",
        "/api/runs/start",
        {
            "project_id": project["id"],
            "models": [{"model_config_id": model["id"], "search_enabled": True}],
            "repeat_count": 1,
        },
    )
    batch_id = started["batch_id"]
    status = {}
    for _ in range(100):
        status = request_json(opener, base_url, "GET", f"/api/runs/progress?batch_id={batch_id}")
        if status.get("status") in {"completed", "failed"}:
            break
        time.sleep(0.2)
    assert status.get("status") == "completed", status
    assert status["success"] + status["failed"] == status["total"], status

    csv_body = request_bytes(opener, base_url, f"/api/export/runs.csv?project_id={project['id']}")
    xls_body = request_bytes(opener, base_url, f"/api/export/runs.xls?project_id={project['id']}")
    assert b"run_id" in csv_body, "CSV 导出内容异常"
    assert b"<table" in xls_body, "XLS 导出内容异常"

    print(
        json.dumps(
            {
                "ok": True,
                "base_url": base_url,
                "project_id": project["id"],
                "batch_id": batch_id,
                "total": status["total"],
                "success": status["success"],
                "failed": status["failed"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"冒烟测试失败：{exc}", file=sys.stderr)
        raise
