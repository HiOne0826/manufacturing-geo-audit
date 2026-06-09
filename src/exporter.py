from __future__ import annotations

import csv
import io


def runs_to_csv(rows: list[dict]) -> str:
    output = io.StringIO()
    fieldnames = [
        "run_id",
        "batch_id",
        "question",
        "question_type",
        "provider",
        "model",
        "search_enabled",
        "search_mode",
        "thinking_type",
        "reasoning_effort",
        "thinking_budget",
        "repeat_index",
        "requested_at",
        "status",
        "target_brand_mentioned",
        "target_brand_rank",
        "recommendation_strength",
        "competitors_mentioned",
        "owned_site_cited",
        "third_party_cited",
        "risk_level",
        "response_text",
        "error_message",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({name: row.get(name, "") for name in fieldnames})
    return output.getvalue()


def analytics_to_csv(data: dict) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["metric", "value"])
    writer.writerow(["total_runs", data.get("total_runs", 0)])
    writer.writerow(["success_runs", data.get("success_runs", 0)])
    writer.writerow(["brand_mention_rate", data.get("brand_mention_rate", 0)])
    writer.writerow(["owned_citation_rate", data.get("owned_citation_rate", 0)])
    writer.writerow([])
    writer.writerow(["provider", "total", "mentioned", "mention_rate", "owned_citation_rate"])
    for provider, item in data.get("providers", {}).items():
        writer.writerow([
            provider,
            item.get("total", 0),
            item.get("mentioned", 0),
            item.get("mention_rate", 0),
            item.get("owned_citation_rate", 0),
        ])
    writer.writerow([])
    writer.writerow(["competitor", "count"])
    for item in data.get("competitors", []):
        writer.writerow([item["name"], item["count"]])
    return output.getvalue()
