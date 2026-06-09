from __future__ import annotations

import json
import re
from urllib.parse import urlparse


def split_names(value: str) -> list[str]:
    return [x.strip() for x in re.split(r"[;；,，、\n]", value or "") if x.strip()]


def find_rank(answer: str, brand: str) -> int | None:
    if not brand or brand not in answer:
        return None
    lines = [line.strip() for line in answer.splitlines() if line.strip()]
    for idx, line in enumerate(lines, start=1):
        if brand in line:
            numbered = re.match(r"^\s*(\d+)[\.、)]", line)
            if numbered:
                return int(numbered.group(1))
            return idx
    return None


def citation_domains(citations: list[dict]) -> list[str]:
    domains = []
    for item in citations:
        url = item.get("url") if isinstance(item, dict) else str(item)
        if not url:
            continue
        domain = urlparse(url).netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        if domain:
            domains.append(domain)
    return domains


def evaluate_answer(
    run_id: str,
    answer: str,
    target_brand: str,
    competitors: str,
    website_domain: str,
    citations: list[dict] | str,
) -> dict:
    if isinstance(citations, str):
        try:
            citations = json.loads(citations)
        except json.JSONDecodeError:
            citations = []

    brand_mentioned = bool(target_brand and target_brand in answer)
    competitor_hits = [name for name in split_names(competitors) if name and name in answer]
    domains = citation_domains(citations)
    owned_domain = (website_domain or "").lower().replace("https://", "").replace("http://", "").strip("/")
    if owned_domain.startswith("www."):
        owned_domain = owned_domain[4:]
    owned_site_cited = bool(owned_domain and any(owned_domain in domain for domain in domains))
    third_party_cited = bool(domains and not owned_site_cited)

    if brand_mentioned and re.search(r"推荐|优先|值得|适合|可以考虑", answer):
        strength = "强推荐"
    elif brand_mentioned:
        strength = "一般提及"
    else:
        strength = "未提及"

    negative_words = ["风险", "不足", "缺少", "不明确", "负面", "投诉", "谨慎"]
    sentiment = "负向" if any(word in answer for word in negative_words) else "中性"
    risk_level = "中" if competitor_hits and not brand_mentioned else "低"
    if sentiment == "负向" and brand_mentioned:
        risk_level = "高"

    return {
        "run_id": run_id,
        "target_brand_mentioned": brand_mentioned,
        "target_brand_rank": find_rank(answer, target_brand),
        "recommendation_strength": strength,
        "sentiment": sentiment,
        "competitors_mentioned": ";".join(competitor_hits),
        "owned_site_cited": owned_site_cited,
        "third_party_cited": third_party_cited,
        "factual_errors": "",
        "risk_level": risk_level,
        "evaluator": "rule",
        "evaluation_notes": "规则评估：品牌命中、竞品共现、引用域名和风险词。",
    }
