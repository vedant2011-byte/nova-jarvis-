# JARVIS — Full Voice Pipeline: Final Report

Milestone: **ENABLE FULL VOICE PIPELINE** (Phase 13 — live verification + report)
Date: 2026-10-07 · Machine: Windows 10/11, Python 3.13.9, 8 GB RAM

## 1. Constraints honored

No rebuild, no redesign. All existing tools, the risk/permission/confirmation
system, and flows were kept; only the voice pipeline wiring, two honest-state
fixes, and test-infrastructure corrections were touched. Every dangerous action
still requires explicit approval; nothing was auto-approved; all failures are
reported honestly.

## 2. Test suite (verified clean exit)

| Metric | Before | After |
|---|---|---|
| Tests | 88 baseline | **115 passed, 1 skipped** (27 new voice tests) |
| Process exit | exit=124 (lingered after green summary) | **exit=0** |

The lingering root cause (found via faulthandler thread dump): the main thread
hung at interpreter finalization in `comtypes\client\_events.py:106 __del__` —
a leaked SAPI COM event advise sink. Fixes:

- `voice/text_to_speech.py`:
  - Real bug: `_speak_sapi` referenced an undefined `engine` variable
    (`NameError` on every real utterance — the engine was created, then the
    utterance crashed). Now uses `_sapi_engine` correctly.
  - `shutdown_worker()` + `_SHUTDOWN` sentinel: the worker releases the COM
    engine **on its own thread** at process exit (`atexit`). Finalizing the
    comtypes sink cross-thread deadlocks the process.
  - Worker restart-safety: `_ensure_worker` re-spawns if the thread died.
- `tests/conftest.py`: `_fake_tts_engine` is now **session-scoped** with a
  permanent patch. The old per-test monkeypatch was undone at each test's
  teardown while the TTS worker is a global singleton — a still-queued
  utterance was then spoken with the *real* SAPI engine inside pytest.
- `security/confirmation.py`: `ConfirmationRequest.future` is now
  `Optional[asyncio.Future] = None` (lazy). The dataclass default factory
  called `asyncio.get_event_loop()` at construction → DeprecationWarning and
  `RuntimeError: There is no current event loop in thread 'MainThread'` on
  Python 3.13. `request_approval` creates the future on the running loop;
  `resolve`/`cancel_all` handle `future=None`.
- `tests/test_voice.py::test_emergency_stop_cancels_everything`: reworked to
  the single-loop `create_task` + `cm.cancel_all()` pattern (no more
  `asyncio.run` in a daemon thread, which left a Windows proactor loop
  running and could crash `test_server.py::test_confirmation_flow` when
  paired). STOP now awaits the waiter resolving **False** (never approve).
- `tests/test_server.py::test_confirmation_flow`: future bound to the test's
  own loop (`loop.create_future()`), loop closed after.

## 3. Live verification (real server, real hardware)

Launch (from `nova`, **not** `jarvis/`):

```
python -m uvicorn jarvis.backend.server:app --host 127.0.0.1 --port 8765
```

Frontend HUD: http://127.0.0.1:8765/ (dist built).

### Microphone
- `/api/status` → 12 capture devices; default = **Microphone Array (Realtek Audio)** (index 1).
- `/api/voice/test` → `{"success": true, "level": 0.008}` — device opens, real
  RMS level measured; honest "No audio detected — check the selected device"
  because nobody spoke (mic array sensitivity, honest per spec).

### STT
- `faster-whisper` installed, model **base.en** (int8 on CPU), loaded lazily
  per utterance. `/api/wakeword/status` → `available: true`
  (engine: transcription-fuzzy).

### TTS
- Engine: **SAPI5 via pyttsx3** (piper not installed → honest `piper: false`).
  `/api/tts/test` → spoken successfully on the dedicated worker thread.
- 2 SAPI voices cached; replies spoken aloud for every command below.

### End-to-end spoken commands (via `/api/command`, source=voice)
| Command | Result |
|---|---|
| "what is my RAM usage" | "You're using 90.5 percent of system memory — 7.4 of 8.17 gigabytes." (real psutil) |
| "create a folder called VoiceTest on my Desktop" | Folder verified on the REAL Desktop (`C:\Users\DELL\OneDrive\Desktop\VoiceTest`) |
| "list the files in my Downloads folder" | Real path resolved (Downloads is NOT under OneDrive); honest "That folder is empty." |
| "delete the VoiceTest folder" → **approve** | Confirmation request (high-risk) pending, folder untouched; spoken announcement; after `/api/confirm {approved:true}` → **folder deleted**, reply "Done." |
| "delete the VoiceTest folder" (re-created) → **deny** | **Folder survived**; honest "I attempted the action, but it failed: User denied permission." |
| "delete all files in my Downloads folder" | Matches NO intent → honest offline reply; nothing pending, Downloads intact (bulk delete can never auto-execute) |

### Emergency STOP
`POST /api/stop` → `{"stopped": true}`; cancels agent, discards PTT audio,
drains TTS queue, denies all pending confirmations. Covered by unit test too.

### Status / state sync
`/api/status` includes `voice.listening`, `voice.mode=push-to-talk`,
`voice.stt{engine,available,model,loaded,install_hint}`, `voice.devices`,
`tts{sapi,piper}`, `tts_voices`, `pending_confirmations`. HUD receives
`listening`/`transcribing`/`speaking`/`confirmation` states, real `voice_level`
waveform, MIC button, TEST MIC / TEST SPEAKERS in Settings (frontend build
passes; served from `dist`).

## 4. Bugs found & fixed during live testing

- **Known-folder paths** (`config/settings.py`): one global OneDrive-vs-home
  guess made "list my Downloads" fail (`OneDrive\Downloads` does not exist on
  this machine). Now resolves each folder via
  **`SHGetKnownFolderPath`** (per-folder ground truth, no new dependencies)
  with an existence-based fallback. Verified: Desktop/Documents/Pictures →
  OneDrive; Downloads → `C:\Users\DELL\Downloads`.
- **Source attribution**: `/api/command` accepted `source` but history logged
  "text". Now threaded through `_submit_user_command` → `planner.execute_plan`
  → `agent.process_text`; history shows `source: voice` correctly.

## 5. Dependencies

Installed and used: faster-whisper (STT), sounddevice (mic/playback),
pyttsx3 (SAPI5 TTS), psutil, fastapi/uvicorn. Not installed (honest
degradation): piper (TTS alt), openwakeword (wake word), Ollama (LLM offline →
intent-engine path with honest replies).

## 6. Limitations (honest)

1. **Mic array level**: Realtek array measures very low RMS at conversational
   distance; speak close to the mic or raise boost. `base.en` int8 on CPU adds
   ~1–2 s transcription latency per utterance (8 GB RAM).
2. **Wake word** is transcription-fuzzy (listens → transcribes → matches);
   `openwakeword` would be lower-latency but is not installed.
3. **LLM offline** (Ollama absent): only direct computer commands work; anything
   else gets the honest offline reply. Installing Ollama + `ollama pull
   llama3.2:3b` switches automatically.
4. TTS speech is synthesized locally (SAPI5 voice quality).
5. OneDrive sync can transiently lock files; filesystem delete already
   retries with unlock.

## 7. Files touched in this milestone

- `jarvis/voice/text_to_speech.py` — NameError fix; COM engine created/used/
  released only on the worker thread; clean shutdown (`atexit`).
- `jarvis/security/confirmation.py` — lazy future; loop-safe resolve.
- `jarvis/tests/conftest.py` — session-scoped fake TTS engine.
- `jarvis/tests/test_voice.py`, `jarvis/tests/test_server.py` — stop-test
  rework; future/loop binding.
- `jarvis/config/settings.py` — SHGetKnownFolderPath per-folder resolution.
- `jarvis/backend/server.py`, `jarvis/backend/planner.py` — command source
  threading.
- `jarvis/docs/VOICE_PIPELINE_REPORT.md` — this report.

Suite after all changes: **115 passed, 1 skipped, exit 0, ~50 s.**
