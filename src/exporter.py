from __future__ import annotations

import csv
import io
import html


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
    headers = [
        "运行ID",
        "批次ID",
        "问题",
        "问题类型",
        "服务商",
        "模型",
        "联网搜索",
        "搜索策略",
        "思考模式",
        "推理强度",
        "思考预算",
        "重复次数",
        "生成时间",
        "状态",
        "品牌命中",
        "推荐强度",
        "竞品共现",
        "官网引用",
        "第三方引用",
        "风险等级",
        "回答摘要",
        "错误信息",
    ]
    body = []
    for row in rows:
        body.append([
            row.get("run_id", ""),
            row.get("batch_id", ""),
            row.get("question", ""),
            row.get("question_type", ""),
            row.get("provider", ""),
            row.get("model", ""),
            "是" if row.get("search_enabled") else "否",
            row.get("search_mode", ""),
            row.get("thinking_type", ""),
            row.get("reasoning_effort", ""),
            row.get("thinking_budget", ""),
            row.get("repeat_index", ""),
            row.get("requested_at", ""),
            row.get("status", ""),
            "是" if row.get("target_brand_mentioned") else "否",
            row.get("recommendation_strength", ""),
            row.get("competitors_mentioned", ""),
            "是" if row.get("owned_site_cited") else "否",
            "是" if row.get("third_party_cited") else "否",
            row.get("risk_level", ""),
            row.get("response_text", ""),
            row.get("error_message", ""),
        ])
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
