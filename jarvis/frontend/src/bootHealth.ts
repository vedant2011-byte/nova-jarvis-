// Boot-screen watchdog logic, extracted so it can be unit-tested.
//
// The HUD's boot screen must never hang forever: after BOOT_TIMEOUT_MS in the
// 'boot' state we probe the backend REST API and report exactly what is wrong
// (backend unreachable vs. reachable-but-link-never-ready), and the user gets
// a Retry button. We never fake a connected state.

export const BOOT_TIMEOUT_MS = 12000
export const BOOT_PROBE_TIMEOUT_MS = 6000

export type ProbeFn = (
  path: string,
  init?: RequestInit,
  timeoutMs?: number,
) => Promise<unknown>

export type BootHealth = { ok: boolean; detail: string }

/** Probe the backend and translate any failure into an honest, readable detail. */
export async function probeBootHealth(probe: ProbeFn): Promise<BootHealth> {
  try {
    await probe('/api/status', undefined, BOOT_PROBE_TIMEOUT_MS)
    return { ok: true, detail: 'backend reachable' }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    const kind =
      err instanceof DOMException && (err.name === 'TimeoutError' || err.name === 'AbortError')
        ? 'timed out'
        : 'unreachable'
    return { ok: false, detail: `backend ${kind} (${msg})` }
  }
}
