import { describe, expect, it } from 'vitest'
import {
  RESET_ON_RECONNECT,
  STUCK_STATES,
  appendConversationEntry,
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
