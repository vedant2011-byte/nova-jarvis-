"""Permission manager: decides whether a tool call may run automatically.

Risk levels:
  LEVEL 1 (safe)      -> auto-execute when auto_approve_safe is on
  LEVEL 2 (confirm)   -> requires user confirmation
  LEVEL 3 (high risk) -> always requires explicit confirmation
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from jarvis.config.settings import get_settings
from jarvis.security import command_policy
from jarvis.security.validator import PathValidationError

log = logging.getLogger("jarvis.permissions")


class RiskLevel(str, Enum):
    SAFE = "safe"
    CONFIRM = "confirm"
    HIGH_RISK = "high-risk"
    BLOCKED = "blocked"


# Tools that are inherently safe
SAFE_TOOLS = {
    "open_application", "focus_application", "list_directory", "search_files",
    "read_file", "get_system_info", "get_cpu_usage", "get_memory_usage",
    "get_disk_usage", "get_battery_status", "get_network_status",
    "get_current_time", "list_running_applications", "take_screenshot",
    "open_url", "open_browser", "search_web", "extract_text", "save_memory",
    "retrieve_memory", "search_memory", "create_folder", "list_tools",
    "scroll",
}

# Tools that always need confirmation
CONFIRM_TOOLS = {
    "create_file", "edit_file", "copy_file", "move_file", "download_file",
    "click", "type", "scroll", "run_terminal_command",  # terminal has own policy
}

# Tools that are high risk (always confirm, some blocked outright)
HIGH_RISK_TOOLS = {
    "delete_file", "system_shutdown", "system_restart", "system_lock",
}


@dataclass
class PermissionDecision:
    allowed: bool
    requires_confirmation: bool
    risk: RiskLevel
    reason: str
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "requires_confirmation": self.requires_confirmation,
            "risk": self.risk.value,
            "reason": self.reason,
            "detail": self.detail,
        }


def classify_tool_call(tool: str, args: Dict[str, Any]) -> PermissionDecision:
    """Return a decision for a (tool, args) pair without executing it."""
    settings = get_settings()

    if tool in HIGH_RISK_TOOLS:
        if tool in ("system_shutdown", "system_restart"):
            return PermissionDecision(
                allowed=True, requires_confirmation=True,
                risk=RiskLevel.HIGH_RISK,
                reason="System power action requires explicit approval.",
            )
        if tool == "delete_file":
            path = str(args.get("path", ""))
            return PermissionDecision(
                allowed=True, requires_confirmation=True,
                risk=RiskLevel.HIGH_RISK,
                reason="File deletion requires explicit approval.",
                detail={"path": path},
            )
        return PermissionDecision(
            allowed=True, requires_confirmation=True,
            risk=RiskLevel.HIGH_RISK,
            reason="High-risk operation requires explicit approval.",
        )

    if tool == "run_terminal_command":
        cmd = str(args.get("command", ""))
        policy = command_policy.classify(cmd)
        if policy.blocked:
            return PermissionDecision(
                allowed=False, requires_confirmation=False,
                risk=RiskLevel.BLOCKED,
                reason=policy.explanation or "Command blocked by policy.",
                detail=policy.to_dict(),
            )
        if policy.risk == "high":
            return PermissionDecision(
                allowed=True, requires_confirmation=True,
                risk=RiskLevel.HIGH_RISK,
                reason=policy.explanation,
                detail=policy.to_dict(),
            )
        if policy.risk == "medium":
            auto = bool(settings.get("permissions.auto_approve_low_risk_terminal", False))
            return PermissionDecision(
                allowed=True, requires_confirmation=not auto,
                risk=RiskLevel.CONFIRM,
                reason=policy.explanation,
                detail=policy.to_dict(),
            )
        # low risk
        if settings.get("permissions.auto_approve_safe", True):
            return PermissionDecision(
                allowed=True, requires_confirmation=False,
                risk=RiskLevel.SAFE,
                reason=policy.explanation or "Safe command.",
                detail=policy.to_dict(),
            )
        return PermissionDecision(
            allowed=True, requires_confirmation=True,
            risk=RiskLevel.SAFE,
            reason="Auto-approve is disabled in settings.",
            detail=policy.to_dict(),
        )

    if tool in SAFE_TOOLS:
        if settings.get("permissions.auto_approve_safe", True):
            return PermissionDecision(
                allowed=True, requires_confirmation=False,
                risk=RiskLevel.SAFE, reason="Safe tool, auto-approved.",
            )
        return PermissionDecision(
            allowed=True, requires_confirmation=True,
            risk=RiskLevel.SAFE, reason="Auto-approve disabled in settings.",
        )

    if tool in CONFIRM_TOOLS:
        # open_application / focus / close are special-cased below
        if tool in ("create_file", "edit_file"):
            if not settings.get("permissions.confirm_edits", True):
                return PermissionDecision(
                    allowed=True, requires_confirmation=False,
                    risk=RiskLevel.SAFE, reason="Edit confirmations disabled by user.",
                )
        return PermissionDecision(
            allowed=True, requires_confirmation=True,
            risk=RiskLevel.CONFIRM, reason=f"{tool} modifies state; confirmation required.",
        )

    # Unknown tool -> treat as needing confirmation (defence in depth)
    return PermissionDecision(
        allowed=True, requires_confirmation=True,
        risk=RiskLevel.CONFIRM, reason=f"Unknown tool '{tool}'; confirmation required.",
    )


def decide(tool: str, args: Dict[str, Any]) -> PermissionDecision:
    """Main entry point used by the executor."""
    decision = classify_tool_call(tool, args)
    log.info("permission tool=%s risk=%s confirm=%s reason=%s",
             tool, decision.risk.value, decision.requires_confirmation, decision.reason)
    return decision
