"""Poll production until the pushed commit is live, then verify its shape.

Discriminators:
  1. frontend bundle hash in / changes (new build = new content hash)
  2. POST /api/command response contains "done" + "reply" keys
     (old code returned only {"accepted": true})

Usage: python verify_deploy.py <old_bundle_hash> [timeout_s]
Exit 0 only when both discriminators confirm the new build.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request

BASE = "https://nova-jarvis-pi.vercel.app"


def get(url: str, timeout: float = 15.0) -> str:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read().decode(errors="replace")


def bundle_hash() -> str:
    html = get(f"{BASE}/")
    m = re.search(r'/assets/index-([A-Za-z0-9_-]+)\.js', html)
    return m.group(1) if m else ""


def post_command_shape() -> dict:
    req = urllib.request.Request(
        f"{BASE}/api/command",
        data=json.dumps({"text": "deployment check ping", "source": "text"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return json.loads(r.read().decode() or "{}")
    except Exception as e:  # noqa: BLE001
        return {"__error": f"{type(e).__name__}: {e}"}


def main() -> int:
    old_hash = sys.argv[1] if len(sys.argv) > 1 else ""
    timeout_s = float(sys.argv[2]) if len(sys.argv) > 2 else 360.0
    deadline = time.monotonic() + timeout_s
    seen = set()
    while time.monotonic() < deadline:
        try:
            h = bundle_hash()
        except Exception as e:  # noqa: BLE001
            print(f"html probe failed: {e}", flush=True)
            time.sleep(10)
            continue
        seen.add(h)
        if old_hash and h == old_hash:
            print(f"still old build {h}", flush=True)
            time.sleep(10)
            continue
        if old_hash and h != old_hash:
            print(f"NEW frontend bundle live: {h} (was {old_hash})", flush=True)
            # give the serverless function a moment, then verify API shape
            time.sleep(8)
            shape = post_command_shape()
            print(f"api shape: {json.dumps(shape)[:300]}", flush=True)
            if "done" in shape and "reply" in shape:
                print("DEPLOY VERIFIED: new commit is live", flush=True)
                return 0
            print("frontend updated but API still old shape — retrying", flush=True)
            continue
        # no baseline given: verify shape directly
        shape = post_command_shape()
        print(f"api shape: {json.dumps(shape)[:300]}", flush=True)
        if "done" in shape and "reply" in shape:
            print("DEPLOY VERIFIED: new commit is live", flush=True)
            return 0
        time.sleep(10)
    print(f"TIMEOUT after {timeout_s}s; seen bundles={seen}", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
