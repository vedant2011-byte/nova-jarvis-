"""Screenshot capture using the real Windows API (no external monitor libs).

Saves PNG files under data/screenshots and returns metadata for the UI.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
from pathlib import Path
from typing import Any, Dict

from jarvis.config.settings import DATA_DIR
from jarvis.tools.tool_registry import Tool, ToolRegistry

log = logging.getLogger("jarvis.tools.screenshot")

SHOT_DIR = DATA_DIR / "screenshots"


def take_screenshot() -> Dict[str, Any]:
    tool = "take_screenshot"
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("jarvis_%Y%m%d_%H%M%S")
    out_path = SHOT_DIR / f"{stamp}.png"

    try:
        from PIL import ImageGrab  # type: ignore
    except ImportError:
        return {"success": False, "tool": tool,
                "error": "Pillow not installed. Run: pip install Pillow"}

    try:
        img = ImageGrab.grab(all_screens=True)
        img.save(out_path, "PNG")
    except OSError as exc:
        return {"success": False, "tool": tool, "error": f"Screenshot failed: {exc}"}

    size_kb = round(out_path.stat().st_size / 1024, 1)
    log.info("screenshot saved %s", out_path)
    return {
        "success": True,
        "tool": tool,
        "result": {
            "path": str(out_path),
            "width": img.width,
            "height": img.height,
            "size_kb": size_kb,
        },
    }


def register(registry: ToolRegistry) -> None:
    registry.register(Tool(
        name="take_screenshot",
        description="Capture a full-screen screenshot and save it locally as PNG.",
        args_schema={"type": "object", "properties": {}, "required": []},
        executor_fn=take_screenshot,
        risk="safe", category="system", timeout_s=20,
    ))
