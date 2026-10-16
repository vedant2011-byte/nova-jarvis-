"""Live 20-turn E2E against the deployed Vercel production site.

Usage:  python jarvis/scripts/e2e_20_turns.py [base_url]
Default base_url: https://nova-jarvis-pi.vercel.app

The deployed HUD resolves a reply through three redundant channels, in order:
  1. the HTTP response of POST /api/command (bounded wait)   — primary
  2. a `conversation` event over the WebSocket                — live update
  3. GET /api/history polling                                — fallback
This script resolves each turn through the SAME channels, so it measures
the contract real users depend on.

Acceptance (exit 0 only if all pass):
  * WS handshake delivers hello + boot replay
  * 20 sequential turns: EVERY turn yields a reply through some channel
  * planner phrasings ("X and then Y") reply (previous NameError path)
  * the run records WebSocket close codes: platform-side drops (1006 after
    minutes) are reported as warnings, and turns MUST still resolve via
    the fallback channels (chat continues after a WS drop — the bug class)
Requires: pip install websockets
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.error
import urllib.request

import websockets

DEFAULT_BASE = "https://nova-jarvis-pi.vercel.app"
WS_PATH = "/ws"

TURNS = [
    "hi",
    "what's your name?",
    "remember that my favorite color is teal",
    "what is my favorite color?",
    "what time is it?",
    "tell me a short joke",
    "check the ram usage and then say done",
    "how are you today?",
    "say the number seven",
    "what can you do?",
    "remember I like espresso",
    "what do I like to drink?",
    "flip a coin",
    "check disk space and then tell me a fun fact",
    "good morning",
    "what's 2 plus 2?",
    "tell me something interesting about space",
    "summarize our conversation so far",
    "thanks",
    "goodbye for now",
]

POST_TIMEOUT = 45.0       # server waits <=25s (command_wait_s); plus margin
WS_WAIT_S = 12.0          # live event grace period before falling back
HISTORY_DEADLINE_S = 40.0 # fallback polling budget from turn start
HISTORY_POLL_S = 2.5
HELLO_TIMEOUT = 15.0
PACE_S = 1.0              # human-like cadence (avoids quota throttling)


def post_command(base: str, text: str) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"{base}/api/command",
        data=json.dumps({"text": text, "source": "text"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=POST_TIMEOUT) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"raw": body}
    except Exception as e:  # noqa: BLE001
        return -1, {"error": f"{type(e).__name__}: {e}"}


def get_history(base: str, limit: int = 40) -> list[dict]:
    try:
        with urllib.request.urlopen(f"{base}/api/history?limit={limit}", timeout=15) as r:
            return json.loads(r.read().decode()).get("entries", [])
    except Exception:  # noqa: BLE001
        return []


class Collector:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()
        self.close_code: int | None = None
        self.close_reason = ""
        self.counts: dict[str, int] = {}

    async def reader(self, ws) -> None:  # noqa: ANN001
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                t = msg.get("type", "?")
                self.counts[t] = self.counts.get(t, 0) + 1
                await self.queue.put((t, msg.get("data") or {}))
        except websockets.exceptions.ConnectionClosed as e:
            code = 1006
            if e.rcvd is not None:
                code = e.rcvd.code
            elif e.sent is not None:
                code = e.sent.code
            self.close_code = code
            self.close_reason = str(e.reason or "")
        except Exception as e:  # noqa: BLE001
            self.close_reason = f"reader-error {type(e).__name__}: {e}"

    async def wait_for(self, pred, timeout: float):  # noqa: ANN001
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                item = await asyncio.wait_for(self.queue.get(), remaining)
            except asyncio.TimeoutError:
                return None
            if pred(item[0], item[1]):
                return item


async def main() -> int:
    base = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else DEFAULT_BASE
    ws_url = ("wss://" + base.split("://", 1)[1]) + WS_PATH
    failures: list[str] = []
    warnings: list[str] = []
    channels: dict[str, int] = {}
    latencies: list[float] = []

    # pre-existing conversation must not count as "new" replies
    seen_replies: set[str] = {
        str(e.get("content") or "")
        for e in get_history(base)
        if e.get("role") == "jarvis"
    }

    print(f"connecting {ws_url}", flush=True)
    async with websockets.connect(ws_url, open_timeout=HELLO_TIMEOUT, max_size=None) as ws:
        col = Collector()
        reader_task = asyncio.create_task(col.reader(ws))

        hello = await col.wait_for(lambda t, d: t == "hello", HELLO_TIMEOUT)
        if not hello:
            failures.append("no hello event within 15s (WS handshake broken)")
        else:
            print("hello: OK", flush=True)
        boot = await col.wait_for(lambda t, d: t == "boot_done", HELLO_TIMEOUT)
        if not boot:
            failures.append("no boot_done replay over WS")
        else:
            print("boot_done replay: OK", flush=True)

        for i, text in enumerate(TURNS, 1):
            if col.close_code is not None:
                warnings.append(
                    f"WS closed (code {col.close_code}) at turn {i} — "
                    "continuing via fallback channels (recovery contract)"
                )
            t0 = time.monotonic()
            status, resp = await asyncio.to_thread(post_command, base, text)
            post_ms = (time.monotonic() - t0) * 1000
            if status != 200 or resp.get("accepted") is not True:
                failures.append(f"turn {i}: POST status {status} body={resp}")
                print(f"turn {i:2d}: POST {status} {resp} -> FAIL", flush=True)
                continue

            reply = str(resp.get("reply") or "")
            channel = "http" if reply else None

            if channel is None and resp.get("done") is False:
                # worker still running within the bounded wait: give the live
                # WS event a grace period, then fall back to history polling
                ev = await col.wait_for(
                    lambda t, d: t == "conversation"
                    and d.get("role") == "jarvis"
                    and str(d.get("content") or "") not in seen_replies,
                    WS_WAIT_S,
                )
                if ev is not None:
                    reply = str(ev[1].get("content") or "")
                    channel = "ws"
                else:
                    deadline = t0 + HISTORY_DEADLINE_S
                    while not reply and time.monotonic() < deadline:
                        await asyncio.sleep(HISTORY_POLL_S)
                        for e in reversed(get_history(base)):
                            if e.get("role") != "jarvis":
                                continue
                            c = str(e.get("content") or "")
                            if c and c not in seen_replies:
                                reply, channel = c, "history"
                                break
            elif channel is None:
                # done but empty reply — a genuinely empty answer is a failure
                # unless the live channel has it
                ev = await col.wait_for(
                    lambda t, d: t == "conversation" and d.get("role") == "jarvis",
                    WS_WAIT_S,
                )
                if ev is not None:
                    reply = str(ev[1].get("content") or "")
                    channel = "ws"

            turn_ms = (time.monotonic() - t0) * 1000
            if not reply.strip():
                failures.append(
                    f"turn {i}: no reply on ANY channel "
                    f"(http done={resp.get('done')}, ws={col.counts.get('conversation', 0)} "
                    f"conversation events total) body={resp}"
                )
                print(
                    f"turn {i:2d}: post={post_ms:6.0f}ms total={turn_ms:6.0f}ms "
                    "NO-REPLY-ANYWHERE -> FAIL",
                    flush=True,
                )
            else:
                seen_replies.add(reply)
                channels[channel or "unknown"] = channels.get(channel or "unknown", 0) + 1
                latencies.append(turn_ms)
                print(
                    f"turn {i:2d}: post={post_ms:6.0f}ms total={turn_ms:6.0f}ms "
                    f"via={channel} reply={reply[:60]!r} OK",
                    flush=True,
                )
            await asyncio.sleep(PACE_S)

        await asyncio.sleep(2.0)
        if col.close_code is not None:
            warnings.append(f"WS closed code={col.close_code} {col.close_reason}")
        else:
            print("WS still open after all 20 turns: OK", flush=True)

        print("\nevent counts:", json.dumps(col.counts), flush=True)
        print("channels used:", json.dumps(channels), flush=True)
        if latencies:
            s = sorted(latencies)
            print(
                f"turn latency ms: min={s[0]:.0f} median={s[len(s)//2]:.0f} "
                f"max={s[-1]:.0f} n={len(s)}",
                flush=True,
            )

    reader_task.cancel()

    if warnings:
        print(f"\n{len(warnings)} warning(s):", flush=True)
        for w in warnings:
            print(f"  ! {w}", flush=True)
    if failures:
        print(f"\nE2E FAILED - {len(failures)} failure(s):", flush=True)
        for f in failures:
            print(f"  - {f}", flush=True)
        return 1
    print(
        f"\nE2E PASS - 20/20 turns resolved via {channels}, "
        f"WS handshake verified, {len(warnings)} warning(s)",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
