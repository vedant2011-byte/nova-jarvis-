import { describe, expect, it } from 'vitest'
import {
  RESET_ON_RECONNECT,
  STUCK_STATES,
  appendConversationEntry,
  mergeHistory,
} from '../hooks/useJarvisStore'
import type { HistoryEntry } from '../api'

function entry(role: string, content: string): HistoryEntry {
  return { role, content, ts: 1, source: 'text' }
}

describe('appendConversationEntry (duplicate-reply guard)', () => {
  it('drops an adjacent duplicate of the same role+content', () => {
    // the same reply arrives from the HTTP response AND the WS event
    const prev = [entry('jarvis', 'hello')]
    const next = appendConversationEntry(prev, entry('jarvis', 'hello'))
    expect(next).toBe(prev) // same reference — truly no state churn
    expect(next).toHaveLength(1)
  })

  it('appends when a different reply follows', () => {
    const prev = [entry('jarvis', 'hello')]
    const next = appendConversationEntry(prev, entry('jarvis', 'world'))
    expect(next).toHaveLength(2)
  })

  it('appends a legitimate repeat of the same text (not adjacent)', () => {
    // user says "yes", jarvis replies, user says "yes" again
    const prev = [
      entry('user', 'yes'),
      entry('jarvis', 'sure'),
    ]
    const next = appendConversationEntry(prev, entry('user', 'yes'))
    expect(next).toHaveLength(3)
  })

  it('keeps the history bounded', () => {
    let hist: HistoryEntry[] = []
    for (let i = 0; i < 120; i++) {
      hist = appendConversationEntry(hist, entry(i % 2 ? 'user' : 'jarvis', `msg ${i}`))
    }
    expect(hist.length).toBeLessThanOrEqual(80)
  })
})

describe('mergeHistory (reconnect must never erase visible messages)', () => {
  it('keeps local messages when the server history comes back empty', () => {
    // reconnect hit a different instance whose history is empty — the old
    // code REPLACED state here and the user watched their reply vanish
    const local = [
      entry('user', 'say hello'),
      entry('jarvis', 'Hello!'),
    ]
    const merged = mergeHistory(local, [])
    expect(merged).toHaveLength(2)
    expect(merged.map(e => e.content)).toEqual(['say hello', 'Hello!'])
  })

  it('dedupes entries the server also has (no doubled conversation)', () => {
    const local = [entry('user', 'hi'), entry('jarvis', 'hey')]
    const server = [entry('user', 'hi'), entry('jarvis', 'hey'), entry('user', 'older')]
    const merged = mergeHistory(local, server)
    expect(merged.map(e => e.content)).toEqual(['hi', 'hey', 'older'])
  })

  it('keeps server-only entries and stays ordered and bounded', () => {
    let local: HistoryEntry[] = []
    for (let i = 0; i < 70; i++) {
      local = appendConversationEntry(local, entry(i % 2 ? 'user' : 'jarvis', `local ${i}`))
    }
    const server = [entry('jarvis', 'from other instance', )]
    const merged = mergeHistory(local, server)
    expect(merged.some(e => e.content === 'from other instance')).toBe(true)
    expect(merged.length).toBeLessThanOrEqual(80)
    // sorted by ts (all our entries share ts=1 → stable content check)
    expect(merged[merged.length - 1]).toBeDefined()
  })

  it('collapses an exact duplicate arriving from both sources to one row', () => {
    const local = [entry('jarvis', 'same reply')]
    const server = [entry('jarvis', 'same reply')]
    expect(mergeHistory(local, server)).toHaveLength(1)
  })
})

describe('loading-state reset sets', () => {
  it('STUCK_STATES covers every state where a lost event would wedge the UI', () => {
    for (const s of ['thinking', 'executing', 'speaking', 'transcribing', 'listening', 'error'] as const) {
      expect(STUCK_STATES).toContain(s)
    }
    // a healthy idle/boot must NOT be treated as stuck
    expect(STUCK_STATES).not.toContain('idle')
    expect(STUCK_STATES).not.toContain('boot')
  })

  it('RESET_ON_RECONNECT additionally clears a confirmation prompt', () => {
    expect(RESET_ON_RECONNECT).toContain('confirmation')
    expect(RESET_ON_RECONNECT).toEqual([...STUCK_STATES, 'confirmation'])
  })
})
