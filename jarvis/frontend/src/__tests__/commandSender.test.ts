import { afterEach, describe, expect, it, vi } from 'vitest'
import { createCommandSender, type CommandSenderDeps, type HistoryEntryLite } from '../net/commandSender'

type Handlers = {
  echoed: string[]
  replied: string[]
  noted: string[]
  failed: string[]
}

function makeDeps(overrides: Partial<CommandSenderDeps> = {}): { deps: CommandSenderDeps; h: Handlers } {
  const h: Handlers = { echoed: [], replied: [], noted: [], failed: [] }
  const deps: CommandSenderDeps = {
    post: async () => ({ accepted: true, done: true, reply: 'ok' }),
    onEcho: t => h.echoed.push(t),
    onReply: t => h.replied.push(t),
    onNote: m => h.noted.push(m),
    onFail: m => h.failed.push(m),
    ...overrides,
  }
  return { deps, h }
}

afterEach(() => {
  vi.useRealTimers()
})

describe('happy path', () => {
  it('echoes optimistically and delivers the reply', async () => {
    const { deps, h } = makeDeps({
      post: async () => ({ accepted: true, done: true, reply: 'hello there' }),
    })
    const s = createCommandSender(deps)
    await s.send('hi')
    expect(h.echoed).toEqual(['hi'])
    expect(h.replied).toEqual(['hello there'])
    expect(h.failed).toEqual([])
    expect(s.inFlight()).toBeNull()
    expect(s.polling()).toBe(false)
  })

  it('ignores empty/whitespace input without calling post', async () => {
    const post = vi.fn(async () => ({ done: true, reply: 'x' }))
    const { deps } = makeDeps({ post })
    const s = createCommandSender(deps)
    await s.send('   ')
    expect(post).not.toHaveBeenCalled()
  })

  it('completes 20 sequential turns with exactly one reply each', async () => {
    vi.useFakeTimers()
    let n = 0
    const { deps, h } = makeDeps({
      post: async (text: string) => ({ accepted: true, done: true, reply: `echo:${text}` }),
      pollHistory: async () => [],
      now: () => Date.now(),
    })
    const s = createCommandSender(deps)
    for (let i = 0; i < 20; i++) {
      await s.send(`turn-${i}`)
      n++
    }
    expect(h.echoed).toHaveLength(20)
    expect(h.replied).toHaveLength(20)
    expect(h.failed).toEqual([])
    for (let i = 0; i < 20; i++) expect(h.replied[i]).toBe(`echo:turn-${i}`)
    expect(s.polling()).toBe(false)
    expect(n).toBe(20)
  })
})

describe('duplicate sends', () => {
  it('suppresses an identical resend inside the dedupe window', async () => {
    const post = vi.fn(async () => ({ done: true, reply: 'once' }))
    const { deps, h } = makeDeps({ post })
    const s = createCommandSender(deps)
    await s.send('same words')
    await s.send('same words') // within 1.5s, nothing changed since last send
    expect(post).toHaveBeenCalledTimes(1)
    expect(h.echoed).toEqual(['same words'])
    expect(h.noted.some(m => m.includes('Duplicate'))).toBe(true)
  })

  it('allows a resend after the dedupe window', async () => {
    vi.useFakeTimers()
    let t = 1_000_000
    const post = vi.fn(async () => ({ done: true, reply: 'again' }))
    const { deps } = makeDeps({ post, now: () => t })
    const s = createCommandSender(deps)
    await s.send('same words')
    t += 5000 // beyond 1.5s
    await s.send('same words')
    expect(post).toHaveBeenCalledTimes(2)
  })

  it('treats different text as a new send even inside the window', async () => {
    const post = vi.fn(async () => ({ done: true, reply: 'ok' }))
    const { deps } = makeDeps({ post })
    const s = createCommandSender(deps)
    await s.send('first')
    await s.send('second')
    expect(post).toHaveBeenCalledTimes(2)
  })
})

describe('provider/POST errors reset the UI (loading never sticks)', () => {
  it('calls onFail exactly once when the POST rejects, then still surfaces a reply found via history', async () => {
    vi.useFakeTimers()
    const entries: HistoryEntryLite[] = [
      { role: 'user', content: 'hi', ts: Date.now() - 1000 },
      { role: 'jarvis', content: 'recovered reply', ts: Date.now() },
    ]
    const { deps, h } = makeDeps({
      post: async () => {
        throw new Error('503 provider unavailable')
      },
      pollHistory: async () => entries,
      now: () => Date.now(),
    })
    const s = createCommandSender(deps)
    await s.send('hi')
    expect(h.failed).toHaveLength(1)
    expect(h.failed[0]).toContain('Send failed')
    expect(h.failed[0]).toContain('503 provider unavailable')
    expect(s.polling()).toBe(true)
    await vi.advanceTimersByTimeAsync(2100) // first history poll
    expect(h.replied).toEqual(['recovered reply'])
    expect(h.failed).toHaveLength(1) // never double-reported
  })

  it('an accepted-but-unfinished command settles through history polling', async () => {
    vi.useFakeTimers()
    let polls = 0
    const { deps, h } = makeDeps({
      post: async () => ({ accepted: true, done: false }),
      pollHistory: async () => {
        polls++
        if (polls < 3) return [] // not finished yet
        return [{ role: 'jarvis', content: 'eventual reply', ts: Date.now() }]
      },
    })
    const s = createCommandSender(deps)
    await s.send('slow task')
    expect(h.noted.some(m => m.includes('Still working'))).toBe(true)
    await vi.advanceTimersByTimeAsync(2100) // poll 1: empty
    await vi.advanceTimersByTimeAsync(2100) // poll 2: empty
    await vi.advanceTimersByTimeAsync(2100) // poll 3: reply ready
    expect(h.replied).toEqual(['eventual reply'])
    expect(h.failed).toEqual([])
    expect(s.polling()).toBe(false)
  })

  it('times out with onFail when no reply ever appears (bounded wait)', async () => {
    vi.useFakeTimers()
    const { deps, h } = makeDeps({
      post: async () => ({ accepted: true, done: false }),
      pollHistory: async () => [],
      pollTimeoutMs: 5000,
      pollIntervalMs: 1000,
    })
    const s = createCommandSender(deps)
    await s.send('never finishes')
    await vi.advanceTimersByTimeAsync(1100 * 6) // past the 5s deadline
    expect(h.failed).toHaveLength(1)
    expect(h.failed[0]).toContain('timed out')
    expect(s.polling()).toBe(false) // timer released
  })

  it('survives a persistently failing history endpoint without unhandled rejections', async () => {
    vi.useFakeTimers()
    const { deps, h } = makeDeps({
      post: async () => ({ accepted: true, done: false }),
      pollHistory: async () => {
        throw new Error('history 500')
      },
      pollTimeoutMs: 3000,
      pollIntervalMs: 1000,
    })
    const s = createCommandSender(deps)
    await s.send('hello')
    await vi.advanceTimersByTimeAsync(1100 * 5)
    expect(h.failed).toHaveLength(1)
    expect(s.polling()).toBe(false)
  })

  it('dispose cancels a pending poll so nothing fires after stop/unmount', async () => {
    vi.useFakeTimers()
    const { deps, h } = makeDeps({
      post: async () => ({ accepted: true, done: false }),
      pollHistory: async () => [{ role: 'jarvis', content: 'too late', ts: Date.now() }],
    })
    const s = createCommandSender(deps)
    await s.send('will be stopped')
    expect(s.polling()).toBe(true)
    s.dispose()
    expect(s.polling()).toBe(false)
    await vi.advanceTimersByTimeAsync(10000)
    expect(h.replied).toEqual([])
    expect(h.failed).toEqual([])
  })
})
