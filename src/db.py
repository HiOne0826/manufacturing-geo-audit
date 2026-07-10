from __future__ import annotations

import csv
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.platforms import test_platform_name


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = Path(os.environ.get("GEO_AUDIT_DB_PATH", ROOT / "data" / "geo_audit.db"))
POSTGRES_SCHEMA_PATH = ROOT / "deploy" / "postgres" / "schema.sql"
QUESTION_CONTENT_KEYS = ("问题内容",)


def normalize_question_row(row: dict[str, Any]) -> dict[str, Any]:
    return {str(key).replace("\ufeff", "").strip(): value for key, value in row.items() if key is not None}


def json_text_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def extract_question_content(row: dict[str, Any]) -> str:
    normalized = normalize_question_row(row)
    for key in QUESTION_CONTENT_KEYS:
        value = normalized.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


class PostgresConnection:
    def __init__(self, dsn: str):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("缺少 PostgreSQL 依赖，请先安装：python3 -m pip install -r requirements-worker.txt") from exc
        self._conn = psycopg.connect(dsn, row_factory=dict_row)
        self.is_postgres = True

    def execute(self, sql: str, params: tuple[Any, ...] | list[Any] = ()):
        return self._conn.execute(sql.replace("?", "%s"), params)

    def executescript(self, sql: str) -> None:
        for statement in sql.split(";"):
            statement = statement.strip()
            if statement:
                self.execute(statement)

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def database_url() -> str:
    return os.environ.get("DATABASE_URL", "").strip()


def should_use_postgres(db_path: Path | str | None = None) -> bool:
    if not database_url():
        return False
    if db_path is None:
        return True
    return Path(db_path) == DEFAULT_DB_PATH


def is_postgres_conn(conn) -> bool:
    return bool(getattr(conn, "is_postgres", False))


def json_db_value(conn, value: Any) -> Any:
    if is_postgres_conn(conn):
        try:
            from psycopg.types.json import Jsonb
        except ImportError as exc:
            raise RuntimeError("缺少 PostgreSQL JSON 依赖，请先安装：python3 -m pip install -r requirements-worker.txt") from exc
        return Jsonb(value)
    return json.dumps(value, ensure_ascii=False)


def parse_json_field(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    if isinstance(value, str):
        return json.loads(value)
    return value

DEFAULT_MODEL_CONFIGS = [
    {
        "provider": "openai",
        "label": "GPT",
        "api_family": "OpenAI Responses API",
        "model": "gpt-5.5",
        "model_version": "",
        "model_type": "chat",
        "api_base": "https://api.openai.com/v1",
        "api_key": "",
        "priority": 100,
        "daily_limit": 0,
        "supports_pure": 1,
        "supports_search": 1,
        "web_search_mode": "Responses API hosted web_search 工具",
        "web_search_param_path": "tools[].type=web_search; tools[].search_context_size; tools[].user_location",
        "supports_reasoning": 1,
        "reasoning_param_path": "reasoning.effort",
        "reasoning_levels": "none;minimal;low;medium;high;xhigh",
        "supports_citation": 1,
        "citation_param_path": "output[].content[].annotations[type=url_citation]; include[]=web_search_call.action.sources",
        "supports_site_filter": 0,
        "supports_time_filter": 0,
        "supports_user_location": 1,
        "supports_tool_calling": 1,
        "active": 1,
        "notes": "OpenAI Responses API；联网搜索使用 hosted web_search，引用从 url_citation annotations 与 web_search_call sources 提取。",
    },
    {
        "provider": "gemini",
        "label": "Gemini",
        "api_family": "Gemini API",
        "model": "gemini-2.5-flash",
        "model_version": "",
        "model_type": "chat",
        "api_base": "https://generativelanguage.googleapis.com/v1beta",
        "api_key": "",
        "priority": 100,
        "daily_limit": 0,
        "supports_pure": 1,
        "supports_search": 1,
        "web_search_mode": "google_search 工具",
        "web_search_param_path": "tools[].google_search",
        "supports_reasoning": 1,
        "reasoning_param_path": "generationConfig.thinkingConfig",
        "reasoning_levels": "budget:0/-1/1024+",
        "supports_citation": 1,
        "citation_param_path": "groundingMetadata",
        "supports_site_filter": 0,
        "supports_time_filter": 0,
        "supports_user_location": 0,
        "supports_tool_calling": 1,
        "active": 1,
        "notes": "Google Gemini generateContent 接口。",
    },
    {
        "provider": "doubao",
        "label": "豆包",
        "api_family": "火山方舟 ARK Responses API",
        "model": "doubao-seed-2-0-mini-260428",
        "model_version": "",
        "model_type": "chat",
        "api_base": "https://ark.cn-beijing.volces.com/api/v3",
        "api_key": "",
        "priority": 100,
        "daily_limit": 0,
        "supports_pure": 1,
        "supports_search": 1,
        "web_search_mode": "Responses API 内置 web_search 工具",
        "web_search_param_path": "tools[].type=web_search; tools[].user_location; tools[].sources; tools[].limit; tools[].max_keyword",
        "supports_reasoning": 1,
        "reasoning_param_path": "reasoning.effort",
        "reasoning_levels": "none;minimal;low;medium;high",
        "supports_citation": 1,
        "citation_param_path": "output[].content[].annotations / citations",
        "supports_site_filter": 0,
        "supports_time_filter": 0,
        "supports_user_location": 1,
        "supports_tool_calling": 1,
        "active": 1,
        "notes": "联网搜索走 /responses，普通对话可走 /chat/completions；web_search 支持 user_location、sources、limit、max_keyword，引用从 annotations/citations 提取。",
    },
    {
        "provider": "deepseek",
        "label": "DeepSeek",
        "api_family": "DeepSeek API",
        "model": "deepseek-v4-flash",
        "model_version": "",
        "model_type": "chat",
        "api_base": "https://api.deepseek.com/v1",
        "api_key": "",
        "priority": 100,
        "daily_limit": 0,
        "supports_pure": 1,
        "supports_search": 1,
        "web_search_mode": "Brave Search 外部检索增强",
        "web_search_param_path": "BRAVE_SEARCH_API_KEY; /res/v1/web/search; q/count/country/search_lang/freshness/safesearch",
        "supports_reasoning": 1,
        "reasoning_param_path": "thinking.type / reasoning_effort",
        "reasoning_levels": "disabled;enabled + low/medium/high",
        "supports_citation": 1,
        "citation_param_path": "Brave Search web.results[].url/title/description",
        "supports_site_filter": 0,
        "supports_time_filter": 0,
        "supports_user_location": 0,
        "supports_tool_calling": 1,
        "active": 1,
        "notes": "DeepSeek OpenAI 兼容接口；标准 API 不提供原生联网搜索，本系统联网口径为 Brave Search 外部检索结果 + DeepSeek 生成。",
    },
    {
        "provider": "deepseek_web",
        "label": "DeepSeek 官网联网搜索",
        "api_family": "DeepSeek Web UI",
        "model": "deepseek-web-search",
        "model_version": "",
        "model_type": "browser",
        "api_base": "https://chat.deepseek.com",
        "api_key": "",
        "priority": 100,
        "daily_limit": 0,
        "supports_pure": 0,
        "supports_search": 1,
        "web_search_mode": "DeepSeek 官网联网搜索",
        "web_search_param_path": "Playwright UI + passive response capture",
        "supports_reasoning": 0,
        "reasoning_param_path": "",
        "reasoning_levels": "",
        "supports_citation": 1,
        "citation_param_path": "rendered answer links / passive network metadata",
        "supports_site_filter": 0,
        "supports_time_filter": 0,
        "supports_user_location": 0,
        "supports_tool_calling": 0,
        "active": 1,
        "notes": "通过官方 chat.deepseek.com 网页执行；每题独立会话，仅支持联网搜索批次。",
    },
    {
        "provider": "openrouter_gpt",
        "label": "OpenRouter-GPT",
        "api_family": "OpenRouter Chat Completions",
        "model": "openai/gpt-5.2",
        "model_version": "",
        "model_type": "chat",
        "api_base": "https://openrouter.ai/api/v1",
        "api_key": "",
        "priority": 100,
        "daily_limit": 0,
        "supports_pure": 1,
        "supports_search": 1,
        "web_search_mode": "OpenRouter web plugin / :online",
        "web_search_param_path": "plugins[].id=web; plugins[].max_results; plugins[].engine",
        "supports_reasoning": 1,
        "reasoning_param_path": "reasoning.effort",
        "reasoning_levels": "none;minimal;low;medium;high;xhigh",
        "supports_citation": 1,
        "citation_param_path": "choices[].message.annotations[type=url_citation].url_citation",
        "supports_site_filter": 1,
        "supports_time_filter": 0,
        "supports_user_location": 0,
        "supports_tool_calling": 1,
        "active": 1,
        "notes": "OpenRouter 中转 GPT 联网口径；联网使用 web plugin，默认搜索引擎为 Exa。不是 OpenAI 官方直连接口。",
    },
    {
        "provider": "openrouter_gemini",
        "label": "OpenRouter-Gemini",
        "api_family": "OpenRouter Chat Completions",
        "model": "google/gemini-2.5-flash",
        "model_version": "",
        "model_type": "chat",
        "api_base": "https://openrouter.ai/api/v1",
        "api_key": "",
        "priority": 100,
        "daily_limit": 0,
        "supports_pure": 1,
        "supports_search": 1,
        "web_search_mode": "OpenRouter web plugin / :online",
        "web_search_param_path": "plugins[].id=web; plugins[].max_results; plugins[].engine",
        "supports_reasoning": 1,
        "reasoning_param_path": "reasoning.effort",
        "reasoning_levels": "按 OpenRouter/Google 路由能力",
        "supports_citation": 1,
        "citation_param_path": "choices[].message.annotations[type=url_citation].url_citation",
        "supports_site_filter": 1,
        "supports_time_filter": 0,
        "supports_user_location": 0,
        "supports_tool_calling": 1,
        "active": 1,
        "notes": "OpenRouter 中转 Gemini 联网口径；联网使用 web plugin，默认搜索引擎为 Exa。不是 Google Gemini 官方直连接口。",
    },
    {
        "provider": "qwen",
        "label": "通义千问",
        "api_family": "阿里云百炼 / DashScope",
        "model": "qwen3.7-plus",
        "model_version": "",
        "model_type": "chat",
        "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "",
        "priority": 100,
        "daily_limit": 0,
        "supports_pure": 1,
        "supports_search": 1,
        "web_search_mode": "Responses API web_search",
        "web_search_param_path": "POST /responses; tools[].type=web_search; output[].action.sources",
        "supports_reasoning": 1,
        "reasoning_param_path": "reasoning.effort / enable_thinking / thinking_budget",
        "reasoning_levels": "none;minimal;low;medium;high;xhigh",
        "supports_citation": 1,
        "citation_param_path": "output[type=web_search_call].action.sources[].url; output[].content[].annotations",
        "supports_site_filter": 0,
        "supports_time_filter": 0,
        "supports_user_location": 0,
        "supports_tool_calling": 1,
        "active": 1,
        "notes": "qwen3.7-plus 联网搜索使用阿里云百炼 OpenAI 兼容 Responses API；仅传用户问题，不附加 system prompt；引用从 web_search_call.action.sources 提取。Responses 思考模式不支持 tool_choice=required，模型自行决定是否检索。",
    },
    {
        "provider": "hunyuan",
        "label": "腾讯元宝",
        "api_family": "腾讯混元 / TokenHub",
        "model": "hunyuan-turbos-latest",
        "model_version": "",
        "model_type": "chat",
        "api_base": "https://api.hunyuan.cloud.tencent.com/v1",
        "api_key": "",
        "priority": 100,
        "daily_limit": 0,
        "supports_pure": 1,
        "supports_search": 1,
        "web_search_mode": "腾讯云联网搜索 API SearchPro + hy3 生成",
        "web_search_param_path": "wsa.tencentcloudapi.com; Action=SearchPro; Query/Mode/Site/Freshness/Cnt; TENCENT_SEARCH_SECRET_ID/TENCENT_SEARCH_SECRET_KEY",
        "supports_reasoning": 1,
        "reasoning_param_path": "按模型能力控制，当前预置仅记录能力",
        "reasoning_levels": "按模型能力",
        "supports_citation": 1,
        "citation_param_path": "SearchPro Response.Pages[].url/title/passage/site",
        "supports_site_filter": 1,
        "supports_time_filter": 1,
        "supports_user_location": 0,
        "supports_tool_calling": 1,
        "active": 1,
        "notes": "腾讯元宝数据源当前走 TokenHub hy3 生成；联网搜索使用腾讯官方 WSA SearchPro，引用来自 SearchPro Pages。",
    },
    {
        "provider": "kimi",
        "label": "Kimi",
        "api_family": "Moonshot / Kimi API",
        "model": "kimi-k2.5",
        "model_version": "",
        "model_type": "chat",
        "api_base": "https://api.moonshot.cn/v1",
        "api_key": "",
        "priority": 100,
        "daily_limit": 0,
        "supports_pure": 1,
        "supports_search": 1,
        "web_search_mode": "官方 builtin_function.$web_search 工具",
        "web_search_param_path": "tools[].type=builtin_function; tools[].function.name=$web_search; thinking.type=disabled",
        "supports_reasoning": 1,
        "reasoning_param_path": "thinking.type",
        "reasoning_levels": "disabled;enabled",
        "supports_citation": 1,
        "citation_param_path": "tool_calls.arguments / citations",
        "supports_site_filter": 0,
        "supports_time_filter": 0,
        "supports_user_location": 0,
        "supports_tool_calling": 1,
        "active": 1,
        "notes": "联网搜索按官方文档走 builtin_function.$web_search 的 tool_calls 闭环；开启联网搜索时必须关闭深度思考。",
    },
    {
        "provider": "ernie",
        "label": "文心一言",
        "api_family": "百度千帆 / ERNIE API",
        "model": "ernie-5.1",
        "model_version": "",
        "model_type": "chat",
        "api_base": "https://qianfan.baidubce.com/v2",
        "api_key": "",
        "priority": 100,
        "daily_limit": 0,
        "supports_pure": 1,
        "supports_search": 1,
        "web_search_mode": "enable_search 联网搜索",
        "web_search_param_path": "enable_search",
        "supports_reasoning": 1,
        "reasoning_param_path": "enable_thinking / thinking_budget / reasoning_effort",
        "reasoning_levels": "low;medium;high",
        "supports_citation": 1,
        "citation_param_path": "citations / references",
        "supports_site_filter": 0,
        "supports_time_filter": 0,
        "supports_user_location": 1,
        "supports_tool_calling": 1,
        "active": 1,
        "notes": "百度千帆 / ERNIE API；支持 Bearer API Key，也兼容通过 AK/SK 先换 Access Token 后调用。",
    },
    {
        "provider": "minimax",
        "label": "MiniMax",
        "api_family": "MiniMax OpenAI 兼容接口",
        "model": "MiniMax-M1",
        "model_version": "",
        "model_type": "chat",
        "api_base": "https://api.minimaxi.com/v1",
        "api_key": "",
        "priority": 100,
        "daily_limit": 0,
        "supports_pure": 1,
        "supports_search": 0,
        "web_search_mode": "",
        "web_search_param_path": "",
        "supports_reasoning": 0,
        "reasoning_param_path": "",
        "reasoning_levels": "",
        "supports_citation": 0,
        "citation_param_path": "",
        "supports_site_filter": 0,
        "supports_time_filter": 0,
        "supports_user_location": 0,
        "supports_tool_calling": 1,
        "active": 1,
        "notes": "MiniMax OpenAI 兼容 Chat Completions。",
    },
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: Path | str | None = DEFAULT_DB_PATH):
    if should_use_postgres(db_path):
        return PostgresConnection(database_url())
    if db_path is None:
        db_path = DEFAULT_DB_PATH
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    if os.environ.get("SQLITE_WAL", "1") == "1":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def get_conn(db_path: Path | str | None = DEFAULT_DB_PATH):
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path | str | None = DEFAULT_DB_PATH) -> None:
    with get_conn(db_path) as conn:
        if is_postgres_conn(conn):
            conn.executescript(POSTGRES_SCHEMA_PATH.read_text(encoding="utf-8"))
            seed_model_configs(conn)
            return
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_name TEXT NOT NULL,
                brand_name TEXT NOT NULL,
                company_name TEXT DEFAULT '',
                product_category TEXT DEFAULT '',
                target_region TEXT DEFAULT '',
                website_domain TEXT DEFAULT '',
                competitors TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                question_id TEXT NOT NULL,
                industry TEXT DEFAULT '',
                product_category TEXT DEFAULT '',
                question_type TEXT DEFAULT '',
                question TEXT NOT NULL,
                question_source TEXT DEFAULT '',
                product_line TEXT DEFAULT '',
                purchase_stage TEXT DEFAULT '',
                scenario TEXT DEFAULT '',
                suggested_platforms TEXT DEFAULT '',
                optimization_goal TEXT DEFAULT '',
                top30_pushed TEXT DEFAULT '',
                first_screen_order INTEGER DEFAULT 0,
                filter_reason TEXT DEFAULT '',
                target_brand TEXT DEFAULT '',
                competitor_brands TEXT DEFAULT '',
                locale TEXT DEFAULT 'zh-CN',
                priority TEXT DEFAULT 'medium',
                notes TEXT DEFAULT '',
                import_row_json TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS model_configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                label TEXT NOT NULL,
                api_family TEXT DEFAULT '',
                model TEXT NOT NULL,
                model_version TEXT DEFAULT '',
                model_type TEXT DEFAULT 'chat',
                api_key TEXT DEFAULT '',
                api_base TEXT DEFAULT '',
                priority INTEGER DEFAULT 100,
                daily_limit INTEGER DEFAULT 0,
                supports_pure INTEGER DEFAULT 1,
                supports_search INTEGER DEFAULT 0,
                web_search_mode TEXT DEFAULT '',
                web_search_param_path TEXT DEFAULT '',
                supports_reasoning INTEGER DEFAULT 0,
                reasoning_param_path TEXT DEFAULT '',
                reasoning_levels TEXT DEFAULT '',
                supports_citation INTEGER DEFAULT 0,
                citation_param_path TEXT DEFAULT '',
                supports_site_filter INTEGER DEFAULT 0,
                supports_time_filter INTEGER DEFAULT 0,
                supports_user_location INTEGER DEFAULT 0,
                supports_tool_calling INTEGER DEFAULT 1,
                active INTEGER DEFAULT 1,
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS model_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL UNIQUE,
                batch_id TEXT NOT NULL,
                project_id INTEGER NOT NULL,
                question_id INTEGER NOT NULL,
                model_config_id INTEGER DEFAULT 0,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                model_version TEXT DEFAULT '',
                search_enabled INTEGER DEFAULT 0,
                temperature REAL DEFAULT 0,
                repeat_index INTEGER DEFAULT 1,
                requested_at TEXT NOT NULL,
                response_text TEXT DEFAULT '',
                citations_json TEXT DEFAULT '[]',
                latency_ms INTEGER DEFAULT 0,
                cost_estimate REAL DEFAULT 0,
                status TEXT NOT NULL,
                search_mode TEXT DEFAULT 'off',
                thinking_type TEXT DEFAULT 'disabled',
                reasoning_effort TEXT DEFAULT '',
                thinking_budget INTEGER,
                error_message TEXT DEFAULT '',
                raw_response_json TEXT DEFAULT '{}',
                is_current INTEGER DEFAULT 1,
                superseded_at TEXT DEFAULT '',
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS sampling_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL UNIQUE,
                project_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                total_count INTEGER DEFAULT 0,
                success_count INTEGER DEFAULT 0,
                failed_count INTEGER DEFAULT 0,
                completed_count INTEGER DEFAULT 0,
                config_json TEXT DEFAULT '{}',
                error_message TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS sampling_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL UNIQUE,
                task_key TEXT NOT NULL UNIQUE,
                batch_id TEXT NOT NULL,
                project_id INTEGER NOT NULL,
                question_id INTEGER NOT NULL,
                model_config_id INTEGER NOT NULL,
                repeat_index INTEGER DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'queued',
                attempt_count INTEGER DEFAULT 0,
                rq_job_id TEXT DEFAULT '',
                lease_owner TEXT DEFAULT '',
                lease_expires_at TEXT,
                heartbeat_at TEXT,
                chat_id TEXT DEFAULT '',
                artifact_dir TEXT DEFAULT '',
                error_code TEXT DEFAULT '',
                error_message TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_sampling_tasks_batch_status
            ON sampling_tasks(batch_id, status, id);

            CREATE TABLE IF NOT EXISTS answer_evaluations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL UNIQUE,
                target_brand_mentioned INTEGER DEFAULT 0,
                target_brand_rank INTEGER,
                recommendation_strength TEXT DEFAULT '未提及',
                sentiment TEXT DEFAULT '中性',
                competitors_mentioned TEXT DEFAULT '',
                owned_site_cited INTEGER DEFAULT 0,
                third_party_cited INTEGER DEFAULT 0,
                factual_errors TEXT DEFAULT '',
                risk_level TEXT DEFAULT '低',
                evaluator TEXT DEFAULT 'rule',
                evaluation_notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES model_runs(run_id) ON DELETE CASCADE
            );
            """
        )
        migrate_questions_schema(conn)
        migrate_model_configs(conn)
        migrate_model_runs_schema(conn)
        migrate_legacy_default_models(conn)
        sync_default_model_capabilities(conn)
        seed_model_configs(conn)


def migrate_model_configs(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(model_configs)").fetchall()}
    additions = {
        "api_family": "ALTER TABLE model_configs ADD COLUMN api_family TEXT DEFAULT ''",
        "model_version": "ALTER TABLE model_configs ADD COLUMN model_version TEXT DEFAULT ''",
        "model_type": "ALTER TABLE model_configs ADD COLUMN model_type TEXT DEFAULT 'chat'",
        "api_key": "ALTER TABLE model_configs ADD COLUMN api_key TEXT DEFAULT ''",
        "api_base": "ALTER TABLE model_configs ADD COLUMN api_base TEXT DEFAULT ''",
        "priority": "ALTER TABLE model_configs ADD COLUMN priority INTEGER DEFAULT 100",
        "daily_limit": "ALTER TABLE model_configs ADD COLUMN daily_limit INTEGER DEFAULT 0",
        "web_search_mode": "ALTER TABLE model_configs ADD COLUMN web_search_mode TEXT DEFAULT ''",
        "web_search_param_path": "ALTER TABLE model_configs ADD COLUMN web_search_param_path TEXT DEFAULT ''",
        "supports_reasoning": "ALTER TABLE model_configs ADD COLUMN supports_reasoning INTEGER DEFAULT 0",
        "reasoning_param_path": "ALTER TABLE model_configs ADD COLUMN reasoning_param_path TEXT DEFAULT ''",
        "reasoning_levels": "ALTER TABLE model_configs ADD COLUMN reasoning_levels TEXT DEFAULT ''",
        "supports_citation": "ALTER TABLE model_configs ADD COLUMN supports_citation INTEGER DEFAULT 0",
        "citation_param_path": "ALTER TABLE model_configs ADD COLUMN citation_param_path TEXT DEFAULT ''",
        "supports_site_filter": "ALTER TABLE model_configs ADD COLUMN supports_site_filter INTEGER DEFAULT 0",
        "supports_time_filter": "ALTER TABLE model_configs ADD COLUMN supports_time_filter INTEGER DEFAULT 0",
        "supports_user_location": "ALTER TABLE model_configs ADD COLUMN supports_user_location INTEGER DEFAULT 0",
        "supports_tool_calling": "ALTER TABLE model_configs ADD COLUMN supports_tool_calling INTEGER DEFAULT 1",
    }
    for name, sql in additions.items():
        if name not in columns:
            conn.execute(sql)


def migrate_model_runs_schema(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(model_runs)").fetchall()}
    additions = {
        "search_mode": "ALTER TABLE model_runs ADD COLUMN search_mode TEXT DEFAULT 'off'",
        "thinking_type": "ALTER TABLE model_runs ADD COLUMN thinking_type TEXT DEFAULT 'disabled'",
        "reasoning_effort": "ALTER TABLE model_runs ADD COLUMN reasoning_effort TEXT DEFAULT ''",
        "thinking_budget": "ALTER TABLE model_runs ADD COLUMN thinking_budget INTEGER",
        "model_config_id": "ALTER TABLE model_runs ADD COLUMN model_config_id INTEGER DEFAULT 0",
        "is_current": "ALTER TABLE model_runs ADD COLUMN is_current INTEGER DEFAULT 1",
        "superseded_at": "ALTER TABLE model_runs ADD COLUMN superseded_at TEXT DEFAULT ''",
    }
    for name, sql in additions.items():
        if name not in columns:
            conn.execute(sql)


def migrate_questions_schema(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(questions)").fetchall()}
    additions = {
        "question_source": "ALTER TABLE questions ADD COLUMN question_source TEXT DEFAULT ''",
        "product_line": "ALTER TABLE questions ADD COLUMN product_line TEXT DEFAULT ''",
        "purchase_stage": "ALTER TABLE questions ADD COLUMN purchase_stage TEXT DEFAULT ''",
        "scenario": "ALTER TABLE questions ADD COLUMN scenario TEXT DEFAULT ''",
        "suggested_platforms": "ALTER TABLE questions ADD COLUMN suggested_platforms TEXT DEFAULT ''",
        "optimization_goal": "ALTER TABLE questions ADD COLUMN optimization_goal TEXT DEFAULT ''",
        "top30_pushed": "ALTER TABLE questions ADD COLUMN top30_pushed TEXT DEFAULT ''",
        "first_screen_order": "ALTER TABLE questions ADD COLUMN first_screen_order INTEGER DEFAULT 0",
        "filter_reason": "ALTER TABLE questions ADD COLUMN filter_reason TEXT DEFAULT ''",
        "import_row_json": "ALTER TABLE questions ADD COLUMN import_row_json TEXT DEFAULT '{}'",
    }
    for name, sql in additions.items():
        if name not in columns:
            conn.execute(sql)


def migrate_legacy_default_models(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        UPDATE model_configs
        SET label = 'GPT',
            api_family = CASE WHEN COALESCE(api_family, '') = '' THEN 'OpenAI Responses API' ELSE api_family END,
            api_base = CASE WHEN api_base = '' THEN 'https://api.openai.com/v1' ELSE api_base END,
            supports_search = 1,
            supports_reasoning = 1,
            web_search_mode = CASE WHEN COALESCE(web_search_mode, '') IN ('', '内置 web_search 工具') THEN 'Responses API hosted web_search 工具' ELSE web_search_mode END,
            web_search_param_path = CASE WHEN COALESCE(web_search_param_path, '') IN ('', 'tools[].type=web_search') THEN 'tools[].type=web_search; tools[].search_context_size; tools[].user_location' ELSE web_search_param_path END,
            reasoning_param_path = CASE WHEN COALESCE(reasoning_param_path, '') = '' THEN 'reasoning.effort' ELSE reasoning_param_path END,
            reasoning_levels = CASE WHEN COALESCE(reasoning_levels, '') = '' THEN 'none;minimal;low;medium;high;xhigh' ELSE reasoning_levels END,
            supports_citation = CASE WHEN COALESCE(supports_citation, 0) = 0 THEN 1 ELSE supports_citation END,
            citation_param_path = CASE WHEN COALESCE(citation_param_path, '') IN ('', 'include[]=web_search_call.action.sources') THEN 'output[].content[].annotations[type=url_citation]; include[]=web_search_call.action.sources' ELSE citation_param_path END,
            supports_user_location = 1,
            model_type = CASE WHEN model_type = '' THEN 'chat' ELSE model_type END
        WHERE provider = 'openai' AND (label LIKE 'OpenAI%' OR label = 'GPT')
        """
    )
    conn.execute(
        """
        DELETE FROM model_configs
        WHERE provider IN ('perplexity', 'mock')
          AND COALESCE(api_key, '') = ''
        """
    )


def sync_default_model_capabilities(conn: sqlite3.Connection) -> None:
    for item in DEFAULT_MODEL_CONFIGS:
        conn.execute(
            """
            UPDATE model_configs
            SET api_family = ?,
                supports_pure = ?,
                supports_search = ?,
                web_search_mode = ?,
                web_search_param_path = ?,
                supports_reasoning = ?,
                reasoning_param_path = ?,
                reasoning_levels = ?,
                supports_citation = ?,
                citation_param_path = ?,
                supports_site_filter = ?,
                supports_time_filter = ?,
                supports_user_location = ?,
                supports_tool_calling = ?,
                notes = CASE WHEN COALESCE(notes, '') = '' THEN ? ELSE notes END
            WHERE provider = ?
            """,
            (
                item.get("api_family", ""),
                item.get("supports_pure", 1),
                item.get("supports_search", 0),
                item.get("web_search_mode", ""),
                item.get("web_search_param_path", ""),
                item.get("supports_reasoning", 0),
                item.get("reasoning_param_path", ""),
                item.get("reasoning_levels", ""),
                item.get("supports_citation", 0),
                item.get("citation_param_path", ""),
                item.get("supports_site_filter", 0),
                item.get("supports_time_filter", 0),
                item.get("supports_user_location", 0),
                item.get("supports_tool_calling", 1),
                item.get("notes", ""),
                item["provider"],
            ),
        )
    conn.execute(
        """
        UPDATE model_configs
        SET model = 'kimi-k2.5'
        WHERE provider = 'kimi' AND model = 'moonshot-v1-8k'
        """
    )
    conn.execute(
        """
        UPDATE model_configs
        SET model = 'gpt-5.5'
        WHERE provider = 'openai' AND model IN ('gpt-4.1', 'gpt-5.2')
        """
    )
    conn.execute(
        """
        UPDATE model_configs
        SET model = 'qwen3.7-plus'
        WHERE provider = 'qwen' AND model IN ('qwen-plus', 'qwen3-plus')
        """
    )
    conn.execute(
        """
        UPDATE model_configs
        SET supports_search = 1,
            web_search_mode = 'Responses API web_search',
            web_search_param_path = 'POST /responses; tools[].type=web_search; output[].action.sources',
            supports_citation = 1,
            citation_param_path = 'output[type=web_search_call].action.sources[].url; output[].content[].annotations',
            notes = 'qwen3.7-plus 联网搜索使用阿里云百炼 OpenAI 兼容 Responses API；仅传用户问题，不附加 system prompt；引用从 web_search_call.action.sources 提取。Responses 思考模式不支持 tool_choice=required，模型自行决定是否检索。'
        WHERE provider = 'qwen'
        """
    )
    conn.execute(
        """
        UPDATE model_configs
        SET model = 'deepseek-v4-flash'
        WHERE provider = 'deepseek' AND model IN ('deepseek-chat', 'deepseek-v3', 'deepseek-v3.1')
        """
    )
    conn.execute(
        """
        UPDATE model_configs
        SET model = 'ernie-5.1'
        WHERE provider = 'ernie' AND model IN ('ernie-4.5-turbo-32k', 'ernie-4.5-turbo-vl-32k')
        """
    )
    conn.execute(
        """
        UPDATE model_configs
        SET supports_search = 1,
            web_search_mode = '腾讯云联网搜索 API SearchPro + hy3 生成',
            web_search_param_path = 'wsa.tencentcloudapi.com; Action=SearchPro; Query/Mode/Site/Freshness/Cnt; TENCENT_SEARCH_SECRET_ID/TENCENT_SEARCH_SECRET_KEY',
            supports_citation = 1,
            citation_param_path = 'SearchPro Response.Pages[].url/title/passage/site',
            notes = '腾讯元宝数据源当前走 TokenHub hy3 生成；联网搜索使用腾讯官方 WSA SearchPro，引用来自 SearchPro Pages。'
        WHERE provider = 'hunyuan'
        """
    )


def row_to_dict(row: Any | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def run_row_to_dict(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["test_platform"] = test_platform_name(item.get("provider"), item.get("model"))
    return item


def list_projects(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT p.*,
               COUNT(DISTINCT q.id) AS question_count,
               COUNT(DISTINCT r.id) AS run_count
        FROM projects p
        LEFT JOIN questions q ON q.project_id = p.id
        LEFT JOIN model_runs r ON r.project_id = p.id
        GROUP BY p.id
        ORDER BY p.id DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def create_project(conn: sqlite3.Connection, payload: dict[str, Any]) -> int:
    returning = " RETURNING id" if is_postgres_conn(conn) else ""
    cur = conn.execute(
        f"""
        INSERT INTO projects (
            client_name, brand_name, company_name, product_category,
            target_region, website_domain, competitors, notes, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?){returning}
        """,
        (
            payload.get("client_name", "").strip() or "未命名客户",
            payload.get("brand_name", "").strip() or "未命名品牌",
            payload.get("company_name", "").strip(),
            payload.get("product_category", "").strip(),
            payload.get("target_region", "").strip(),
            payload.get("website_domain", "").strip(),
            payload.get("competitors", "").strip(),
            payload.get("notes", "").strip(),
            utc_now(),
        ),
    )
    if is_postgres_conn(conn):
        return int(cur.fetchone()["id"])
    return int(cur.lastrowid)


def update_project(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    conn.execute(
        """
        UPDATE projects
        SET client_name = ?, brand_name = ?, company_name = ?, product_category = ?,
            target_region = ?, website_domain = ?, competitors = ?, notes = ?
        WHERE id = ?
        """,
        (
            payload.get("client_name", "").strip() or "未命名客户",
            payload.get("brand_name", "").strip() or "未命名品牌",
            payload.get("company_name", "").strip(),
            payload.get("product_category", "").strip(),
            payload.get("target_region", "").strip(),
            payload.get("website_domain", "").strip(),
            payload.get("competitors", "").strip(),
            payload.get("notes", "").strip(),
            int(payload["id"]),
        ),
    )


def delete_project(conn: sqlite3.Connection, project_id: int) -> None:
    conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))


def get_project(conn: sqlite3.Connection, project_id: int) -> dict[str, Any] | None:
    return row_to_dict(conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone())


QUESTION_TEMPLATES = [
    ("Q001", "brand_direct", "请介绍一下{brand_name}这家公司和它的主要产品。"),
    ("Q002", "category_recommendation", "如果我要采购{product_category}，有哪些值得优先了解的品牌？"),
    ("Q003", "procurement", "选择{product_category}供应商时应该重点比较哪些因素？有哪些公司可以参考？"),
    ("Q004", "comparison", "{brand_name}和{competitor}相比，各自更适合什么采购场景？"),
    ("Q005", "technical", "{product_category}常见技术参数有哪些？哪些品牌资料比较完整？"),
    ("Q006", "regional_supplier", "在{target_region}采购{product_category}，有哪些供应商值得了解？"),
    ("Q007", "risk_after_sales", "采购{product_category}时，售后和交付风险通常有哪些？哪些品牌信息更透明？"),
]


def render_template(template: str, project: dict[str, Any]) -> str:
    competitors = [x.strip() for x in project.get("competitors", "").split(";") if x.strip()]
    competitor = competitors[0] if competitors else "主要竞品"
    values = {
        "brand_name": project.get("brand_name") or "目标品牌",
        "product_category": project.get("product_category") or "目标品类",
        "target_region": project.get("target_region") or "目标地区",
        "competitor": competitor,
    }
    return template.format(**values)


def seed_questions(conn: sqlite3.Connection, project_id: int) -> int:
    project = get_project(conn, project_id)
    if not project:
        raise ValueError("项目不存在")
    count = 0
    for qid, qtype, template in QUESTION_TEMPLATES:
        question = render_template(template, project)
        conn.execute(
            """
            INSERT INTO questions (
                project_id, question_id, industry, product_category, question_type,
                question, question_source, product_line, purchase_stage, scenario,
                suggested_platforms, optimization_goal, top30_pushed, first_screen_order, filter_reason,
                target_brand, competitor_brands, locale, priority, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                qid,
                "制造业",
                project.get("product_category", ""),
                qtype,
                question,
                "品牌推荐",
                project.get("product_category", ""),
                "认知阶段",
                project.get("target_region", ""),
                "ChatGPT;Gemini;豆包;通义千问;Kimi;MiniMax",
                "首轮核心样本",
                "否",
                0,
                "系统模板生成",
                project.get("brand_name", ""),
                project.get("competitors", ""),
                "zh-CN",
                "high" if qtype in {"brand_direct", "category_recommendation", "comparison"} else "medium",
                "系统模板生成",
                utc_now(),
            ),
        )
        count += 1
    return count


def _insert_question_row(
    conn: sqlite3.Connection, project: dict[str, Any], row: dict[str, Any], fallback_id: str
) -> None:
    row = normalize_question_row(row)
    question = extract_question_content(row)
    if not question:
        return
    def pick(*keys, default=""):
        for key in keys:
            value = row.get(key)
            if value is not None and str(value).strip() != "":
                return str(value).strip()
        return default
    conn.execute(
        """
        INSERT INTO questions (
            project_id, question_id, industry, product_category, question_type,
            question, question_source, product_line, purchase_stage, scenario,
            suggested_platforms, optimization_goal, top30_pushed, first_screen_order, filter_reason,
            target_brand, competitor_brands, locale, priority, notes, import_row_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(row.get("project_id") or project["id"]),
            pick("问题ID", default=fallback_id),
            pick("industry", "行业", default="制造业"),
            pick("产品品类", "产品类型", default=project.get("product_category", "")),
            pick("问题类型", default="custom"),
            question,
            pick("问题来源", default=""),
            pick("产品线", default=project.get("product_category", "")),
            pick("采购阶段", default=""),
            pick("场景", default=""),
            pick("平台", default=""),
            pick("首先核心样本可优先筛选高优先级问题", "优化目标", default=""),
            pick("拜访前30题", "推进前30词", default=""),
            int(pick("首轮顺序", "首发顺序", default="0") or 0),
            pick("filter_reason", "筛选理由", default=""),
            pick("target_brand", "目标品牌", default=project.get("brand_name", "")),
            pick("competitor_brands", "竞品", default=project.get("competitors", "")),
            pick("locale", "地区", default="zh-CN"),
            pick("priority", "优先级", default="medium"),
            pick("notes", "备注", default=""),
            json_text_value(row),
            utc_now(),
        ),
    )


def import_questions_text(conn: sqlite3.Connection, project_id: int, text: str) -> int:
    project = get_project(conn, project_id)
    if not project:
        raise ValueError("项目不存在")
    normalized_text = text.replace("\\n", "\n")
    questions = [line.strip() for line in normalized_text.splitlines() if line.strip()]
    count = 0
    for idx, question in enumerate(questions, start=1):
        _insert_question_row(
            conn,
            project,
            {
                "问题内容": question,
                "问题类型": "品牌推荐",
                "优先级": "medium",
            },
            f"TXT{idx:03d}",
        )
        count += 1
    return count


def looks_like_csv(text: str) -> bool:
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return "," in first_line and "问题内容" in first_line


def import_questions_csv(conn: sqlite3.Connection, project_id: int, csv_text: str) -> int:
    project = get_project(conn, project_id)
    if not project:
        raise ValueError("项目不存在")
    if not looks_like_csv(csv_text):
        first_line = next((line.strip() for line in csv_text.splitlines() if line.strip()), "")
        if "," in first_line or "\t" in first_line:
            return 0
        return import_questions_text(conn, project_id, csv_text)
    reader = csv.DictReader(csv_text.splitlines())
    count = 0
    for idx, row in enumerate(reader, start=1):
        if not extract_question_content(row):
            continue
        _insert_question_row(conn, project, row, f"CSV{idx:03d}")
        count += 1
    return count


def import_questions_rows(conn: sqlite3.Connection, project_id: int, rows: list[dict[str, Any]]) -> int:
    project = get_project(conn, project_id)
    if not project:
        raise ValueError("项目不存在")
    count = 0
    for idx, row in enumerate(rows, start=1):
        if not extract_question_content(row):
            continue
        _insert_question_row(conn, project, row, f"FILE{idx:03d}")
        count += 1
    return count


def import_question_content_rows(conn: sqlite3.Connection, project_id: int, rows: list[dict[str, Any]]) -> int:
    project = get_project(conn, project_id)
    if not project:
        raise ValueError("项目不存在")
    count = 0
    for idx, row in enumerate(rows, start=1):
        if not extract_question_content(row):
            continue
        _insert_question_row(conn, project, row, f"FILE{idx:03d}")
        count += 1
    return count


def list_questions(conn: sqlite3.Connection, project_id: int | None = None) -> list[dict[str, Any]]:
    sql = """
        SELECT q.*, p.client_name, p.brand_name AS project_brand_name
        FROM questions q
        JOIN projects p ON p.id = q.project_id
    """
    params: tuple[Any, ...] = ()
    if project_id:
        sql += " WHERE q.project_id = ?"
        params = (project_id,)
    sql += " ORDER BY q.id DESC"
    rows = conn.execute(sql, params).fetchall()
    items = [dict(row) for row in rows]
    for item in items:
        item.pop("import_row_json", None)
    return items


def update_question(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    conn.execute(
        """
        UPDATE questions
        SET project_id = ?, question_id = ?, industry = ?, product_category = ?, question_type = ?,
            question = ?, question_source = ?, product_line = ?, purchase_stage = ?, scenario = ?,
            suggested_platforms = ?, optimization_goal = ?, top30_pushed = ?, first_screen_order = ?, filter_reason = ?,
            target_brand = ?, competitor_brands = ?, locale = ?, priority = ?, notes = ?
        WHERE id = ?
        """,
        (
            int(payload["project_id"]),
            payload.get("question_id", "").strip(),
            payload.get("industry", "").strip(),
            payload.get("product_category", "").strip(),
            payload.get("question_type", "").strip(),
            payload.get("question", "").strip(),
            payload.get("question_source", "").strip(),
            payload.get("product_line", "").strip(),
            payload.get("purchase_stage", "").strip(),
            payload.get("scenario", "").strip(),
            payload.get("suggested_platforms", "").strip(),
            payload.get("optimization_goal", "").strip(),
            payload.get("top30_pushed", "").strip(),
            int(payload.get("first_screen_order", 0) or 0),
            payload.get("filter_reason", "").strip(),
            payload.get("target_brand", "").strip(),
            payload.get("competitor_brands", "").strip(),
            payload.get("locale", "zh-CN").strip(),
            payload.get("priority", "medium").strip(),
            payload.get("notes", "").strip(),
            int(payload["id"]),
        ),
    )


def delete_question(conn: sqlite3.Connection, question_id: int) -> None:
    conn.execute("DELETE FROM questions WHERE id = ?", (question_id,))


def seed_model_configs(conn: sqlite3.Connection) -> None:
    now = utc_now()
    for item in DEFAULT_MODEL_CONFIGS:
        existing = conn.execute(
            "SELECT id FROM model_configs WHERE provider = ? LIMIT 1",
            (item["provider"],),
        ).fetchone()
        if existing:
            continue
        conn.execute(
            """
            INSERT INTO model_configs (
                provider, label, api_family, model, model_version, model_type, api_key, api_base,
                priority, daily_limit, supports_pure, supports_search, web_search_mode, web_search_param_path,
                supports_reasoning, reasoning_param_path, reasoning_levels,
                supports_citation, citation_param_path, supports_site_filter, supports_time_filter,
                supports_user_location, supports_tool_calling, active, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["provider"],
                item["label"],
                item.get("api_family", ""),
                item["model"],
                item["model_version"],
                item["model_type"],
                item["api_key"],
                item["api_base"],
                item["priority"],
                item["daily_limit"],
                item["supports_pure"],
                item["supports_search"],
                item.get("web_search_mode", ""),
                item.get("web_search_param_path", ""),
                item.get("supports_reasoning", 0),
                item.get("reasoning_param_path", ""),
                item.get("reasoning_levels", ""),
                item.get("supports_citation", 0),
                item.get("citation_param_path", ""),
                item.get("supports_site_filter", 0),
                item.get("supports_time_filter", 0),
                item.get("supports_user_location", 0),
                item.get("supports_tool_calling", 1),
                item["active"],
                item["notes"],
                now,
                now,
            ),
        )


def list_model_configs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM model_configs ORDER BY id ASC").fetchall()
    return [dict(row) for row in rows]


def update_model_config(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    existing = conn.execute("SELECT api_key FROM model_configs WHERE id = ?", (int(payload["id"]),)).fetchone()
    api_key = payload.get("api_key", "")
    if api_key == "__KEEP__":
        api_key = existing["api_key"] if existing else ""
    conn.execute(
        """
        UPDATE model_configs
        SET provider = ?, label = ?, api_family = ?, model = ?, model_version = ?, model_type = ?, api_key = ?, api_base = ?,
            priority = ?, daily_limit = ?, supports_pure = ?, supports_search = ?, web_search_mode = ?, web_search_param_path = ?,
            supports_reasoning = ?, reasoning_param_path = ?, reasoning_levels = ?,
            supports_citation = ?, citation_param_path = ?, supports_site_filter = ?, supports_time_filter = ?,
            supports_user_location = ?, supports_tool_calling = ?, active = ?, notes = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            payload.get("provider", "").strip(),
            payload.get("label", "").strip(),
            payload.get("api_family", "").strip(),
            payload.get("model", "").strip(),
            payload.get("model_version", "").strip(),
            payload.get("model_type", "chat").strip(),
            api_key.strip(),
            payload.get("api_base", "").strip(),
            int(payload.get("priority", 100) or 100),
            int(payload.get("daily_limit", 0) or 0),
            1 if payload.get("supports_pure") else 0,
            1 if payload.get("supports_search") else 0,
            payload.get("web_search_mode", "").strip(),
            payload.get("web_search_param_path", "").strip(),
            1 if payload.get("supports_reasoning") else 0,
            payload.get("reasoning_param_path", "").strip(),
            payload.get("reasoning_levels", "").strip(),
            1 if payload.get("supports_citation") else 0,
            payload.get("citation_param_path", "").strip(),
            1 if payload.get("supports_site_filter") else 0,
            1 if payload.get("supports_time_filter") else 0,
            1 if payload.get("supports_user_location") else 0,
            1 if payload.get("supports_tool_calling", True) else 0,
            1 if payload.get("active") else 0,
            payload.get("notes", "").strip(),
            utc_now(),
            int(payload["id"]),
        ),
    )


def create_model_config(conn: sqlite3.Connection, payload: dict[str, Any]) -> int:
    returning = " RETURNING id" if is_postgres_conn(conn) else ""
    cur = conn.execute(
        f"""
        INSERT INTO model_configs (
            provider, label, api_family, model, model_version, model_type, api_key, api_base,
            priority, daily_limit, supports_pure, supports_search, web_search_mode, web_search_param_path,
            supports_reasoning, reasoning_param_path, reasoning_levels,
            supports_citation, citation_param_path, supports_site_filter, supports_time_filter,
            supports_user_location, supports_tool_calling, active, notes, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?){returning}
        """,
        (
            payload.get("provider", "").strip() or "custom",
            payload.get("label", "").strip() or "自定义模型",
            payload.get("api_family", "").strip(),
            payload.get("model", "").strip() or "custom-model",
            payload.get("model_version", "").strip(),
            payload.get("model_type", "chat").strip(),
            payload.get("api_key", "").strip(),
            payload.get("api_base", "").strip(),
            int(payload.get("priority", 100) or 100),
            int(payload.get("daily_limit", 0) or 0),
            1 if payload.get("supports_pure") else 0,
            1 if payload.get("supports_search") else 0,
            payload.get("web_search_mode", "").strip(),
            payload.get("web_search_param_path", "").strip(),
            1 if payload.get("supports_reasoning") else 0,
            payload.get("reasoning_param_path", "").strip(),
            payload.get("reasoning_levels", "").strip(),
            1 if payload.get("supports_citation") else 0,
            payload.get("citation_param_path", "").strip(),
            1 if payload.get("supports_site_filter") else 0,
            1 if payload.get("supports_time_filter") else 0,
            1 if payload.get("supports_user_location") else 0,
            1 if payload.get("supports_tool_calling", True) else 0,
            1 if payload.get("active", True) else 0,
            payload.get("notes", "").strip(),
            utc_now(),
            utc_now(),
        ),
    )
    if is_postgres_conn(conn):
        return int(cur.fetchone()["id"])
    return int(cur.lastrowid)


def delete_model_config(conn: sqlite3.Connection, model_id: int) -> None:
    conn.execute("DELETE FROM model_configs WHERE id = ?", (model_id,))


def get_model_config(conn: sqlite3.Connection, model_id: int) -> dict[str, Any] | None:
    return row_to_dict(conn.execute("SELECT * FROM model_configs WHERE id = ?", (model_id,)).fetchone())


def create_sampling_batch(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO sampling_batches (
            batch_id, project_id, status, total_count, success_count, failed_count,
            completed_count, config_json, error_message, created_at, started_at,
            finished_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["batch_id"],
            int(payload["project_id"]),
            payload.get("status", "queued"),
            int(payload.get("total_count", 0) or 0),
            int(payload.get("success_count", 0) or 0),
            int(payload.get("failed_count", 0) or 0),
            int(payload.get("completed_count", 0) or 0),
            json_db_value(conn, payload.get("config", {})),
            payload.get("error_message", ""),
            payload.get("created_at", now),
            payload.get("started_at"),
            payload.get("finished_at"),
            payload.get("updated_at", now),
        ),
    )


def update_sampling_batch(conn: sqlite3.Connection, batch_id: str, updates: dict[str, Any]) -> None:
    allowed = {
        "status",
        "total_count",
        "success_count",
        "failed_count",
        "completed_count",
        "error_message",
        "started_at",
        "finished_at",
        "updated_at",
    }
    fields = [field for field in updates if field in allowed]
    if not fields:
        return
    assignments = ", ".join(f"{field} = ?" for field in fields)
    values = [updates[field] for field in fields]
    conn.execute(
        f"UPDATE sampling_batches SET {assignments} WHERE batch_id = ?",
        (*values, batch_id),
    )


def get_sampling_batch(conn: sqlite3.Connection, batch_id: str) -> dict[str, Any] | None:
    row = row_to_dict(conn.execute("SELECT * FROM sampling_batches WHERE batch_id = ?", (batch_id,)).fetchone())
    if not row:
        return None
    row["config"] = parse_json_field(row.pop("config_json"), {})
    return row


def list_sampling_batches(conn: sqlite3.Connection, project_id: int | None = None) -> list[dict[str, Any]]:
    if project_id:
        rows = conn.execute(
            """
            SELECT b.*, p.client_name, p.brand_name
            FROM sampling_batches b
            JOIN projects p ON p.id = b.project_id
            WHERE b.project_id = ?
            ORDER BY b.id DESC
            """,
            (project_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT b.*, p.client_name, p.brand_name
            FROM sampling_batches b
            JOIN projects p ON p.id = b.project_id
            ORDER BY b.id DESC
            LIMIT 200
            """
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["config"] = parse_json_field(item.pop("config_json"), {})
        result.append(item)
    return result


def create_sampling_tasks(conn, tasks: list[dict[str, Any]]) -> int:
    if not tasks:
        return 0
    insert_prefix = "INSERT OR IGNORE INTO"
    conflict_clause = ""
    if is_postgres_conn(conn):
        insert_prefix = "INSERT INTO"
        conflict_clause = "ON CONFLICT (task_key) DO NOTHING"
    created = 0
    for task in tasks:
        cur = conn.execute(
            f"""
            {insert_prefix} sampling_tasks (
                task_id, task_key, batch_id, project_id, question_id, model_config_id,
                repeat_index, status, attempt_count, rq_job_id, lease_owner,
                lease_expires_at, heartbeat_at, chat_id, artifact_dir, error_code,
                error_message, created_at, started_at, finished_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            {conflict_clause}
            """,
            (
                task["task_id"],
                task["task_key"],
                task["batch_id"],
                int(task["project_id"]),
                int(task["question_id"]),
                int(task["model_config_id"]),
                int(task.get("repeat_index", 1)),
                task.get("status", "queued"),
                int(task.get("attempt_count", 0)),
                task.get("rq_job_id", ""),
                task.get("lease_owner", ""),
                task.get("lease_expires_at"),
                task.get("heartbeat_at"),
                task.get("chat_id", ""),
                task.get("artifact_dir", ""),
                task.get("error_code", ""),
                task.get("error_message", ""),
                task.get("created_at", utc_now()),
                task.get("started_at"),
                task.get("finished_at"),
                task.get("updated_at", utc_now()),
            ),
        )
        if getattr(cur, "rowcount", 0) > 0:
            created += 1
    return created


def get_sampling_task(conn, task_id: str) -> dict[str, Any] | None:
    return row_to_dict(
        conn.execute(
            """
            SELECT t.*, q.question, q.target_brand, q.competitor_brands,
                   m.provider, m.model, m.model_version, m.label AS model_label,
                   p.brand_name, p.website_domain, p.competitors AS project_competitors
            FROM sampling_tasks t
            JOIN questions q ON q.id = t.question_id
            JOIN model_configs m ON m.id = t.model_config_id
            JOIN projects p ON p.id = t.project_id
            WHERE t.task_id = ?
            """,
            (task_id,),
        ).fetchone()
    )


def list_sampling_tasks(conn, batch_id: str, limit: int = 10000) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT t.*, q.question, m.provider, m.model, m.label AS model_label
        FROM sampling_tasks t
        JOIN questions q ON q.id = t.question_id
        JOIN model_configs m ON m.id = t.model_config_id
        WHERE t.batch_id = ?
        ORDER BY t.id ASC
        LIMIT ?
        """,
        (batch_id, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def update_sampling_task(conn, task_id: str, updates: dict[str, Any]) -> None:
    allowed = {
        "status",
        "attempt_count",
        "rq_job_id",
        "lease_owner",
        "lease_expires_at",
        "heartbeat_at",
        "chat_id",
        "artifact_dir",
        "error_code",
        "error_message",
        "started_at",
        "finished_at",
        "updated_at",
    }
    fields = [field for field in updates if field in allowed]
    if not fields:
        return
    assignments = ", ".join(f"{field} = ?" for field in fields)
    conn.execute(
        f"UPDATE sampling_tasks SET {assignments} WHERE task_id = ?",
        (*[updates[field] for field in fields], task_id),
    )


def claim_sampling_task(conn, task_id: str, lease_owner: str, lease_seconds: int = 360) -> bool:
    now = utc_now()
    lease_until = (datetime.now(timezone.utc) + timedelta(seconds=max(60, lease_seconds))).isoformat()
    cur = conn.execute(
        """
        UPDATE sampling_tasks
        SET status = 'running', attempt_count = attempt_count + 1,
            lease_owner = ?, lease_expires_at = ?, heartbeat_at = ?,
            started_at = COALESCE(started_at, ?), updated_at = ?,
            error_code = '', error_message = ''
        WHERE task_id = ?
          AND (
              status = 'queued'
              OR (status = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?)
          )
        """,
        (lease_owner, lease_until, now, now, now, task_id, now),
    )
    return getattr(cur, "rowcount", 0) == 1


def sampling_task_counts(conn, batch_id: str) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS count FROM sampling_tasks WHERE batch_id = ? GROUP BY status",
        (batch_id,),
    ).fetchall()
    counts = {str(row["status"]): int(row["count"]) for row in rows}
    total = sum(counts.values())
    success = counts.get("success", 0)
    failed = counts.get("failed", 0)
    blocked = counts.get("blocked", 0)
    return {
        "total": total,
        "queued": counts.get("queued", 0),
        "running": counts.get("running", 0),
        "success": success,
        "failed": failed,
        "blocked": blocked,
        "completed": success + failed + blocked,
    }


def next_queued_sampling_task(conn, batch_id: str) -> dict[str, Any] | None:
    now = utc_now()
    return row_to_dict(
        conn.execute(
            """
            SELECT * FROM sampling_tasks
            WHERE batch_id = ?
              AND (
                  status = 'queued'
                  OR (status = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?)
              )
            ORDER BY id ASC
            LIMIT 1
            """,
            (batch_id, now),
        ).fetchone()
    )


def reset_resumable_sampling_tasks(conn, batch_id: str) -> int:
    now = utc_now()
    cur = conn.execute(
        """
        UPDATE sampling_tasks
        SET status = 'queued', rq_job_id = '', lease_owner = '', lease_expires_at = NULL,
            heartbeat_at = NULL, updated_at = ?
        WHERE batch_id = ?
          AND (
              status IN ('failed', 'blocked')
              OR (status = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?)
          )
        """,
        (now, batch_id, now),
    )
    return max(0, int(getattr(cur, "rowcount", 0)))


def reset_running_sampling_tasks(conn, batch_id: str) -> int:
    cur = conn.execute(
        """
        UPDATE sampling_tasks
        SET status = 'queued', rq_job_id = '', lease_owner = '', lease_expires_at = NULL,
            heartbeat_at = NULL, updated_at = ?
        WHERE batch_id = ? AND status = 'running'
        """,
        (utc_now(), batch_id),
    )
    return max(0, int(getattr(cur, "rowcount", 0)))


def recent_sampling_task_error_codes(conn, batch_id: str, limit: int = 3) -> list[str]:
    rows = conn.execute(
        """
        SELECT status, error_code FROM sampling_tasks
        WHERE batch_id = ? AND status IN ('success', 'failed', 'blocked')
        ORDER BY finished_at DESC, id DESC
        LIMIT ?
        """,
        (batch_id, limit),
    ).fetchall()
    return [str(row["error_code"] or "") if row["status"] != "success" else "" for row in rows]


def run_logical_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("question_id"),
        row.get("model_config_id"),
        bool(row.get("search_enabled")),
        row.get("search_mode") or "",
        row.get("thinking_type") or "",
        row.get("reasoning_effort") or "",
        row.get("thinking_budget"),
        int(row.get("repeat_index") or 1),
    )


def refresh_current_run_flags(conn: sqlite3.Connection, batch_id: str | None = None) -> dict[str, int]:
    where = "WHERE batch_id = ?" if batch_id else ""
    params: tuple[Any, ...] = (batch_id,) if batch_id else ()
    rows = conn.execute(
        f"""
        SELECT id, batch_id, question_id, model_config_id, search_enabled, search_mode,
               thinking_type, reasoning_effort, thinking_budget, repeat_index
        FROM model_runs
        {where}
        ORDER BY id DESC
        """,
        params,
    ).fetchall()
    current_ids: list[int] = []
    seen = set()
    for row in rows:
        item = dict(row)
        key = (item.get("batch_id"), *run_logical_key(item))
        if key in seen:
            continue
        seen.add(key)
        current_ids.append(int(item["id"]))
    conn.execute(
        f"UPDATE model_runs SET is_current = 0, superseded_at = ? {where}",
        (utc_now(), *params),
    )
    if current_ids:
        placeholders = ",".join("?" for _ in current_ids)
        conn.execute(
            f"UPDATE model_runs SET is_current = 1, superseded_at = '' WHERE id IN ({placeholders})",
            tuple(current_ids),
        )
    return {"total": len(rows), "current": len(current_ids), "historical": len(rows) - len(current_ids)}


def mark_superseded_runs(conn: sqlite3.Connection, run: dict[str, Any]) -> None:
    conn.execute(
        """
        UPDATE model_runs
        SET is_current = 0, superseded_at = ?
        WHERE batch_id = ?
          AND question_id = ?
          AND model_config_id = ?
          AND search_enabled = ?
          AND COALESCE(search_mode, '') = ?
          AND COALESCE(thinking_type, '') = ?
          AND COALESCE(reasoning_effort, '') = ?
          AND COALESCE(thinking_budget, -1) = ?
          AND repeat_index = ?
          AND COALESCE(is_current, 1) = 1
        """,
        (
            run.get("requested_at") or utc_now(),
            run["batch_id"],
            run["question_id"],
            int(run.get("model_config_id", 0) or 0),
            1 if run.get("search_enabled") else 0,
            run.get("search_mode", "off") or "",
            run.get("thinking_type", "disabled") or "",
            run.get("reasoning_effort", "") or "",
            run.get("thinking_budget") if run.get("thinking_budget") is not None else -1,
            int(run.get("repeat_index", 1)),
        ),
    )


def list_runs_by_batch(conn: sqlite3.Connection, batch_id: str, limit: int = 10000, include_history: bool = False) -> list[dict[str, Any]]:
    current_filter = "" if include_history else "AND COALESCE(r.is_current, 1) = 1"
    rows = conn.execute(
        f"""
        SELECT r.*, q.question_id AS source_question_id, q.question, q.question_type,
               q.product_category, q.product_line, q.purchase_stage, q.scenario,
               q.priority AS question_priority, q.suggested_platforms, q.import_row_json,
               e.target_brand_mentioned,
               e.target_brand_rank, e.recommendation_strength, e.competitors_mentioned,
               e.owned_site_cited, e.third_party_cited, e.risk_level
        FROM model_runs r
        JOIN questions q ON q.id = r.question_id
        LEFT JOIN answer_evaluations e ON e.run_id = r.run_id
        WHERE r.batch_id = ?
        {current_filter}
        ORDER BY r.id DESC
        LIMIT ?
        """,
        (batch_id, limit),
    ).fetchall()
    return [run_row_to_dict(row) for row in rows]


def insert_run(conn: sqlite3.Connection, run: dict[str, Any]) -> None:
    mark_superseded_runs(conn, run)
    conn.execute(
        """
        INSERT INTO model_runs (
            run_id, batch_id, project_id, question_id, model_config_id, provider, model, model_version,
            search_enabled, temperature, repeat_index, requested_at, response_text,
            citations_json, latency_ms, cost_estimate, status, search_mode, thinking_type,
            reasoning_effort, thinking_budget, error_message, raw_response_json, is_current, superseded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run["run_id"],
            run["batch_id"],
            run["project_id"],
            run["question_id"],
            int(run.get("model_config_id", 0) or 0),
            run["provider"],
            run["model"],
            run.get("model_version", ""),
            1 if run.get("search_enabled") else 0,
            None if run.get("temperature") is None else float(run.get("temperature", 0)),
            int(run.get("repeat_index", 1)),
            run["requested_at"],
            run.get("response_text", ""),
            json_db_value(conn, run.get("citations", [])),
            int(run.get("latency_ms", 0)),
            float(run.get("cost_estimate", 0)),
            run.get("status", "success"),
            run.get("search_mode", "off"),
            run.get("thinking_type", "disabled"),
            run.get("reasoning_effort", ""),
            run.get("thinking_budget"),
            run.get("error_message", ""),
            json_db_value(conn, run.get("raw_response", {})),
            1,
            "",
        ),
    )


def list_failed_runs_by_batch(conn: sqlite3.Connection, batch_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT r.*, q.question, q.target_brand, q.competitor_brands
        FROM model_runs r
        JOIN questions q ON q.id = r.question_id
        WHERE r.batch_id = ? AND r.status = 'failed'
          AND COALESCE(r.is_current, 1) = 1
          AND NOT EXISTS (
              SELECT 1
              FROM model_runs newer
              WHERE newer.batch_id = r.batch_id
                AND newer.question_id = r.question_id
                AND newer.model_config_id = r.model_config_id
                AND newer.search_enabled = r.search_enabled
                AND COALESCE(newer.search_mode, '') = COALESCE(r.search_mode, '')
                AND COALESCE(newer.thinking_type, '') = COALESCE(r.thinking_type, '')
                AND COALESCE(newer.reasoning_effort, '') = COALESCE(r.reasoning_effort, '')
                AND COALESCE(newer.thinking_budget, -1) = COALESCE(r.thinking_budget, -1)
                AND newer.repeat_index = r.repeat_index
                AND newer.id > r.id
          )
        ORDER BY r.id ASC
        """,
        (batch_id,),
    ).fetchall()
    return [run_row_to_dict(row) for row in rows]


def insert_evaluation(conn: sqlite3.Connection, evaluation: dict[str, Any]) -> None:
    conflict_clause = ""
    insert_prefix = "INSERT OR REPLACE INTO"
    if is_postgres_conn(conn):
        insert_prefix = "INSERT INTO"
        conflict_clause = """
        ON CONFLICT (run_id) DO UPDATE SET
            target_brand_mentioned = EXCLUDED.target_brand_mentioned,
            target_brand_rank = EXCLUDED.target_brand_rank,
            recommendation_strength = EXCLUDED.recommendation_strength,
            sentiment = EXCLUDED.sentiment,
            competitors_mentioned = EXCLUDED.competitors_mentioned,
            owned_site_cited = EXCLUDED.owned_site_cited,
            third_party_cited = EXCLUDED.third_party_cited,
            factual_errors = EXCLUDED.factual_errors,
            risk_level = EXCLUDED.risk_level,
            evaluator = EXCLUDED.evaluator,
            evaluation_notes = EXCLUDED.evaluation_notes,
            created_at = EXCLUDED.created_at
        """
    conn.execute(
        f"""
        {insert_prefix} answer_evaluations (
            run_id, target_brand_mentioned, target_brand_rank, recommendation_strength,
            sentiment, competitors_mentioned, owned_site_cited, third_party_cited,
            factual_errors, risk_level, evaluator, evaluation_notes, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        {conflict_clause}
        """,
        (
            evaluation["run_id"],
            1 if evaluation.get("target_brand_mentioned") else 0,
            evaluation.get("target_brand_rank"),
            evaluation.get("recommendation_strength", "未提及"),
            evaluation.get("sentiment", "中性"),
            evaluation.get("competitors_mentioned", ""),
            1 if evaluation.get("owned_site_cited") else 0,
            1 if evaluation.get("third_party_cited") else 0,
            evaluation.get("factual_errors", ""),
            evaluation.get("risk_level", "低"),
            evaluation.get("evaluator", "rule"),
            evaluation.get("evaluation_notes", ""),
            utc_now(),
        ),
    )


def list_runs(conn: sqlite3.Connection, project_id: int, limit: int = 200, include_history: bool = False) -> list[dict[str, Any]]:
    current_filter = "" if include_history else "AND COALESCE(r.is_current, 1) = 1"
    rows = conn.execute(
        f"""
        SELECT r.*, q.question_id AS source_question_id, q.question, q.question_type,
               q.product_category, q.product_line, q.purchase_stage, q.scenario,
               q.priority AS question_priority, q.suggested_platforms, q.import_row_json,
               e.target_brand_mentioned,
               e.target_brand_rank, e.recommendation_strength, e.competitors_mentioned,
               e.owned_site_cited, e.third_party_cited, e.risk_level
        FROM model_runs r
        JOIN questions q ON q.id = r.question_id
        LEFT JOIN answer_evaluations e ON e.run_id = r.run_id
        WHERE r.project_id = ?
        {current_filter}
        ORDER BY r.id DESC
        LIMIT ?
        """,
        (project_id, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def analytics(conn: sqlite3.Connection, project_id: int) -> dict[str, Any]:
    runs = list_runs(conn, project_id, limit=10000)
    total = len(runs)
    success = sum(1 for row in runs if row["status"] == "success")
    mentioned = sum(1 for row in runs if row.get("target_brand_mentioned"))
    owned_cited = sum(1 for row in runs if row.get("owned_site_cited"))
    providers: dict[str, dict[str, Any]] = {}
    competitors: dict[str, int] = {}
    for row in runs:
        mode = "联网搜索" if row["search_enabled"] else "纯模型"
        key = f"{row.get('test_platform') or test_platform_name(row.get('provider'), row.get('model'))} / {mode}"
        item = providers.setdefault(key, {"total": 0, "mentioned": 0, "owned_cited": 0})
        item["total"] += 1
        item["mentioned"] += 1 if row.get("target_brand_mentioned") else 0
        item["owned_cited"] += 1 if row.get("owned_site_cited") else 0
        for name in (row.get("competitors_mentioned") or "").split(";"):
            name = name.strip()
            if name:
                competitors[name] = competitors.get(name, 0) + 1
    for item in providers.values():
        item["mention_rate"] = round(item["mentioned"] / item["total"] * 100, 2) if item["total"] else 0
        item["owned_citation_rate"] = round(item["owned_cited"] / item["total"] * 100, 2) if item["total"] else 0
    return {
        "total_runs": total,
        "success_runs": success,
        "brand_mention_rate": round(mentioned / total * 100, 2) if total else 0,
        "owned_citation_rate": round(owned_cited / total * 100, 2) if total else 0,
        "providers": providers,
        "competitors": sorted(
            [{"name": name, "count": count} for name, count in competitors.items()],
            key=lambda item: item["count"],
            reverse=True,
        ),
    }
