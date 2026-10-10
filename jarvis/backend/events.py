"""Canonical event types emitted on the bus (documentation + consistency)."""
from __future__ import annotations

# State
STATE = "state"
BOOT = "boot"
BOOT_STEP = "boot_step"

# Voice
VOICE_STATE = "voice_state"
VOICE_ERROR = "voice_error"
VOICE_LEVEL = "voice_level"
PTT_STATE = "ptt_state"
STT_RESULT = "stt_result"
STT_IGNORED = "stt_ignored"
WAKE_DETECTED = "wake_detected"

# Conversation
CONVERSATION = "conversation"

# Agent / tools
TOOL_CALL = "tool_call"
TOOL_RESULT = "tool_result"
CONFIRMATION_REQUEST = "confirmation_request"
CONFIRMATION_RESOLVED = "confirmation_resolved"

# Tasks
TASK_STARTED = "task_started"
TASK_PROGRESS = "task_progress"
TASK_DONE = "task_done"
TASK_STOPPED = "task_stopped"

# System
ERROR = "error"
SYSTEM_STATS = "system_stats"
DEBUG_INFO = "debug_info"
