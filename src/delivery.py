from __future__ import annotations

"""Trusted-delivery services for review, report versioning and exports.

The module deliberately depends only on Python's DB-API shaped connection used by
``src.db``.  It can therefore be called from HTTP handlers, workers, maintenance
scripts, and tests without importing the application.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from src.db import is_postgres_conn, json_db_value, parse_json_field


REVIEW_DECISIONS = frozenset({"valid", "excluded", "needs_review"})
REPORT_STATUSES = frozenset({"draft", "reviewed", "frozen", "archived"})


SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS run_review_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('valid', 'excluded', 'needs_review')),
    reason TEXT DEFAULT '',
    actor TEXT NOT NULL,
    metadata_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES model_runs(run_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_run_review_events_run
ON run_review_events(run_id, id DESC);

CREATE TABLE IF NOT EXISTS report_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id TEXT NOT NULL UNIQUE,
    project_id INTEGER NOT NULL,
    batch_id TEXT NOT NULL DEFAULT '',
    version_no INTEGER NOT NULL,
    title TEXT DEFAULT '',
    status TEXT NOT NULL CHECK(status IN ('draft', 'reviewed', 'frozen', 'archived')),
    summary_snapshot_json TEXT DEFAULT '{}',
    run_ids_json TEXT DEFAULT '[]',
    config_snapshot_json TEXT DEFAULT '{}',
    created_by TEXT NOT NULL,
    reviewed_by TEXT DEFAULT '',
    reviewed_at TEXT,
    frozen_by TEXT DEFAULT '',
    frozen_at TEXT,
    archived_by TEXT DEFAULT '',
    archived_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(project_id, batch_id, version_no)
);
CREATE INDEX IF NOT EXISTS idx_report_versions_project
ON report_versions(project_id, batch_id, version_no DESC);

CREATE TABLE IF NOT EXISTS operation_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id TEXT NOT NULL UNIQUE,
    project_id INTEGER,
    batch_id TEXT DEFAULT '',
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    details_json TEXT DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_operation_audit_project
ON operation_audit_log(project_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_operation_audit_entity
ON operation_audit_log(entity_type, entity_id, id DESC);
"""


POSTGRES_DDL = """
CREATE TABLE IF NOT EXISTS run_review_events (
    id BIGSERIAL PRIMARY KEY,
    review_id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL REFERENCES model_runs(run_id) ON DELETE CASCADE,
    decision TEXT NOT NULL CHECK(decision IN ('valid', 'excluded', 'needs_review')),
    reason TEXT DEFAULT '',
    actor TEXT NOT NULL,
    metadata_json JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_run_review_events_run
ON run_review_events(run_id, id DESC);

CREATE TABLE IF NOT EXISTS report_versions (
    id BIGSERIAL PRIMARY KEY,
    report_id TEXT NOT NULL UNIQUE,
    project_id BIGINT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    batch_id TEXT NOT NULL DEFAULT '',
    version_no INTEGER NOT NULL,
    title TEXT DEFAULT '',
    status TEXT NOT NULL CHECK(status IN ('draft', 'reviewed', 'frozen', 'archived')),
    summary_snapshot_json JSONB DEFAULT '{}'::jsonb,
    run_ids_json JSONB DEFAULT '[]'::jsonb,
    config_snapshot_json JSONB DEFAULT '{}'::jsonb,
    created_by TEXT NOT NULL,
    reviewed_by TEXT DEFAULT '',
    reviewed_at TIMESTAMPTZ,
    frozen_by TEXT DEFAULT '',
    frozen_at TIMESTAMPTZ,
    archived_by TEXT DEFAULT '',
    archived_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    UNIQUE(project_id, batch_id, version_no)
);
CREATE INDEX IF NOT EXISTS idx_report_versions_project
ON report_versions(project_id, batch_id, version_no DESC);

CREATE TABLE IF NOT EXISTS operation_audit_log (
    id BIGSERIAL PRIMARY KEY,
    audit_id TEXT NOT NULL UNIQUE,
    project_id BIGINT,
    batch_id TEXT DEFAULT '',
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    details_json JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_operation_audit_project
ON operation_audit_log(project_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_operation_audit_entity
ON operation_audit_log(entity_type, entity_id, id DESC);
"""


class DeliveryError(ValueError):
    """A stable, user-safe delivery-domain error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dict(row: Any) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _json(conn: Any, value: Any) -> Any:
    return json_db_value(conn, value)


def _actor(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        raise DeliveryError("ACTOR_REQUIRED", "操作人不能为空")
    return value


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _export_run(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["citations"] = parse_json_field(item.pop("citations_json", None), [])
    item["raw_response"] = parse_json_field(item.pop("raw_response_json", None), {})
    return _json_safe(item)


def ensure_delivery_schema(conn: Any) -> None:
    """Install additive delivery tables on either supported database."""
    conn.executescript(POSTGRES_DDL if is_postgres_conn(conn) else SQLITE_DDL)


def write_audit(
    conn: Any,
    *,
    entity_type: str,
    entity_id: str,
    action: str,
    actor: str,
    project_id: int | None = None,
    batch_id: str = "",
    details: dict[str, Any] | None = None,
) -> str:
    actor = _actor(actor)
    audit_id = f"audit_{uuid.uuid4().hex}"
    conn.execute(
        """
        INSERT INTO operation_audit_log (
            audit_id, project_id, batch_id, entity_type, entity_id,
            action, actor, details_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            audit_id,
            project_id,
            batch_id,
            entity_type,
            str(entity_id),
            action,
            actor,
            _json(conn, details or {}),
            _now(),
        ),
    )
    return audit_id


def list_audit_events(
    conn: Any,
    *,
    project_id: int | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if project_id is not None:
        clauses.append("project_id = ?")
        params.append(project_id)
    if entity_type:
        clauses.append("entity_type = ?")
        params.append(entity_type)
    if entity_id:
        clauses.append("entity_id = ?")
        params.append(str(entity_id))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM operation_audit_log {where} ORDER BY id DESC LIMIT ?",
        (*params, max(1, min(int(limit), 1000))),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["details"] = parse_json_field(item.pop("details_json", None), {})
        result.append(_json_safe(item))
    return result


def review_run(
    conn: Any,
    run_id: str,
    decision: str,
    *,
    actor: str,
    reason: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append a review decision; prior decisions remain immutable history."""
    actor = _actor(actor)
    if decision not in REVIEW_DECISIONS:
        raise DeliveryError("INVALID_REVIEW_DECISION", "质检结果必须是 valid、excluded 或 needs_review")
    run = _dict(
        conn.execute(
            "SELECT run_id, project_id, batch_id FROM model_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    )
    if not run:
        raise DeliveryError("RUN_NOT_FOUND", f"运行记录不存在：{run_id}")
    review_id = f"review_{uuid.uuid4().hex}"
    created_at = _now()
    conn.execute(
        """
        INSERT INTO run_review_events (
            review_id, run_id, decision, reason, actor, metadata_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (review_id, run_id, decision, reason.strip(), actor, _json(conn, metadata or {}), created_at),
    )
    write_audit(
        conn,
        project_id=int(run["project_id"]),
        batch_id=run["batch_id"],
        entity_type="run",
        entity_id=run_id,
        action="run.reviewed",
        actor=actor,
        details={"review_id": review_id, "decision": decision, "reason": reason.strip()},
    )
    return {
        "review_id": review_id,
        "run_id": run_id,
        "decision": decision,
        "reason": reason.strip(),
        "actor": actor,
        "metadata": metadata or {},
        "created_at": created_at,
    }


def get_run_review(conn: Any, run_id: str, *, include_history: bool = False) -> dict[str, Any] | None:
    rows = conn.execute(
        "SELECT * FROM run_review_events WHERE run_id = ? ORDER BY id DESC",
        (run_id,),
    ).fetchall()
    if not rows:
        return None
    history = []
    for row in rows:
        item = dict(row)
        item["metadata"] = parse_json_field(item.pop("metadata_json", None), {})
        history.append(_json_safe(item))
    current = dict(history[0])
    if include_history:
        current["history"] = history
    return current


def _batch(conn: Any, batch_id: str) -> dict[str, Any]:
    batch = _dict(conn.execute("SELECT * FROM sampling_batches WHERE batch_id = ?", (batch_id,)).fetchone())
    if not batch:
        raise DeliveryError("BATCH_NOT_FOUND", f"批次不存在：{batch_id}")
    return batch


def _current_run_rows(conn: Any, *, project_id: int, batch_id: str | None = None) -> list[dict[str, Any]]:
    batch_clause = "AND r.batch_id = ?" if batch_id else ""
    params: tuple[Any, ...] = (project_id, batch_id) if batch_id else (project_id,)
    rows = conn.execute(
        f"""
        SELECT r.*,
               q.question_id AS source_question_id,
               q.question,
               e.target_brand_mentioned,
               e.target_brand_rank,
               e.owned_site_cited,
               e.third_party_cited,
               rv.decision AS review_decision,
               rv.reason AS review_reason
        FROM model_runs r
        JOIN questions q ON q.id = r.question_id
        LEFT JOIN answer_evaluations e ON e.run_id = r.run_id
        LEFT JOIN run_review_events rv ON rv.id = (
            SELECT rr.id FROM run_review_events rr
            WHERE rr.run_id = r.run_id ORDER BY rr.id DESC LIMIT 1
        )
        WHERE r.project_id = ? AND COALESCE(r.is_current, 1) = 1 {batch_clause}
        ORDER BY r.id
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def build_summary(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(rows)
    included = [row for row in items if row.get("review_decision") != "excluded"]
    successes = [row for row in included if row.get("status") == "success"]
    mentioned = [row for row in successes if bool(row.get("target_brand_mentioned"))]
    cited = [row for row in successes if bool(row.get("owned_site_cited")) or bool(row.get("third_party_cited"))]
    needs_review = sum(1 for row in included if row.get("review_decision") == "needs_review")
    denominator = len(included)
    success_denominator = len(successes)
    return {
        "total_runs": len(items),
        "included_runs": denominator,
        "excluded_runs": len(items) - denominator,
        "needs_review_runs": needs_review,
        "success_runs": len(successes),
        "failed_runs": denominator - len(successes),
        "success_rate": round(len(successes) / denominator, 6) if denominator else 0.0,
        "brand_mention_rate": round(len(mentioned) / success_denominator, 6) if success_denominator else 0.0,
        "citation_rate": round(len(cited) / success_denominator, 6) if success_denominator else 0.0,
        "average_latency_ms": round(
            sum(int(row.get("latency_ms") or 0) for row in successes) / success_denominator, 2
        ) if success_denominator else 0.0,
        "total_cost_estimate": round(sum(float(row.get("cost_estimate") or 0) for row in included), 8),
    }


def create_report_version(
    conn: Any,
    *,
    project_id: int,
    batch_id: str | None,
    title: str,
    actor: str,
) -> dict[str, Any]:
    actor = _actor(actor)
    if not _dict(conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()):
        raise DeliveryError("PROJECT_NOT_FOUND", f"项目不存在：{project_id}")
    config_snapshot: dict[str, Any] = {}
    scope_batch_id = batch_id or ""
    if batch_id:
        batch = _batch(conn, batch_id)
        if int(batch["project_id"]) != int(project_id):
            raise DeliveryError("BATCH_PROJECT_MISMATCH", "批次不属于指定项目")
        config_snapshot = parse_json_field(batch.get("config_snapshot_json"), {})
    version_row = conn.execute(
        """
        SELECT COALESCE(MAX(version_no), 0) AS max_version
        FROM report_versions
        WHERE project_id = ? AND batch_id = ?
        """,
        (project_id, scope_batch_id),
    ).fetchone()
    version_no = int(dict(version_row)["max_version"] or 0) + 1
    report_id = f"report_{uuid.uuid4().hex}"
    now = _now()
    conn.execute(
        """
        INSERT INTO report_versions (
            report_id, project_id, batch_id, version_no, title, status,
            summary_snapshot_json, run_ids_json, config_snapshot_json,
            created_by, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?)
        """,
        (
            report_id,
            project_id,
            scope_batch_id,
            version_no,
            title.strip(),
            _json(conn, {}),
            _json(conn, []),
            _json(conn, config_snapshot),
            actor,
            now,
            now,
        ),
    )
    write_audit(
        conn,
        project_id=project_id,
        batch_id=scope_batch_id,
        entity_type="report",
        entity_id=report_id,
        action="report.created",
        actor=actor,
        details={"version_no": version_no, "title": title.strip()},
    )
    return get_report_version(conn, report_id)


def get_report_version(conn: Any, report_id: str) -> dict[str, Any]:
    item = _dict(conn.execute("SELECT * FROM report_versions WHERE report_id = ?", (report_id,)).fetchone())
    if not item:
        raise DeliveryError("REPORT_NOT_FOUND", f"报告版本不存在：{report_id}")
    item["summary_snapshot"] = parse_json_field(item.pop("summary_snapshot_json", None), {})
    item["run_ids"] = parse_json_field(item.pop("run_ids_json", None), [])
    item["config_snapshot"] = parse_json_field(item.pop("config_snapshot_json", None), {})
    return _json_safe(item)


def list_report_versions(
    conn: Any, *, project_id: int, batch_id: str | None = None
) -> list[dict[str, Any]]:
    clause = "AND batch_id = ?" if batch_id is not None else ""
    params: tuple[Any, ...] = (project_id, batch_id) if batch_id is not None else (project_id,)
    rows = conn.execute(
        f"SELECT report_id FROM report_versions WHERE project_id = ? {clause} ORDER BY version_no DESC",
        params,
    ).fetchall()
    return [get_report_version(conn, dict(row)["report_id"]) for row in rows]


def review_report(conn: Any, report_id: str, *, actor: str) -> dict[str, Any]:
    actor = _actor(actor)
    report = get_report_version(conn, report_id)
    if report["status"] == "reviewed":
        return report
    if report["status"] != "draft":
        raise DeliveryError("INVALID_REPORT_TRANSITION", "只有草稿报告可以审核")
    now = _now()
    conn.execute(
        "UPDATE report_versions SET status = 'reviewed', reviewed_by = ?, reviewed_at = ?, updated_at = ? WHERE report_id = ? AND status = 'draft'",
        (actor, now, now, report_id),
    )
    write_audit(
        conn, project_id=report["project_id"], batch_id=report.get("batch_id") or "",
        entity_type="report", entity_id=report_id, action="report.reviewed", actor=actor,
    )
    return get_report_version(conn, report_id)


def freeze_report(
    conn: Any,
    report_id: str,
    *,
    actor: str,
) -> dict[str, Any]:
    actor = _actor(actor)
    report = get_report_version(conn, report_id)
    if report["status"] == "frozen":
        return report
    if report["status"] != "reviewed":
        raise DeliveryError("INVALID_REPORT_TRANSITION", "只有已审核报告可以冻结")
    rows = _current_run_rows(
        conn,
        project_id=int(report["project_id"]),
        batch_id=report.get("batch_id"),
    )
    run_ids = [str(row["run_id"]) for row in rows]
    snapshot = build_summary(rows)
    snapshot.setdefault("snapshot_run_count", len(run_ids))
    snapshot.setdefault("snapshot_at", _now())
    now = _now()
    conn.execute(
        """
        UPDATE report_versions
        SET status = 'frozen', summary_snapshot_json = ?, run_ids_json = ?,
            frozen_by = ?, frozen_at = ?, updated_at = ?
        WHERE report_id = ? AND status = 'reviewed'
        """,
        (_json(conn, snapshot), _json(conn, run_ids), actor, now, now, report_id),
    )
    write_audit(
        conn, project_id=report["project_id"], batch_id=report.get("batch_id") or "",
        entity_type="report", entity_id=report_id, action="report.frozen", actor=actor,
        details={"run_count": len(run_ids)},
    )
    return get_report_version(conn, report_id)


def archive_report(conn: Any, report_id: str, *, actor: str) -> dict[str, Any]:
    actor = _actor(actor)
    report = get_report_version(conn, report_id)
    if report["status"] == "archived":
        return report
    if report["status"] != "frozen":
        raise DeliveryError("INVALID_REPORT_TRANSITION", "只有已冻结报告可以归档")
    now = _now()
    conn.execute(
        """
        UPDATE report_versions
        SET status = 'archived', archived_by = ?, archived_at = ?, updated_at = ?
        WHERE report_id = ? AND status = 'frozen'
        """,
        (actor, now, now, report_id),
    )
    write_audit(
        conn, project_id=report["project_id"], batch_id=report.get("batch_id") or "",
        entity_type="report", entity_id=report_id, action="report.archived", actor=actor,
        details={"previous_status": report["status"]},
    )
    return get_report_version(conn, report_id)


def _scope(rows: list[dict[str, Any]]) -> dict[str, list[Any]]:
    return {
        "question_ids": sorted({int(row["question_id"]) for row in rows}),
        "model_config_ids": sorted({int(row.get("model_config_id") or 0) for row in rows}),
        "model_matrix": sorted({
            "|".join((
                str(row.get("model_config_id") or 0),
                str(row.get("provider") or ""),
                str(row.get("model") or ""),
                str(row.get("model_version") or ""),
            ))
            for row in rows
        }),
        "search_modes": sorted({str(row.get("search_mode") or "off") for row in rows}),
        "thinking_modes": sorted({
            "|".join((
                str(row.get("thinking_type") or "disabled"),
                str(row.get("reasoning_effort") or ""),
                str(row.get("thinking_budget") if row.get("thinking_budget") is not None else ""),
            ))
            for row in rows
        }),
        "repeat_indexes": sorted({int(row.get("repeat_index") or 1) for row in rows}),
    }


def compare_batches(conn: Any, baseline_batch_id: str, candidate_batch_id: str) -> dict[str, Any]:
    baseline = _batch(conn, baseline_batch_id)
    candidate = _batch(conn, candidate_batch_id)
    reasons: list[dict[str, Any]] = []
    if int(baseline["project_id"]) != int(candidate["project_id"]):
        reasons.append({"code": "PROJECT_MISMATCH", "message": "两个批次不属于同一项目"})
    baseline_rows = _current_run_rows(conn, project_id=int(baseline["project_id"]), batch_id=baseline_batch_id)
    candidate_rows = _current_run_rows(conn, project_id=int(candidate["project_id"]), batch_id=candidate_batch_id)
    baseline_scope = _scope(baseline_rows)
    candidate_scope = _scope(candidate_rows)
    labels = {
        "question_ids": ("QUESTION_SET_MISMATCH", "问题集不一致"),
        "model_matrix": ("MODEL_MATRIX_MISMATCH", "模型矩阵不一致"),
        "search_modes": ("SEARCH_MODE_MISMATCH", "搜索模式不一致"),
        "thinking_modes": ("THINKING_MODE_MISMATCH", "推理配置不一致"),
        "repeat_indexes": ("REPEAT_COUNT_MISMATCH", "重复次数不一致"),
    }
    for key, (code, message) in labels.items():
        if baseline_scope[key] != candidate_scope[key]:
            reasons.append({
                "code": code,
                "message": message,
                "baseline": baseline_scope[key],
                "candidate": candidate_scope[key],
            })
    baseline_summary = build_summary(baseline_rows)
    candidate_summary = build_summary(candidate_rows)
    metric_keys = (
        "success_rate", "brand_mention_rate", "citation_rate", "average_latency_ms", "total_cost_estimate"
    )
    delta = {
        key: round(float(candidate_summary[key]) - float(baseline_summary[key]), 8)
        for key in metric_keys
    }
    return {
        "baseline_batch_id": baseline_batch_id,
        "candidate_batch_id": candidate_batch_id,
        "comparable": not reasons,
        "comparability_reasons": reasons,
        "baseline_scope": baseline_scope,
        "candidate_scope": candidate_scope,
        "baseline_summary": baseline_summary,
        "candidate_summary": candidate_summary,
        "delta": delta,
        "delta_is_directional_only": bool(reasons),
    }


def _rows_by_ids(conn: Any, run_ids: list[str]) -> list[dict[str, Any]]:
    if not run_ids:
        return []
    placeholders = ",".join("?" for _ in run_ids)
    rows = conn.execute(
        f"""
        SELECT r.*, q.question_id AS source_question_id, q.question,
               e.target_brand_mentioned, e.target_brand_rank,
               e.owned_site_cited, e.third_party_cited,
               rv.decision AS review_decision, rv.reason AS review_reason
        FROM model_runs r
        JOIN questions q ON q.id = r.question_id
        LEFT JOIN answer_evaluations e ON e.run_id = r.run_id
        LEFT JOIN run_review_events rv ON rv.id = (
            SELECT rr.id FROM run_review_events rr WHERE rr.run_id = r.run_id ORDER BY rr.id DESC LIMIT 1
        )
        WHERE r.run_id IN ({placeholders})
        """,
        tuple(run_ids),
    ).fetchall()
    by_id = {str(dict(row)["run_id"]): dict(row) for row in rows}
    return [by_id[run_id] for run_id in run_ids if run_id in by_id]


def build_export(
    conn: Any,
    *,
    batch_id: str | None = None,
    report_id: str | None = None,
    include_attempts: bool = False,
) -> dict[str, Any]:
    """Build the canonical delivery object used by JSON/CSV/XLSX renderers."""
    if bool(batch_id) == bool(report_id):
        raise DeliveryError("EXPORT_SCOPE_REQUIRED", "必须且只能指定 batch_id 或 report_id")
    report: dict[str, Any] | None = None
    if report_id:
        report = get_report_version(conn, report_id)
        if report["status"] not in {"frozen", "archived"}:
            raise DeliveryError("REPORT_NOT_FROZEN", "只有已冻结的报告可以导出")
        if not report.get("frozen_at"):
            raise DeliveryError("REPORT_NOT_FROZEN", "归档报告缺少冻结快照，不能导出")
        rows = _rows_by_ids(conn, report["run_ids"])
        summary = report["summary_snapshot"]
        batch_id = report.get("batch_id")
        project_id = int(report["project_id"])
    else:
        batch = _batch(conn, str(batch_id))
        project_id = int(batch["project_id"])
        rows = _current_run_rows(conn, project_id=project_id, batch_id=batch_id)
        summary = build_summary(rows)
    attempts: list[dict[str, Any]] = []
    if include_attempts and batch_id:
        try:
            attempts = [dict(row) for row in conn.execute(
                "SELECT * FROM execution_attempts WHERE batch_id = ? ORDER BY id", (batch_id,)
            ).fetchall()]
            for item in attempts:
                item["usage"] = parse_json_field(item.pop("usage_json", None), {})
                item.update(_json_safe(item))
        except Exception as exc:
            # Older databases may not have execution_attempts yet. Other errors
            # are deliberately not swallowed.
            if "execution_attempts" not in str(exc):
                raise
    return {
        "schema_version": "geo-audit-delivery/v1",
        "generated_at": _now(),
        "scope": {
            "project_id": project_id,
            "batch_id": batch_id,
            "report_id": report_id,
            "report_version": report["version_no"] if report else None,
            "frozen": bool(report),
        },
        "summary": summary,
        "runs": [_export_run(row) for row in rows],
        "attempts": attempts,
    }
