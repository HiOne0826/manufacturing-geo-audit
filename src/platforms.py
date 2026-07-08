from __future__ import annotations


TEST_PLATFORM_NAMES = {
    "openai": "ChatGPT",
    "deepseek": "DeepSeek",
    "gemini": "Gemini",
    "doubao": "豆包",
    "qwen": "千问",
    "hunyuan": "腾讯元宝",
    "openrouter_gpt": "ChatGPT",
    "openrouter_gemini": "Gemini",
}


def test_platform_name(provider: str | None, fallback: str | None = None) -> str:
    key = str(provider or "").strip().lower()
    if key in TEST_PLATFORM_NAMES:
        return TEST_PLATFORM_NAMES[key]
    return str(fallback or provider or "unknown").strip() or "unknown"
