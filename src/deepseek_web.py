from __future__ import annotations

import atexit
import difflib
import importlib.util
import json
import os
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]
CHAT_URL = "https://chat.deepseek.com"
SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "did",
    "email",
    "mobile_number",
    "phone",
    "phone_number",
    "set-cookie",
    "token",
    "access_token",
    "refresh_token",
    "session_token",
    "localstorage",
}


class DeepSeekWebError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class DeepSeekWebConfig:
    chat_url: str
    auth_state: Path
    artifact_root: Path
    headless: bool
    navigation_timeout_ms: int
    response_timeout_seconds: int
    stable_seconds: int
    viewport_width: int
    viewport_height: int

    @classmethod
    def from_env(cls) -> "DeepSeekWebConfig":
        return cls(
            chat_url=os.environ.get("DEEPSEEK_WEB_CHAT_URL", CHAT_URL).strip() or CHAT_URL,
            auth_state=resolve_path(os.environ.get("DEEPSEEK_WEB_AUTH_STATE", "private/deepseek-web/storage-state.json")),
            artifact_root=resolve_path(os.environ.get("DEEPSEEK_WEB_ARTIFACT_DIR", "data/deepseek-web-artifacts")),
            headless=os.environ.get("DEEPSEEK_WEB_HEADLESS", "0") == "1",
            navigation_timeout_ms=env_int("DEEPSEEK_WEB_NAVIGATION_TIMEOUT_MS", 30000, 5000, 120000),
            response_timeout_seconds=env_int("DEEPSEEK_WEB_RESPONSE_TIMEOUT_SECONDS", 240, 30, 600),
            stable_seconds=env_int("DEEPSEEK_WEB_STABLE_SECONDS", 5, 2, 30),
            viewport_width=env_int("DEEPSEEK_WEB_VIEWPORT_WIDTH", 1440, 1024, 2560),
            viewport_height=env_int("DEEPSEEK_WEB_VIEWPORT_HEIGHT", 1000, 720, 1600),
        )


def env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def resolve_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


def deepseek_web_status() -> dict[str, Any]:
    config = DeepSeekWebConfig.from_env()
    artifact_files = 0
    artifact_bytes = 0
    if config.artifact_root.exists():
        for path in config.artifact_root.rglob("*"):
            if path.is_file():
                artifact_files += 1
                try:
                    artifact_bytes += path.stat().st_size
                except OSError:
                    pass
    return {
        "enabled": os.environ.get("DEEPSEEK_WEB_ENABLED", "0") == "1",
        "playwright_installed": importlib.util.find_spec("playwright") is not None,
        "auth_configured": config.auth_state.is_file(),
        "auth_state_mode": oct(stat.S_IMODE(config.auth_state.stat().st_mode)) if config.auth_state.is_file() else "",
        "queue_name": os.environ.get("RQ_WEB_QUEUE_NAME", "geo-audit-web"),
        "concurrency": 1,
        "headless": config.headless,
        "artifact_files": artifact_files,
        "artifact_bytes": artifact_bytes,
    }


def sanitize_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        query = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            query.append((key, "[REDACTED]" if key.lower() in SENSITIVE_KEYS else value))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    except Exception:
        return url


def sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if str(key).lower() in SENSITIVE_KEYS else sanitize_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_value(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)(bearer\s+)[a-z0-9._~+/=-]+", r"\1[REDACTED]", value)
        value = re.sub(r'(?i)("?(?:access_token|refresh_token|session_token|token)"?\s*[:=]\s*")[^"]+', r'\1[REDACTED]', value)
        value = re.sub(r"(?i)\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b", "[REDACTED]", value)
        value = re.sub(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)", "[REDACTED]", value)
        return value
    return value


def normalize_answer(value: str) -> str:
    value = re.sub(r"[`*_>#\[\]()~-]+", " ", value or "")
    return re.sub(r"\s+", " ", value).strip().lower()


def answers_match(dom_text: str, network_text: str) -> tuple[bool, float]:
    left = normalize_answer(dom_text)
    right = normalize_answer(network_text)
    if len(right) < 50:
        return True, 0.0
    ratio = difflib.SequenceMatcher(None, left, right).ratio()
    return ratio >= 0.60 or left in right or right in left, ratio


def extract_chat_id(url: str, network_events: list[dict[str, Any]] | None = None) -> str:
    patterns = (r"/chat/s/([a-zA-Z0-9_-]+)", r"[?&](?:chat_session_id|session_id)=([a-zA-Z0-9_-]+)")
    candidates = [url]
    for event in network_events or []:
        candidates.append(str(event.get("url") or ""))
        candidates.append(str(event.get("body") or ""))
    for candidate in candidates:
        for pattern in patterns:
            match = re.search(pattern, candidate)
            if match:
                return match.group(1)
    return ""


def _extract_text_fragments(value: Any, parent_key: str = "") -> list[str]:
    fragments: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            lower = str(key).lower()
            if lower in {"content", "text", "answer"} and isinstance(item, str) and "thinking" not in parent_key:
                fragments.append(item)
            else:
                fragments.extend(_extract_text_fragments(item, lower))
    elif isinstance(value, list):
        for item in value:
            fragments.extend(_extract_text_fragments(item, parent_key))
    return fragments


def network_answer(events: list[dict[str, Any]]) -> str:
    fragments: list[str] = []
    current_path = ""
    for event in events:
        body = str(event.get("body") or "")
        for raw_line in body.splitlines():
            line = raw_line.removeprefix("data:").strip()
            if not line or line == "[DONE]":
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            if isinstance(payload.get("content"), str):
                fragments.append(payload["content"])
            if isinstance(payload.get("p"), str):
                current_path = payload["p"]
            value = payload.get("v")
            if current_path == "response" and payload.get("o") == "BATCH" and isinstance(value, list):
                for patch in value:
                    if not isinstance(patch, dict) or patch.get("p") != "fragments" or patch.get("o") != "APPEND":
                        continue
                    for fragment in patch.get("v") or []:
                        if isinstance(fragment, dict) and fragment.get("type") == "RESPONSE" and isinstance(fragment.get("content"), str):
                            fragments.append(fragment["content"])
            elif current_path.endswith("/content") and isinstance(value, str):
                fragments.append(value)
    if not fragments:
        return ""
    if all(fragments[index].startswith(fragments[index - 1]) for index in range(1, len(fragments))):
        return fragments[-1]
    return "".join(fragments)


class DeepSeekWebBrowser:
    def __init__(self, config: DeepSeekWebConfig | None = None):
        self.config = config or DeepSeekWebConfig.from_env()
        self._playwright = None
        self._browser = None

    def close(self) -> None:
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    def _ensure_browser(self):
        if self._browser is not None and self._browser.is_connected():
            return self._browser
        if self._browser is not None or self._playwright is not None:
            self.close()
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise DeepSeekWebError("playwright_missing", "缺少 Playwright，请安装 requirements-web-worker.txt", retryable=False) from exc
        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch(headless=self.config.headless)
        except Exception as exc:
            self.close()
            raise DeepSeekWebError("browser_launch_failed", f"Chromium 启动失败：{exc}", retryable=True) from exc
        return self._browser

    def _new_context(self):
        if not self.config.auth_state.is_file():
            raise DeepSeekWebError("auth_missing", "DeepSeek 网页登录态不存在，请先运行登录初始化 CLI", retryable=False)
        browser = self._ensure_browser()
        return browser.new_context(
            storage_state=str(self.config.auth_state),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": self.config.viewport_width, "height": self.config.viewport_height},
        )

    @staticmethod
    def _blocked_page(page) -> str:
        text = page.locator("body").inner_text(timeout=5000).lower()
        if any(marker in text for marker in ("captcha", "验证码", "安全验证", "verify you are human", "访问受限")):
            return "captcha"
        if any(marker in text for marker in ("登录", "log in", "sign in")) and page.locator("textarea, [contenteditable='true']").count() == 0:
            return "auth_expired"
        return ""

    @staticmethod
    def _composer(page):
        selectors = [
            os.environ.get("DEEPSEEK_WEB_COMPOSER_SELECTOR", "").strip(),
            "textarea",
            "[contenteditable='true']",
        ]
        for selector in selectors:
            if not selector:
                continue
            locator = page.locator(selector).last
            if locator.count() and locator.is_visible():
                return locator
        raise DeepSeekWebError("selector_composer", "找不到 DeepSeek 输入框", retryable=False)

    @staticmethod
    def _search_toggle(page):
        override = os.environ.get("DEEPSEEK_WEB_SEARCH_TOGGLE_SELECTOR", "").strip()
        candidates = []
        if override:
            candidates.append(page.locator(override).last)
        candidates.extend(
            [
                page.get_by_role("button", name=re.compile("智能搜索|联网搜索|Web Search|Search", re.I)).last,
                page.locator("button").filter(has_text=re.compile("智能搜索|联网搜索|Web Search", re.I)).last,
                page.get_by_text(re.compile("^(智能搜索|联网搜索|Web Search)$", re.I)).last.locator("xpath=.."),
            ]
        )
        for locator in candidates:
            if locator.count() and locator.is_visible():
                return locator
        raise DeepSeekWebError("selector_search_toggle", "找不到联网搜索开关", retryable=False)

    @staticmethod
    def _new_chat_control(page):
        override = os.environ.get("DEEPSEEK_WEB_NEW_CHAT_SELECTOR", "").strip()
        candidates = []
        if override:
            candidates.append(page.locator(override).first)
        candidates.extend(
            [
                page.get_by_role("button", name=re.compile("新建对话|新对话|New Chat", re.I)).first,
                page.get_by_role("link", name=re.compile("新建对话|新对话|New Chat", re.I)).first,
                page.get_by_text(re.compile("^(开启新对话|新建对话|新对话|New Chat)$", re.I)).first.locator("xpath=.."),
            ]
        )
        for locator in candidates:
            if locator.count() and locator.is_visible():
                return locator
        return None

    def _open_new_chat(self, page, trace: list[dict[str, Any]]) -> None:
        initial_chat_id = extract_chat_id(page.url)
        existing_answer = self._answer_locator(page)
        needs_reset = bool(initial_chat_id or (existing_answer is not None and existing_answer.inner_text().strip()))
        if needs_reset:
            control = self._new_chat_control(page)
            if control is None:
                raise DeepSeekWebError("selector_new_chat", "页面恢复到旧会话，但找不到新建对话控件", retryable=False)
            control.click()
            page.wait_for_timeout(500)
            if extract_chat_id(page.url) == initial_chat_id and self._answer_locator(page) is not None:
                raise DeepSeekWebError("new_chat_failed", "新建对话后页面仍停留在旧会话", retryable=False)
            trace.append({"event": "new_chat_clicked", "at": time.time()})
        self._composer(page)
        trace.append({"event": "new_chat_ready", "at": time.time()})

    @staticmethod
    def _toggle_active(locator) -> bool:
        for attr in ("aria-pressed", "aria-checked"):
            if str(locator.get_attribute(attr) or "").lower() == "true":
                return True
        if str(locator.get_attribute("data-state") or "").lower() in {"on", "checked", "active", "selected"}:
            return True
        classes = str(locator.get_attribute("class") or "").lower().split()
        active_markers = set(filter(None, os.environ.get("DEEPSEEK_WEB_SEARCH_ACTIVE_CLASSES", "active selected checked").lower().split()))
        return bool(active_markers.intersection(classes))

    def _enable_search(self, page, trace: list[dict[str, Any]]) -> None:
        toggle = self._search_toggle(page)
        if not self._toggle_active(toggle):
            toggle.click()
            trace.append({"event": "search_toggle_clicked", "at": time.time()})
        page.wait_for_timeout(500)
        toggle = self._search_toggle(page)
        if not self._toggle_active(toggle):
            raise DeepSeekWebError(
                "search_state_unknown",
                "联网搜索开关已操作，但页面未暴露可验证的启用状态；请更新 selector contract",
                retryable=False,
            )
        trace.append({"event": "search_enabled", "at": time.time()})

    @staticmethod
    def _answer_locator(page):
        override = os.environ.get("DEEPSEEK_WEB_ANSWER_SELECTOR", "").strip()
        selectors = [override, "[data-testid='message-content']", ".ds-markdown", "[class*='markdown']"]
        best = None
        best_length = 0
        for selector in selectors:
            if not selector:
                continue
            locator = page.locator(selector)
            for index in range(locator.count()):
                item = locator.nth(index)
                if not item.is_visible():
                    continue
                text = item.inner_text().strip()
                if len(text) >= best_length:
                    best = item
                    best_length = len(text)
        return best

    @staticmethod
    def _is_generating(page) -> bool:
        buttons = page.get_by_role("button", name=re.compile("停止|Stop", re.I))
        return any(buttons.nth(index).is_visible() for index in range(buttons.count()))

    def _wait_for_answer(self, page) -> tuple[Any, str]:
        deadline = time.monotonic() + self.config.response_timeout_seconds
        stable_since = 0.0
        previous = ""
        answer_locator = None
        while time.monotonic() < deadline:
            blocked = self._blocked_page(page)
            if blocked:
                raise DeepSeekWebError(blocked, "DeepSeek 页面要求登录或安全验证", retryable=False)
            answer_locator = self._answer_locator(page)
            current = answer_locator.inner_text().strip() if answer_locator is not None else ""
            generating = self._is_generating(page)
            if current and current == previous and not generating:
                stable_since = stable_since or time.monotonic()
                if time.monotonic() - stable_since >= self.config.stable_seconds:
                    return answer_locator, current
            else:
                stable_since = 0.0
                previous = current
            page.wait_for_timeout(1000)
        raise DeepSeekWebError("response_timeout", "等待 DeepSeek 网页回答超时", retryable=True)

    @staticmethod
    def _citations(answer_locator) -> list[dict[str, str]]:
        citations: list[dict[str, str]] = []
        seen: set[str] = set()
        links = answer_locator.locator("a[href]")
        for index in range(links.count()):
            link = links.nth(index)
            url = str(link.get_attribute("href") or "").strip()
            if not url.startswith(("http://", "https://")) or url in seen:
                continue
            seen.add(url)
            citations.append({"url": url, "title": link.inner_text().strip() or str(link.get_attribute("title") or "")})
        return citations

    @staticmethod
    def _prompt_occurrences(page, question: str) -> int:
        override = os.environ.get("DEEPSEEK_WEB_USER_MESSAGE_SELECTOR", "").strip()
        if override:
            messages = page.locator(override)
            return sum(1 for index in range(messages.count()) if messages.nth(index).inner_text().strip() == question)
        main = page.locator("main, [role='main']")
        if main.count():
            return main.get_by_text(question, exact=True).count()
        return min(page.get_by_text(question, exact=True).count(), 1)

    @staticmethod
    def _network_events(responses: list[Any]) -> list[dict[str, Any]]:
        events = []
        body_paths = {
            "/api/v0/chat_session/create",
            "/api/v0/chat/create_pow_challenge",
            "/api/v0/chat/completion",
        }
        for response in responses:
            url = str(response.url or "")
            if "deepseek.com" not in url:
                continue
            content_type = str(response.headers.get("content-type") or "")
            if not any(marker in content_type for marker in ("json", "text", "event-stream")):
                continue
            try:
                body = response.body().decode("utf-8", errors="replace")[:10_000_000]
            except Exception:
                body = ""
            if urlsplit(url).path not in body_paths:
                body = ""
            events.append(sanitize_value({"url": sanitize_url(url), "status": response.status, "content_type": content_type, "body": body}))
        return events

    def _artifact_dir(self, batch_id: str, task_id: str) -> Path:
        path = self.config.artifact_root / batch_id / task_id
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            path.chmod(0o700)
        except OSError:
            pass
        return path

    @staticmethod
    def _artifact_reference(path: Path) -> str:
        try:
            return str(path.relative_to(ROOT))
        except ValueError:
            return str(path)

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        path.write_text(json.dumps(sanitize_value(value), ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def sample(self, *, batch_id: str, task_id: str, question: str) -> dict[str, Any]:
        artifact_dir = self._artifact_dir(batch_id, task_id)
        context = None
        page = None
        responses: list[Any] = []
        trace: list[dict[str, Any]] = [{"event": "started", "at": time.time()}]
        started = time.monotonic()
        result: dict[str, Any] = {}
        error: DeepSeekWebError | None = None
        try:
            context = self._new_context()
            page = context.new_page()
            page.set_default_timeout(self.config.navigation_timeout_ms)
            page.on("response", lambda response: responses.append(response))
            page.goto(self.config.chat_url, wait_until="domcontentloaded")
            trace.append({"event": "page_opened", "at": time.time(), "url": sanitize_url(page.url)})
            blocked = self._blocked_page(page)
            if blocked:
                raise DeepSeekWebError(blocked, "DeepSeek 页面要求登录或安全验证", retryable=False)
            self._open_new_chat(page, trace)
            composer = self._composer(page)
            self._enable_search(page, trace)
            composer.fill(question)
            trace.append({"event": "question_filled", "at": time.time(), "length": len(question)})
            composer.press("Enter")
            trace.append({"event": "question_submitted", "at": time.time()})
            answer_locator, answer = self._wait_for_answer(page)
            network = self._network_events(responses)
            chat_id = extract_chat_id(page.url, network)
            if not chat_id:
                raise DeepSeekWebError("chat_id_missing", "未能确认独立 DeepSeek chat_id", retryable=False)
            exact_prompt_count = self._prompt_occurrences(page, question)
            if exact_prompt_count != 1:
                raise DeepSeekWebError("session_not_isolated", f"当前会话中问题文本出现 {exact_prompt_count} 次", retryable=False)
            captured_network_answer = network_answer(network)
            matched, match_ratio = answers_match(answer, captured_network_answer)
            if not matched:
                raise DeepSeekWebError("capture_mismatch", f"DOM 与网络流回答不一致，ratio={match_ratio:.3f}", retryable=False)
            citations = self._citations(answer_locator)
            result = {
                "response_text": answer,
                "citations": citations,
                "chat_id": chat_id,
                "network_match_ratio": match_ratio,
                "network_answer_available": bool(captured_network_answer),
                "latency_ms": int((time.monotonic() - started) * 1000),
                "artifact_dir": self._artifact_reference(artifact_dir),
                "network": network,
            }
            trace.append({"event": "completed", "at": time.time(), "chat_id": chat_id})
        except DeepSeekWebError as exc:
            error = exc
        except Exception as exc:
            error = DeepSeekWebError("browser_error", str(exc), retryable=True)
        finally:
            network = result.get("network") or self._network_events(responses)
            if page is not None:
                try:
                    (artifact_dir / "page.html").write_text(str(sanitize_value(page.content())), encoding="utf-8")
                    (artifact_dir / "page.html").chmod(0o600)
                except Exception:
                    pass
                try:
                    page.screenshot(path=str(artifact_dir / "final.png"), full_page=True)
                    (artifact_dir / "final.png").chmod(0o600)
                except Exception:
                    pass
            self._write_json(artifact_dir / "network.json", network)
            self._write_json(artifact_dir / "trace.json", trace)
            self._write_json(
                artifact_dir / "metadata.json",
                {
                    "batch_id": batch_id,
                    "task_id": task_id,
                    "question": question,
                    "url": sanitize_url(page.url) if page is not None else self.config.chat_url,
                    "browser": "chromium",
                    "locale": "zh-CN",
                    "timezone": "Asia/Shanghai",
                    "viewport": [self.config.viewport_width, self.config.viewport_height],
                    "error_code": error.code if error else "",
                    "error_message": str(error) if error else "",
                },
            )
            if result:
                self._write_json(artifact_dir / "answer.json", {key: value for key, value in result.items() if key != "network"})
                self._write_json(artifact_dir / "citations.json", result.get("citations", []))
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
        if error:
            error.artifact_dir = self._artifact_reference(artifact_dir)
            raise error
        result.pop("network", None)
        return result

    def preflight(self) -> dict[str, Any]:
        context = None
        try:
            context = self._new_context()
            page = context.new_page()
            page.set_default_timeout(self.config.navigation_timeout_ms)
            page.goto(self.config.chat_url, wait_until="domcontentloaded")
            blocked = self._blocked_page(page)
            if blocked:
                raise DeepSeekWebError(blocked, "DeepSeek 页面要求登录或安全验证", retryable=False)
            self._composer(page)
            toggle = self._search_toggle(page)
            return {"ok": True, "search_toggle_visible": toggle.is_visible(), "url": self.config.chat_url}
        finally:
            if context is not None:
                context.close()


_BROWSER: DeepSeekWebBrowser | None = None


def get_deepseek_web_browser() -> DeepSeekWebBrowser:
    global _BROWSER
    if _BROWSER is None:
        _BROWSER = DeepSeekWebBrowser()
    return _BROWSER


def close_deepseek_web_browser() -> None:
    global _BROWSER
    if _BROWSER is not None:
        _BROWSER.close()
        _BROWSER = None


atexit.register(close_deepseek_web_browser)
