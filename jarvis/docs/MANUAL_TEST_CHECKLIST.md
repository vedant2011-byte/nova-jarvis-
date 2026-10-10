# JARVIS — Manual Test Checklist

Run with the HUD open (http://127.0.0.1:8765). Mark each item after verifying.
Automated coverage: `python -m pytest tests/ -q` (88 passed, 1 skipped).

## VOICE

| # | Test | Expected | ✅ |
|---|---|---|---|
| V1 | Settings → Voice shows the real microphone list | Actual input devices listed | ☐ |
| V2 | "Test microphone" shows a moving level while speaking | Real RMS level > 0 | ☐ |
| V3 | Wake word ON, say "Jarvis" | Core activates, state → LISTENING | ☐ |
| V4 | "Jarvis, open Chrome." (wake + command in one breath) | Chrome opens | ☐ |
| V5 | Push-to-talk: hold Space (MIC active), speak, release | Command transcribed and executed | ☐ |
| V6 | Silence while listening | Times out, returns to IDLE | ☐ |
| V7 | TTS enabled → responses are spoken | Audible, matches the HUD text | ☐ |
| V8 | STT not installed → say a command | Honest "voice engine unavailable" message, no fake result | ☐ |

## TOOLS

| # | Test | Expected | ✅ |
|---|---|---|---|
| T1 | "Open Chrome." | Process verified running; HUD shows EXECUTING → result | ☐ |
| T2 | "Close Chrome." | Closed; honest report if it wasn't running | ☐ |
| T3 | "What applications are running?" | Real list from the process table | ☐ |
| T4 | "Take a screenshot." | File in data/screenshots, shown in HUD | ☐ |
| T5 | "What's my RAM usage?" | Value matches Task Manager | ☐ |
| T6 | "Find my resume." | Searches Documents/Desktop/Downloads, reports findings | ☐ |
| T7 | "Create a folder called TestJarvis on my Desktop." | Folder exists on the real Desktop | ☐ |
| T8 | "Open github.com" (Playwright installed) | Visible Chromium window opens the page | ☐ |
| T9 | Run a dev server: confirm low/medium risk classification and that a managed `npm run dev` is detected on its port | Server starts, port detected, verifiable | ☐ |

## AGENT

| # | Test | Expected | ✅ |
|---|---|---|---|
| A1 | "Open Chrome." then "Search for Blackgrid." | Second command uses context (search happens in the browser) | ☐ |
| A2 | "Open my Blackgrid project." after saving its location in memory | Memory retrieved, project opened | ☐ |
| A3 | Multi-step: "Open Chrome, then take a screenshot." | Task plan shown, steps tick off in TASK panel | ☐ |
| A4 | Natural phrasing: "Can you open Chrome?" / "I need Chrome." | Intent recognized without exact keywords | ☐ |
| A5 | Unrecognized phrase with LLM offline | Honest reduced-mode reply, no fake action | ☐ |

## SECURITY

| # | Test | Expected | ✅ |
|---|---|---|---|
| S1 | "Delete the folder X on my Desktop." | Confirmation card appears; nothing deleted before APPROVE | ☐ |
| S2 | Approve S1 | Deletion verified (folder gone), result reported | ☐ |
| S3 | Cancel S1 (button or "Cancel") | Folder untouched, action abandoned | ☐ |
| S4 | "Delete all files in my Downloads folder." | Never auto-executes; confirmation/clarification required | ☐ |
| S5 | Terminal: "shutdown /s" request | Blocked or explicit high-risk confirmation; never silent | ☐ |
| S6 | Emergency STOP during a long task | Task/confirmations/speech stop immediately; UI shows TASK STOPPED | ☐ |
| S7 | Path traversal: ask to delete C:\Windows\System32 | Refused by path validation | ☐ |
| S8 | Malformed LLM tool call (debug mode) | Rejected by schema validation, error logged | ☐ |

## STATUS / UI

| # | Test | Expected | ✅ |
|---|---|---|---|
| U1 | Status panel CPU/RAM match Task Manager | Real values, refreshing | ☐ |
| U2 | Go offline (unplug network) | Network shows OFFLINE; local commands still work | ☐ |
| U3 | Animation intensity slider | Effect visible immediately | ☐ |
| U4 | Clear history / clear memory | Panels empty; DB rows removed | ☐ |
| U5 | Boot sequence toggle | Startup animation can be disabled | ☐ |
