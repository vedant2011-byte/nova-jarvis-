# Deploying JARVIS to Vercel (Vercel Services)

## What this is

The repo contains two independently-built apps:

| Service (name in `vercel.json`) | Directory | Framework | What it is |
|---|---|---|---|
| `hud` | `jarvis/frontend/` | Vite + React + TS | the cinematic HUD |
| `api` | `jarvis/` | FastAPI (Python) | REST + WebSocket backend |

`vercel.json` (at the repo **root**) routes public traffic like this:

```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "services": {
    "hud": {
      "root": "jarvis/frontend/"
    },
    "api": {
      "root": "jarvis/",
      "entrypoint": "vercel_entry:app",
      "installCommand": "pip install -r vercel-requirements.txt",
      "functions": {
        "vercel_entry.py": {
          "excludeFiles": "{frontend/**,data/**,data-test/**,logs/**,logs-test/**,tests/**,docs/**,scripts/**,.pytest_cache/**,**/__pycache__/**,*.log,.venv/**}"
        }
      }
    }
  },
  "rewrites": [
    { "source": "/api/(.*)", "destination": { "service": "api" } },
    { "source": "/ws", "destination": { "service": "api" } },
    { "source": "/(.*)", "destination": { "service": "hud" } }
  ]
}
```

### Why exactly this shape

- **Public routes**: the HUD calls **same-origin** paths — `/api/*` for REST and
  `/ws` for the live event stream (`hooks/useJarvisStore.ts` builds
  `${proto}://${location.host}/...`; there is no hardcoded localhost in the
  production bundle — verified by scanning `dist/assets/*.js`). The rewrites
  therefore route `/api/(.*)` and `/ws` to the `api` service and everything
  else (`/` → SPA) to the `hud` service. Rules are ordered most-specific
  first and end with a catch-all, exactly as Vercel requires.
- **Service receives the original path** (e.g. `/api/status` reaches the
  backend as `/api/status`) — which matches the FastAPI routes as defined,
  so no path rewriting or re-mounting is needed.
- **Bindings**: **none are declared, on purpose.** A binding lets one
  *service* call another service privately; here the **browser** is the
  caller, and browsers cannot use service bindings — they just hit the same
  domain. There is only one backend in this project, so no service-to-server
  call exists anywhere.
- The `api` entrypoint is `jarvis/vercel_entry.py`, which puts the repo root
  on `sys.path` and re-exports `jarvis.backend.server:app`. Vercel loads the
  entrypoint relative to the service root; from inside `jarvis/` the package
  `jarvis` itself is not importable without this shim. Application code was
  not modified by this — it only fixes import resolution.
- `installCommand` points the `api` service at **`jarvis/vercel-requirements.txt`**,
  a minimal cloud dependency set (fastapi, uvicorn[standard], websockets,
  pydantic, requests, psutil, numpy). The default (`pip install -r
  requirements.txt`) would install the FULL desktop set — measured on
  Linux/Python 3.13 that is **~190 MB of wheels** dominated by playwright
  (48 MB), ctranslate2 (40 MB), av/FFmpeg (35 MB), onnxruntime (24 MB),
  numpy (17 MB) — and the first deployment failed with *"Total bundle size
  (608.97 MB) exceeds the maximum function size (500 MB)"*. The minimal
  set is **~23 MB of wheels**. The local Windows app is untouched: it still
  installs the full `requirements.txt`.
- `excludeFiles` keeps tests/data/logs/frontend-source (including
  `frontend/node_modules`) and stray `*.log`/`.venv` out of the Python
  function bundle (the bundle limit is 500 MB uncompressed and every
  reachable file is included by default). The bundled application source
  itself measured **0.25 MB** after these exclusions.
- Logging: the backend now only attaches its `RotatingFileHandler` when the
  log directory is **writable**; on Vercel (read-only FS) logging falls back
  to stdout-only. Local file logging on Windows is unchanged.

## What will and will not work on Vercel (important)

JARVIS is a **Windows computer-control agent**. The parts that talk to your
PC can only run on your PC. This is what a Vercel deployment means:

| Feature | On Vercel | Why |
|---|---|---|
| HUD (full UI) | ✅ works | static SPA served by `hud` |
| LLM chat / intent engine / planning | ✅ works | pure Python |
| Memory (SQLite) | ⚠️ read-mostly | works per-instance; **ephemeral** — each function instance has its own `/tmp`-scoped DB and it is wiped on redeploy. Same for settings/history |
| RAM/CPU/disk **of the Vercel instance** | ✅ works | psutil reports the container, not your PC |
| Mic / wake word / push-to-talk | ❌ | no audio hardware in a serverless function; backend reports it honestly |
| TTS / voice output | ❌ (server-side) | no audio output; HUD will show TTS unavailable |
| Open/close apps, terminal, files, screenshots on **your machine** | ❌ | the deployed backend is not your computer — it will answer honestly that the action can't be performed |
| Playwright browser automation | ❌ | Not installed in the cloud bundle (deliberately — the Python package alone is 48 MB compressed); the tool reports honestly if unavailable |
| WebSocket live events | ✅ works | Vercel Functions support WS (Beta, Fluid compute on by default); the HUD already reconnects automatically |

The desktop app you run locally at `127.0.0.1:8765` keeps **every** feature —
this deployment is the cloud-accessible copy of the same backend+HUD, not a
replacement for it.

## CORS: what was determined (and why nothing was added)

The frontend and backend are served from the **same origin** (one Vercel
domain), so browser requests are same-origin and **no CORS middleware is
needed**. Adding a permissive `allow_origins=["*"]` would be worse than
useless (defense-in-depth loss for a desktop-control API). If you later move
the HUD or API to a *different* domain, add `CORSMiddleware` allowing only
that specific origin — not `*`.

There is **no login/authentication system** in the codebase (verified by
content search — no auth/JWT/login code anywhere), so there is nothing to
preserve or configure; the confirmation gate is per-action, not per-user.
If the deployment will be publicly reachable, consider adding Vercel
Deployment Protection (dashboard setting, no code change) as a basic guard.

## Environment variables to set in Vercel (no secrets needed)

| Variable | Set on service | Value for deploy | Meaning |
|---|---|---|---|
| `JARVIS_DATA_DIR` | api | `/tmp/jarvis-data` | where settings.json/jarvis.db/screenshots go (only writable path in the function) |
| `JARVIS_LOG_DIR` | api | `/tmp/jarvis-logs` | log output dir (falls back to stdout-only if unset/blocked) |
| `JARVIS_DISABLE_DEFAULT_PROTECTED` | api | `1` | the Windows-only protected-path defaults (C:\Windows, AppData...) are meaningless on Linux |
| `JARVIS_EXTRA_PROTECTED` | api | *(optional)* | semicolon-separated extra paths to always deny |

Optional, currently **unused by the app** (checked): the frontend has no
`import.meta.env`/`process.env` usage at all and the backend reads no
`OLLAMA_HOST`/`JARVIS_MODEL` env vars — those live in `data/settings.json`
and the README's mention of them in `.env.example` is aspirational. The
Ollama host can be changed at runtime via Settings → AI; a Vercel deployment
cannot reach your local Ollama, so LLM will stay offline there (intent
engine continues to work, honestly labeled).

There are **no secrets** to configure: no API keys, no DB URLs, no auth.
Nothing sensitive is written into the repo or this config.

## Manual steps you must perform (in order)

1. In the Vercel dashboard: **Project → Settings → Build & Deployment →
   Framework Preset = "Services"** (a `services` key alone is not enough —
   the project must be set to the Services framework to build this way).
2. Deploy (CLI `vercel` from the repo root, or connect the GitHub repo).
   The `hud` service runs `npm install && npm run build` in
   `jarvis/frontend/` automatically; the `api` service installs
   **`vercel-requirements.txt`** (via `installCommand` in `vercel.json`) —
   the minimal cloud-only dependency set, ~23 MB instead of ~190 MB — and
   runs on Python **3.13** (pinned via `jarvis/.python-version`; Vercel
   supports 3.12/3.13/3.14).
3. Set the env vars from the table above on the `api` service, then
   redeploy so they are present at build/runtime.
4. Do NOT point a real domain at this expecting full desktop control — see
   the capability table above.

## Incident 2026-10-11: black screen ("INITIALIZING JARVIS" forever)

**Symptom**: HUD loaded, but stayed on the boot screen forever; every
`/api/*` request returned `500 FUNCTION_INVOCATION_FAILED`.

**Root cause**: Vercel bundles only the `api` service root's contents, so in
the deployed function `vercel_entry.py` sits at the bundle root with no
`jarvis/` directory above it. The entrypoint's old shim
(`Path(__file__).resolve().parent.parent`) therefore pointed outside the
bundle, `from jarvis.backend.server import app` raised
`ModuleNotFoundError: No module named 'jarvis'`, and every invocation died at
import — so the HUD's WebSocket never connected and the `boot_done` event
that unmounts the boot screen never arrived. Reproduced locally in a
cloud-only venv against a flattened bundle copy before fixing.

**Fixes**:
- `jarvis/vercel_entry.py` now resolves BOTH layouts: if the repo-style
  `jarvis/` package is found next to/above the entrypoint it is put on
  `sys.path`; otherwise the service root itself is registered as the
  `jarvis` package (the deployed-bundle case).
- `jarvis/config/settings.py`: a failed `mkdir` of the data dir (read-only
  serverless FS, e.g. when env vars from the table below are not set) no
  longer crashes at import — settings stay in memory, honestly unpersisted.
- HUD: a boot watchdog (12 s) can no longer hang forever. If the backend
  doesn't finish booting, the boot screen shows CONNECTION FAILED with the
  exact reason (unreachable vs. reachable-but-link-not-ready) and a RETRY
  button; it never fakes a connected state. Timeouts added to boot probes
  (`AbortController`). Tests: `jarvis/tests/test_vercel_deployment.py`
  (flattened-bundle import, repo-layout import, read-only data dir) and
  `jarvis/frontend/src/__tests__/boot.test.ts` (startup success, API
  failure, timeout handling).

**After redeploying, verify** (the deploy log alone is not enough):
1. `curl https://<your-domain>/api/status` must return **200** with JSON.
2. Open the site: the boot screen must transition to the HUD within ~3 s.
3. If it shows CONNECTION FAILED instead, the message tells you which probe
   failed — a `/ws`-specific failure would appear as "backend responded, but
   the live event link never became ready" (WebSocket support on Vercel is
   beta; check that Fluid compute is enabled).

## Honest limitations / blockers found

- **Desktop-control features cannot work from a cloud function** — that is a
  hardware/physics constraint, not a bug; the app's design already demands
  **honest failures**, and that's exactly what it will report.
- **Persistent data is ephemeral**: SQLite/settings live inside the function
  instance. For genuinely durable server memory you'd need an external store
  (e.g. Vercel Marketplace Redis/Postgres) — deliberately NOT added here
  because it would change the product (and add credentials) without your OK.
- **Bundle size (the 500 MB limit)**: the failed first deploy reported
  608.97 MB. Measured contributions on Linux/Python 3.13: full
  `requirements.txt` ≈ **190 MB of wheels** (playwright 48, ctranslate2 40,
  av 35, onnxruntime 24, numpy 17, pillow 7, plus faster-whisper's
  huggingface/tokenizers/xet chain), application source ≈ 0.25 MB after
  exclusions, and `frontend/node_modules` ≈ 66 MB (excluded by
  `excludeFiles`). After switching the `api` service to
  `vercel-requirements.txt`, the installed Python payload drops to
  **~23 MB** — a >160 MB reduction with a large safety margin. The exact
  bundle Vercel reports can only be confirmed by a real deployment; the
  next deploy's build log is the authoritative measurement.
- Deployment itself was **not** run (that requires your Vercel account and
  authorization); everything that could be verified locally was: `npm run
  build` exit 0, entrypoint import exit 0 (verified with
  sounddevice/pyttsx3/faster_whisper/PIL/playwright/piper imports
  artificially blocked to simulate the bare Linux cloud environment —
  `GET /api/status` then answers 200 with STT/TTS/mic honestly reported
  unavailable, proving the app degrades gracefully instead of crashing at
  import), `vercel.json` validated against the official schema
  (`https://openapi.vercel.sh/vercel.json`, zero violations), Linux wheel
  sizes measured with `pip download --platform manylinux… --python-version
  3.13`, and 115/116 tests passing after every change.
