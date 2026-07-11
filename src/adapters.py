from __future__ import annotations

import hmac
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

from src.runtime_env import (
    provider_has_credentials,
    resolve_baidu_ak_sk,
    resolve_bocha_search_api_key,
    resolve_provider_api_key,
    resolve_tencent_search_credentials,
)
from src.reliability import classify_error


class AdapterError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
        retry_after: float | None = None,
        raw_response: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
        self.retry_after = retry_after
        self.raw_response = raw_response or {}
        self.error_code = classify_error(message, status_code, retryable).code.value


OPENAI_REASONING_LEVELS = "none;minimal;low;medium;high;xhigh"
BOCHA_TOOL_NAME = "bocha_web_search"

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
        "defaults_note": "DeepSeek 联网口径使用原生 Function Calling，由模型调用博查 Web Search API。",
    },
    "deepseek_web": {
        "temperature": 0,
        "reasoning_effort": "",
        "search_mode": "official_web",
        "defaults_note": "DeepSeek 官网联网搜索；每题使用独立网页会话，DOM 为最终答案真相源。",
    },
    "qwen": {
        "temperature": 0.7,
        "reasoning_effort": "",
        "search_strategy": "turbo",
        "defaults_note": "通义千问：按 Qwen3 非思考模型官方默认 temperature=0.7；联网搜索 search_strategy 预填 turbo。",
    },
    "hunyuan": {
        "temperature": None,
        "reasoning_effort": "",
        "search_mode": "force",
        "defaults_note": "腾讯元宝/混元：不传 temperature，使用腾讯官方模型推荐默认值；联网搜索使用腾讯官方 WSA SearchPro，再把搜索结果作为引用材料交给 hy3 生成。",
    },
    "kimi": {
        "temperature": 0.6,
        "reasoning_effort": "",
        "defaults_note": "Kimi K2.5 当前 API 仅接受 temperature=0.6；联网搜索需关闭深度思考。",
    },
    "ernie": {
        "temperature": None,
        "reasoning_effort": "",
        "defaults_note": "文心/千帆：不传 temperature，使用千帆模型默认参数；思考默认关闭。",
    },
    "minimax": {
        "temperature": None,
        "reasoning_effort": "",
        "defaults_note": "MiniMax：不传 temperature，使用模型默认参数；思考默认关闭。",
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
        "model": "deepseek-v4-flash",
        "model_version": "",
        "model_type": "chat",
        "api_base": "https://api.deepseek.com/v1",
        "supports_pure": True,
        "supports_search": True,
        "web_search_mode": "DeepSeek Function Calling + 博查 Web Search API",
        "web_search_param_path": "tools[].function.name=bocha_web_search; assistant.tool_calls -> role=tool",
        "supports_reasoning": True,
        "reasoning_param_path": "thinking.type / reasoning_effort",
        "reasoning_levels": "disabled;enabled + low/medium/high",
        "supports_citation": True,
        "citation_param_path": "博查 data.webPages.value[].url/name/snippet/summary",
        "supports_site_filter": False,
        "supports_time_filter": False,
        "supports_user_location": False,
        "supports_tool_calling": True,
        "notes": "DeepSeek OpenAI 兼容接口；不注入 system prompt，由模型生成博查检索参数，应用执行后以 role=tool 回传。",
    },
    "deepseek_web": {
        "label": "DeepSeek 官网联网搜索",
        "provider": "deepseek_web",
        "api_family": "DeepSeek Web UI",
        "model": "deepseek-web-search",
        "model_version": "",
        "model_type": "browser",
        "api_base": "https://chat.deepseek.com",
        "supports_pure": False,
        "supports_search": True,
        "web_search_mode": "DeepSeek 官网联网搜索",
        "web_search_param_path": "Playwright UI + passive response capture",
        "supports_reasoning": False,
        "reasoning_param_path": "",
        "reasoning_levels": "",
        "supports_citation": True,
        "citation_param_path": "rendered answer links / passive network metadata",
        "supports_site_filter": False,
        "supports_time_filter": False,
        "supports_user_location": False,
        "supports_tool_calling": False,
        "notes": "通过官方 chat.deepseek.com 网页执行；每题独立会话，仅支持联网搜索批次。",
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
        "model": "qwen3.7-plus",
        "model_version": "",
        "model_type": "chat",
        "api_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "supports_pure": True,
        "supports_search": True,
        "web_search_mode": "Responses API web_search",
        "web_search_param_path": "POST /responses; tools[].type=web_search; output[].action.sources",
        "supports_reasoning": True,
        "reasoning_param_path": "reasoning.effort / enable_thinking / thinking_budget",
        "reasoning_levels": OPENAI_REASONING_LEVELS,
        "supports_citation": True,
        "citation_param_path": "output[type=web_search_call].action.sources[].url; output[].content[].annotations",
        "supports_site_filter": False,
        "supports_time_filter": False,
        "supports_user_location": False,
        "supports_tool_calling": True,
        "notes": "qwen3.7-plus 联网搜索使用阿里云百炼 OpenAI 兼容 Responses API；仅传用户问题，不附加 system prompt；引用从 web_search_call.action.sources 提取。Responses 思考模式不支持 tool_choice=required，模型自行决定是否检索。",
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
        "web_search_mode": "腾讯云联网搜索 API SearchPro + hy3 生成",
        "web_search_param_path": "wsa.tencentcloudapi.com; Action=SearchPro; Query/Mode/Site/Freshness/Cnt; TENCENT_SEARCH_SECRET_ID/TENCENT_SEARCH_SECRET_KEY",
        "supports_reasoning": True,
        "reasoning_param_path": "按模型能力控制，当前预置仅记录能力",
        "reasoning_levels": "按模型能力",
        "supports_citation": True,
        "citation_param_path": "SearchPro Response.Pages[].url/title/passage/site",
        "supports_site_filter": True,
        "supports_time_filter": True,
        "supports_user_location": False,
        "supports_tool_calling": True,
        "notes": "腾讯元宝数据源当前走 TokenHub hy3 生成；联网搜索使用腾讯官方 WSA SearchPro，引用来自 SearchPro Pages。",
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
        "model": "ernie-5.1",
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
        retry_after = None
        try:
            retry_after = float(exc.headers.get("Retry-After", ""))
        except (TypeError, ValueError):
            pass
        raise AdapterError(
            f"HTTP {exc.code}: {body[:1200]}",
            retryable=exc.code in {408, 409, 425, 429, 500, 502, 503, 504},
            status_code=exc.code,
            retry_after=retry_after,
        ) from exc
    except urllib.error.URLError as exc:
        raise AdapterError(str(exc), retryable=True) from exc
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
        retry_after = None
        try:
            retry_after = float(exc.headers.get("Retry-After", ""))
        except (TypeError, ValueError):
            pass
        raise AdapterError(
            f"HTTP {exc.code}: {body[:1200]}",
            retryable=exc.code in {408, 409, 425, 429, 500, 502, 503, 504},
            status_code=exc.code,
            retry_after=retry_after,
        ) from exc
    except urllib.error.URLError as exc:
        raise AdapterError(str(exc), retryable=True) from exc
    except Exception as exc:
        raise AdapterError(str(exc)) from exc


def normalize_base(base: str) -> str:
    return (base or "").rstrip("/")


def dashscope_generation_url(base: str) -> str:
    normalized = normalize_base(base)
    if normalized.endswith("/compatible-mode/v1"):
        return f"{normalized[: -len('/compatible-mode/v1')]}/api/v1/services/aigc/text-generation/generation"
    if normalized.endswith("/api/v1"):
        return f"{normalized}/services/aigc/text-generation/generation"
    return "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"


def mask_key(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}***{value[-4:]}"


def normalize_choice_text(data: dict[str, Any]) -> str:
    output = data.get("output") or {}
    output_choices = output.get("choices") or []
    if output_choices:
        message = output_choices[0].get("message") or {}
        content = message.get("content") or ""
        if isinstance(content, str):
            return content
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


def first_choice_finish_reason(data: dict[str, Any]) -> tuple[str, str]:
    choices = data.get("choices") or []
    if not choices:
        output = data.get("output") or {}
        choices = output.get("choices") or []
    if not choices:
        return "", ""
    choice = choices[0] or {}
    return str(choice.get("finish_reason") or ""), str(choice.get("native_finish_reason") or "")


def ensure_openrouter_complete_response(data: dict[str, Any], response_text: str) -> None:
    finish_reason, native_finish_reason = first_choice_finish_reason(data)
    if finish_reason == "length" or native_finish_reason == "max_output_tokens":
        raise AdapterError("OpenRouter 返回被 max_tokens 截断，未产出完整回答", raw_response=data)
    choices = data.get("choices") or (data.get("output") or {}).get("choices") or []
    choice = (choices[0] or {}) if choices else {}
    choice_error = choice.get("error") or {}
    if finish_reason == "error" or choice_error:
        raw_code = choice_error.get("code")
        try:
            status_code = int(raw_code)
        except (TypeError, ValueError):
            status_code = None
        metadata = choice_error.get("metadata") or {}
        error_type = str(metadata.get("error_type") or "").strip()
        detail = error_type or str(choice_error.get("message") or "上游生成异常").strip()
        retryable = finish_reason == "error" and (
            status_code is None or status_code == 429 or status_code >= 500
        )
        raise AdapterError(
            f"OpenRouter 返回异常结束：{detail}，部分回答未作为成功结果保存",
            retryable=retryable,
            status_code=status_code,
            raw_response=data,
        )
    if not response_text.strip():
        raise AdapterError("OpenRouter 返回成功但回答内容为空", retryable=True, raw_response=data)


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


def extract_bocha_results(data: dict[str, Any], limit: int = 5) -> list[dict[str, str]]:
    results = (((data.get("data") or {}).get("webPages") or {}).get("value") or [])[:limit]
    items: list[dict[str, str]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        title = str(item.get("name") or item.get("title") or "").strip()
        snippets = [
            str(value).strip()
            for value in (item.get("snippet"), item.get("summary"))
            if str(value or "").strip()
        ]
        if url or title or snippets:
            items.append(
                {
                    "url": url,
                    "title": title,
                    "description": "\n".join(dict.fromkeys(snippets)),
                }
            )
    return items


def bocha_citations(results: list[dict[str, str]]) -> list[dict[str, str]]:
    return dedupe_citations(
        [{"url": item.get("url", ""), "title": item.get("title", "")} for item in results if item.get("url")]
    )


def tc3_hmac_sha256(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def tencent_search_authorization(secret_id: str, secret_key: str, payload_json: str, timestamp: int) -> str:
    service = "wsa"
    host = "wsa.tencentcloudapi.com"
    action = "SearchPro"
    date = time.strftime("%Y-%m-%d", time.gmtime(timestamp))
    algorithm = "TC3-HMAC-SHA256"
    canonical_headers = f"content-type:application/json; charset=utf-8\nhost:{host}\nx-tc-action:{action.lower()}\n"
    signed_headers = "content-type;host;x-tc-action"
    hashed_request_payload = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    canonical_request = "\n".join(
        [
            "POST",
            "/",
            "",
            canonical_headers,
            signed_headers,
            hashed_request_payload,
        ]
    )
    credential_scope = f"{date}/{service}/tc3_request"
    hashed_canonical_request = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    string_to_sign = "\n".join([algorithm, str(timestamp), credential_scope, hashed_canonical_request])
    secret_date = tc3_hmac_sha256(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = tc3_hmac_sha256(secret_date, service)
    secret_signing = tc3_hmac_sha256(secret_service, "tc3_request")
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return (
        f"{algorithm} Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )


def normalize_tencent_freshness(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"[dmy](\d+)?", text):
        return text
    if text.isdigit():
        return f"d{text}"
    return text


def tencent_search_request(question: str, options: dict[str, Any]) -> dict[str, Any]:
    _appid, secret_id, secret_key = resolve_tencent_search_credentials()
    if not secret_id or not secret_key:
        raise AdapterError("腾讯官方联网搜索需要 TENCENT_SEARCH_SECRET_ID 和 TENCENT_SEARCH_SECRET_KEY。")
    payload: dict[str, Any] = {"Query": question}
    if options.get("search_site_filter"):
        first_site = str(options["search_site_filter"]).split(",", 1)[0].strip()
        if first_site:
            payload["Site"] = first_site
    freshness = normalize_tencent_freshness(options.get("search_freshness", ""))
    if freshness:
        payload["Freshness"] = freshness
    if options.get("search_limit") in {10, 20, 30, 40, 50}:
        payload["Cnt"] = int(options["search_limit"])
    payload_json = json.dumps(payload)
    timestamp = int(time.time())
    headers = {
        "Authorization": tencent_search_authorization(secret_id, secret_key, payload_json, timestamp),
        "Content-Type": "application/json; charset=utf-8",
        "Host": "wsa.tencentcloudapi.com",
        "X-TC-Action": "SearchPro",
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Version": "2025-05-08",
    }
    data = post_json("https://wsa.tencentcloudapi.com", headers, payload)
    response = data.get("Response") or {}
    if response.get("Error"):
        error = response["Error"]
        raise AdapterError(f"腾讯官方联网搜索失败：{error.get('Code', '')} {error.get('Message', '')}".strip())
    results = extract_tencent_search_results(response)
    if not results:
        raise AdapterError("腾讯官方联网搜索未返回可用网页结果。")
    return {"raw_response": data, "results": results}


def extract_tencent_search_results(response: dict[str, Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for page in response.get("Pages") or []:
        try:
            parsed = json.loads(page) if isinstance(page, str) else page
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        url = str(parsed.get("url") or "").strip()
        title = str(parsed.get("title") or "").strip()
        description = str(parsed.get("passage") or parsed.get("content") or "").strip()
        site = str(parsed.get("site") or "").strip()
        date = str(parsed.get("date") or "").strip()
        if url or title or description:
            items.append({"url": url, "title": title, "description": description, "site": site, "date": date})
    return items


def tencent_search_citations(results: list[dict[str, str]]) -> list[dict[str, str]]:
    return dedupe_citations(
        [{"url": item.get("url", ""), "title": item.get("title", "")} for item in results if item.get("url")]
    )


def build_tencent_augmented_question(question: str, results: list[dict[str, str]]) -> str:
    source_blocks = []
    for idx, item in enumerate(results, start=1):
        parts = [
            f"[{idx}] 标题: {item.get('title') or '-'}",
            f"URL: {item.get('url') or '-'}",
        ]
        if item.get("site"):
            parts.append(f"站点: {item['site']}")
        if item.get("date"):
            parts.append(f"日期: {item['date']}")
        parts.append(f"摘要: {item.get('description') or '-'}")
        source_blocks.append("\n".join(parts))
    return (
        "你是制造业品牌 GEO 审计助手。\n"
        "以下是腾讯云联网搜索 API SearchPro 返回的公开网页检索结果。请只基于这些资料回答用户问题。\n"
        "如果资料不足，请明确说明“检索结果不足以判断”。\n\n"
        "要求：\n"
        "1. 客观回答，不要编造未出现在资料中的事实。\n"
        "2. 涉及品牌、厂家、产品能力时尽量引用来源编号，例如 [1]。\n"
        "3. 不要声称你自己进行了联网搜索；信息来源是下方腾讯云 SearchPro 结果。\n\n"
        f"用户问题：\n{question}\n\n"
        "腾讯云 SearchPro 结果：\n"
        + "\n\n".join(source_blocks)
    )


def extract_qwen_citations(data: dict[str, Any]) -> list[dict[str, str]]:
    citations = extract_generic_citations(data)
    search_info = data.get("search_info") or {}
    for item in search_info.get("search_results") or []:
        if isinstance(item, dict):
            citations.append({"url": item.get("url", ""), "title": item.get("title", "")})
    output_search_info = (data.get("output") or {}).get("search_info") or {}
    for item in output_search_info.get("search_results") or []:
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
    for item in data.get("search_results") or []:
        if isinstance(item, dict):
            citations.append({"url": item.get("url", ""), "title": item.get("title", "")})
    web_search = data.get("web_search") or {}
    for item in web_search.get("references") or []:
        if isinstance(item, dict):
            citations.append({"url": item.get("url", ""), "title": item.get("title", "")})
    for choice in data.get("choices") or []:
        message = choice.get("message") or {}
        for item in message.get("search_results") or []:
            if isinstance(item, dict):
                citations.append({"url": item.get("url", ""), "title": item.get("title", "")})
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
    # V2 never changes the measured source inside one logical task. A fallback
    # must be configured as a separate source/generation so reports can label it.
    if os.getenv("OPENROUTER_DIRECT_FALLBACK") == "1" or os.getenv("ALLOW_CROSS_PROVIDER_FALLBACK") == "1":
        raise AdapterError(
            f"已禁止 {provider} 跨 provider fallback；请创建独立信息源配置并启动新 generation。",
            retryable=False,
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


def build_openai_chat_payload(model: str, question: str, temperature: float | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是制造业品牌 GEO 审计助手。请基于公开信息客观回答。"},
            {"role": "user", "content": question},
        ],
    }
    if temperature is not None:
        payload["temperature"] = temperature
    return payload


def build_user_only_chat_payload(model: str, question: str, temperature: float | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": question}],
    }
    if temperature is not None:
        payload["temperature"] = temperature
    return payload


def bocha_freshness(value: str) -> str:
    normalized = str(value or "").strip()
    aliases = {
        "day": "oneDay", "week": "oneWeek", "month": "oneMonth", "year": "oneYear",
        "one_day": "oneDay", "one_week": "oneWeek", "one_month": "oneMonth", "one_year": "oneYear",
    }
    return aliases.get(normalized, normalized or "noLimit")


def bocha_search_request(question: str, options: dict[str, Any]) -> dict[str, Any]:
    api_key = resolve_bocha_search_api_key()
    if not api_key:
        raise AdapterError("DeepSeek 联网口径需要 BOCHA_API_KEY。")
    count = options.get("search_limit") or 5
    try:
        count = max(1, min(int(count), 10))
    except (TypeError, ValueError):
        count = 5
    payload: dict[str, Any] = {
        "query": question,
        "count": count,
        "summary": True,
        "freshness": bocha_freshness(options.get("search_freshness", "")),
    }
    if options.get("search_site_filter"):
        payload["include"] = str(options["search_site_filter"])
    data = post_json(
        "https://api.bochaai.com/v1/web-search",
        {"Authorization": f"Bearer {api_key}"},
        payload,
    )
    results = extract_bocha_results(data, limit=count)
    if not results:
        raise AdapterError("博查 Web Search API 未返回可用网页结果，无法执行联网搜索口径。")
    return {"raw_response": data, "results": results}


def bocha_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": BOCHA_TOOL_NAME,
            "description": "搜索公开网页以回答需要最新信息、事实核验、品牌或产品调研的问题。可以使用不同关键词多次搜索。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "适合网页搜索的精确检索词"},
                    "count": {"type": "integer", "minimum": 1, "maximum": 10, "description": "返回结果数量"},
                    "freshness": {"type": "string", "description": "时间范围，如 noLimit、oneDay、oneWeek、oneMonth、oneYear"},
                    "include": {"type": "string", "description": "可选，限定搜索的网站域名"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    }


def execute_bocha_tool(arguments: Any, options: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise AdapterError("DeepSeek 返回的博查工具参数不是 JSON 对象。", retryable=True)
    query = str(arguments.get("query") or "").strip()
    if not query:
        raise AdapterError("DeepSeek 返回的博查工具参数缺少 query。", retryable=True)
    tool_options = dict(options)
    if arguments.get("count") is not None:
        tool_options["search_limit"] = arguments["count"]
    if arguments.get("freshness"):
        tool_options["search_freshness"] = str(arguments["freshness"])
    if arguments.get("include"):
        tool_options["search_site_filter"] = str(arguments["include"])
    return bocha_search_request(query, tool_options)


def deepseek_bocha_tool_request(
    base: str,
    api_key: str,
    model: str,
    question: str,
    temperature: float | None,
    options: dict[str, Any],
) -> dict[str, Any]:
    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
    deepseek_rounds: list[dict[str, Any]] = []
    tool_audit: list[dict[str, Any]] = []
    citations: list[dict[str, str]] = []
    usage: dict[str, int] = {}
    try:
        max_rounds = max(1, min(int(os.environ.get("DEEPSEEK_BOCHA_MAX_TOOL_ROUNDS", "4")), 8))
    except ValueError:
        max_rounds = 4

    for round_index in range(max_rounds):
        payload = build_deepseek_chat_payload(model, question, temperature, options)
        payload["messages"] = deepcopy(messages)
        payload["tools"] = [bocha_tool_definition()]
        payload["tool_choice"] = "required" if round_index == 0 else "none"
        data = post_json(
            f"{normalize_base(base)}/chat/completions",
            {"Authorization": f"Bearer {api_key}"},
            payload,
        )
        deepseek_rounds.append(data)
        for key, value in (data.get("usage") or {}).items():
            if isinstance(value, int):
                usage[key] = usage.get(key, 0) + value
        choices = data.get("choices") or []
        message = (choices[0].get("message") or {}) if choices else {}
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            response_text = normalize_choice_text(data)
            if not response_text.strip():
                raise AdapterError("DeepSeek 工具调用结束后未返回最终回答。", retryable=True, raw_response=data)
            return {
                "response_text": response_text,
                "citations": dedupe_citations(citations),
                "usage": usage,
                "raw_response": {
                    "tool_mode": "deepseek_function_calling",
                    "deepseek_rounds": deepseek_rounds,
                    "bocha_tool_calls": tool_audit,
                    "messages": messages + [{key: value for key, value in message.items() if key in {"role", "content"}}],
                },
                "returned_model": data.get("model", model),
            }

        assistant_message = {
            key: deepcopy(value)
            for key, value in message.items()
            if key in {"role", "content", "tool_calls", "reasoning_content"}
        }
        assistant_message.setdefault("role", "assistant")
        messages.append(assistant_message)
        for tool_call in tool_calls:
            function = tool_call.get("function") or {}
            if function.get("name") != BOCHA_TOOL_NAME:
                raise AdapterError(f"DeepSeek 请求了不受支持的工具：{function.get('name') or '-'}", retryable=True)
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except (TypeError, json.JSONDecodeError) as exc:
                raise AdapterError("DeepSeek 返回的博查工具参数不是有效 JSON。", retryable=True) from exc
            try:
                bocha_payload = execute_bocha_tool(arguments, options)
            except AdapterError as exc:
                raise AdapterError(f"DeepSeek 调用博查 Web Search API 失败：{exc}", retryable=exc.retryable) from exc
            results = bocha_payload["results"]
            citations.extend(bocha_citations(results))
            tool_call_id = str(tool_call.get("id") or "").strip()
            if not tool_call_id:
                raise AdapterError("DeepSeek 工具调用缺少 tool_call_id。", retryable=True)
            tool_content = json.dumps({"query": arguments.get("query"), "results": results}, ensure_ascii=False)
            messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": tool_content})
            tool_audit.append({
                "tool_call_id": tool_call_id,
                "name": BOCHA_TOOL_NAME,
                "arguments": arguments,
                "results": results,
                "raw_response": bocha_payload["raw_response"],
            })

    raise AdapterError(
        f"DeepSeek 连续 {max_rounds} 轮仍未结束博查工具调用。",
        retryable=True,
        raw_response={"deepseek_rounds": deepseek_rounds, "bocha_tool_calls": tool_audit},
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
    payload = build_user_only_chat_payload(model, question, temperature)
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
    temperature: float | None,
    options: dict[str, Any],
) -> dict[str, Any]:
    payload = build_user_only_chat_payload(model, question, temperature)
    if options["search_enabled"]:
        payload["enable_search"] = True
        search_options: dict[str, Any] = {
            "enable_source": True,
            "enable_citation": True,
        }
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
        if options["search_citation_format"]:
            search_options["citation_format"] = options["search_citation_format"]
        else:
            search_options["citation_format"] = "[ref_<number>]"
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


def build_qwen_generation_payload(
    model: str,
    question: str,
    temperature: float | None,
    options: dict[str, Any],
) -> dict[str, Any]:
    chat_payload = build_qwen_chat_payload(model, question, temperature, options)
    parameters: dict[str, Any] = {
        "enable_search": True,
        "search_options": chat_payload.get("search_options", {"enable_source": True, "enable_citation": True}),
        "result_format": "message",
    }
    if temperature is not None:
        parameters["temperature"] = temperature
    return {
        "model": model,
        "input": {"messages": chat_payload["messages"]},
        "parameters": parameters,
    }


def build_qwen_responses_payload(
    model: str,
    question: str,
    temperature: float | None,
    options: dict[str, Any],
) -> dict[str, Any]:
    """Build the documented Qwen Responses web-search request without prompt injection."""
    payload: dict[str, Any] = {
        "model": model,
        "input": question,
        "tools": [{"type": "web_search"}],
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if options["reasoning_effort"]:
        payload["reasoning"] = {"effort": options["reasoning_effort"]}
    return payload


def build_kimi_chat_payload(
    model: str,
    question: str,
    temperature: float | None,
    options: dict[str, Any],
) -> dict[str, Any]:
    normalized_temperature = normalize_kimi_temperature(model, temperature if temperature is not None else 0.6)
    payload = build_openai_chat_payload(model, question, normalized_temperature)
    if options["search_enabled"] and options["thinking_type"] != "disabled":
        raise AdapterError("Kimi 官方联网搜索要求关闭深度思考，请在采样页关闭深度思考后再试。")
    if options["thinking_type"] in {"enabled", "disabled"}:
        payload["thinking"] = {"type": options["thinking_type"]}
    return payload


def build_hunyuan_chat_payload(
    model: str,
    question: str,
    temperature: float | None,
    options: dict[str, Any],
) -> dict[str, Any]:
    return build_openai_chat_payload(model, question, temperature)


def build_ernie_chat_payload(
    model: str,
    question: str,
    temperature: float | None,
    options: dict[str, Any],
) -> dict[str, Any]:
    payload = build_openai_chat_payload(model, question, temperature)
    if options["search_enabled"]:
        payload["web_search"] = {
            "enable": True,
            "enable_trace": True,
            "enable_citation": True,
            "search_mode": "auto",
            "search_number": 10,
            "reference_number": 5,
        }
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
    temperature: float | None,
    options: dict[str, Any],
) -> dict[str, Any]:
    return build_openai_chat_payload(model, question, temperature)


def build_openrouter_chat_payload(
    model: str,
    question: str,
    temperature: float | None,
    options: dict[str, Any],
) -> dict[str, Any]:
    payload = build_openai_chat_payload(model, question, temperature)
    payload["max_tokens"] = 4096
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
    temperature: float | None,
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
    temperature: float | None,
    provider: str,
    options: dict[str, Any],
) -> dict[str, Any]:
    if provider == "deepseek" and options["search_enabled"]:
        return deepseek_bocha_tool_request(base, api_key, model, question, temperature, options)
    tencent_search_payload: dict[str, Any] | None = None
    external_citations: list[dict[str, str]] = []
    request_question = question
    if provider == "hunyuan" and options["search_enabled"]:
        try:
            tencent_search_payload = tencent_search_request(question, options)
            tencent_results = tencent_search_payload["results"]
            external_citations = tencent_search_citations(tencent_results)
            request_question = build_tencent_augmented_question(question, tencent_results)
        except AdapterError as exc:
            raise AdapterError(f"腾讯元宝联网口径依赖腾讯云 SearchPro 失败：{exc}") from exc
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
    if tencent_search_payload is not None:
        raw_response = {
            f"{provider}_response": data,
            "tencent_search": tencent_search_payload["raw_response"],
            "tencent_search_results": tencent_search_payload["results"],
        }
    response_text = normalize_choice_text(data)
    if provider in {"openrouter_gpt", "openrouter_gemini"}:
        ensure_openrouter_complete_response(data, response_text)
    return {
        "response_text": response_text,
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


def qwen_responses_request(
    base: str,
    api_key: str,
    model: str,
    question: str,
    temperature: float | None,
    options: dict[str, Any],
) -> dict[str, Any]:
    payload = build_qwen_responses_payload(model, question, temperature, options)
    data = post_json(
        f"{normalize_base(base)}/responses",
        {"Authorization": f"Bearer {api_key}"},
        payload,
    )
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
    elif runtime_provider == "qwen":
        if options["search_enabled"]:
            result = qwen_responses_request(runtime_base, api_key, runtime_model, question, temperature, options)
        else:
            result = openai_compatible_request(runtime_base, api_key, runtime_model, question, temperature, runtime_provider, options)
    elif runtime_provider in {"deepseek", "hunyuan", "ernie", "minimax", "openrouter_gpt", "openrouter_gemini"}:
        result = openai_compatible_request(runtime_base, api_key, runtime_model, question, temperature, runtime_provider, options)
    elif runtime_provider == "gemini":
        result = gemini_request(runtime_base, api_key, runtime_model, question, temperature, options)
    else:
        raise AdapterError(f"暂不支持的模型服务商：{runtime_provider}")
    return {
        "provider": provider,
        "actual_provider": runtime_provider,
        "fallback_used": runtime_provider != provider,
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
    if provider == "deepseek_web":
        from src.deepseek_web import DeepSeekWebBrowser

        browser = DeepSeekWebBrowser()
        try:
            result = browser.preflight()
        finally:
            browser.close()
        return {"ok": True, "provider": provider, "model": model_config.get("model", "deepseek-web-search"), **result}
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
