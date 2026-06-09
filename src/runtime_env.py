from __future__ import annotations

import os
from pathlib import Path


_LOADED_ENV_FILES: set[str] = set()


PROVIDER_ENV_KEYS: dict[str, tuple[str, ...]] = {
    "openai": ("OPENAI_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "doubao": ("VOLCENGINE_ARK_API_KEY", "DOUBAO_API_KEY", "ARK_API_KEY"),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "qwen": ("DASHSCOPE_API_KEY", "QWEN_API_KEY"),
    "hunyuan": ("HUNYUAN_API_KEY", "TENCENT_API_KEY"),
    "kimi": ("MOONSHOT_API_KEY", "KIMI_API_KEY"),
    "ernie": ("QIANFAN_ACCESS_TOKEN", "BAIDU_QIANFAN_API_KEY", "ERNIE_API_KEY"),
    "minimax": ("MINIMAX_API_KEY",),
}

BAIDU_AK_ENV_KEYS = ("BAIDU_QIANFAN_AK", "QIANFAN_AK", "BAIDU_AK", "ERNIE_AK")
BAIDU_SK_ENV_KEYS = ("BAIDU_QIANFAN_SK", "QIANFAN_SK", "BAIDU_SK", "ERNIE_SK")


def load_dotenv_file(path: Path) -> None:
    env_path = str(path.resolve())
    if env_path in _LOADED_ENV_FILES or not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)
    _LOADED_ENV_FILES.add(env_path)


def first_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def resolve_provider_api_key(provider: str, current_value: str = "") -> str:
    current = str(current_value or "").strip()
    if current:
        return current
    return first_env(*PROVIDER_ENV_KEYS.get(provider, ()))


def resolve_baidu_ak_sk() -> tuple[str, str]:
    return first_env(*BAIDU_AK_ENV_KEYS), first_env(*BAIDU_SK_ENV_KEYS)


def provider_has_credentials(provider: str, current_value: str = "") -> bool:
    if resolve_provider_api_key(provider, current_value):
        return True
    if provider == "ernie":
        ak, sk = resolve_baidu_ak_sk()
        return bool(ak and sk)
    return False
