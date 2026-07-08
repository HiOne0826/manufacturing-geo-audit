from __future__ import annotations

import json
import os
import re
import hashlib
import time
import urllib.error
import urllib.parse
import urllib.request
from copy import deepcopy
from typing import Any

from src.runtime_env import provider_has_credentials, resolve_baidu_ak_sk, resolve_brave_search_api_key, resolve_provider_api_key


class AdapterError(RuntimeError):
    pass


OPENAI_REASONING_LEVELS = "none;minimal;low;medium;high;xhigh"
BRAVE_AUGMENTED_PROVIDERS = {"deepseek", "qwen", "ernie"}

PROVIDER_SAMPLING_DEFAULTS: dict[str, dict[str, Any]] = {
    "openai": {
        "temperature": 1,
        "reasoning_effort": "medium",
        "defaults_note": "OpenAI Responses API：temperature 默认 1；reasoning.effort 未指定时按 medium 处理。",
    },
    "gemini": {
        "temperature": 1,
        "thinking_budget": 0,
        "defaults_note": "Gemini：temperature 默认 1；thinkingBudget=0 用于关闭思考，留空可走模型动态默认。",
    },
    "doubao": {
        "temperature": 1,
        "reasoning_effort": "",
        "defaults_note": "豆包：普通对话按 temperature=1；联网搜索走 Responses API，思考默认关闭。",
    },
    "deepseek": {
        "temperature": 1,
        "reasoning_effort": "",
        "defaults_note": "DeepSeek 标准 API 无原生联网搜索；联网口径使用 Brave Search 外部检索增强。",
    },
    "qwen": {
        "temperature": 0.1,
        "reasoning_effort": "",
        "search_strategy": "turbo",
        "defaults_note": "通义千问：事实问答推荐 temperature=0.1；联网搜索 search_strategy 预填 turbo。",
    },
    "hunyuan": {
        "temperature": 0,
        "reasoning_effort": "",
        "search_mode": "force",
        "defaults_note": "腾讯元宝：确定性审计默认 temperature=0；联网搜索默认强制启用搜索增强并返回 search_info/citation。",
    },
    "kimi": {
        "temperature": 0.6,
        "reasoning_effort": "",
        "defaults_note": "Kimi K2.5 当前 API 仅接受 temperature=0.6；联网搜索需关闭深度思考。",
    },
    "ernie": {
        "temperature": 0.1,
        "reasoning_effort": "",
        "defaults_note": "文心/千帆：事实问答默认 temperature=0.1；思考默认关闭。",
    },
    "minimax": {
        "temperature": 0.1,
        "reasoning_effort": "",
        "defaults_note": "MiniMax：事实问答默认 temperature=0.1；思考默认关闭。",
    },
    "openrouter_gpt": {
        "temperature": 1,
        "reasoning_effort": "",
        "search_strategy": "exa",
        "defaults_note": "OpenRouter-GPT：联网搜索默认使用 OpenRouter web plugin 的 Exa 引擎，提升引用链接稳定性。",
    },
    "openrouter_gemini": {
        "temperature": 1,
        "reasoning_effort": "",
        "search_strategy": "exa",
        "defaults_note": "OpenRouter-Gemini：联网搜索默认使用 OpenRouter web plugin 的 Exa 引擎，提升引用链接稳定性。",
    },
}


def provider_sampling_defaults(provider: str, model: str = "") -> dict[str, Any]:
    defaults = dict(PROVIDER_SAMPLING_DEFAULTS.get(provider, {}))
    if provider == "kimi" and str(model).startswith("kimi-k2.5"):
        defaults["temperature"] = 0.6
    return defaults


PROVIDER_PRESETS = {
    "mock": {
        "label": "Mock / 流程演示",
        "provider": "mock",
        "api_family": "Local Mock",
        "model": "mock-model",
        "model_version": "",
        "model_type": "mock",
        "api_base": "",
        "supports_pure": True,
        "supports_search": True,
        "web_search_mode": "本地模拟",
        "web_search_param_path": "",
        "supports_reasoning": False,
        "reasoning_param_path": "",
        "reasoning_levels": "",
        "supports_citation": True,
        "citation_param_path": "mock citations",
        "supports_site_filter": False,
        "supports_time_filter": False,
        "supports_user_location": False,
        "supports_tool_calling": False,
        "notes": "本地测试和流程演示使用，不调用真实模型。",
    },
    "openai": {
        "label": "GPT",
        "provider": "openai",
        "api_family": "OpenAI Responses API",
        "model": "gpt-5.5",
        "model_version": "",
        "model_type": "chat",
        "api_base": "https://api.openai.com/v1",
        "supports_pure": True,
        "supports_search": True,
        "web_search_mode": "Responses API hosted web_search 工具",
        "web_search_param_path": "tools[].type=web_search; tools[].search_context_size; tools[].user_location",
        "supports_reasoning": True,
        "reasoning_param_path": "reasoning.effort",
        "reasoning_levels": OPENAI_REASONING_LEVELS,
        "supports_citation": True,
        "citation_param_path": "output[].content[].annotations[type=url_citation]; include[]=web_search_call.action.sources",
        "supports_site_filter": False,
        "supports_time_filter": False,
        "supports_user_location": True,
        "supports_tool_calling": True,
        "notes": "OpenAI Responses API；联网搜索使用 hosted web_search，引用从 url_citation annotations 与 web_search_call sources 提取。",
    },
    "gemini": {
        "label": "Gemini",
        "provider": "gemini",
        "api_family": "Gemini API",
        "model": "gemini-2.5-flash",
        "model_version": "",
        "model_type": "chat",
        "api_base": "https://generativelanguage.googleapis.com/v1beta",
        "supports_pure": True,
        "supports_search": True,
        "web_search_mode": "google_search 工具",
        "web_search_param_path": "tools[].google_search",
        "supports_reasoning": True,
        "reasoning_param_path": "generationConfig.thinkingConfig",
        "reasoning_levels": "budget:0/-1/1024+",
        "supports_citation": True,
        "citation_param_path": "groundingMetadata",
        "supports_site_filter": False,
        "supports_time_filter": False,
        "supports_user_location": False,
        "supports_tool_calling": True,
        "notes": "Google Gemini generateContent 接口。",
    },
    "doubao": {
        "label": "豆包",
        "provider": "doubao",
        "api_family": "火山方舟 ARK Responses API",
        "model": "doubao-seed-2-0-mini-260428",
        "model_version": "",
        "model_type": "chat",
        "api_base": "https://ark.cn-beijing.volces.com/api/v3",
        "supports_pure": True,
        "supports_search": True,
        "web_search_mode": "Responses API 内置 web_search 工具",
        "web_search_param_path": "tools[].type=web_search; tools[].user_location; tools[].sources; tools[].limit; tools[].max_keyword",
        "supports_reasoning": True,
        "reasoning_param_path": "reasoning.effort",
        "reasoning_levels": "none;minimal;low;medium;high",
        "supports_citation": True,
        "citation_param_path": "output[].content[].annotations / citations",
        "supports_site_filter": False,
        "supports_time_filter": False,
        "supports_user_location": True,
        "supports_tool_calling": True,
        "notes": "联网搜索走 /responses，普通对话可走 /chat/completions；web_search 支持 user_location、sources、limit、max_keyword，引用从 annotations/citations 提取。",
    },
    "deepseek": {
        "label": "DeepSeek",
        "provider": "deepseek",
        "api_family": "DeepSeek API",
        "model": "deepseek-chat",
        "model_version": "",
        "model_type": "chat",
        "api_base": "https://api.deepseek.com/v1",
        "supports_pure": True,
        "supports_search": True,
        "web_search_mode": "Brave Search 外部检索增强",
        "web_search_param_path": "BRAVE_SEARCH_API_KEY; /res/v1/web/search; q/count/country/search_lang/freshness/safesearch",
        "supports_reasoning": True,
        "reasoning_param_path": "thinking.type / reasoning_effort",
        "reasoning_levels": "disabled;enabled + low/medium/high",
        "supports_citation": True,
        "citation_param_path": "Brave Search web.results[].url/title/description",
        "supports_site_filter": False,
        "supports_time_filter": False,
        "supports_user_location": False,
        "supports_tool_calling": True,
        "notes": "DeepSeek OpenAI 兼容接口；标准 API 不提供原生联网搜索，本系统联网口径为 Brave Search 外部检索结果 + DeepSeek 生成。",
    },
    "openrouter_gpt": {
        "label": "OpenRouter-GPT",
        "provider": "openrouter_gpt",
        "api_family": "OpenRouter Chat Completions",
        "model": "openai/gpt-5.2",
        "model_version": "",
        "model_type": "chat",
        "api_base": "https://openrouter.ai/api/v1",
        "supports_pure": True,
        "supports_search": True,
        "web_search_mode": "OpenRouter web plugin / :online",
        "web_search_param_path": "plugins[].id=web; plugins[].max_results; plugins[].engine",
        "supports_reasoning": True,
        "reasoning_param_path": "reasoning.effort",
        "reasoning_levels": OPENAI_REASONING_LEVELS,
        "supports_citation": True,
        "citation_param_path": "choices[].message.annotations[type=url_citation].url_citation",
        "supports_site_filter": True,
        "supports_time_filter": False,
        "supports_user_location": False,
        "supports_tool_calling": True,
        "notes": "OpenRouter 中转 GPT 联网口径；联网使用 web plugin，默认搜索引擎为 Exa。不是 OpenAI 官方直连接口。",
    },
    "openrouter_gemini": {
        "label": "OpenRouter-Gemini",
        "provider": "openrouter_gemini",
        "api_family": "OpenRouter Chat Completions",
        "model": "google/gemini-2.5-flash",
        "model_version": "",
        "model_type": "chat",
        "api_base": "https://openrouter.ai/api/v1",
        "supports_pure": True,
        "supports_search": True,
        "web_search_mode": "OpenRouter web plugin / :online",
        "web_search_param_path": "plugins[].id=web; plugins[].max_results; plugins[].engine",
        "supports_reasoning": True,
        "reasoning_param_path": "reasoning.effort",
        "reasoning_levels": "按 OpenRouter/Google 路由能力",
        "supports_citation": True,
        "citation_param_path": "choices[].message.annotations[type=url_citation].url_citation",
        "supports_site_filter": True,
        "supports_time_filter": False,
        "supports_user_location": False,
        "supports_tool_calling": True,
        "notes": "OpenRouter 中转 Gemini 联网口径；联网使用 web plugin，默认搜索引擎为 Exa。不是 Google Gemini 官方直连接口。",
    },
    "qwen": {
        "label": "通义千问",
        "provider": "qwen",
        "api_family": "阿里云百炼 / DashScope",
        "model": "qwen-plus",
        "model_version": "",
        "model_type": "chat",
        "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "supports_pure": True,
        "supports_search": True,
        "web_search_mode": "enable_search + search_options",
        "web_search_param_path": "enable_search; search_options.forced_search/search_strategy/freshness/assigned_site_list/intention_options.prompt_intervene/enable_source/enable_citation/citation_format",
        "supports_reasoning": True,
        "reasoning_param_path": "reasoning.effort / enable_thinking / thinking_budget",
        "reasoning_levels": OPENAI_REASONING_LEVELS,
        "supports_citation": True,
        "citation_param_path": "search_info.search_results / citations",
        "supports_site_filter": False,
        "supports_time_filter": False,
        "supports_user_location": False,
        "supports_tool_calling": True,
        "notes": "联网搜索按阿里云百炼文档走 enable_search=true，可配 search_options；引用优先从 search_info.search_results 提取。",
    },
    "hunyuan": {
        "label": "腾讯元宝",
        "provider": "hunyuan",
        "api_family": "腾讯混元 / TokenHub",
        "model": "hunyuan-turbos-latest",
        "model_version": "",
        "model_type": "chat",
        "api_base": "https://api.hunyuan.cloud.tencent.com/v1",
        "supports_pure": True,
        "supports_search": True,
        "web_search_mode": "搜索增强 / 强制搜索增强",
        "web_search_param_path": "enable_enhancement=true; force_search_enhancement=true; search_info=true; citation=true",
        "supports_reasoning": True,
        "reasoning_param_path": "按模型能力控制，当前预置仅记录能力",
        "reasoning_levels": "按模型能力",
        "supports_citation": True,
        "citation_param_path": "citation / search_info",
        "supports_site_filter": True,
        "supports_time_filter": True,
        "supports_user_location": False,
        "supports_tool_calling": True,
        "notes": "腾讯元宝数据源基于腾讯混元 OpenAI 兼容接口；联网默认强制启用搜索增强，并要求返回 search_info 与 citation。",
    },
    "kimi": {
        "label": "Kimi",
        "provider": "kimi",
        "api_family": "Moonshot / Kimi API",
        "model": "kimi-k2.5",
        "model_version": "",
        "model_type": "chat",
        "api_base": "https://api.moonshot.cn/v1",
        "supports_pure": True,
        "supports_search": True,
        "web_search_mode": "官方 builtin_function.$web_search 工具",
        "web_search_param_path": "tools[].type=builtin_function; tools[].function.name=$web_search; thinking.type=disabled",
        "supports_reasoning": True,
        "reasoning_param_path": "thinking.type",
        "reasoning_levels": "disabled;enabled",
        "supports_citation": True,
        "citation_param_path": "tool_calls.arguments / citations",
        "supports_site_filter": False,
        "supports_time_filter": False,
        "supports_user_location": False,
        "supports_tool_calling": True,
        "notes": "联网搜索按官方文档走 builtin_function.$web_search 的 tool_calls 闭环；开启联网搜索时必须关闭深度思考。",
    },
    "ernie": {
        "label": "文心一言",
        "provider": "ernie",
        "api_family": "百度千帆 / ERNIE API",
        "model": "ernie-4.5-turbo-32k",
        "model_version": "",
        "model_type": "chat",
        "api_base": "https://qianfan.baidubce.com/v2",
        "supports_pure": True,
        "supports_search": True,
        "web_search_mode": "enable_search 联网搜索",
        "web_search_param_path": "enable_search",
        "supports_reasoning": True,
        "reasoning_param_path": "enable_thinking / thinking_budget / reasoning_effort",
        "reasoning_levels": "low;medium;high",
        "supports_citation": True,
        "citation_param_path": "citations / references",
        "supports_site_filter": False,
        "supports_time_filter": False,
        "supports_user_location": True,
        "supports_tool_calling": True,
        "notes": "百度千帆 / ERNIE API；支持 Bearer API Key，也兼容通过 AK/SK 先换 Access Token 后调用。",
    },
    "minimax": {
        "label": "MiniMax",
        "provider": "minimax",
        "api_family": "MiniMax OpenAI 兼容接口",
        "model": "MiniMax-M1",
        "model_version": "",
        "model_type": "chat",
        "api_base": "https://api.minimaxi.com/v1",
        "supports_pure": True,
        "supports_search": False,
        "web_search_mode": "",
        "web_search_param_path": "",
        "supports_reasoning": False,
        "reasoning_param_path": "",
        "reasoning_levels": "",
        "supports_citation": False,
        "citation_param_path": "",
        "supports_site_filter": False,
        "supports_time_filter": False,
        "supports_user_location": False,
        "supports_tool_calling": True,
        "notes": "MiniMax OpenAI 兼容 Chat Completions。",
    },
}


def post_json(url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    timeout = int(os.environ.get("SAMPLING_REQUEST_TIMEOUT", "90") or 90)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise AdapterError(f"HTTP {exc.code}: {body[:1200]}") from exc
    except Exception as exc:
        raise AdapterError(str(exc)) from exc


def get_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
    timeout = int(os.environ.get("SAMPLING_REQUEST_TIMEOUT", "90") or 90)
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise AdapterError(f"HTTP {exc.code}: {body[:1200]}") from exc
    except Exception as exc:
        raise AdapterError(str(exc)) from exc


def normalize_base(base: str) -> str:
    return (base or "").rstrip("/")


def mask_key(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}***{value[-4:]}"


def normalize_choice_text(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content") or choices[0].get("text") or ""
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") in {"text", "output_text"}:
                parts.append(part.get("text", ""))
        return "\n".join(item for item in parts if item)
    return content


def normalize_responses_text(data: dict[str, Any]) -> str:
    if isinstance(data.get("output_text"), str) and data["output_text"]:
        return data["output_text"]
    parts: list[str] = []
    for item in data.get("output") or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if content.get("type") == "output_text":
                parts.append(content.get("text", ""))
    return "\n".join(item for item in parts if item)


def extract_openai_response_citations(data: dict[str, Any]) -> list[dict[str, str]]:
    citations: list[dict[str, str]] = []
    for item in data.get("output") or []:
        if item.get("type") == "web_search_call":
            action = item.get("action") or {}
            for source in action.get("sources") or []:
                citations.append({"url": source.get("url", ""), "title": source.get("title", "")})
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            for annotation in content.get("annotations") or []:
                if annotation.get("type") == "url_citation":
                    citations.append(
                        {
                            "url": annotation.get("url", ""),
                            "title": annotation.get("title", ""),
                        }
                    )
    return dedupe_citations(citations)


def extract_generic_citations(data: dict[str, Any]) -> list[dict[str, str]]:
    citations: list[dict[str, str]] = []
    for item in data.get("citations") or data.get("search_results") or []:
        if isinstance(item, str):
            citations.append({"url": item, "title": ""})
        elif isinstance(item, dict):
            citations.append({"url": item.get("url", ""), "title": item.get("title", "")})
    return citations


def extract_brave_results(data: dict[str, Any], limit: int = 5) -> list[dict[str, str]]:
    results = ((data.get("web") or {}).get("results") or [])[:limit]
    items: list[dict[str, str]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or "").strip()
        description = str(item.get("description") or item.get("snippet") or "").strip()
        extra = item.get("extra_snippets") or []
        snippets = [description] if description else []
        snippets.extend(str(value).strip() for value in extra if str(value).strip())
        if url or title or snippets:
            items.append(
                {
                    "url": url,
                    "title": title,
                    "description": "\n".join(snippets[:3]),
                }
            )
    return items


def brave_citations(results: list[dict[str, str]]) -> list[dict[str, str]]:
    return dedupe_citations(
        [{"url": item.get("url", ""), "title": item.get("title", "")} for item in results if item.get("url")]
    )


def extract_qwen_citations(data: dict[str, Any]) -> list[dict[str, str]]:
    citations = extract_generic_citations(data)
    search_info = data.get("search_info") or {}
    for item in search_info.get("search_results") or []:
        if isinstance(item, dict):
            citations.append({"url": item.get("url", ""), "title": item.get("title", "")})
    for choice in data.get("choices") or []:
        message = choice.get("message") or {}
        message_search_info = message.get("search_info") or {}
        for item in message_search_info.get("search_results") or []:
            if isinstance(item, dict):
                citations.append({"url": item.get("url", ""), "title": item.get("title", "")})
    return dedupe_citations(citations)


def extract_hunyuan_citations(data: dict[str, Any]) -> list[dict[str, str]]:
    citations = extract_generic_citations(data)
    search_info = data.get("search_info") or {}
    for item in search_info.get("search_results") or []:
        if isinstance(item, dict):
            citations.append({"url": item.get("url", ""), "title": item.get("title", "")})
    for choice in data.get("choices") or []:
        message = choice.get("message") or {}
        message_search_info = message.get("search_info") or {}
        for item in message_search_info.get("search_results") or []:
            if isinstance(item, dict):
                citations.append({"url": item.get("url", ""), "title": item.get("title", "")})
    return dedupe_citations(citations)


def extract_ernie_citations(data: dict[str, Any]) -> list[dict[str, str]]:
    citations = extract_generic_citations(data)
    web_search = data.get("web_search") or {}
    for item in web_search.get("references") or []:
        if isinstance(item, dict):
            citations.append({"url": item.get("url", ""), "title": item.get("title", "")})
    for choice in data.get("choices") or []:
        message = choice.get("message") or {}
        for item in (message.get("references") or message.get("citations") or []):
            if isinstance(item, dict):
                citations.append({"url": item.get("url", ""), "title": item.get("title", "")})
    return dedupe_citations(citations)


def extract_gemini_citations(data: dict[str, Any]) -> list[dict[str, str]]:
    citations: list[dict[str, str]] = []
    for candidate in data.get("candidates") or []:
        grounding = candidate.get("groundingMetadata") or {}
        for chunk in grounding.get("groundingChunks") or []:
            web = chunk.get("web") or {}
            citations.append({"url": web.get("uri", ""), "title": web.get("title", "")})
    return dedupe_citations(citations)


def extract_openrouter_citations(data: dict[str, Any]) -> list[dict[str, str]]:
    citations: list[dict[str, str]] = []
    for choice in data.get("choices") or []:
        message = choice.get("message") or {}
        for annotation in message.get("annotations") or []:
            if annotation.get("type") != "url_citation":
                continue
            payload = annotation.get("url_citation") or {}
            citations.append(
                {
                    "url": payload.get("url", ""),
                    "title": payload.get("title", ""),
                }
            )
    return dedupe_citations(citations)


def dedupe_citations(items: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, str]] = []
    for item in items:
        key = (item.get("url", ""), item.get("title", ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def extract_urls_from_payload(payload: Any) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            url = str(node.get("url") or node.get("link") or "").strip()
            title = str(node.get("title") or node.get("name") or "").strip()
            if url:
                items.append({"url": url, "title": title})
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(payload)
    return dedupe_citations(items)


def normalize_run_options(options: dict[str, Any] | None) -> dict[str, Any]:
    data = dict(options or {})
    search_enabled = bool(data.get("search_enabled", False))
    search_mode = str(data.get("search_mode", "auto")).strip() or "auto"
    if search_mode == "off":
        search_enabled = False
    thinking_type = str(data.get("thinking_type", "disabled")).strip() or "disabled"
    reasoning_effort = str(data.get("reasoning_effort", "")).strip()
    raw_budget = data.get("thinking_budget")
    thinking_budget = None
    if raw_budget not in (None, "", "null"):
        try:
            thinking_budget = int(raw_budget)
        except (TypeError, ValueError):
            thinking_budget = None
    search_limit = None
    raw_search_limit = data.get("search_limit")
    if raw_search_limit not in (None, "", "null"):
        try:
            search_limit = int(raw_search_limit)
        except (TypeError, ValueError):
            search_limit = None
    search_max_keyword = None
    raw_search_max_keyword = data.get("search_max_keyword")
    if raw_search_max_keyword not in (None, "", "null"):
        try:
            search_max_keyword = int(raw_search_max_keyword)
        except (TypeError, ValueError):
            search_max_keyword = None
    return {
        "search_enabled": search_enabled,
        "search_mode": search_mode if search_enabled else "off",
        "thinking_type": thinking_type,
        "reasoning_effort": reasoning_effort,
        "thinking_budget": thinking_budget,
        "runtime_model": str(data.get("runtime_model", "")).strip(),
        "runtime_model_version": str(data.get("runtime_model_version", "")).strip(),
        "search_sources": str(data.get("search_sources", "")).strip(),
        "search_limit": search_limit,
        "search_max_keyword": search_max_keyword,
        "search_user_location": str(data.get("search_user_location", "")).strip(),
        "search_site_filter": str(data.get("search_site_filter", "")).strip(),
        "search_time_filter": str(data.get("search_time_filter", "")).strip(),
        "search_strategy": str(data.get("search_strategy", "")).strip(),
        "search_freshness": str(data.get("search_freshness", "")).strip(),
        "search_prompt_intervene": str(data.get("search_prompt_intervene", "")).strip(),
        "search_enable_source": bool(data.get("search_enable_source", False)),
        "search_enable_citation": bool(data.get("search_enable_citation", False)),
        "search_citation_format": str(data.get("search_citation_format", "")).strip(),
    }


def resolve_provider_runtime_config(provider: str, api_key: str, api_base: str, model_name: str) -> tuple[str, str]:
    return api_base, model_name


def resolve_openrouter_direct_fallback(provider: str) -> tuple[str, str, str, str] | None:
    if os.getenv("OPENROUTER_DIRECT_FALLBACK") != "1":
        return None
    if provider == "openrouter_gpt":
        direct_key = resolve_provider_api_key("openai")
        if direct_key:
            return (
                "openai",
                direct_key,
                os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                os.getenv("OPENROUTER_GPT_FALLBACK_MODEL", os.getenv("OPENAI_CHATGPT_MODEL", "gpt-4.1-mini")),
            )
    if provider == "openrouter_gemini":
        direct_key = resolve_provider_api_key("gemini")
        if direct_key:
            return (
                "gemini",
                direct_key,
                os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"),
                os.getenv("OPENROUTER_GEMINI_FALLBACK_MODEL", os.getenv("GEMINI_MODEL", "gemini-2.5-flash")),
            )
    return None


def normalize_kimi_temperature(model: str, temperature: float) -> float:
    if str(model).startswith("kimi-k2.5"):
        return 0.6
    return temperature


def extract_required_temperature(error_text: str) -> float | None:
    match = re.search(r"only ([0-9.]+) is allowed", error_text or "")
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def parse_openai_user_location(value: str) -> dict[str, str]:
    parts = [part.strip() for part in value.split(",") if part.strip()]
    location: dict[str, str] = {"type": "approximate"}
    if not parts:
        return location
    location["city"] = parts[0]
    if len(parts) >= 2:
        if len(parts[1]) == 2 and parts[1].isalpha():
            location["country"] = parts[1].upper()
        else:
            location["region"] = parts[1]
    if len(parts) >= 3:
        if "country" in location and "/" in parts[2]:
            location["timezone"] = parts[2]
        else:
            location["country"] = parts[2].upper() if len(parts[2]) == 2 else parts[2]
    if len(parts) >= 4:
        location["timezone"] = parts[3]
    return location


def build_openai_web_search_tool(options: dict[str, Any]) -> dict[str, Any]:
    tool: dict[str, Any] = {"type": "web_search"}
    if options["search_strategy"] in {"low", "medium", "high"}:
        tool["search_context_size"] = options["search_strategy"]
    if options["search_user_location"]:
        tool["user_location"] = parse_openai_user_location(options["search_user_location"])
    return tool


def build_openai_responses_payload(model: str, question: str, options: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "input": question,
        "instructions": "你是制造业品牌 GEO 审计助手。请基于公开信息客观回答。",
    }
    if openai_model_supports_reasoning(model):
        reasoning_effort = options["reasoning_effort"]
        if not reasoning_effort:
            reasoning_effort = "none" if options["thinking_type"] == "disabled" else "medium"
        payload["reasoning"] = {"effort": reasoning_effort}
    if options["search_enabled"]:
        payload["tools"] = [build_openai_web_search_tool(options)]
        payload["include"] = ["web_search_call.action.sources"]
    return payload


def openai_model_supports_reasoning(model: str) -> bool:
    normalized = str(model or "").lower()
    return normalized.startswith(("gpt-5", "o1", "o3", "o4"))


def build_openai_chat_payload(model: str, question: str, temperature: float) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是制造业品牌 GEO 审计助手。请基于公开信息客观回答。"},
            {"role": "user", "content": question},
        ],
        "temperature": temperature,
    }


def brave_country(value: str) -> str:
    parts = [part.strip() for part in str(value or "").split(",") if part.strip()]
    for part in parts:
        if len(part) == 2 and part.isalpha():
            return part.upper()
    return "CN"


def brave_search_request(question: str, options: dict[str, Any]) -> dict[str, Any]:
    api_key = resolve_brave_search_api_key()
    if not api_key:
        raise AdapterError("DeepSeek 联网口径需要 BRAVE_SEARCH_API_KEY。")
    count = options.get("search_limit") or 5
    try:
        count = max(1, min(int(count), 10))
    except (TypeError, ValueError):
        count = 5
    params = {
        "q": question,
        "count": str(count),
        "country": brave_country(options.get("search_user_location", "")),
        "search_lang": "zh-hans",
        "ui_lang": "zh-CN",
        "safesearch": "moderate",
        "extra_snippets": "true",
    }
    if options.get("search_freshness"):
        params["freshness"] = str(options["search_freshness"])
    url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode(params)
    data = get_json(
        url,
        {
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "X-Subscription-Token": api_key,
        },
    )
    results = extract_brave_results(data, limit=count)
    if not results:
        raise AdapterError("Brave Search 未返回可用网页结果，无法执行联网搜索口径。")
    return {"raw_response": data, "results": results}


def build_brave_augmented_question(question: str, results: list[dict[str, str]]) -> str:
    source_blocks = []
    for idx, item in enumerate(results, start=1):
        source_blocks.append(
            "\n".join(
                [
                    f"[{idx}] 标题: {item.get('title') or '-'}",
                    f"URL: {item.get('url') or '-'}",
                    f"摘要: {item.get('description') or '-'}",
                ]
            )
        )
    return (
        "你是制造业品牌 GEO 审计助手。\n"
        "以下是 Brave Search 返回的公开网页检索结果。请只基于这些资料回答用户问题。\n"
        "如果资料不足，请明确说明“检索结果不足以判断”。\n\n"
        "要求：\n"
        "1. 客观回答，不要编造未出现在资料中的事实。\n"
        "2. 涉及品牌、厂家、产品能力时尽量引用来源编号，例如 [1]。\n"
        "3. 不要声称你自己进行了联网搜索；信息来源是下方 Brave Search 结果。\n\n"
        f"用户问题：\n{question}\n\n"
        "Brave Search 结果：\n"
        + "\n\n".join(source_blocks)
    )


def build_doubao_chat_payload(
    model: str,
    question: str,
    temperature: float,
    options: dict[str, Any],
) -> dict[str, Any]:
    payload = build_openai_chat_payload(model, question, temperature)
    if options["thinking_type"] in {"enabled", "auto", "disabled"}:
        payload["thinking"] = {"type": options["thinking_type"]}
    if options["reasoning_effort"]:
        payload["reasoning"] = {"effort": options["reasoning_effort"]}
    return payload


def build_deepseek_chat_payload(
    model: str,
    question: str,
    temperature: float,
    options: dict[str, Any],
) -> dict[str, Any]:
    payload = build_openai_chat_payload(model, question, temperature)
    if options["thinking_type"] == "enabled":
        payload["thinking"] = {"type": "enabled"}
    elif options["thinking_type"] == "disabled":
        payload["thinking"] = {"type": "disabled"}
    if options["reasoning_effort"]:
        payload["reasoning_effort"] = options["reasoning_effort"]
    return payload


def build_qwen_chat_payload(
    model: str,
    question: str,
    temperature: float,
    options: dict[str, Any],
) -> dict[str, Any]:
    payload = build_openai_chat_payload(model, question, temperature)
    if options["search_enabled"]:
        payload["enable_search"] = True
        search_options: dict[str, Any] = {}
        if options["search_mode"] == "force":
            search_options["forced_search"] = True
        if options["search_strategy"]:
            search_options["search_strategy"] = options["search_strategy"]
        if options["search_freshness"]:
            try:
                search_options["freshness"] = int(options["search_freshness"])
            except ValueError:
                pass
        if options["search_site_filter"]:
            search_options["assigned_site_list"] = [
                item.strip() for item in options["search_site_filter"].split(",") if item.strip()
            ]
        if options["search_prompt_intervene"]:
            search_options["intention_options"] = {"prompt_intervene": options["search_prompt_intervene"]}
        if options["search_enable_source"]:
            search_options["enable_source"] = True
        if options["search_enable_citation"]:
            search_options["enable_citation"] = True
        if options["search_citation_format"]:
            search_options["citation_format"] = options["search_citation_format"]
        if search_options:
            payload["search_options"] = search_options
    if options["reasoning_effort"]:
        payload["reasoning"] = {"effort": options["reasoning_effort"]}
    elif options["thinking_type"] == "disabled":
        payload["enable_thinking"] = False
    elif options["thinking_type"] in {"enabled", "auto"}:
        payload["enable_thinking"] = True
    if options["thinking_budget"] is not None:
        payload["thinking_budget"] = options["thinking_budget"]
    return payload


def build_kimi_chat_payload(
    model: str,
    question: str,
    temperature: float,
    options: dict[str, Any],
) -> dict[str, Any]:
    payload = build_openai_chat_payload(model, question, normalize_kimi_temperature(model, temperature))
    if options["search_enabled"] and options["thinking_type"] != "disabled":
        raise AdapterError("Kimi 官方联网搜索要求关闭深度思考，请在采样页关闭深度思考后再试。")
    if options["thinking_type"] in {"enabled", "disabled"}:
        payload["thinking"] = {"type": options["thinking_type"]}
    return payload


def build_hunyuan_chat_payload(
    model: str,
    question: str,
    temperature: float,
    options: dict[str, Any],
) -> dict[str, Any]:
    payload = build_openai_chat_payload(model, question, temperature)
    if options["search_enabled"]:
        payload["enable_enhancement"] = True
        payload["search_info"] = True
        payload["citation"] = True
        payload["force_search_enhancement"] = options["search_mode"] == "force"
    return payload


def build_ernie_chat_payload(
    model: str,
    question: str,
    temperature: float,
    options: dict[str, Any],
) -> dict[str, Any]:
    payload = build_openai_chat_payload(model, question, temperature)
    if options["search_enabled"]:
        payload["enable_search"] = True
    if options["thinking_type"] in {"enabled", "auto"}:
        payload["enable_thinking"] = True
    elif options["thinking_type"] == "disabled":
        payload["enable_thinking"] = False
    if options["reasoning_effort"]:
        payload["reasoning_effort"] = options["reasoning_effort"]
    if options["thinking_budget"] is not None:
        payload["thinking_budget"] = options["thinking_budget"]
    return payload


def build_minimax_chat_payload(
    model: str,
    question: str,
    temperature: float,
    options: dict[str, Any],
) -> dict[str, Any]:
    return build_openai_chat_payload(model, question, temperature)


def build_openrouter_chat_payload(
    model: str,
    question: str,
    temperature: float,
    options: dict[str, Any],
) -> dict[str, Any]:
    payload = build_openai_chat_payload(model, question, temperature)
    payload["max_tokens"] = 512
    if options["search_enabled"]:
        plugin: dict[str, Any] = {"id": "web"}
        if options.get("search_limit"):
            plugin["max_results"] = max(1, min(int(options["search_limit"]), 10))
        else:
            plugin["max_results"] = 5
        if options.get("search_strategy") in {"native", "exa", "firecrawl", "parallel", "perplexity"}:
            plugin["engine"] = options["search_strategy"]
        if options.get("search_site_filter"):
            plugin["include_domains"] = [
                item.strip() for item in str(options["search_site_filter"]).split(",") if item.strip()
            ]
        payload["plugins"] = [plugin]
    if options["reasoning_effort"]:
        payload["reasoning"] = {"effort": options["reasoning_effort"]}
    return payload


def build_openai_compatible_payload(
    provider: str,
    model: str,
    question: str,
    temperature: float,
    options: dict[str, Any],
) -> dict[str, Any]:
    search_enabled = options["search_enabled"]
    if provider == "doubao":
        if search_enabled:
            raise AdapterError("豆包联网搜索已切换为 Responses API 路径，请走 doubao_search_request。")
        return build_doubao_chat_payload(model, question, temperature, options)
    if provider == "deepseek":
        return build_deepseek_chat_payload(model, question, temperature, options)
    if provider == "qwen":
        return build_qwen_chat_payload(model, question, temperature, options)
    if provider == "kimi":
        return build_kimi_chat_payload(model, question, temperature, options)
    if provider == "hunyuan":
        return build_hunyuan_chat_payload(model, question, temperature, options)
    if provider == "ernie":
        return build_ernie_chat_payload(model, question, temperature, options)
    if provider == "minimax":
        return build_minimax_chat_payload(model, question, temperature, options)
    if provider in {"openrouter_gpt", "openrouter_gemini"}:
        return build_openrouter_chat_payload(model, question, temperature, options)
    raise AdapterError(f"未定义的 OpenAI 兼容请求构造器：{provider}")


def openai_compatible_request(
    base: str,
    api_key: str,
    model: str,
    question: str,
    temperature: float,
    provider: str,
    options: dict[str, Any],
) -> dict[str, Any]:
    brave_payload: dict[str, Any] | None = None
    external_citations: list[dict[str, str]] = []
    request_question = question
    if provider in BRAVE_AUGMENTED_PROVIDERS and options["search_enabled"]:
        try:
            brave_payload = brave_search_request(question, options)
            brave_results = brave_payload["results"]
            external_citations = brave_citations(brave_results)
            request_question = build_brave_augmented_question(question, brave_results)
        except AdapterError as exc:
            label = {"deepseek": "DeepSeek", "qwen": "通义千问", "ernie": "文心一言"}.get(provider, provider)
            raise AdapterError(f"{label} 联网口径依赖 Brave Search 失败：{exc}") from exc
    payload = build_openai_compatible_payload(provider, model, request_question, temperature, options)
    data = post_json(
        f"{normalize_base(base)}/chat/completions",
        {"Authorization": f"Bearer {api_key}"},
        payload,
    )
    citations = dedupe_citations(extract_generic_citations(data))
    if provider == "qwen":
        citations = extract_qwen_citations(data)
    elif provider == "hunyuan":
        citations = extract_hunyuan_citations(data)
    elif provider == "ernie":
        citations = extract_ernie_citations(data)
    elif provider in {"openrouter_gpt", "openrouter_gemini"}:
        citations = extract_openrouter_citations(data)
    citations = dedupe_citations(external_citations + citations)
    raw_response: dict[str, Any] = data
    if brave_payload is not None:
        raw_response = {
            f"{provider}_response": data,
            "brave_search": brave_payload["raw_response"],
            "brave_results": brave_payload["results"],
        }
    return {
        "response_text": normalize_choice_text(data),
        "citations": citations,
        "usage": data.get("usage", {}),
        "raw_response": raw_response,
        "returned_model": data.get("model", model),
    }


def openai_responses_request(
    base: str,
    api_key: str,
    model: str,
    question: str,
    options: dict[str, Any],
) -> dict[str, Any]:
    payload = build_openai_responses_payload(model, question, options)
    try:
        data = post_json(
            f"{normalize_base(base)}/responses",
            {"Authorization": f"Bearer {api_key}"},
            payload,
        )
    except AdapterError as exc:
        if "ToolNotOpen" in str(exc):
            raise AdapterError("豆包账号当前未开通 Web Search 插件能力，请先在火山方舟控制台开通对应搜索插件后再测试联网搜索。") from exc
        raise
    return {
        "response_text": normalize_responses_text(data),
        "citations": extract_openai_response_citations(data),
        "usage": data.get("usage", {}),
        "raw_response": data,
        "returned_model": data.get("model", model),
    }


def doubao_search_request(
    base: str,
    api_key: str,
    model: str,
    question: str,
    options: dict[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": "你是制造业品牌 GEO 审计助手。请基于公开信息客观回答。"}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": question}],
            },
        ],
    }
    if options["thinking_type"] in {"enabled", "auto"}:
        payload["reasoning"] = {"effort": options["reasoning_effort"] or "medium"}
    tool: dict[str, Any] = {"type": "web_search"}
    if options["search_sources"]:
        tool["sources"] = [item.strip() for item in options["search_sources"].split(",") if item.strip()]
    if options["search_limit"] is not None:
        tool["limit"] = options["search_limit"]
    if options["search_max_keyword"]:
        tool["max_keyword"] = options["search_max_keyword"]
    if options["search_user_location"]:
        tool["user_location"] = {"type": "approximate", "city": options["search_user_location"]}
    payload["tools"] = [tool]
    try:
        data = post_json(
            f"{normalize_base(base)}/responses",
            {"Authorization": f"Bearer {api_key}"},
            payload,
        )
    except AdapterError as exc:
        if "ToolNotOpen" in str(exc):
            raise AdapterError("豆包账号当前未开通 Web Search 插件能力，请先在火山方舟控制台开通对应搜索插件后再测试联网搜索。") from exc
        raise
    response_text = normalize_responses_text(data)
    citations = extract_openai_response_citations(data)
    if not citations and (
        "搜索服务暂时无法使用" in response_text
        or "搜索服务当前无法正常响应" in response_text
        or "搜索服务当前无法正常获取信息" in response_text
        or "无法正常获取信息" in response_text
    ):
        raise AdapterError("豆包已触发联网搜索，但当前搜索服务未返回可用结果，请稍后重试或先关闭联网搜索。")
    return {
        "response_text": response_text,
        "citations": citations,
        "usage": data.get("usage", {}),
        "raw_response": data,
        "returned_model": data.get("model", model),
    }


def kimi_search_request(
    base: str,
    api_key: str,
    model: str,
    question: str,
    temperature: float,
    options: dict[str, Any],
) -> dict[str, Any]:
    if options["thinking_type"] != "disabled":
        raise AdapterError("Kimi 官方联网搜索要求关闭深度思考，请在采样页关闭深度思考后再试。")
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "你是制造业品牌 GEO 审计助手。请基于公开信息客观回答。"},
        {"role": "user", "content": question},
    ]
    tool_spec = {
        "type": "builtin_function",
        "function": {
            "name": "$web_search",
        },
    }
    citations: list[dict[str, str]] = []
    final_data: dict[str, Any] = {}
    final_text = ""
    forced_temperature = normalize_kimi_temperature(model, temperature)
    for _ in range(4):
        payload = {
            "model": model,
            "messages": deepcopy(messages),
            "tools": [tool_spec],
            "tool_choice": "auto",
            "temperature": forced_temperature,
            "thinking": {"type": "disabled"},
        }
        try:
            data = post_json(
                f"{normalize_base(base)}/chat/completions",
                {"Authorization": f"Bearer {api_key}"},
                payload,
            )
        except AdapterError as exc:
            required_temperature = extract_required_temperature(str(exc))
            if required_temperature is not None and required_temperature != forced_temperature:
                forced_temperature = required_temperature
                data = post_json(
                    f"{normalize_base(base)}/chat/completions",
                    {"Authorization": f"Bearer {api_key}"},
                    {**payload, "temperature": forced_temperature},
                )
            else:
                raise
        final_data = data
        choices = data.get("choices") or []
        if not choices:
            break
        message = choices[0].get("message") or {}
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            final_text = normalize_choice_text(data)
            break
        assistant_message = deepcopy(message)
        assistant_message["role"] = assistant_message.get("role", "assistant")
        messages.append(assistant_message)
        for call in tool_calls:
            function = call.get("function") or {}
            arguments_text = function.get("arguments") or "{}"
            try:
                arguments = json.loads(arguments_text)
            except json.JSONDecodeError:
                arguments = {"raw_arguments": arguments_text}
            citations.extend(extract_urls_from_payload(arguments))
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "name": function.get("name", "$web_search"),
                    "content": json.dumps(arguments, ensure_ascii=False),
                }
            )
    if not final_text:
        final_text = normalize_choice_text(final_data)
    return {
        "response_text": final_text,
        "citations": dedupe_citations(citations + extract_generic_citations(final_data)),
        "usage": final_data.get("usage", {}),
        "raw_response": final_data,
        "returned_model": final_data.get("model", model),
    }


def gemini_request(
    base: str,
    api_key: str,
    model: str,
    question: str,
    temperature: float,
    options: dict[str, Any],
) -> dict[str, Any]:
    endpoint = f"{normalize_base(base)}/models/{model}:generateContent"
    payload: dict[str, Any] = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": question}],
            }
        ],
        "generationConfig": {
            "temperature": temperature,
        },
    }
    if options["search_enabled"]:
        payload["tools"] = [{"google_search": {}}]
    if options["thinking_budget"] is not None:
        payload["generationConfig"]["thinkingConfig"] = {"thinkingBudget": options["thinking_budget"]}
    elif options["thinking_type"] == "disabled":
        payload["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}
    elif options["thinking_type"] in {"enabled", "auto"}:
        payload["generationConfig"]["thinkingConfig"] = {"thinkingBudget": -1}
    data = post_json(endpoint, {"x-goog-api-key": api_key}, payload)
    text = ""
    for candidate in data.get("candidates") or []:
        parts = ((candidate.get("content") or {}).get("parts") or [])
        texts = [part.get("text", "") for part in parts if isinstance(part, dict)]
        if texts:
            text = "\n".join(texts)
            break
    return {
        "response_text": text,
        "citations": extract_gemini_citations(data),
        "usage": data.get("usageMetadata", {}),
        "raw_response": data,
        "returned_model": data.get("modelVersion", model),
    }


def fetch_baidu_access_token(ak: str, sk: str) -> str:
    if not ak or not sk:
        return ""
    query = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": ak,
            "client_secret": sk,
        }
    )
    request = urllib.request.Request(f"https://aip.baidubce.com/oauth/2.0/token?{query}", method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise AdapterError(f"百度千帆 Access Token 获取失败：HTTP {exc.code}: {body[:600]}") from exc
    except Exception as exc:
        raise AdapterError(f"百度千帆 Access Token 获取失败：{exc}") from exc
    token = str(data.get("access_token") or "").strip()
    if not token:
        raise AdapterError(f"百度千帆 Access Token 获取失败：{json.dumps(data, ensure_ascii=False)[:600]}")
    return token


def call_configured_model(
    model_config: dict[str, Any],
    question: str,
    search_enabled: bool,
    temperature: float,
    run_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provider = model_config.get("provider", "")
    if provider == "mock":
        start = time.time()
        model_name = model_config.get("model", "mock-model") or "mock-model"
        if str(model_name).startswith("mock-fail"):
            raise AdapterError("Mock forced failure")
        try:
            failure_rate = float(os.environ.get("MOCK_FAILURE_RATE", "0") or 0)
        except ValueError:
            failure_rate = 0
        if failure_rate > 0:
            digest = hashlib.sha256(f"{model_name}:{question}".encode("utf-8")).hexdigest()
            score = int(digest[:8], 16) / 0xFFFFFFFF
            if score < min(max(failure_rate, 0), 1):
                raise AdapterError(f"Mock failure rate triggered: {failure_rate}")
        citations = [
            {
                "url": "https://example.com/mock-source",
                "title": "Mock citation source",
            }
        ] if search_enabled else []
        response_text = (
            f"Mock answer for: {question}\n"
            "目标品牌在本地模拟回答中被提及，用于验证采样、评估和导出流程。"
        )
        return {
            "provider": "mock",
            "model": model_name,
            "configured_model": model_name,
            "model_version": model_config.get("model_version", ""),
            "search_enabled": search_enabled,
            "search_mode": "auto" if search_enabled else "off",
            "thinking_type": "disabled",
            "reasoning_effort": "",
            "thinking_budget": None,
            "latency_ms": int((time.time() - start) * 1000),
            "response_text": response_text,
            "citations": citations,
            "usage": {"mock": True},
            "raw_response": {"mock": True, "question": question},
        }
    if os.environ.get("ALLOW_LIVE_MODEL_CALLS") != "1":
        raise AdapterError("真实模型调用默认关闭。需要调用真实模型时请设置 ALLOW_LIVE_MODEL_CALLS=1。")
    runtime_provider = provider
    fallback = resolve_openrouter_direct_fallback(provider)
    if fallback:
        runtime_provider, api_key, api_base, model_name = fallback
    else:
        api_key = resolve_provider_api_key(provider, model_config.get("api_key", ""))
        api_base = model_config.get("api_base", "")
        model_name = model_config.get("model", "")
    options = normalize_run_options({**(run_options or {}), "search_enabled": search_enabled})
    if runtime_provider == "ernie" and not api_key:
        ak, sk = resolve_baidu_ak_sk()
        if ak and sk:
            api_key = fetch_baidu_access_token(ak, sk)
    if not api_key:
        raise AdapterError("缺少 API Key")
    runtime_base, runtime_model = resolve_provider_runtime_config(runtime_provider, api_key, api_base, model_name)
    start = time.time()
    if runtime_provider == "openai":
        result = openai_responses_request(runtime_base, api_key, runtime_model, question, options)
    elif runtime_provider == "doubao":
        if options["search_enabled"]:
            result = doubao_search_request(runtime_base, api_key, runtime_model, question, options)
        else:
            result = openai_compatible_request(runtime_base, api_key, runtime_model, question, temperature, runtime_provider, options)
    elif runtime_provider == "kimi":
        if options["search_enabled"]:
            result = kimi_search_request(runtime_base, api_key, runtime_model, question, temperature, options)
        else:
            result = openai_compatible_request(runtime_base, api_key, runtime_model, question, temperature, runtime_provider, options)
    elif runtime_provider in {"deepseek", "qwen", "hunyuan", "ernie", "minimax", "openrouter_gpt", "openrouter_gemini"}:
        result = openai_compatible_request(runtime_base, api_key, runtime_model, question, temperature, runtime_provider, options)
    elif runtime_provider == "gemini":
        result = gemini_request(runtime_base, api_key, runtime_model, question, temperature, options)
    else:
        raise AdapterError(f"暂不支持的模型服务商：{runtime_provider}")
    return {
        "provider": provider,
        "model": result.get("returned_model", runtime_model),
        "configured_model": model_name,
        "model_version": model_config.get("model_version", ""),
        "search_enabled": options["search_enabled"],
        "search_mode": options["search_mode"],
        "thinking_type": options["thinking_type"],
        "reasoning_effort": options["reasoning_effort"],
        "thinking_budget": options["thinking_budget"],
        "latency_ms": int((time.time() - start) * 1000),
        **result,
    }


def test_model_config(model_config: dict[str, Any]) -> dict[str, Any]:
    provider = model_config.get("provider", "")
    test_temperature = 1 if provider == "kimi" else 0
    result = call_configured_model(
        model_config,
        "请回复“连接测试成功”。",
        False,
        test_temperature,
        {
            "search_mode": "off",
            "thinking_type": "disabled",
            "reasoning_effort": "",
            "thinking_budget": None,
        },
    )
    return {
        "ok": True,
        "provider": result["provider"],
        "model": result["model"],
        "configured_model": result.get("configured_model", ""),
        "latency_ms": result["latency_ms"],
        "preview": (result.get("response_text") or "")[:120],
    }


def enrich_model_config(item: dict[str, Any]) -> dict[str, Any]:
    preset = PROVIDER_PRESETS.get(item.get("provider", ""), {})
    api_key = item.get("api_key", "") or ""
    resolved_api_key = resolve_provider_api_key(item.get("provider", ""), api_key)
    safe_item = {**item}
    safe_item.pop("api_key", None)
    return {
        **preset,
        **safe_item,
        "sampling_defaults": provider_sampling_defaults(item.get("provider", ""), item.get("model", "")),
        "has_key": provider_has_credentials(item.get("provider", ""), api_key),
        "api_key_masked": mask_key(resolved_api_key),
    }
