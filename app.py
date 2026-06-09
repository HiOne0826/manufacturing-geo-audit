import json
from base64 import b64decode
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from src.adapters import PROVIDER_PRESETS, enrich_model_config, test_model_config
from src.db import (
    DEFAULT_DB_PATH,
    analytics,
    create_model_config,
    create_project,
    delete_model_config,
    delete_project,
    delete_question,
    get_model_config,
    get_conn,
    import_questions_csv,
    import_questions_rows,
    init_db,
    list_model_configs,
    list_projects,
    list_questions,
    list_runs,
    seed_questions,
    update_model_config,
    update_project,
    update_question,
)
from src.exporter import analytics_to_csv, runs_to_csv
from src.runtime_env import load_dotenv_file
from src.runner import run_batch


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        parsed = urlparse(path)
        if parsed.path == "/":
            return str(STATIC_DIR / "index.html")
        if parsed.path.startswith("/static/"):
            return str(ROOT / parsed.path.lstrip("/"))
        return str(STATIC_DIR / parsed.path.lstrip("/"))

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.handle_api_get(parsed)
            return
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self.handle_api_post(parsed)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw or "{}")

    def json_response(self, data, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def csv_response(self, body: str, filename: str):
        raw = body.encode("utf-8-sig")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def handle_api_get(self, parsed):
        query = parse_qs(parsed.query)
        try:
            with get_conn(DEFAULT_DB_PATH) as conn:
                if parsed.path == "/api/health":
                    self.json_response({"ok": True, "db": str(DEFAULT_DB_PATH)})
                elif parsed.path == "/api/projects":
                    self.json_response({"projects": list_projects(conn)})
                elif parsed.path == "/api/models":
                    self.json_response(
                        {
                            "models": [enrich_model_config(item) for item in list_model_configs(conn)],
                            "presets": PROVIDER_PRESETS,
                        }
                    )
                elif parsed.path == "/api/questions":
                    project_raw = query.get("project_id", [None])[0]
                    project_id = int(project_raw) if project_raw and project_raw != "all" else None
                    self.json_response({"questions": list_questions(conn, project_id)})
                elif parsed.path == "/api/runs":
                    project_id = int(query.get("project_id", [0])[0])
                    self.json_response({"runs": list_runs(conn, project_id)})
                elif parsed.path == "/api/analytics":
                    project_id = int(query.get("project_id", [0])[0])
                    self.json_response(analytics(conn, project_id))
                elif parsed.path == "/api/export/runs.csv":
                    project_id = int(query.get("project_id", [0])[0])
                    self.csv_response(runs_to_csv(list_runs(conn, project_id, limit=10000)), "geo-runs.csv")
                elif parsed.path == "/api/export/summary.csv":
                    project_id = int(query.get("project_id", [0])[0])
                    self.csv_response(analytics_to_csv(analytics(conn, project_id)), "geo-summary.csv")
                else:
                    self.json_response({"error": "接口不存在"}, 404)
        except Exception as exc:
            self.json_response({"error": str(exc)}, 500)

    def handle_api_post(self, parsed):
        try:
            payload = self.read_json()
            with get_conn(DEFAULT_DB_PATH) as conn:
                if parsed.path == "/api/projects":
                    project_id = create_project(conn, payload)
                    self.json_response({"id": project_id})
                elif parsed.path == "/api/projects/update":
                    update_project(conn, payload)
                    self.json_response({"ok": True})
                elif parsed.path == "/api/projects/delete":
                    delete_project(conn, int(payload.get("id", 0)))
                    self.json_response({"ok": True})
                elif parsed.path == "/api/models":
                    model_id = create_model_config(conn, payload)
                    self.json_response({"id": model_id})
                elif parsed.path == "/api/models/update":
                    update_model_config(conn, payload)
                    self.json_response({"ok": True})
                elif parsed.path == "/api/models/delete":
                    delete_model_config(conn, int(payload.get("id", 0)))
                    self.json_response({"ok": True})
                elif parsed.path == "/api/models/test":
                    model_id = int(payload.get("id", 0))
                    model_config = get_model_config(conn, model_id)
                    if not model_config:
                        raise ValueError("模型配置不存在")
                    if payload.get("api_key") and payload.get("api_key") != "__KEEP__":
                        model_config["api_key"] = payload["api_key"]
                    if payload.get("api_base"):
                        model_config["api_base"] = payload["api_base"]
                    if payload.get("model"):
                        model_config["model"] = payload["model"]
                    self.json_response(test_model_config(model_config))
                elif parsed.path == "/api/models/preset":
                    preset = PROVIDER_PRESETS.get(payload.get("provider", ""))
                    if not preset:
                        raise ValueError("未找到默认服务商模板")
                    model_id = create_model_config(conn, preset)
                    self.json_response({"id": model_id})
                elif parsed.path == "/api/questions/seed":
                    count = seed_questions(conn, int(payload.get("project_id", 0)))
                    self.json_response({"count": count})
                elif parsed.path == "/api/questions/import":
                    count = import_questions_csv(conn, int(payload.get("project_id", 0)), payload.get("csv_text", ""))
                    self.json_response({"count": count})
                elif parsed.path == "/api/questions/import_rows":
                    rows = payload.get("rows", [])
                    if not rows and payload.get("file_base64"):
                        rows = self.decode_csv_rows(payload["file_base64"])
                    count = import_questions_rows(conn, int(payload.get("project_id", 0)), rows)
                    self.json_response({"count": count})
                elif parsed.path == "/api/questions/update":
                    update_question(conn, payload)
                    self.json_response({"ok": True})
                elif parsed.path == "/api/questions/delete":
                    delete_question(conn, int(payload.get("id", 0)))
                    self.json_response({"ok": True})
                elif parsed.path == "/api/runs/start":
                    result = run_batch(conn, int(payload.get("project_id", 0)), payload)
                    self.json_response(result)
                else:
                    self.json_response({"error": "接口不存在"}, 404)
        except Exception as exc:
            self.json_response({"error": str(exc)}, 500)

    def decode_csv_rows(self, file_base64: str):
        raw = b64decode(file_base64.encode("utf-8")).decode("utf-8-sig")
        return list(__import__("csv").DictReader(raw.splitlines()))


def main():
    load_dotenv_file(ROOT / ".env")
    init_db(DEFAULT_DB_PATH)
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("制造业品牌 GEO 工作台已启动：http://127.0.0.1:8765")
    server.serve_forever()


if __name__ == "__main__":
    main()
