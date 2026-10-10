"""Browser automation via Playwright.

A single persistent Chromium context is lazily created; automation runs on a
dedicated asyncio loop in a worker thread. Sensitive actions (purchases,
account changes) are mapped to high-risk tools by the permission system.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from jarvis.config.settings import DATA_DIR
from jarvis.tools.tool_registry import Tool, ToolRegistry

log = logging.getLogger("jarvis.tools.browser")

USER_DATA_DIR = DATA_DIR / "browser_profile"

_loop: Optional[asyncio.AbstractEventLoop] = None
_thread: Optional[threading.Thread] = None
_playwright: Any = None
_browser_ctx: Any = None
_playwright_available: Optional[bool] = None


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _loop, _thread
    if _loop is not None and _loop.is_running():
        return _loop
    ready = threading.Event()

    def _run(loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        ready.set()
        loop.run_forever()

    _loop = asyncio.new_event_loop()
    _thread = threading.Thread(target=_run, args=(_loop,), daemon=True, name="jarvis-browser")  # daemon: never blocks exit
    _thread.start()
    ready.wait(timeout=5)
    return _loop


def _run_on_browser_loop(coro, timeout: float = 60.0):
    loop = _ensure_loop()
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    return fut.result(timeout=timeout)


async def _ensure_context():
    global _playwright, _browser_ctx, _playwright_available
    if _browser_ctx is not None:
        return _browser_ctx
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        _playwright_available = False
        raise RuntimeError(
            "Playwright is not installed. Run: pip install playwright && playwright install chromium")
    _playwright = await async_playwright().start()
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    _browser_ctx = await _playwright.chromium.launch_persistent_context(
        str(USER_DATA_DIR), headless=False,
        viewport={"width": 1280, "height": 800},
        args=["--disable-blink-features=AutomationControlled"],
    )
    _playwright_available = True
    return _browser_ctx


def playwright_available() -> bool:
    global _playwright_available
    if _playwright_available is None:
        try:
            import playwright  # noqa: F401
            _playwright_available = True
        except ImportError:
            _playwright_available = False
    return _playwright_available


async def _open_url(url: str) -> Dict[str, Any]:
    ctx = await _ensure_context()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    title = await page.title()
    return {"url": page.url, "title": title}


def open_url(url: str) -> Dict[str, Any]:
    tool = "open_url"
    try:
        info = _run_on_browser_loop(_open_url(url), timeout=45)
        return {"success": True, "tool": tool,
                "result": {"url": info["url"], "title": info["title"]}}
    except RuntimeError as exc:
        return {"success": False, "tool": tool, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        log.exception("open_url failed")
        return {"success": False, "tool": tool, "error": f"{type(exc).__name__}: {exc}"}


def open_browser(start_url: str = "https://www.google.com") -> Dict[str, Any]:
    return open_url(start_url)


async def _search_web(query: str) -> Dict[str, Any]:
    ctx = await _ensure_context()
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    url = f"https://duckduckgo.com/?q={query.replace(' ', '+')}"
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    try:
        await page.wait_for_selector("article", timeout=8000)
    except Exception:  # noqa: BLE001
        pass
    results = await page.evaluate(
        """() => Array.from(document.querySelectorAll('article')).slice(0, 8).map(a => {
            const link = a.querySelector('a');
            const snippet = a.querySelector('div span');
            return {
                title: link ? link.innerText.split('\\n')[0] : '',
                url: link ? link.href : '',
                snippet: snippet ? snippet.innerText : ''
            };
        })"""
    )
    return {"url": page.url, "query": query, "results": results}


def search_web(query: str) -> Dict[str, Any]:
    tool = "search_web"
    try:
        info = _run_on_browser_loop(_search_web(query), timeout=60)
        return {"success": True, "tool": tool, "result": info}
    except RuntimeError as exc:
        return {"success": False, "tool": tool, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        log.exception("search_web failed")
        return {"success": False, "tool": tool, "error": f"{type(exc).__name__}: {exc}"}


async def _click(selector: str, text: Optional[str], index: int) -> Dict[str, Any]:
    ctx = await _ensure_context()
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    if text:
        locator = page.get_by_text(text, exact=False).first
        await locator.click(timeout=8000)
        return {"clicked": text}
    if selector:
        await page.locator(selector).nth(max(0, index)).click(timeout=8000)
        return {"clicked": selector}
    raise ValueError("Provide selector or text")


def click(selector: str = "", text: str = "", index: int = 0) -> Dict[str, Any]:
    tool = "click"
    try:
        info = _run_on_browser_loop(_click(selector, text or None, index), timeout=30)
        return {"success": True, "tool": tool, "result": info}
    except RuntimeError as exc:
        return {"success": False, "tool": tool, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "tool": tool, "error": f"{type(exc).__name__}: {exc}"}


async def _type_text(selector: str, text: str, submit: bool) -> Dict[str, Any]:
    ctx = await _ensure_context()
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    loc = page.locator(selector).first if selector else page.locator("textarea, input[type=text]").first
    await loc.click(timeout=8000)
    await loc.fill(text)
    if submit:
        await loc.press("Enter")
    return {"typed": text, "submitted": submit}


def type_text(selector: str, text: str, submit: bool = False) -> Dict[str, Any]:
    tool = "type"
    try:
        info = _run_on_browser_loop(_type_text(selector, text, submit), timeout=30)
        return {"success": True, "tool": tool, "result": info}
    except RuntimeError as exc:
        return {"success": False, "tool": tool, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "tool": tool, "error": f"{type(exc).__name__}: {exc}"}


async def _scroll(direction: str, amount: int) -> Dict[str, Any]:
    ctx = await _ensure_context()
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    if direction == "down":
        await page.mouse.wheel(0, amount)
    else:
        await page.mouse.wheel(0, -amount)
    return {"scrolled": direction}


def scroll(direction: str = "down", amount: int = 600) -> Dict[str, Any]:
    tool = "scroll"
    try:
        info = _run_on_browser_loop(_scroll(direction, amount), timeout=20)
        return {"success": True, "tool": tool, "result": info}
    except RuntimeError as exc:
        return {"success": False, "tool": tool, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "tool": tool, "error": f"{type(exc).__name__}: {exc}"}


async def _extract_text(max_chars: int) -> Dict[str, Any]:
    ctx = await _ensure_context()
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    body = await page.inner_text("body")
    return {"url": page.url, "title": await page.title(), "text": body[:max_chars]}


def extract_text(max_chars: int = 4000) -> Dict[str, Any]:
    tool = "extract_text"
    try:
        info = _run_on_browser_loop(_extract_text(max_chars), timeout=30)
        return {"success": True, "tool": tool, "result": info}
    except RuntimeError as exc:
        return {"success": False, "tool": tool, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "tool": tool, "error": f"{type(exc).__name__}: {exc}"}


async def _download(url: str, filename: str) -> Dict[str, Any]:
    ctx = await _ensure_context()
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    downloads_dir = DATA_DIR / "downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)
    target = downloads_dir / (filename or f"download_{int(time.time())}")
    async with page.expect_download(timeout=60000) as dl_info:
        await page.goto(url)
    download = await dl_info.value
    await download.save_as(str(target))
    return {"path": str(target), "suggested_filename": download.suggested_filename}


def download_file(url: str, filename: str = "") -> Dict[str, Any]:
    tool = "download_file"
    try:
        info = _run_on_browser_loop(_download(url, filename), timeout=90)
        return {"success": True, "tool": tool, "result": info}
    except RuntimeError as exc:
        return {"success": False, "tool": tool, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "tool": tool, "error": f"{type(exc).__name__}: {exc}"}


def browser_status() -> Dict[str, Any]:
    return {
        "success": True, "tool": "browser_status",
        "result": {
            "playwright_installed": playwright_available(),
            "context_open": _browser_ctx is not None,
        },
    }


def register(registry: ToolRegistry) -> None:
    registry.register(Tool(
        name="open_url", description="Open a URL in the automated browser.",
        args_schema={"type": "object", "properties": {
            "url": {"type": "string", "description": "URL to open"},
        }, "required": ["url"]},
        executor_fn=open_url, risk="safe", category="browser", timeout_s=60,
    ))
    registry.register(Tool(
        name="open_browser", description="Open the automated browser window.",
        args_schema={"type": "object", "properties": {
            "start_url": {"type": "string"},
        }, "required": []},
        executor_fn=open_browser, risk="safe", category="browser", timeout_s=60,
    ))
    registry.register(Tool(
        name="search_web",
        description="Search the web with DuckDuckGo and return top results.",
        args_schema={"type": "object", "properties": {
            "query": {"type": "string"},
        }, "required": ["query"]},
        executor_fn=search_web, risk="safe", category="browser", timeout_s=70,
    ))
    registry.register(Tool(
        name="click", description="Click an element in the automated browser by text or CSS selector.",
        args_schema={"type": "object", "properties": {
            "selector": {"type": "string"}, "text": {"type": "string"},
            "index": {"type": "integer", "minimum": 0},
        }, "required": []},
        executor_fn=click, risk="confirm", category="browser", timeout_s=40,
        confirmation_title="Browser click",
        confirmation_description="JARVIS will click an element in the automated browser.",
    ))
    registry.register(Tool(
        name="type", description="Type text into a page field in the automated browser.",
        args_schema={"type": "object", "properties": {
            "selector": {"type": "string"}, "text": {"type": "string"},
            "submit": {"type": "boolean"},
        }, "required": ["text"]},
        executor_fn=type_text, risk="confirm", category="browser", timeout_s=40,
        confirmation_title="Browser typing",
        confirmation_description="JARVIS will type text into the automated browser page.",
    ))
    registry.register(Tool(
        name="scroll", description="Scroll the automated browser page.",
        args_schema={"type": "object", "properties": {
            "direction": {"type": "string", "enum": ["up", "down"]},
            "amount": {"type": "integer", "minimum": 50, "maximum": 5000},
        }, "required": []},
        executor_fn=scroll, risk="safe", category="browser", timeout_s=30,
    ))
    registry.register(Tool(
        name="extract_text", description="Extract visible text from the current browser page.",
        args_schema={"type": "object", "properties": {
            "max_chars": {"type": "integer", "minimum": 200, "maximum": 20000},
        }, "required": []},
        executor_fn=extract_text, risk="safe", category="browser", timeout_s=40,
    ))
    registry.register(Tool(
        name="download_file", description="Download a file from a URL into the Downloads folder.",
        args_schema={"type": "object", "properties": {
            "url": {"type": "string"}, "filename": {"type": "string"},
        }, "required": ["url"]},
        executor_fn=download_file, risk="confirm", category="browser", timeout_s=120,
        confirmation_title="Download file",
        confirmation_description="A file will be downloaded from the internet.",
    ))
