# JARVIS — Local-First AI Voice Computer Agent for Windows

A real, functional personal AI assistant you control with your voice. Everything
runs locally: speech recognition, language model, text-to-speech, memory. No paid
APIs, no cloud dependency for core features.

**VOICE → SPEECH RECOGNITION → LOCAL AI → PLANNER → TOOL SELECTION → PERMISSION →
COMPUTER ACTION → VERIFICATION → VOICE RESPONSE**

Every layer actually talks to the next one. Nothing is simulated: if JARVIS says
Chrome opened, it verified the process is running; if it says a folder was created,
the folder exists.

---

## Features

- **Voice input** — faster-whisper local STT, wake word ("Jarvis"), push-to-talk,
  voice activity detection, silence detection, microphone selection and test.
- **Local AI brain** — Ollama-compatible runtime (any model you have installed,
  configurable in Settings). When the LLM is offline, a built-in rule-based intent
  engine keeps core computer control working fully offline.
- **Local TTS** — Piper when configured, Windows SAPI5 (pyttsx3) fallback, always
  available on Windows.
- **Computer control** — open/close/focus applications (with verification), file
  operations (search/read/create/edit/copy/move/delete with path safety), controlled
  terminal execution, Playwright browser automation, screenshots, real system stats.
- **Memory** — SQLite-backed: preferences, project locations, custom instructions,
  recent tasks. View and delete stored memories in Settings.
- **Security** — every tool call goes through schema validation → risk
  classification → permission manager → (confirmation for risky actions) →
  execution → verification. The LLM never gets arbitrary shell access.
- **Emergency STOP** — one button cancels the current task, pending confirmations,
  browser automation, and speech.
- **Cinematic HUD** — React + Canvas: AI core with state-dependent animation,
  live conversation, task progress, real system telemetry, full settings.

---

## Architecture

```
jarvis/
├── backend/          FastAPI server, WebSocket event bus, planner, app state
│   ├── server.py     HTTP + WS endpoints, static frontend hosting
│   ├── planner.py    Multi-step task planning and execution
│   ├── state.py      Shared application state
│   └── events/       Thread-safe event bus → UI
├── brain/            The AI
│   ├── llm.py        Ollama client (configurable model, offline detection)
│   ├── agent.py      Pipeline orchestrator: intent → tool → permission → result
│   ├── intent_engine.py  Offline rule-based fallback brain
│   ├── prompts.py    System prompt for the LLM (tool-call JSON schema)
│   ├── context.py    Conversation context assembly
│   └── tool_router.py    LLM JSON → validated tool call
├── voice/
│   ├── listener.py   Mic → VAD → wake word → STT pipeline thread
│   ├── audio.py      sounddevice capture, device enumeration (cached)
│   ├── vad.py        Energy-based voice activity detection
│   ├── wakeword.py   "Jarvis" wake word matching + sensitivity
│   ├── speech_to_text.py  faster-whisper wrapper (lazy model load)
│   └── text_to_speech.py  Piper + SAPI5 fallback, single worker thread
├── tools/            The controlled tool layer
│   ├── base.py, tool_registry.py   Registry: schema, risk, timeout, validation
│   ├── applications.py  open/close/focus/list apps (Start-Menu + PATH lookup)
│   ├── filesystem.py    Safe file ops (path validation, traversal protection)
│   ├── terminal.py      Managed processes with output monitoring, port detection
│   ├── browser.py       Playwright automation (visible window)
│   ├── screenshot.py    Full-screen capture
│   └── system.py        Real CPU/RAM/disk/battery/network/time via psutil
├── memory/           SQLite database + manager (memory, conversations, tasks)
├── security/
│   ├── validator.py      Path safety: normalization, traversal, protected dirs
│   ├── command_policy.py Terminal command risk classifier + block list
│   ├── permissions.py    Risk-level → auto/confirm/deny decisions
│   └── confirmation.py   Pending confirmation queue (UI + voice resolve)
├── config/settings.py    Layered config: defaults → settings.json → env vars
├── frontend/         React + TypeScript + Vite HUD
└── tests/            88 automated tests
```

### Permission levels

| Level | Behavior | Examples |
|---|---|---|
| SAFE | Auto-execute | open app, search files, create folder, open URL, screenshot, system info |
| CONFIRM | Ask first | edit file, copy/move, install packages, unfamiliar commands |
| HIGH-RISK | Always ask, never auto | delete files, shutdown, restart, bulk operations |

Terminal commands are classified: `npm run dev`, `git status`, `python script.py`
→ low risk; `npm install`, `pip install` → medium (confirmation by default);
`shutdown`, `rm -rf`, `format`, registry edits → high risk / blocked.

---

## Installation

### Prerequisites

- **Windows 10/11**
- **Python 3.10–3.13** — https://python.org (check "Add python.exe to PATH")
- **Node.js 18+** (frontend build only) — https://nodejs.org
- Optional, for full AI reasoning:
  - **Ollama** — https://ollama.com
  - A small model: `ollama pull llama3.2:3b` (runs well in 8 GB RAM)

### Option A — automated

```bat
scripts\setup.bat
```

(installs Python deps, builds the frontend, copies .env.example)

### Option B — manual

```bat
python -m pip install -r requirements.txt
cd frontend
npm install
npm run build
cd ..
copy .env.example .env
```

Optional voice quality upgrades:

```bat
python -m pip install faster-whisper     :: local speech recognition
python -m pip install piper-tts          :: local neural TTS (or configure in Settings)
```

Without faster-whisper the app still runs; voice input reports
"Microphone/STT engine unavailable" honestly instead of pretending.

### Local AI model

```bat
ollama pull llama3.2:3b
```

Then in JARVIS: **Settings → AI → Model** = `llama3.2:3b`. Any Ollama model works;
nothing is hard-coded. With Ollama offline, JARVIS switches to LOCAL MODE: direct
computer commands still work via the intent engine.

---

## Running JARVIS

```bat
scripts\start_jarvis.bat        :: or:
python -m uvicorn jarvis.backend.server:app --host 127.0.0.1 --port 8765
```
(run from the folder **above** `jarvis/`, or use the scripts)

Open **http://127.0.0.1:8765** — the HUD loads with a boot sequence, then
"SYSTEM READY".

- Enable **Settings → Voice → Listening** for wake-word mode ("Jarvis, open Chrome")
  or hold **Space** (push-to-talk) — click MIC first.
- Type in the command bar as a text fallback.

### Start with Windows

Settings → Startup → "Start JARVIS with Windows" (writes a per-user registry Run
entry; uncheck to remove).

---

## Voice commands (examples)

| Say | JARVIS does |
|---|---|
| "Jarvis, open Chrome." | Opens Chrome, verifies the process |
| "Open VS Code." | Opens Visual Studio Code |
| "Close Spotify." | Closes it, reports if it wasn't running |
| "What applications are running?" | Lists real running apps |
| "What's my RAM usage?" | Reads actual memory via psutil |
| "Take a screenshot." | Saves to data/screenshots, shows it in the HUD |
| "Find my resume." | Searches Documents/Desktop/Downloads |
| "Create a folder called Projects on my Desktop." | Creates it |
| "Delete the folder OldStuff on my Desktop." | **Asks for approval first** |
| "Open github.com" / "Search for free AI tools." | Browser automation (Playwright) |
| "Remember my Blackgrid project is on my Desktop." | Saves to memory |
| "What do you remember?" | Reads memory back |
| "Jarvis, stop." | Emergency stop |

Natural phrasings work: "Can you open Chrome?", "I need Chrome", "Launch my browser."

---

## Configuration

- **Settings UI** (gear icon): voice, AI model, wake word, memory, permissions,
  appearance (animation intensity), startup, system logs/diagnostics.
- **`data/settings.json`** — persisted settings (created on first run).
- **`.env`** — see `.env.example`: `JARVIS_DATA_DIR`, `JARVIS_HOST`, `JARVIS_PORT`,
  `JARVIS_OLLAMA_URL`, `JARVIS_MODEL`, `JARVIS_STT_MODEL`.
- Logs: `logs/jarvis.log` (+ diagnostics page in the UI). Credentials are never logged.

---

## Testing

```bat
cd jarvis
python -m pytest tests/ -q
```

Covers: tool schemas, permission decisions, command risk classification, path
traversal protection, memory, intent parsing, server endpoints. Current result:
**88 passed, 1 skipped**.

`docs/MANUAL_TEST_CHECKLIST.md` — the voice/tool/agent/security acceptance checklist
(the 10 acceptance workflows from the spec, all verified live).

---

## Troubleshooting

| Problem | Fix |
|---|---|
| "Module jarvis not found" on start | Run from the folder **above** `jarvis/`, or use `scripts\start_jarvis.bat` |
| No microphone devices | Check Windows privacy → Microphone access; pick device in Settings → Voice |
| Voice input says engine unavailable | `pip install faster-whisper` (downloads a model on first use) |
| AI engine OFFLINE in status | Install/start Ollama: `ollama serve`, then `ollama pull llama3.2:3b` |
| Browser tools fail | `python -m pip install playwright && python -m playwright install chromium` |
| Port 8765 busy | `set JARVIS_PORT=8766` or Settings → System |
| TTS silent | Windows SAPI5 fallback always exists; check volume + Settings → Voice |
| Delete says Access denied | OneDrive/AV transient lock — JARVIS retries automatically; try again |

## Known limitations

- Wake word is energy+text based (whisper transcribes the wake phrase); dedicated
  wake-word models (openWakeWord) can be added later — the interface is isolated
  in `voice/wakeword.py`.
- Screen-understanding is not implemented (screenshots are captured/saved/shown,
  not analyzed by the LLM).
- 8 GB RAM: use ≤3B-class models; STT uses the `base.en` whisper model by default.
- Browser automation targets Chromium via Playwright; first run downloads it.
- Windows-only (uses Windows APIs for app control, SAPI5 TTS, startup registration).
