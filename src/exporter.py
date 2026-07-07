from __future__ import annotations

import csv
import io
import html
import json

from src.platforms import test_platform_name


EXPORT_PLATFORM_NAMES = {
    "openrouter_gpt": "OpenRouter-GPT",
    "openrouter_gemini": "OpenRouter-Gemini",
}

RUN_EXPORT_COLUMNS = [
    ("问题ID", "source_question_id"),
    ("问题内容", "question"),
    ("回答文本", "response_text"),
    ("引用来源", "citation_sources"),
    ("问题类型", "question_type"),
    ("产品类型", "product_category"),
    ("产品线", "product_line"),
    ("采购阶段", "purchase_stage"),
    ("场景", "scenario"),
    ("优先级", "question_priority"),
    ("建议测试平台", "suggested_platforms"),
    ("运行ID", "run_id"),
    ("批次ID", "batch_id"),
    ("测试平台", "test_platform"),
    ("联网搜索", "search_enabled_label"),
    ("生成时间", "requested_at"),
    ("状态", "status"),
    ("耗时", "latency_ms_label"),
    ("错误信息", "error_message"),
]


def export_test_platform_name(row: dict) -> str:
    provider = str(row.get("provider") or "").strip().lower()
    if provider in EXPORT_PLATFORM_NAMES:
        return EXPORT_PLATFORM_NAMES[provider]
    return row.get("test_platform") or test_platform_name(row.get("provider"), row.get("model"))


def run_export_value(row: dict, key: str) -> str:
    if key == "test_platform":
        return export_test_platform_name(row)
    if key == "search_enabled_label":
        return "是" if row.get("search_enabled") else "否"
    if key == "latency_ms_label":
        return f'{row.get("latency_ms", 0) or 0} ms'
    if key == "citation_sources":
        return citation_urls(row.get("citations_json"))
    return row.get(key, "")


def citation_urls(value: object) -> str:
    if not value:
        return ""
    try:
        items = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError):
        return ""
    if not isinstance(items, list):
        return ""
    urls: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("link") or item.get("uri") or "").strip()
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return "; ".join(urls)


def runs_to_csv(rows: list[dict]) -> str:
    output = io.StringIO()
    fieldnames = [label for label, _ in RUN_EXPORT_COLUMNS]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({label: run_export_value(row, key) for label, key in RUN_EXPORT_COLUMNS})
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


def rows_to_excel_html(title: str, headers: list[str], rows: list[list[object]]) -> str:
    def render_cell(value: object) -> str:
        text = "" if value is None else str(value)
        compact = " ".join(text.splitlines()).replace("\t", " ")
        return f"<td>{html.escape(compact)}</td>"

    header_html = "".join(f"<th>{html.escape(item)}</th>" for item in headers)
    body_html = "".join(
        "<tr>" + "".join(render_cell(cell) for cell in row) + "</tr>"
        for row in rows
    )
    return f"""<!doctype html>
<html xmlns:o="urn:schemas-microsoft-com:office:office"
      xmlns:x="urn:schemas-microsoft-com:office:excel"
      xmlns="http://www.w3.org/TR/REC-html40">
  <head>
    <meta charset="utf-8" />
    <title>{html.escape(title)}</title>
    <style>
      table {{
        border-collapse: collapse;
      }}
      th, td {{
        white-space: nowrap;
        vertical-align: top;
        font-size: 12px;
        padding: 4px 6px;
      }}
    </style>
  </head>
  <body>
    <table border="1">
      <thead><tr>{header_html}</tr></thead>
      <tbody>{body_html}</tbody>
    </table>
  </body>
</html>"""


def runs_to_excel_html(rows: list[dict]) -> str:
    headers = [label for label, _ in RUN_EXPORT_COLUMNS]
    body = [[run_export_value(row, key) for _, key in RUN_EXPORT_COLUMNS] for row in rows]
    return rows_to_excel_html("运行明细", headers, body)


def analytics_to_excel_html(data: dict) -> str:
    headers = ["指标", "值"]
    rows = [
        ["总运行", data.get("total_runs", 0)],
        ["成功运行", data.get("success_runs", 0)],
        ["品牌命中率", f'{data.get("brand_mention_rate", 0)}%'],
        ["官网引用率", f'{data.get("owned_citation_rate", 0)}%'],
        ["", ""],
        ["服务商 / 模式", "运行 / 命中 / 官网引用"],
    ]
    for provider, item in data.get("providers", {}).items():
        rows.append([
            provider,
            f'运行 {item.get("total", 0)} / 命中率 {item.get("mention_rate", 0)}% / 官网引用率 {item.get("owned_citation_rate", 0)}%',
        ])
    rows.append(["", ""])
    rows.append(["竞品", "出现次数"])
    for item in data.get("competitors", []):
        rows.append([item.get("name", ""), item.get("count", 0)])
    return rows_to_excel_html("摘要指标", headers, rows)
