/**
 * Browser-native speech recognition (Web Speech API).
 *
 * The deployed backend runs on serverless infrastructure: it has no
 * microphone and no faster-whisper, so server-side STT can never work
 * there. The browser, however, can capture and transcribe with the user's
 * own mic — so the HUD uses this path whenever the browser supports it,
 * and falls back to the backend push-to-talk pipeline for the local
 * desktop app (where faster-whisper runs).
 *
 * This module is deliberately framework-free so it can be unit-tested with
 * a fake recognition implementation.
 */

export type SpeechErrorCode =
  | 'permission-denied'
  | 'no-speech'
  | 'no-mic'
  | 'network'
  | 'unsupported'
  | 'empty'
  | 'timeout'
  | 'unknown'

export type SpeechHandlers = {
  onStart?: () => void
  onInterim?: (text: string) => void
  /** A final transcript — submit this to chat exactly once. */
  onFinal?: (text: string) => void
  onError?: (code: SpeechErrorCode, message: string) => void
  onEnd?: () => void
}

export interface SpeechRecognitionLike {
  lang: string
  continuous: boolean
  interimResults: boolean
  onstart: (() => void) | null
  onresult: ((event: unknown) => void) | null
  onerror: ((event: unknown) => void) | null
  onend: (() => void) | null
  start(): void
  stop(): void
  abort(): void
}

export type SpeechCtor = new () => SpeechRecognitionLike

const ERROR_MESSAGES: Record<string, string> = {
  'not-allowed': 'Microphone permission denied — allow mic access in your browser settings.',
  'service-not-allowed': 'Speech recognition is blocked in this browser.',
  'no-speech': 'No speech detected — try again.',
  'audio-capture': 'No microphone found.',
  network: 'Speech recognition network error.',
}

/** The platform ctor (prefixed on Chromium), or null where unsupported. */
export function defaultSpeechCtor(): SpeechCtor | null {
  const w = globalThis as unknown as Record<string, unknown>
  return (w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null) as SpeechCtor | null
}

export function browserSpeechSupported(): boolean {
  return defaultSpeechCtor() !== null
}

export class BrowserSpeech {
  private rec: SpeechRecognitionLike | null = null
  private handlers: SpeechHandlers
  private activeFlag = false
  private finalsThisSession = 0
  private lastSubmitted = { text: '', at: 0 }
  private cancelling = false
  private sawError = false
  /** First meaningful event (start/error/end) arrived from the service. */
  private sessionLive = false
  private watchdog: ReturnType<typeof setTimeout> | null = null

  constructor(
    private readonly ctor: SpeechCtor | null,
    handlers: SpeechHandlers,
  ) {
    this.handlers = handlers
  }

  get active(): boolean {
    return this.activeFlag
  }

  /** Begin a single-utterance capture. Never throws. */
  start(): 'started' | 'unsupported' | 'already-active' {
    if (!this.ctor) {
      this.handlers.onError?.(
        'unsupported',
        'Speech recognition is not supported in this browser — try Chrome or Edge.',
      )
      return 'unsupported'
    }
    if (this.rec) return 'already-active' // started or starting — never double-start

    let rec: SpeechRecognitionLike
    try {
      rec = new this.ctor()
    } catch {
      this.handlers.onError?.('unknown', 'Speech recognition failed to start.')
      return 'unsupported'
    }
    rec.lang = 'en-US'
    rec.continuous = false
    rec.interimResults = true
    this.finalsThisSession = 0
    this.cancelling = false
    this.sawError = false
    this.sessionLive = false
    rec.onstart = () => {
      this.sessionLive = true
      this.clearWatchdog()
      this.activeFlag = true
      this.handlers.onStart?.()
    }
    rec.onresult = (event: unknown) => this.handleResult(event)
    rec.onerror = (event: unknown) => {
      this.sessionLive = true
      this.clearWatchdog()
      this.handleError(event)
    }
    rec.onend = () => {
      this.sessionLive = true
      this.clearWatchdog()
      this.handleEnd()
    }
    this.rec = rec
    try {
      rec.start()
    } catch {
      this.detach()
      this.handlers.onError?.('unknown', 'Speech recognition failed to start.')
      return 'unsupported'
    }
    this.armWatchdog(rec)
    void this.tuneWatchdogByPermission()
    return 'started'
  }

  /** Graceful stop: final results (if any) still arrive before onend. */
  stop(): void {
    if (!this.rec) return
    try {
      this.rec.stop()
    } catch {
      /* already stopped */
    }
  }

  /** Hard cancellation: nothing in flight is submitted, no empty error. */
  cancel(): void {
    this.cancelling = true
    this.activeFlag = false
    this.clearWatchdog()
    const rec = this.rec
    this.detach()
    if (rec) {
      try {
        rec.abort()
      } catch {
        /* already aborted */
      }
    }
  }

  /** Unmount cleanup: cancel and drop handlers so nothing fires later. */
  dispose(): void {
    this.cancel()
    this.handlers = {}
  }

  // ------------------------------------------------------------------
  // Start watchdog: some browser/OS combinations never fire ANY event
  // for a blocked microphone (observed in production: no onstart, no
  // onerror, no onend). Without this, the UI would wait forever with no
  // feedback. The first real event clears the timer; permission state
  // only tunes how long we wait.
  // ------------------------------------------------------------------
  private static readonly WATCHDOG_DEFAULT_MS = 30000 // covers a pending permission prompt
  private static readonly WATCHDOG_GRANTED_MS = 10000
  private static readonly WATCHDOG_DENIED_MS = 3000

  private armWatchdog(rec: SpeechRecognitionLike, ms = BrowserSpeech.WATCHDOG_DEFAULT_MS): void {
    this.clearWatchdog()
    this.watchdog = setTimeout(() => {
      this.watchdog = null
      if (this.rec !== rec || this.sessionLive || this.cancelling) return
      // service never answered — surface it instead of hanging silently
      this.sawError = true
      this.activeFlag = false
      this.detach()
      try {
        rec.abort()
      } catch {
        /* ignore */
      }
      this.handlers.onError?.(
        'timeout',
        'Speech recognition did not respond — the mic may be blocked by the browser or OS.',
      )
    }, ms)
  }

  private clearWatchdog(): void {
    if (this.watchdog !== null) clearTimeout(this.watchdog)
    this.watchdog = null
  }

  private async tuneWatchdogByPermission(): Promise<void> {
    try {
      const perms = (globalThis as { navigator?: { permissions?: {
        query: (d: { name: string }) => Promise<{ state?: string }>
      } } }).navigator?.permissions
      if (!perms?.query) return
      const status = await perms.query({ name: 'microphone' })
      if (!this.rec || this.sessionLive || this.cancelling) return
      if (status?.state === 'denied') {
        this.armWatchdog(this.rec, BrowserSpeech.WATCHDOG_DENIED_MS)
      } else if (status?.state === 'granted') {
        this.armWatchdog(this.rec, BrowserSpeech.WATCHDOG_GRANTED_MS)
      }
      // 'prompt' / unknown → keep the generous default
    } catch {
      /* permissions API unavailable — default watchdog still covers us */
    }
  }

  private detach(): void {
    const rec = this.rec
    if (rec) {
      rec.onstart = null
      rec.onresult = null
      rec.onerror = null
      rec.onend = null
    }
    this.rec = null
  }

  private handleResult(event: unknown): void {
    const ev = event as {
      resultIndex?: number
      results?: ArrayLike<{ isFinal?: boolean; 0?: { transcript?: string } }>
    }
    const results = ev?.results
    if (!results) return
    let interim = ''
    let finalText = ''
    const from = typeof ev.resultIndex === 'number' ? ev.resultIndex : 0
    for (let i = from; i < results.length; i++) {
      const r = results[i]
      const txt = String(r?.[0]?.transcript ?? '')
      if (r?.isFinal) finalText += txt
      else interim += txt
    }
    if (interim) this.handlers.onInterim?.(interim)
    const text = finalText.trim()
    if (!text) return
    // Chromium can re-deliver the same final after internal restarts; submit once.
    const now = Date.now()
    if (text === this.lastSubmitted.text && now - this.lastSubmitted.at < 1500) return
    this.lastSubmitted = { text, at: now }
    this.finalsThisSession += 1
    this.handlers.onFinal?.(text)
  }

  private handleError(event: unknown): void {
    const err = String((event as { error?: string })?.error ?? '')
    if (err === 'aborted' || this.cancelling) return
    this.sawError = true // the browser already explained this failure
    if (err === 'not-allowed' || err === 'service-not-allowed') {
      this.activeFlag = false
      this.handlers.onError?.('permission-denied', ERROR_MESSAGES[err])
    } else if (err === 'no-speech') {
      this.handlers.onError?.('no-speech', ERROR_MESSAGES['no-speech'])
    } else if (err === 'audio-capture') {
      this.activeFlag = false
      this.handlers.onError?.('no-mic', ERROR_MESSAGES['audio-capture'])
    } else if (err) {
      this.handlers.onError?.(
        err === 'network' ? 'network' : 'unknown',
        ERROR_MESSAGES[err] ?? `Speech recognition error (${err}).`,
      )
    }
  }

  private handleEnd(): void {
    this.activeFlag = false
    this.clearWatchdog()
    this.detach()
    // A session that ended without producing anything (silence) is reported
    // honestly instead of silently doing nothing — but never on top of an
    // error the browser already reported, and never when cancelled.
    if (!this.cancelling && !this.sawError && this.finalsThisSession === 0) {
      this.handlers.onError?.('empty', "Didn't catch that — no speech was recognized.")
    }
    this.handlers.onEnd?.()
  }
}
