import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createWsLink } from '../net/wsLink'

/** Minimal controllable WebSocket for lifecycle tests. */
class FakeWS {
  static instances: FakeWS[] = []
  readyState = 0 // 0 connecting, 1 open, 3 closed
  sent: string[] = []
  url: string
  onopen: (() => void) | null = null
  onmessage: ((e: { data: unknown }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null

  constructor(url: string) {
    this.url = url
    FakeWS.instances.push(this)
  }

  send(data: string): void {
    if (this.readyState !== 1) throw new Error('socket not open')
    this.sent.push(data)
  }

  close(): void {
    if (this.readyState === 3) return
    this.readyState = 3
    this.onclose?.()
  }

  // -- test drivers --
  serverOpen(): void {
    this.readyState = 1
    this.onopen?.()
  }

  serverMessage(obj: unknown): void {
    this.onmessage?.({ data: JSON.stringify(obj) })
  }

  serverRawMessage(text: string): void {
    this.onmessage?.({ data: text })
  }

  /** Abrupt server-side death (the production 1006 case). */
  die(): void {
    this.readyState = 3
    this.onclose?.()
  }
}

beforeEach(() => {
  FakeWS.instances = []
  vi.useFakeTimers()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

function makeLink(overrides: Record<string, unknown> = {}) {
  const events: Record<string, unknown[]> = {
    open: [],
    close: [],
    status: [],
    message: [],
  }
  const link = createWsLink({
    url: 'wss://example.test/ws',
    WebSocketImpl: FakeWS,
    onOpen: () => events.open.push(true),
    onClose: () => events.close.push(true),
    onStatus: c => events.status.push(c),
    onMessage: d => events.message.push(d),
    ...overrides,
  })
  return { link, events }
}

describe('connection lifecycle', () => {
  it('opens immediately and reports status', () => {
    const { link, events } = makeLink()
    expect(FakeWS.instances).toHaveLength(1)
    FakeWS.instances[0].serverOpen()
    expect(events.status).toEqual([true])
    expect(events.open).toHaveLength(1)
    expect(link.openSockets()).toBe(1)
  })

  it('dispatches parsed JSON messages and survives malformed frames', () => {
    const { link, events } = makeLink()
    const ws = FakeWS.instances[0]
    ws.serverOpen()
    ws.serverMessage({ type: 'hello', data: {} })
    ws.serverRawMessage('not-json{')
    ws.serverMessage({ type: 'boot_done', data: {} })
    expect(events.message).toHaveLength(2)
    expect(link.openSockets()).toBe(1) // malformed frame did not kill the socket
  })

  it('sends application pings on the keepalive interval', () => {
    makeLink({ pingIntervalMs: 1000 })
    const ws = FakeWS.instances[0]
    ws.serverOpen()
    vi.advanceTimersByTime(3100)
    const pings = ws.sent.filter(s => s.includes('"ping"'))
    expect(pings.length).toBeGreaterThanOrEqual(3)
  })
})

describe('silent death and recovery (production 1006 hang)', () => {
  it('watchdog force-closes a socket with no inbound traffic and reconnects', () => {
    const { link, events } = makeLink({
      pingIntervalMs: 1000,
      deadAfterMs: 5000,
      watchdogTickMs: 1000,
      retryBaseMs: 500,
      retryMaxMs: 2000,
    })
    const first = FakeWS.instances[0]
    first.serverOpen()
    // no inbound traffic at all → watchdog must kill it after deadAfter
    vi.advanceTimersByTime(7000)
    expect(first.readyState).toBe(3)
    expect(events.close).toHaveLength(1)
    expect(events.status).toContain(false)
    // bounded backoff → a new socket appears
    vi.advanceTimersByTime(3000)
    expect(FakeWS.instances.length).toBeGreaterThanOrEqual(2)
    const second = FakeWS.instances[FakeWS.instances.length - 1]
    second.serverOpen()
    expect(events.status.filter(c => c === true).length).toBe(2)
    expect(link.openSockets()).toBe(1) // old socket released — no leak
  })

  it('inbound messages keep the connection alive past deadAfter', () => {
    makeLink({ deadAfterMs: 5000, watchdogTickMs: 1000, retryBaseMs: 500 })
    const ws = FakeWS.instances[0]
    ws.serverOpen()
    for (let t = 0; t < 40; t++) {
      vi.advanceTimersByTime(1000)
      ws.serverMessage({ type: 'ping', data: {} }) // server chatter
    }
    expect(ws.readyState).toBe(1) // never killed
    expect(FakeWS.instances).toHaveLength(1)
  })

  it('abrupt death mid-conversation recovers with a fresh socket', () => {
    const { link, events } = makeLink({ retryBaseMs: 300, retryMaxMs: 1000 })
    const first = FakeWS.instances[0]
    first.serverOpen()
    first.serverMessage({ type: 'conversation', data: { role: 'jarvis' } })
    first.die() // production close code 1006 shape
    expect(events.close).toHaveLength(1)
    vi.advanceTimersByTime(1500)
    expect(FakeWS.instances.length).toBe(2)
    const second = FakeWS.instances[1]
    second.serverOpen()
    second.serverMessage({ type: 'conversation', data: { role: 'jarvis' } })
    expect(events.message).toHaveLength(2)
    expect(link.openSockets()).toBe(1)
  })

  it('repeated deaths stay bounded: only one live socket ever (listener leak guard)', () => {
    const { link } = makeLink({ retryBaseMs: 100, retryMaxMs: 300 })
    for (let i = 0; i < 5; i++) {
      const ws = FakeWS.instances[FakeWS.instances.length - 1]
      ws.serverOpen()
      ws.die()
      vi.advanceTimersByTime(1000)
    }
    // 1 initial + 5 reconnects; each open resets backoff, delays ≤ 1s
    expect(FakeWS.instances).toHaveLength(6)
    const alive = FakeWS.instances.filter(w => w.readyState !== 3)
    expect(alive.length).toBeLessThanOrEqual(1) // never a pile-up of sockets
    expect(link.openSockets()).toBeLessThanOrEqual(1)
  })

  it('reconnectNow bypasses the pending backoff immediately', () => {
    const { link } = makeLink({ retryBaseMs: 10000, retryMaxMs: 20000 })
    const first = FakeWS.instances[0]
    first.serverOpen()
    first.die()
    expect(link.retryScheduled()).toBe(true)
    const before = FakeWS.instances.length
    link.reconnectNow()
    expect(FakeWS.instances.length).toBe(before + 1) // no 10s wait
    expect(link.retryScheduled()).toBe(false)
  })
})

describe('shutdown semantics', () => {
  it('close() releases the socket, cancels reconnect and never comes back', () => {
    const { link, events } = makeLink({ retryBaseMs: 100 })
    const ws = FakeWS.instances[0]
    ws.serverOpen()
    link.close()
    expect(ws.readyState).toBe(3)
    expect(link.openSockets()).toBe(0)
    expect(link.retryScheduled()).toBe(false)
    const before = FakeWS.instances.length
    vi.advanceTimersByTime(60000)
    expect(FakeWS.instances).toHaveLength(before) // no zombie reconnects
    expect(events.status[events.status.length - 1]).toBe(false)
    expect(link.send({ type: 'chat' })).toBe(false) // send while dead is safe
  })

  it('send() delivers only on an open socket', () => {
    const { link } = makeLink()
    const ws = FakeWS.instances[0]
    expect(link.send({ type: 'chat' })).toBe(false) // still connecting
    ws.serverOpen()
    expect(link.send({ type: 'chat' })).toBe(true)
    expect(ws.sent.some(s => s.includes('"chat"'))).toBe(true)
  })

  it('missing WebSocket implementation reports disconnected without throwing', () => {
    vi.stubGlobal('WebSocket', undefined)
    const events: boolean[] = []
    let link!: ReturnType<typeof createWsLink>
    expect(() => {
      link = createWsLink({
        url: 'wss://example.test/ws',
        onStatus: c => events.push(c),
      })
    }).not.toThrow()
    expect(events).toEqual([false])
    expect(FakeWS.instances).toHaveLength(0) // no socket attempted
    expect(() => link.close()).not.toThrow()
  })
})
