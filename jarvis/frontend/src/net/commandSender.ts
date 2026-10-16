/**
 * Chat command submission with optimistic echo, duplicate suppression,
 * bounded reply-wait and guaranteed loading reset.
 *
 * WHY: on serverless, the reply for a POST /api/command only arrives over
 * the WebSocket *local to the instance that ran the command*. If that link
 * is stale or dies, the UI previously waited forever. This sender:
 *   - echoes the user's message immediately (never "vanishes"),
 *   - never submits the same text twice inside a short window,
 *   - keeps the UI's loading state bounded: every success, failure and
 *     timeout path reports through onReply/onFail so callers can reset,
 *   - as a last resort polls /api/history so a reply that was computed
 *     but never delivered over the WS still surfaces.
 */

export type HistoryEntryLite = {
  role: string
  content: string
  ts: number
  source?: string
}

export type CommandPostResponse = {
  accepted?: boolean
  done?: boolean
  reply?: string
  handled?: string
}

export type CommandSenderDeps = {
  /** POST /api/command — injected so tests can fake timeouts/5xx. */
  post: (text: string) => Promise<CommandPostResponse>
  /** Optional recovery path: read /api/history to find a computed reply. */
  pollHistory?: () => Promise<HistoryEntryLite[]>
  /** Optimistic echo of the user's message (deduplicated against events). */
  onEcho: (text: string) => void
  /** A reply arrived (from POST or from history polling). */
  onReply: (text: string) => void
  /** Informational note (duplicate ignored, still working, ...). */
  onNote: (message: string) => void
  /** Terminal failure — the UI must return to a usable state NOW. */
  onFail: (message: string) => void
  pollIntervalMs?: number
  pollTimeoutMs?: number
  duplicateWindowMs?: number
  now?: () => number
}

export type CommandSender = {
  send: (text: string) => Promise<void>
  dispose: () => void
  /** The text currently being submitted (null when idle) — for tests. */
  inFlight: () => string | null
  /** True while a history fallback poll is scheduled — for tests. */
  polling: () => boolean
}

export function createCommandSender(deps: CommandSenderDeps): CommandSender {
  const now = deps.now ?? Date.now
  const dupWindow = deps.duplicateWindowMs ?? 1500
  const pollInterval = deps.pollIntervalMs ?? 2000
  const pollTimeout = deps.pollTimeoutMs ?? 120000

  let inFlight: string | null = null
  let lastAttempt: { text: string; at: number } | null = null
  let pollTimer: ReturnType<typeof setTimeout> | null = null
  let pollDeadline = 0

  const stopPolling = (): void => {
    if (pollTimer !== null) {
      clearTimeout(pollTimer)
      pollTimer = null
    }
  }

  const findReply = (entries: HistoryEntryLite[], sentAt: number): string | null => {
    // newest-first search: first agent reply at/after the send wins
    for (let i = entries.length - 1; i >= 0; i--) {
      const e = entries[i]
      if (e.role === 'jarvis' && e.ts >= sentAt - 1000) return e.content
    }
    return null
  }

  const pollForReply = (sentAt: number): void => {
    if (!deps.pollHistory) return
    pollDeadline = sentAt + pollTimeout
    const tick = async (): Promise<void> => {
      let entries: HistoryEntryLite[] = []
      try {
        entries = (await deps.pollHistory!()) ?? []
      } catch {
        entries = [] // transient — try again until the deadline
      }
      const reply = findReply(entries, sentAt)
      if (reply) {
        pollTimer = null
        deps.onReply(reply)
        return
      }
      if (now() > pollDeadline) {
        pollTimer = null
        deps.onFail('The reply timed out — please try again.')
        return
      }
      pollTimer = setTimeout(() => void tick(), pollInterval)
    }
    pollTimer = setTimeout(() => void tick(), pollInterval)
  }

  const send = async (text: string): Promise<void> => {
    const t = text.trim()
    if (!t) return
    const ts = now()
    // rapid double-submit of the SAME text inside the window is a duplicate:
    // covers both a concurrent second click and an immediate resend after
    // the first one completed (double Enter / double MIC tap).
    if (lastAttempt && lastAttempt.text === t && ts - lastAttempt.at < dupWindow) {
      deps.onNote('Duplicate send ignored.')
      return
    }
    stopPolling()
    lastAttempt = { text: t, at: ts }
    inFlight = t
    deps.onEcho(t)
    try {
      const r = await deps.post(t)
      if (r.reply) {
        deps.onReply(r.reply)
      } else if (!r.done) {
        // Accepted but not finished: the completion (or reply) should come
        // over the WS; the bounded history poll guarantees the UI settles.
        deps.onNote('Still working — watching for the reply…')
        inFlight = null
        pollForReply(ts)
        return
      } else if (r.handled === 'confirmation') {
        deps.onNote('Confirmation recorded.')
      }
      inFlight = null
    } catch (err) {
      inFlight = null
      const msg = err instanceof Error ? err.message : String(err)
      deps.onFail(`Send failed: ${msg}`)
      // The POST may still have reached the server (e.g. timeout after
      // start). One bounded poll gives the computed reply a chance to land.
      pollForReply(ts)
    }
  }

  return {
    send,
    dispose: stopPolling,
    inFlight: () => inFlight,
    polling: () => pollTimer !== null,
  }
}
