import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  BrowserSpeech,
  browserSpeechSupported,
  defaultSpeechCtor,
  type SpeechErrorCode,
  type SpeechHandlers,
  type SpeechRecognitionLike,
} from '../voice/browserSpeech'

/** Controllable recognition implementation. */
class FakeRec implements SpeechRecognitionLike {
  static instances: FakeRec[] = []
  lang = ''
  continuous = false
  interimResults = false
  onstart: (() => void) | null = null
  onresult: ((event: unknown) => void) | null = null
  onerror: ((event: unknown) => void) | null = null
  onend: (() => void) | null = null
  started = false
  stopped = false
  aborted = false

  constructor() {
    FakeRec.instances.push(this)
  }

  start(): void {
    this.started = true
  }

  stop(): void {
    // Real browsers deliver any pending final result BEFORE firing end;
    // the test drives that sequence explicitly.
    this.stopped = true
  }

  abort(): void {
    this.aborted = true
    this.onend?.()
  }

  // -- test drivers --
  fireStart(): void {
    this.onstart?.()
  }

  fireResult(finals: string[], interim = ''): void {
    const results: { isFinal: boolean; 0: { transcript: string } }[] = finals.map(t => ({
      isFinal: true,
      0: { transcript: t },
    }))
    if (interim) {
      results.push({ isFinal: false, 0: { transcript: interim } })
    }
    this.onresult?.({ resultIndex: 0, results })
  }

  fireError(code: string): void {
    this.onerror?.({ error: code })
  }

  fireEnd(): void {
    this.onend?.()
  }
}

type Seen = {
  starts: number
  finals: string[]
  interims: string[]
  errors: { code: SpeechErrorCode; message: string }[]
  ends: number
}

const speeches: BrowserSpeech[] = []

function makeSpeech(ctor: (new () => SpeechRecognitionLike) | null = FakeRec): {
  speech: BrowserSpeech
  seen: Seen
} {
  const seen: Seen = { starts: 0, finals: [], interims: [], errors: [], ends: 0 }
  const handlers: SpeechHandlers = {
    onStart: () => seen.starts++,
    onFinal: t => seen.finals.push(t),
    onInterim: t => seen.interims.push(t),
    onError: (code, message) => seen.errors.push({ code, message }),
    onEnd: () => seen.ends++,
  }
  const speech = new BrowserSpeech(ctor, handlers)
  speeches.push(speech) // disposed in afterEach → no leaked watchdog timers
  return { speech, seen }
}

beforeEach(() => {
  FakeRec.instances = []
})

afterEach(() => {
  for (const s of speeches) s.dispose()
  speeches.length = 0
  vi.unstubAllGlobals()
})

describe('unsupported browsers', () => {
  it('reports unsupported instead of throwing when no ctor exists', () => {
    const { speech, seen } = makeSpeech(null)
    expect(speech.start()).toBe('unsupported')
    expect(seen.errors).toHaveLength(1)
    expect(seen.errors[0].code).toBe('unsupported')
    expect(seen.errors[0].message).toContain('not supported')
    expect(speech.active).toBe(false)
  })

  it('defaultSpeechCtor reads the global (unprefixed or webkit) safely', () => {
    vi.stubGlobal('SpeechRecognition', undefined)
    vi.stubGlobal('webkitSpeechRecognition', undefined)
    expect(defaultSpeechCtor()).toBeNull()
    expect(browserSpeechSupported()).toBe(false)
    vi.stubGlobal('webkitSpeechRecognition', FakeRec)
    expect(defaultSpeechCtor()).toBe(FakeRec)
    expect(browserSpeechSupported()).toBe(true)
  })
})

describe('successful transcript path', () => {
  it('delivers a final transcript exactly once and cleans up on end', () => {
    const { speech, seen } = makeSpeech()
    expect(speech.start()).toBe('started')
    expect(speech.start()).toBe('already-active') // no duplicate session
    expect(FakeRec.instances).toHaveLength(1)
    const rec = FakeRec.instances[0]
    rec.fireStart()
    expect(speech.active).toBe(true)
    expect(seen.starts).toBe(1)
    rec.fireResult(['Hello there. '])
    expect(seen.finals).toEqual(['Hello there.'])
    rec.fireEnd()
    expect(speech.active).toBe(false)
    expect(seen.ends).toBe(1)
    expect(seen.errors).toEqual([]) // final delivered → no empty error
  })

  it('suppresses a re-delivered duplicate final (Chromium restart quirk)', () => {
    const { speech, seen } = makeSpeech()
    speech.start()
    const rec = FakeRec.instances[0]
    rec.fireStart()
    rec.fireResult(['same words'])
    rec.fireResult(['same words']) // duplicated final
    expect(seen.finals).toEqual(['same words'])
  })

  it('emits interim results while listening', () => {
    const { speech, seen } = makeSpeech()
    speech.start()
    const rec = FakeRec.instances[0]
    rec.fireStart()
    rec.fireResult([], 'hel')
    rec.fireResult(['hello'])
    expect(seen.interims).toEqual(['hel'])
    expect(seen.finals).toEqual(['hello'])
  })

  it('a final arriving after graceful stop is still delivered', () => {
    const { speech, seen } = makeSpeech()
    speech.start()
    const rec = FakeRec.instances[0]
    rec.fireStart()
    speech.stop()
    expect(rec.stopped).toBe(true)
    expect(speech.active).toBe(true) // still winding down until end fires
    rec.fireResult(['late but valid']) // pending final lands first…
    rec.fireEnd()                       // …then end
    expect(speech.active).toBe(false)
    expect(seen.finals).toEqual(['late but valid'])
    expect(seen.errors).toEqual([]) // session produced speech → no empty error
    expect(seen.ends).toBe(1)
  })
})

describe('permission denial', () => {
  it("maps 'not-allowed' to a clear permission error and never adds an empty error", () => {
    const { speech, seen } = makeSpeech()
    speech.start()
    const rec = FakeRec.instances[0]
    rec.fireStart()
    rec.fireError('not-allowed')
    expect(seen.errors).toHaveLength(1)
    expect(seen.errors[0].code).toBe('permission-denied')
    expect(seen.errors[0].message).toContain('permission')
    expect(speech.active).toBe(false)
    rec.fireEnd() // browsers fire end after the error
    expect(seen.errors).toHaveLength(1) // no second "didn't catch that"
    expect(seen.ends).toBe(1)
  })

  it("maps 'service-not-allowed' and 'audio-capture' honestly", () => {
    const a = makeSpeech()
    a.speech.start()
    FakeRec.instances[0].fireError('service-not-allowed')
    expect(a.seen.errors[0].code).toBe('permission-denied')

    FakeRec.instances = []
    const b = makeSpeech()
    b.speech.start()
    FakeRec.instances[0].fireError('audio-capture')
    expect(b.seen.errors[0].code).toBe('no-mic')
    expect(b.speech.active).toBe(false)
  })
})

describe('empty and silent sessions', () => {
  it('reports an empty transcript honestly when the session ends with nothing', () => {
    const { speech, seen } = makeSpeech()
    speech.start()
    const rec = FakeRec.instances[0]
    rec.fireStart()
    rec.fireEnd() // no results at all
    expect(seen.errors).toHaveLength(1)
    expect(seen.errors[0].code).toBe('empty')
    expect(seen.ends).toBe(1)
    expect(speech.active).toBe(false)
  })

  it("'no-speech' is reported once, not followed by a second empty error", () => {
    const { speech, seen } = makeSpeech()
    speech.start()
    const rec = FakeRec.instances[0]
    rec.fireStart()
    rec.fireError('no-speech')
    rec.fireEnd()
    expect(seen.errors.map(e => e.code)).toEqual(['no-speech'])
    expect(seen.ends).toBe(1)
  })
})

describe('cancellation and cleanup', () => {
  it('cancel() aborts the session and suppresses everything afterwards', () => {
    const { speech, seen } = makeSpeech()
    speech.start()
    const rec = FakeRec.instances[0]
    rec.fireStart()
    speech.cancel()
    expect(rec.aborted).toBe(true)
    expect(speech.active).toBe(false)
    // late events from the cancelled session must be inert
    rec.fireResult(['should not appear'])
    rec.fireEnd()
    expect(seen.finals).toEqual([])
    expect(seen.errors).toEqual([])
    expect(seen.ends).toBe(0) // cancellation is silent — no empty error, no end
  })

  it('dispose() detaches handlers so nothing fires after unmount', () => {
    const { speech, seen } = makeSpeech()
    speech.start()
    const rec = FakeRec.instances[0]
    rec.fireStart()
    speech.dispose()
    expect(speech.active).toBe(false)
    // handlers were detached from the recognition object itself
    expect(rec.onresult).toBeNull()
    expect(rec.onerror).toBeNull()
    expect(rec.onend).toBeNull()
    expect(seen.starts).toBe(1)
    expect(seen.ends).toBe(0)
    expect(seen.errors).toEqual([])
  })

  it('stopping when never started is a safe no-op', () => {
    const { speech, seen } = makeSpeech()
    speech.stop()
    speech.cancel()
    expect(seen.ends).toBe(0)
    expect(FakeRec.instances).toHaveLength(0)
  })
})

describe('start watchdog (service never answers)', () => {
  // Some browser/OS combos fire NO event at all for a blocked mic
  // (observed on the deployed site) — the watchdog converts that hang
  // into visible feedback instead of an infinite silent wait.

  it('reports a timeout quickly when permission is known-denied', async () => {
    vi.useFakeTimers()
    try {
      vi.stubGlobal('navigator', {
        permissions: { query: async () => ({ state: 'denied' }) },
      })
      const { speech, seen } = makeSpeech()
      speech.start()
      const rec = FakeRec.instances[0]
      await Promise.resolve() // permission tune resolves → tightens to 3s
      await Promise.resolve()
      await vi.advanceTimersByTimeAsync(1000)
      expect(seen.errors).toEqual([]) // still within the tightened window
      await vi.advanceTimersByTimeAsync(2500)
      expect(seen.errors).toHaveLength(1)
      expect(seen.errors[0].code).toBe('timeout')
      expect(seen.errors[0].message).toContain('did not respond')
      expect(speech.active).toBe(false)
      expect(rec.aborted).toBe(true)
      rec.fireEnd() // late events from the aborted session are inert
      expect(seen.ends).toBe(0)
      expect(seen.errors).toHaveLength(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('waits the generous default window when permission state is unknown', async () => {
    vi.useFakeTimers()
    try {
      vi.stubGlobal('navigator', {}) // no permissions API
      const { speech, seen } = makeSpeech()
      speech.start()
      await vi.advanceTimersByTimeAsync(29000)
      expect(seen.errors).toEqual([]) // a permission prompt may still be open
      await vi.advanceTimersByTimeAsync(1500)
      expect(seen.errors).toHaveLength(1)
      expect(seen.errors[0].code).toBe('timeout')
      expect(speech.active).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  it('the first live event clears the watchdog permanently', async () => {
    vi.useFakeTimers()
    try {
      const { speech, seen } = makeSpeech()
      speech.start()
      FakeRec.instances[0].fireStart()
      await vi.advanceTimersByTimeAsync(120000) // far past every window
      expect(seen.errors).toEqual([])
      expect(seen.starts).toBe(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('an early error event also clears it (no late double-report)', async () => {
    vi.useFakeTimers()
    try {
      const { speech, seen } = makeSpeech()
      speech.start()
      await vi.advanceTimersByTimeAsync(500)
      FakeRec.instances[0].fireError('no-speech')
      await vi.advanceTimersByTimeAsync(60000) // watchdog must not fire later
      expect(seen.errors.map(e => e.code)).toEqual(['no-speech'])
      FakeRec.instances[0].fireEnd() // browsers end after the error
      expect(seen.errors.map(e => e.code)).toEqual(['no-speech']) // no empty double-report
      expect(seen.ends).toBe(1)
      expect(speech.active).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })

  it('dispose cancels a pending watchdog (no callback after unmount)', async () => {
    vi.useFakeTimers()
    try {
      const { speech, seen } = makeSpeech()
      speech.start()
      speech.dispose()
      await vi.advanceTimersByTimeAsync(120000)
      expect(seen.errors).toEqual([])
      expect(seen.ends).toBe(0)
    } finally {
      vi.useRealTimers()
    }
  })
})
