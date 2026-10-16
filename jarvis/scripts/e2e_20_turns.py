"""Live 20-turn E2E against the deployed Vercel production site.

Usage:  python jarvis/scripts/e2e_20_turns.py [base_url]
Default base_url: https://nova-jarvis-pi.vercel.app

Acceptance (exit 0 only if every assertion passes):
  * WS connects and delivers `hello`
  * 20 sequential turns: each POST /api/command returns 200 with a non-empty
    reply AND a matching `conversation` (jarvis) event arrives over the
    production WebSocket (the original bug: turns after ~4 stopped
    delivering any events and the socket died with close code 1006)
  * the same WS stays open across all 20 turns (no abnormal close)
  * planner phrasings ("X and then Y") reply (previous NameError path)

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

POST_TIMEOUT = 40.0     # server waits <=15s for a reply; margin for network+Gemini
WS_REPLY_TIMEOUT = 15.0  # conversation event must arrive within this of POST end
HELLO_TIMEOUT = 15.0


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

    def drain(self) -> list[tuple[str, dict]]:
        out = []
        while not self.queue.empty():
            out.append(self.queue.get_nowait())
        return out

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


def jarvis_reply_pred(t: str, d: dict) -> bool:
    return t == "conversation" and d.get("role") == "jarvis"


async def main() -> int:
    base = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else DEFAULT_BASE
    ws_url = ("wss://" + base.split("://", 1)[1]) + WS_PATH
    failures: list[str] = []
    latencies: list[float] = []
    print(f"connecting {ws_url}", flush=True)
    async with websockets.connect(ws_url, open_timeout=HELLO_TIMEOUT, max_size=None) as ws:
        col = Collector()
        reader_task = asyncio.create_task(col.reader(ws))

        hello = await col.wait_for(lambda t, d: t == "hello", HELLO_TIMEOUT)
        if not hello:
            failures.append("no hello event within 15s")
            print("FAIL: no hello", flush=True)
        else:
            print("hello: OK", flush=True)

        for i, text in enumerate(TURNS, 1):
            if col.close_code is not None:
                failures.append(f"WS closed (code {col.close_code}) before turn {i}")
                break
            t0 = time.monotonic()
            status, resp = await asyncio.to_thread(post_command, base, text)
            post_ms = (time.monotonic() - t0) * 1000
            if status != 200:
                failures.append(f"turn {i}: POST status {status} body={resp}")
                print(f"turn {i:2d}: POST {status} {resp} -> FAIL", flush=True)
                continue
            http_reply = str(resp.get("reply") or "")
            if not http_reply:
                failures.append(
                    f"turn {i}: empty HTTP reply, done={resp.get('done')} body={resp}"
                )
                print(f"turn {i:2d}: empty reply {resp} -> FAIL", flush=True)
            ev = await col.wait_for(jarvis_reply_pred, WS_REPLY_TIMEOUT)
            turn_ms = (time.monotonic() - t0) * 1000
            if ev is None:
                failures.append(
                    f"turn {i}: NO conversation event over WS within {WS_REPLY_TIMEOUT}s"
                )
                print(
                    f"turn {i:2d}: post={post_ms:6.0f}ms total={turn_ms:6.0f}ms "
                    "WS-EVENT-MISSING -> FAIL",
                    flush=True,
                )
            else:
                ws_reply = str(ev[1].get("content") or "")
                ok = bool(ws_reply.strip())
                if http_reply and ws_reply and http_reply != ws_reply:
                    failures.append(
                        f"turn {i}: HTTP reply != WS reply\n"
                        f"  http: {http_reply[:120]!r}\n"
                        f"  ws:   {ws_reply[:120]!r}"
                    )
                latencies.append(turn_ms)
                print(
                    f"turn {i:2d}: post={post_ms:6.0f}ms total={turn_ms:6.0f}ms "
                    f"reply={ws_reply[:60]!r} {'OK' if ok else 'FAIL'}",
                    flush=True,
                )
                if not ok:
                    failures.append(f"turn {i}: empty WS reply content")
            state = getattr(ws, "state", None)
            state_name = getattr(state, "name", "OPEN")
            if state_name != "OPEN" and col.close_code is None:
                failures.append(
                    f"turn {i}: WS state={state_name} but no close code recorded"
                )
            await asyncio.sleep(0.4)  # gentle pacing, mirrors a real user

        await asyncio.sleep(2.0)
        col.drain()
        if col.close_code is not None and col.close_code not in (1000, 1001):
            failures.append(
                f"WS abnormal close code {col.close_code} {col.close_reason}"
            )
        elif col.close_code is not None:
            print(f"WS closed cleanly code={col.close_code} (after all turns)", flush=True)
        else:
            print("WS still open after all 20 turns: OK", flush=True)

        print("\nevent counts:", json.dumps(col.counts), flush=True)
        if latencies:
            s = sorted(latencies)
            print(
                f"turn latency ms: min={s[0]:.0f} median={s[len(s)//2]:.0f} "
                f"max={s[-1]:.0f} n={len(s)}",
                flush=True,
            )

    reader_task.cancel()
    if failures:
        print(f"\nE2E FAILED - {len(failures)} failure(s):", flush=True)
        for f in failures:
            print(f"  - {f}", flush=True)
        return 1
    print(
        f"\nE2E PASS - 20/20 turns, WS alive, {len(latencies)} replies verified over WS",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
