"""Terminal command policy: risk classification and safety rules.

The LLM never receives a raw shell. It submits a command string; this module
classifies it, and the permission manager decides whether to run it.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import List, Optional

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

_HIGH_RISK = [
    (r"\b(shutdown|restart(-computer)?|restart-computer|logoff)\b", "shutdown/restart/logoff"),
    (r"\b(format|format-volume)\b", "disk formatting"),
    (r"\bdel\b.*[/\\][sS]\b", "recursive delete (del /s)"),
    (r"\brmdir\b.*[/\\][sS]\b", "recursive rmdir"),
    (r"\brm\b\s+(-[a-zA-Z]*[rR][a-zA-Z]*[fF][a-zA-Z]*|-[a-zA-Z]*[fF][a-zA-Z]*[rR])", "rm -rf"),
    (r"\brm\b\s+-[a-zA-Z]*r", "rm -r"),
    (r"\b(remove-item)\b.*-recurse", "Remove-Item -Recurse"),
    (r"\b(rd)\b\s+/s", "rd /s"),
    (r"\bdiskpart\b", "diskpart"),
    (r"\bbcdedit\b", "boot configuration"),
    (r"\breg\s+(delete|add)\b", "registry modification"),
    (r"\bregedit\b", "registry editor"),
    (r"\bset- executionpolicy\b|\bset-executionpolicy\b", "execution policy change"),
    (r"\bvssadmin\b", "shadow copies"),
    (r"\bwevtutil\s+cl\b", "event log clearing"),
    (r"\bschtasks\b", "scheduled tasks"),
    (r"\bsc(\.exe)?\s+(delete|config|stop)\b", "service modification"),
    (r"\bnet\s+(user|localgroup)\b", "user/group management"),
    (r"\bnetsh\b", "network stack changes"),
    (r"\btaskkill\b.*[/\\][fF]\b", "force kill"),
    (r"\bpkill\b|\bkillall\b", "process killing"),
    (r"\bcipher\b.*[/\\][wW]\b", "secure wipe"),
    (r"/dev/sd[a-z]", "raw disk write"),
    (r"\bmkfs\b", "filesystem creation"),
    (r"\bdd\b\s+if=", "raw disk copy"),
    (r"\bwheel\b|\bsudo\b", "privilege escalation"),
    (r"\brunas\b", "run as another user"),
    (r"\bstart-process\b.*-verb\s+runas", "elevated start"),
    (r"\bgit\s+push\s+.*--force\b", "force push"),
    (r"\bgit\s+reset\s+--hard\b", "git hard reset"),
    (r"\bdrop\s+(table|database)\b", "database destruction"),
    (r"\bxc?copy\b.*[/\\][yY]\b.*\bC:\\Windows\b", "overwrite Windows"),
    (r"\b.windows\\\\?system32\b", "system32 modification"),
    (r"system32", "system32 reference"),
    (r"\bsyskey\b", "syskey"),
    (r"\bwpeutil\b", "winpe"),
]

_MEDIUM_RISK = [
    (r"\b(npm|pnpm|yarn|bun)\s+(install|i|add)\b", "package installation"),
    (r"\bpip3?\s+install\b", "python package installation"),
    (r"\bwinget\s+install\b", "winget installation"),
    (r"\bcurl\b.*(-o|--output|>[^\s])", "file download via curl"),
    (r"\bwget\b", "wget download"),
    (r"\binvoke-webrequest\b", "PowerShell download"),
    (r"\bgit\s+(clone|pull|push|checkout)\b", "git network/branch operation"),
    (r"\bdel\b|\berase\b", "file delete"),
    (r"\bremove-item\b", "PowerShell remove"),
    (r"\bmv\b|\bmove\b|\bmove-item\b", "move operation"),
    (r"\bcurl\b", "network transfer"),
]

# Commands considered LOW risk by default (development friendly)
_LOW_SAFE_FIRST = [
    "npm run", "npm test", "npm start", "npx ", "node ", "python ", "python3 ",
    "pip list", "pip show", "git status", "git log", "git diff", "git branch",
    "dir ", "cd ", "echo ", "type ", "where ", "whoami", "systeminfo",
    "tasklist", "ipconfig", "ping ", "ver", "hostname", "cls", "tree ",
    "dotnet --", "cargo build", "cargo test", "go build", "go test",
    "ls", "pwd", "cat ", "head ", "tail ", "wc ", "findstr ",
    "code --", "pytest", "ruff", "mypy", "prettier", "eslint",
]

_BLOCKED_ALWAYS = [
    (r"\bdel\b.*[/\\][qQ]\s+(?:[/\\][sS]\s+)?[a-zA-Z]:\\\s*$", ""),
    (r"format\s+[a-zA-Z]:", "drive format"),
    (r"[:;|]\s*rm\s+-rf\s+/", "root rm"),
    (r"rm\s+-rf\s+/(?:\s|$)", "root rm"),
    (r">\s*[a-zA-Z]:\\?Windows", "overwrite Windows files"),
    (r"\bshutdown\b", "shutdown"),
]

_INJECTION_PATTERNS = [
    (r"\$\(\s*.*\)", "command substitution $()"),
    (r"`[^`]+`", "backtick substitution"),
]


@dataclass
class PolicyDecision:
    risk: str                    # "low" | "medium" | "high" | "blocked"
    reasons: List[str] = field(default_factory=list)
    requires_confirmation: bool = False
    blocked: bool = False
    explanation: str = ""

    def to_dict(self) -> dict:
        return {
            "risk": self.risk,
            "reasons": self.reasons,
            "requires_confirmation": self.requires_confirmation,
            "blocked": self.blocked,
            "explanation": self.explanation,
        }


def classify(command: str) -> PolicyDecision:
    """Classify a command string into a policy decision."""
    cmd = (command or "").strip()
    if not cmd:
        return PolicyDecision(risk="blocked", blocked=True, explanation="Empty command")

    lowered = cmd.lower()

    # 1. Always blocked patterns
    for pattern, reason in _BLOCKED_ALWAYS:
        if pattern and re.search(pattern, lowered):
            return PolicyDecision(
                risk="blocked", blocked=True,
                reasons=[reason or pattern],
                explanation=f"Command is always blocked by policy ({reason}).",
            )

    # 2. High-risk detection
    high_hits: List[str] = []
    for pattern, reason in _HIGH_RISK:
        if reason is None:
            continue
        if re.search(pattern, lowered):
            high_hits.append(reason)

    # 3. Injection heuristics (double execution tricks)
    injection: List[str] = []
    # multiple statements chained
    separators = re.findall(r"&&|\|\||;|\|", cmd)
    if len(separators) >= 2:
        injection.append("multiple chained statements")

    # 4. Medium risk
    medium_hits: List[str] = []
    for pattern, reason in _MEDIUM_RISK:
        if re.search(pattern, lowered):
            medium_hits.append(reason)

    if high_hits:
        return PolicyDecision(
            risk="high",
            reasons=high_hits + injection,
            requires_confirmation=True,
            explanation="High-risk operation; explicit user approval required.",
        )

    if injection:
        return PolicyDecision(
            risk="medium",
            reasons=medium_hits + injection,
            requires_confirmation=True,
            explanation="Command contains chained statements that need review.",
        )

    if medium_hits:
        return PolicyDecision(
            risk="medium",
            reasons=medium_hits,
            requires_confirmation=True,
            explanation="Potentially impactful operation; confirmation recommended.",
        )

    # 5. Low-risk whitelist prefix
    for prefix in _LOW_SAFE_FIRST:
        if lowered.startswith(prefix):
            return PolicyDecision(
                risk="low",
                reasons=[f"matches safe prefix '{prefix.strip()}'"],
                requires_confirmation=False,
                explanation="Recognised safe development command.",
            )

    # Unknown command -> medium risk, confirm first
    return PolicyDecision(
        risk="medium",
        reasons=["unrecognised command"],
        requires_confirmation=True,
        explanation="Command not on the known-safe list; confirmation required.",
    )


def is_allowed_without_confirmation(command: str) -> bool:
    decision = classify(command)
    if decision.blocked:
        return False
    if decision.risk == "low":
        return True
    from jarvis.config.settings import get_settings
    if decision.risk == "medium" and get_settings().get("permissions.auto_approve_low_risk_terminal", False):
        return True
    return False
