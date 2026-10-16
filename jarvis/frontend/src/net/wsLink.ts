/**
 * Resilient WebSocket link for the HUD.
 *
 * WHY: the previous implementation opened one socket on mount and never
 * watched it — a silent serverless connection death (observed as close
 * code 1006 in production) left the client with no link, while POSTs kept
 * returning 200 into an instance the client could no longer hear. This
 * link provides:
 *   - an application-level ping every `pingIntervalMs` (keeps idle
 *     function instances warm and confirms liveness),
 *   - a watchdog that force-closes a socket with no inbound traffic for
 *     `deadAfterMs` (silent death → fast detection instead of hanging),
 *   - reconnect with bounded exponential backoff + jitter, unlimited
 *     attempts, and immediate reconnection on browser online events,
 *   - tracked sockets so tests can prove no listener/socket leaks.
 */

export type WsLinkDeps = {
  url: string
  /** Injected for tests; defaults to the global WebSocket. */
  WebSocketImpl?: unknown
  onOpen?: () => void
  onMessage?: (data: unknown) => void
  onClose?: () => void
  onStatus?: (connected: boolean) => void
  pingIntervalMs?: number
  deadAfterMs?: number
  watchdogTickMs?: number
  retryBaseMs?: number
  retryMaxMs?: number
  /** Injected timers for deterministic tests. */
  setTimer?: (fn: () => void, ms: number) => unknown
  clearTimer?: (id: unknown) => void
  now?: () => number
}

export type WsLink = {
  /** Current connection attempt's socket (null when disconnected). */
  socket: () => unknown
  send: (payload: unknown) => boolean
  /** Close now and do not reconnect (component unmount). */
  close: () => void
  /** Force an immediate reconnect (e.g. browser came back online). */
  reconnectNow: () => void
  /** Number of sockets ever opened minus closed — for leak tests. */
  openSockets: () => number
  /** Whether a retry is scheduled — for tests. */
  retryScheduled: () => boolean
}

type AnySocket = {
  readyState: number
  send: (data: string) => void
  close: () => void
  onopen: (() => void) | null
  onmessage: ((evt: { data: unknown }) => void) | null
  onerror: (() => void) | null
  onclose: (() => void) | null
}

const OPEN = 1

export function createWsLink(deps: WsLinkDeps): WsLink {
  const pingInterval = deps.pingIntervalMs ?? 20000
  const deadAfter = deps.deadAfterMs ?? 45000
  const watchdogTick = deps.watchdogTickMs ?? 10000
  const retryBase = deps.retryBaseMs ?? 1000
  const retryMax = deps.retryMaxMs ?? 10000
  const now = deps.now ?? Date.now
  const setTimer = deps.setTimer ?? ((fn: () => void, ms: number) => setTimeout(fn, ms))
  const clearTimer =
    deps.clearTimer ?? ((id: unknown) => clearTimeout(id as ReturnType<typeof setTimeout>))

  let current: AnySocket | null = null
  let lastMsgAt = 0
  let retries = 0
  let closedByUser = false
  let pingTimer: unknown = null
  let watchTimer: unknown = null
  let retryTimer: unknown = null
  let openedTotal = 0
  let liveSockets = 0

  const clearTimers = (): void => {
    if (pingTimer !== null) clearTimer(pingTimer)
    if (watchTimer !== null) clearTimer(watchTimer)
    pingTimer = null
    watchTimer = null
  }

  const stopRetry = (): void => {
    if (retryTimer !== null) clearTimer(retryTimer)
    retryTimer = null
  }

  const connect = (): void => {
    if (closedByUser) return
    const Impl =
      (deps.WebSocketImpl as undefined | (new (url: string) => AnySocket)) ??
      (globalThis as { WebSocket?: new (url: string) => AnySocket }).WebSocket
    if (!Impl) {
      deps.onStatus?.(false)
      return
    }
    let sock: AnySocket
    try {
      sock = new Impl(deps.url)
    } catch {
      scheduleRetry()
      return
    }
    stopRetry()
    openedTotal += 1
    liveSockets += 1
    current = sock

    sock.onopen = () => {
      if (current !== sock) return
      retries = 0
      lastMsgAt = now()
      deps.onStatus?.(true)
      // ping keeps the serverless instance warm and proves liveness
      const ping = (): void => {
        if (current !== sock) return
        if (sock.readyState === OPEN) {
          try {
            sock.send(JSON.stringify({ type: 'ping' }))
          } catch {
            /* handled by close/error */
          }
        }
        pingTimer = setTimer(ping, pingInterval)
      }
      pingTimer = setTimer(ping, pingInterval)
      // watchdog: no inbound traffic for deadAfter → force a clean close,
      // which triggers the normal reconnect path instead of a silent hang
      const watch = (): void => {
        if (current !== sock) return
        if (now() - lastMsgAt > deadAfter) {
          try {
            sock.close()
          } catch {
            /* already closing */
          }
          return
        }
        watchTimer = setTimer(watch, watchdogTick)
      }
      watchTimer = setTimer(watch, watchdogTick)
      deps.onOpen?.()
    }

    sock.onmessage = (evt) => {
      if (current !== sock) return
      lastMsgAt = now()
      try {
        deps.onMessage?.(JSON.parse(String(evt.data)))
      } catch {
        /* non-JSON frame (e.g. raw pong) — only liveness matters */
      }
    }

    sock.onerror = () => {
      try {
        sock.close()
      } catch {
        /* ignore */
      }
    }

    sock.onclose = () => {
      liveSockets -= 1
      if (current !== sock) return
      current = null
      clearTimers()
      deps.onStatus?.(false)
      deps.onClose?.()
      if (!closedByUser) scheduleRetry()
    }
  }

  const scheduleRetry = (): void => {
    if (closedByUser || retryTimer !== null) return
    const backoff = Math.min(retryBase * 2 ** retries, retryMax)
    const delay = backoff + Math.floor(Math.random() * Math.min(backoff, 500)) // jitter
    retries += 1
    retryTimer = setTimer(() => {
      retryTimer = null
      connect()
    }, delay)
  }

  const hardReset = (): void => {
    const sock = current
    current = null
    clearTimers()
    if (sock) {
      sock.onopen = null
      sock.onmessage = null
      sock.onerror = null
      sock.onclose = null
      try {
        sock.close()
      } catch {
        /* ignore */
      }
      liveSockets -= 1
    }
  }

  connect()

  return {
    socket: () => current,
    send: (payload: unknown) => {
      const sock = current
      if (!sock || sock.readyState !== OPEN) return false
      try {
        sock.send(JSON.stringify(payload))
        return true
      } catch {
        return false
      }
    },
    close: () => {
      closedByUser = true
      stopRetry()
      hardReset()
      deps.onStatus?.(false) // an intentional close is still a disconnected UI
    },
    reconnectNow: () => {
      if (closedByUser) return
      retries = 0
      stopRetry()
      hardReset()
      connect()
    },
    openSockets: () => liveSockets,
    retryScheduled: () => retryTimer !== null,
  }
}
