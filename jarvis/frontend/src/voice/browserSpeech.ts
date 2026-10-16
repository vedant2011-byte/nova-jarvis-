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
    rec.onstart = () => {
      this.activeFlag = true
      this.handlers.onStart?.()
    }
    rec.onresult = (event: unknown) => this.handleResult(event)
    rec.onerror = (event: unknown) => this.handleError(event)
    rec.onend = () => this.handleEnd()
    this.rec = rec
    try {
      rec.start()
    } catch {
      this.detach()
      this.handlers.onError?.('unknown', 'Speech recognition failed to start.')
      return 'unsupported'
    }
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
