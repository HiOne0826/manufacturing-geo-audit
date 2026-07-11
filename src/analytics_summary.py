from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from .db import get_project, get_sampling_batch, list_runs, list_runs_by_batch, list_sampling_batches
from .evaluator import split_names
from .platforms import test_platform_name


def pct(numerator: int | float, denominator: int | float) -> float:
    return round(float(numerator) / float(denominator) * 100, 2) if denominator else 0


def avg(values: list[int | float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def mode_label(row: dict[str, Any]) -> str:
    if row.get("search_enabled"):
        return str(row.get("search_mode") or "联网搜索")
    return "纯模型"


def provider_key(row: dict[str, Any]) -> str:
    platform = row.get("test_platform") or test_platform_name(row.get("provider"), row.get("model"))
    return f"{platform} / {mode_label(row)}"


def parse_citations(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []
    return []


def citation_domain(item: dict[str, Any]) -> str:
    url = str(item.get("url") or "")
    domain = urlparse(url).netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def project_question_entities(conn, project: dict[str, Any]) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT target_brand, competitor_brands
        FROM questions
        WHERE project_id = ?
        """,
        (int(project["id"]),),
    ).fetchall()
    target_aliases = {project.get("brand_name", "").strip()}
    competitor_aliases = set(split_names(project.get("competitors", "")))
    for row in rows:
        target_aliases.update(split_names(dict(row).get("target_brand", "")))
        competitor_aliases.update(split_names(dict(row).get("competitor_brands", "")))
    target_aliases = {item for item in target_aliases if item}
    competitor_aliases = {item for item in competitor_aliases if item and item not in target_aliases}
    return {
        "target": {
            "canonical_name": project.get("brand_name", ""),
            "entity_type": "brand",
            "aliases": sorted(target_aliases),
        },
        "competitors": [{"canonical_name": item, "aliases": [item], "entity_type": "brand"} for item in sorted(competitor_aliases)],
    }


def collect_runs(conn, project_id: int, batch_id: str | None) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    if not batch_id:
        return list_runs(conn, project_id, limit=10000), None
    batch = get_sampling_batch(conn, batch_id)
    if not batch:
        raise ValueError("批次不存在")
    if int(batch["project_id"]) != project_id:
        raise ValueError("批次不属于当前项目")
    return list_runs_by_batch(conn, batch_id, limit=10000), batch


def sample_quality(conn, project_id: int, batch_id: str | None, batch: dict[str, Any] | None, runs: list[dict[str, Any]]) -> dict[str, Any]:
    total_runs = len(runs)
    completed = sum(1 for row in runs if row.get("status") in {"success", "failed"})
    failed = sum(1 for row in runs if row.get("status") == "failed")
    valid = sum(1 for row in runs if row.get("status") == "success")
    if batch:
        planned = int(batch.get("total_count") or total_runs)
    else:
        batches = list_sampling_batches(conn, project_id)
        planned = sum(int(item.get("total_count") or 0) for item in batches) or total_runs
    pending = max(planned - completed, 0)
    return {
        "planned": planned,
        "completed": completed,
        "valid": valid,
        "failed": failed,
        "pending": pending,
        "valid_rate": pct(valid, planned),
        "failure_rate": pct(failed, planned),
    }


def visibility_summary(runs: list[dict[str, Any]], target_aliases: list[str]) -> dict[str, Any]:
    valid_rows = [row for row in runs if row.get("status") == "success"]
    mentioned_rows = [row for row in valid_rows if row.get("target_brand_mentioned")]
    ranks = [int(row["target_brand_rank"]) for row in mentioned_rows if row.get("target_brand_rank")]
    mentions = []
    for row in valid_rows:
        answer = str(row.get("response_text") or "")
        mentions.append(sum(answer.count(alias) for alias in target_aliases if alias))
    return {
        "target_brand": target_aliases[0] if target_aliases else "",
        "valid_samples": len(valid_rows),
        "mentioned": len(mentioned_rows),
        "mention_rate": pct(len(mentioned_rows), len(valid_rows)),
        "top1_rate": pct(sum(1 for rank in ranks if rank == 1), len(valid_rows)),
        "top3_rate": pct(sum(1 for rank in ranks if rank <= 3), len(valid_rows)),
        "top5_rate": pct(sum(1 for rank in ranks if rank <= 5), len(valid_rows)),
        "average_rank": avg(ranks),
        "avg_mentions_per_sample": avg(mentions) or 0,
    }


def aggregate_group(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    valid_rows = [row for row in rows if row.get("status") == "success"]
    failed = sum(1 for row in rows if row.get("status") == "failed")
    mentioned = sum(1 for row in valid_rows if row.get("target_brand_mentioned"))
    owned_cited = sum(1 for row in valid_rows if row.get("owned_site_cited"))
    ranks = [int(row["target_brand_rank"]) for row in valid_rows if row.get("target_brand_rank")]
    latencies = [int(row.get("latency_ms") or 0) for row in valid_rows]
    high_risk = sum(1 for row in valid_rows if row.get("risk_level") == "高")
    return {
        "total": total,
        "valid": len(valid_rows),
        "failed": failed,
        "mention_rate": pct(mentioned, len(valid_rows)),
        "top3_rate": pct(sum(1 for rank in ranks if rank <= 3), len(valid_rows)),
        "owned_citation_rate": pct(owned_cited, len(valid_rows)),
        "average_rank": avg(ranks),
        "avg_latency_ms": avg(latencies) or 0,
        "high_risk": high_risk,
    }


def provider_breakdown(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in runs:
        groups.setdefault(provider_key(row), []).append(row)
    return [
        {"name": name, **aggregate_group(rows)}
        for name, rows in sorted(groups.items(), key=lambda item: item[0])
    ]


def question_type_breakdown(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in runs:
        groups.setdefault(str(row.get("question_type") or "未分类"), []).append(row)
    result = []
    for name, rows in sorted(groups.items(), key=lambda item: item[0]):
        competitor_hits = sum(1 for row in rows if row.get("competitors_mentioned"))
        result.append({"name": name, "competitor_hit_rate": pct(competitor_hits, len(rows)), **aggregate_group(rows)})
    return result


def competitor_risks(runs: list[dict[str, Any]], competitors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names = [item["canonical_name"] for item in competitors]
    counts: Counter[str] = Counter()
    pressure: Counter[str] = Counter()
    valid = sum(1 for row in runs if row.get("status") == "success")
    for row in runs:
        mentioned = split_names(row.get("competitors_mentioned", ""))
        for name in mentioned:
            counts[name] += 1
            if not row.get("target_brand_mentioned"):
                pressure[name] += 1
    for name in names:
        counts.setdefault(name, 0)
        pressure.setdefault(name, 0)
    return [
        {
            "name": name,
            "count": count,
            "share_rate": pct(count, valid),
            "target_absent_count": pressure[name],
            "pressure_rate": pct(pressure[name], valid),
        }
        for name, count in counts.most_common()
    ]


def source_analysis(runs: list[dict[str, Any]]) -> dict[str, Any]:
    valid_rows = [row for row in runs if row.get("status") == "success"]
    owned = sum(1 for row in valid_rows if row.get("owned_site_cited"))
    third_party = sum(1 for row in valid_rows if row.get("third_party_cited"))
    domains: Counter[str] = Counter()
    for row in valid_rows:
        for item in parse_citations(row.get("citations_json")):
            domain = citation_domain(item)
            if domain:
                domains[domain] += 1
    return {
        "owned_citation_rate": pct(owned, len(valid_rows)),
        "third_party_citation_rate": pct(third_party, len(valid_rows)),
        "top_domains": [{"domain": name, "count": count} for name, count in domains.most_common(10)],
    }


def representative_rows(runs: list[dict[str, Any]]) -> dict[str, Any]:
    failed = [row for row in runs if row.get("status") == "failed"]
    high_risk = [row for row in runs if row.get("risk_level") == "高"]
    missed = [row for row in runs if row.get("status") == "success" and not row.get("target_brand_mentioned")]
    return {
        "failed": failed[:8],
        "high_risk": high_risk[:8],
        "brand_missed": missed[:8],
    }


def recommendations(summary: dict[str, Any]) -> list[str]:
    items: list[str] = []
    visibility = summary["visibility"]
    quality = summary["sample_quality"]
    sources = summary["source_analysis"]
    if quality["failure_rate"] > 10:
        items.append("先处理失败样本，当前失败率会影响报告可信度。")
    if visibility["mention_rate"] < 50:
        items.append("目标品牌总体可见性偏低，应优先补齐品牌介绍、选型对比和采购场景内容。")
    if visibility["top3_rate"] < 30:
        items.append("目标品牌进入前三的概率不足，需要强化高意图问题下的权威内容和第三方证据。")
    if sources["owned_citation_rate"] < 20:
        items.append("官网引用率偏低，建议补充可被模型引用的产品页、案例页和参数页。")
    weak_types = [item for item in summary["question_type_breakdown"] if item["mention_rate"] < 50 and item["valid"]]
    if weak_types:
        names = "、".join(item["name"] for item in weak_types[:3])
        items.append(f"问题类型短板集中在 {names}，适合作为下一轮内容建设优先级。")
    pressured = [item for item in summary["competitor_risks"] if item["pressure_rate"] > 0]
    if pressured:
        items.append(f"{pressured[0]['name']} 在目标品牌缺席时仍有出现，建议补充差异化对比内容。")
    return items[:6] or ["当前样本未暴露明显短板，可扩大问题库或增加重复采样以提高判断稳定性。"]


def build_analytics_summary(conn, project_id: int, batch_id: str | None = None) -> dict[str, Any]:
    project = get_project(conn, project_id)
    if not project:
        raise ValueError("项目不存在")
    runs, batch = collect_runs(conn, project_id, batch_id)
    entities = project_question_entities(conn, project)
    target_aliases = entities["target"]["aliases"] or [project.get("brand_name", "")]
    data_cutoff = max((str(row.get("requested_at") or "") for row in runs), default="")
    latest_report = conn.execute(
        "SELECT report_id, version_no, status FROM report_versions WHERE project_id = ? ORDER BY version_no DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    config_scope = batch.get("config_snapshot", {}) if batch else {
        "mode": "mixed_project_batches",
        "batch_count": len(list_sampling_batches(conn, project_id)),
    }
    summary = {
        "meta": {
            "project_id": project_id,
            "client_name": project.get("client_name", ""),
            "brand_name": project.get("brand_name", ""),
            "batch_id": batch_id or "",
            "scope": "batch" if batch_id else "project",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "data_cutoff": data_cutoff,
            "configuration": config_scope,
            "report_version": dict(latest_report) if latest_report else None,
        },
        "entities": entities,
        "sample_quality": sample_quality(conn, project_id, batch_id, batch, runs),
        "visibility": visibility_summary(runs, target_aliases),
        "provider_breakdown": provider_breakdown(runs),
        "question_type_breakdown": question_type_breakdown(runs),
        "competitor_risks": competitor_risks(runs, entities["competitors"]),
        "source_analysis": source_analysis(runs),
        "evidence": representative_rows(runs),
    }
    summary["recommendations"] = recommendations(summary)
    return summary
