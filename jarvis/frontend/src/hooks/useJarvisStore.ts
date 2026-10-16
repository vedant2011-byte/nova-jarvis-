import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ConfirmationRequest, HistoryEntry, SettingsShape, SystemStats, TaskPlanUI } from '../api'
import { api } from '../api'
import { BOOT_TIMEOUT_MS, probeBootHealth } from '../bootHealth'
import { createWsLink, type WsLink } from '../net/wsLink'
import { createCommandSender } from '../net/commandSender'
import { BrowserSpeech, browserSpeechSupported, defaultSpeechCtor } from '../voice/browserSpeech'

export type UIState =
  | 'boot'
  | 'idle'
  | 'listening'
  | 'transcribing'
  | 'thinking'
  | 'executing'
  | 'confirmation'
  | 'speaking'
  | 'error'
  | 'stopped'

export type LogLine = { ts: number; text: string; kind: 'in' | 'out' | 'sys' }

/** A reply for a POST can only reach this client over the WS of the
 * instance-local link; these states must never survive a link recovery. */
export const STUCK_STATES: readonly UIState[] = [
  'thinking', 'executing', 'speaking', 'transcribing', 'listening', 'error',
]

/** Everything the UI must let go when the event link is replaced: the old
 * connection is gone with any confirmation/plan it was tracking. */
export const RESET_ON_RECONNECT: readonly UIState[] = [...STUCK_STATES, 'confirmation']

/** Append a conversation entry unless it duplicates the immediate previous
 * one (the same entry arrives from both the HTTP reply and the WS event). */
export function appendConversationEntry(
  prev: HistoryEntry[],
  entry: HistoryEntry,
): HistoryEntry[] {
  const last = prev[prev.length - 1]
  if (last && last.role === entry.role && last.content === entry.content) return prev
  return [...prev.slice(-80), entry].slice(-80) // hard cap at 80 entries
}

export function useJarvisStore() {
  const [state, setState] = useState<UIState>('boot')
  const [stateText, setStateText] = useState('INITIALIZING')
  const [actionText, setActionText] = useState('')
  const [connected, setConnected] = useState(false)
  const [stats, setStats] = useState<SystemStats | null>(null)
  const [history, setHistory] = useState<HistoryEntry[]>([])
  const [plan, setPlan] = useState<TaskPlanUI | null>(null)
  const [confirmation, setConfirmation] = useState<ConfirmationRequest | null>(null)
  const [level, setLevel] = useState(0)
  const [settings, setSettings] = useState<SettingsShape>({})
  const [status, setStatus] = useState<Partial<import('../api').StatusInfo>>({})
  const [logs, setLogs] = useState<LogLine[]>([])
  const [bootError, setBootError] = useState<string | null>(null)
  const [bootNonce, setBootNonce] = useState(0)
  const linkRef = useRef<WsLink | null>(null)
  const handleRef = useRef<((msg: { type: string; data: any }) => void) | null>(null)
  const levelRef = useRef(0)
  const stateRef = useRef<UIState>('boot')
  stateRef.current = state

  const pushLog = useCallback((text: string, kind: LogLine['kind'] = 'sys') => {
    setLogs(prev => {
      const last = prev[prev.length - 1]
      // the same entry can arrive twice (optimistic echo + WS event);
      // an identical consecutive line inside 3 s is that duplicate
      if (last && last.text === text && Date.now() - last.ts < 3000) return prev
      return [...prev.slice(-120), { ts: Date.now(), text, kind }]
    })
  }, [])

  // -- websocket: resilient link (app ping, staleness watchdog, backoff) ---
  useEffect(() => {
    const link = createWsLink({
      url: `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`,
      onStatus: connectedNow => setConnected(connectedNow),
      onOpen: () => {
        pushLog('link established', 'sys')
        // a recovered link must never leave a loading state stuck: any
        // in-flight task, confirmation or plan died with the old connection,
        // so settle the UI and resync full state (history/settings/status)
        if (RESET_ON_RECONNECT.includes(stateRef.current)) {
          setState('idle')
          setStateText('SYSTEM ONLINE')
          setActionText('')
          setConfirmation(null)
          setPlan(null)
        }
        api<{ entries: HistoryEntry[] }>('/api/history?limit=60')
          .then(r => setHistory(r.entries.slice(-40)))
          .catch(() => undefined)
        api<{ settings: SettingsShape }>('/api/settings')
          .then(r => setSettings(r.settings))
          .catch(() => undefined)
        api<import('../api').StatusInfo>('/api/status')
          .then(s => setStatus(s))
          .catch(() => undefined)
      },
      onMessage: data => handleRef.current?.(data as { type: string; data: any }),
      onClose: () => pushLog('link dropped — reconnecting…', 'sys'),
    })
    linkRef.current = link
    const goOnline = () => link.reconnectNow()
    window.addEventListener('online', goOnline)

    const handle = (msg: { type: string; data: any }) => {
      const d = msg.data ?? {}
      switch (msg.type) {
        case 'hello':
          break
        case 'boot_done':
          setState(s => (s === 'boot' ? 'idle' : s))
          setStateText('SYSTEM READY')
          break
        case 'boot_step':
          pushLog(`${d.name} ${d.ok ? 'ONLINE' : 'OFFLINE'}`, 'sys')
          break
        case 'state': {
          const s = d.state as UIState
          if (s === 'confirmation') {
            setState('confirmation')
            setStateText('ACTION REQUIRES APPROVAL')
            if (d.action) setActionText(String(d.action))
          } else if (s === 'executing') {
            setState('executing')
            setStateText('EXECUTING')
            setActionText(String(d.action ?? ''))
          } else if (s === 'speaking') {
            setState('speaking')
            setStateText('SPEAKING...')
          } else if (s === 'thinking') {
            setState('thinking')
            setStateText('ANALYZING...')
          } else if (s === 'listening') {
            setState('listening')
            setStateText('LISTENING...')
          } else if (s === 'transcribing') {
            setState('transcribing')
            setStateText('TRANSCRIBING...')
          } else if (s === 'idle') {
            setState(prev => (prev === 'confirmation' ? 'confirmation' : 'idle'))
            if (d.message) setStateText(String(d.message))
            else setStateText('SYSTEM ONLINE')
            setActionText('')
          } else if (s === 'error') {
            setState('error')
            setStateText('ACTION FAILED')
          }
          if (typeof d.action === 'string' && s !== 'executing') setActionText('')
          break
        }
        case 'system_stats':
          setStats(d as SystemStats)
          break
        case 'conversation': {
          const entry: HistoryEntry = {
            role: d.role,
            content: d.content,
            ts: (d.ts ?? Date.now() / 1000) * 1000,
            source: d.source ?? 'text',
          }
          setHistory(prev => appendConversationEntry(prev, entry))
          // same text as the optimistic echo ("you:") → don't log twice
          pushLog(
            d.role === 'user' ? `you: ${d.content}` : `jarvis: ${d.content}`,
            d.role === 'user' ? 'in' : 'out',
          )
          break
        }
        case 'task_started':
          setPlan(d as TaskPlanUI)
          break
        case 'task_progress':
          setPlan(d as TaskPlanUI)
          break
        case 'task_done':
          setPlan(d as TaskPlanUI)
          setTimeout(() => setPlan(null), 6000)
          break
        case 'task_stopped':
          setPlan(null)
          setState('idle')
          setStateText('TASK STOPPED')
          break
        case 'confirmation_request':
          setConfirmation(d as ConfirmationRequest)
          setState('confirmation')
          setStateText('ACTION REQUIRES APPROVAL')
          break
        case 'confirmation_resolved':
          setConfirmation(null)
          break
        case 'voice_error':
          pushLog(`voice: ${d.error}`, 'sys')
          setStateText('MIC UNAVAILABLE')
          // never stay stuck in LISTENING after a mic failure
          setState(prev => (prev === 'listening' ? 'idle' : prev))
          break
        case 'voice_level':
          if (stateRef.current === 'listening') {
            levelRef.current = Math.min(1, Number(d.level ?? 0))
          }
          break
        case 'ptt_state':
          if (d.active) {
            setState('listening')
            setStateText('LISTENING...')
          }
          break
        case 'stt_result':
          if (d.text) pushLog(`heard: ${d.text}`, 'in')
          break
        case 'wake_detected':
          setState('listening')
          setStateText('LISTENING...')
          break
        case 'settings_updated':
          api<{ settings: SettingsShape }>('/api/settings')
            .then(r => setSettings(r.settings))
            .catch(() => undefined)
          break
        case 'error':
          // provider/link errors must be visible AND must not wedge the UI
          pushLog(`error: ${d.message ?? d.error ?? 'unknown error'}`, 'sys')
          if (STUCK_STATES.includes(stateRef.current)) {
            setState('idle')
            setStateText('SYSTEM ONLINE')
            setActionText('')
          }
          break
        default:
          break
      }
    }

    handleRef.current = handle
    return () => {
      window.removeEventListener('online', goOnline)
      link.close()
      linkRef.current = null
      handleRef.current = null
    }
  }, [pushLog, bootNonce])

  // -- boot watchdog: never stay on the boot screen forever ---------------
  useEffect(() => {
    if (state !== 'boot') return
    let cancelled = false
    const timer = window.setTimeout(() => {
      if (cancelled) return
      void probeBootHealth(api).then(h => {
        if (cancelled) return
        setBootError(
          h.ok
            ? 'backend responded, but the live event link never became ready'
            : h.detail,
        )
      })
    }, BOOT_TIMEOUT_MS)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [state, bootNonce])

  // -- mic level: real voice_level events when listening, gentle motion otherwise --
  useEffect(() => {
    const t = setInterval(() => {
      if (state === 'listening') {
        // decay toward zero between real voice_level events (10 Hz from backend)
        levelRef.current = Math.max(0, levelRef.current * 0.72)
        setLevel(levelRef.current)
      } else if (state === 'speaking') {
        levelRef.current = 0.35 + Math.random() * 0.3
        setLevel(levelRef.current)
      } else {
        levelRef.current = Math.max(0, levelRef.current - 0.08)
        setLevel(levelRef.current)
      }
    }, 90)
    return () => clearInterval(t)
  }, [state])

  // -- actions ----------------------------------------------------------
  // Chat submission: optimistic echo, duplicate-send guard, bounded reply
  // wait with a history fallback — every success/failure/timeout path
  // settles the UI so the chat can continue without a refresh.
  const sender = useMemo(() => createCommandSender({
    post: text => api<{ accepted?: boolean; done?: boolean; reply?: string; handled?: string }>(
      '/api/command',
      { method: 'POST', body: JSON.stringify({ text, source: 'text' }) },
      45000, // bounded: server waits ≤15 s for a reply, plus network margin
    ),
    pollHistory: async () =>
      (await api<{ entries: HistoryEntry[] }>('/api/history?limit=60')).entries,
    onEcho: text => {
      pushLog(`you: ${text}`, 'in')
      setHistory(prev => appendConversationEntry(prev, {
        role: 'user', content: text, ts: Date.now(), source: 'text',
      }))
    },
    onReply: text => {
      pushLog(`jarvis: ${text}`, 'out')
      setHistory(prev => appendConversationEntry(prev, {
        role: 'jarvis', content: text, ts: Date.now(), source: 'agent',
      }))
    },
    onNote: message => pushLog(message, 'sys'),
    onFail: message => {
      pushLog(message, 'sys')
      if (STUCK_STATES.includes(stateRef.current)) {
        setState('idle')
        setStateText('SYSTEM ONLINE')
        setActionText('')
      }
    },
  }), [pushLog])

  const sendCommand = useCallback((text: string) => {
    void sender.send(text)
  }, [sender])

  // Voice input: browser-native recognition when the browser supports it
  // (works against the serverless deployment — no server mic needed);
  // otherwise the backend push-to-talk pipeline for the local desktop app.
  const speech = useMemo(() => {
    let terminalError = false
    return new BrowserSpeech(defaultSpeechCtor(), {
      onStart: () => {
        terminalError = false
        setState('listening')
        setStateText('LISTENING...')
        pushLog('listening — speak now', 'sys')
      },
      onFinal: text => {
        pushLog(`heard: ${text}`, 'in')
        void sendCommand(text)
      },
      onError: (code, message) => {
        pushLog(`voice: ${message}`, 'sys')
        // empty/no-speech end normally — onEnd settles the state
        if (code === 'empty' || code === 'no-speech') return
        terminalError = true
        if (stateRef.current === 'listening') setState('idle')
        setStateText(
          code === 'permission-denied' ? 'MIC PERMISSION DENIED'
            : code === 'unsupported' ? 'MIC UNSUPPORTED'
              : 'MIC UNAVAILABLE',
        )
        setActionText('')
      },
      onEnd: () => {
        if (!terminalError && stateRef.current === 'listening') {
          setState('idle')
          setStateText('SYSTEM ONLINE')
          setActionText('')
        }
        terminalError = false
      },
    })
  }, [pushLog, sendCommand])

  // unmount: never leave timers or recognition sessions behind
  useEffect(() => () => {
    sender.dispose()
    speech.dispose()
  }, [sender, speech])

  const confirm = useCallback(async (approved: boolean) => {
    const conf = confirmation
    setConfirmation(null)
    if (conf) {
      await api('/api/confirm', { method: 'POST', body: JSON.stringify({ id: conf.id, approved }) })
        .catch(() => undefined)
    }
  }, [confirmation])

  const stop = useCallback(() => {
    speech.cancel()          // cancel any in-flight dictation
    sender.dispose()         // cancel any pending reply poll
    api('/api/stop', { method: 'POST' }).catch(() => undefined)
    setStateText('TASK STOPPED')
    setState('idle')
    setPlan(null)
  }, [speech, sender])

  const ptt = useCallback(async (start: boolean) => {
    if (start) {
      if (browserSpeechSupported()) {
        // in-browser dictation: mic permission, empty transcripts and
        // errors are handled by the speech handlers above
        speech.start()
        return
      }
      // No browser support: backend pipeline — works in the local desktop
      // app; on serverless there is no mic, so the 503 is reported
      // honestly instead of failing silently.
      try {
        await api('/api/ptt/start', { method: 'POST' })
      } catch {
        pushLog('voice input unavailable: this browser has no speech recognition and the backend has no microphone — type your message instead', 'sys')
        if (stateRef.current === 'listening') setState('idle')
      }
    } else if (speech.active) {
      speech.stop()
    } else {
      try {
        await api('/api/ptt/end', { method: 'POST' })
      } catch {
        /* transcript (if any) arrives via the stt_result event */
      }
    }
  }, [speech, pushLog])

  const testMic = useCallback(async () => {
    try {
      const r = await api<{ success: boolean; detected: boolean; level: number; error: string | null }>(
        '/api/voice/test', { method: 'POST' })
      if (r.success && r.detected) pushLog(`mic test: signal detected (level ${r.level})`, 'sys')
      else pushLog(`mic test: ${r.error ?? 'no signal'}`, 'sys')
      return r
    } catch (err) {
      pushLog(`mic test failed: ${err}`, 'sys')
      return null
    }
  }, [pushLog])

  const updateSettings = useCallback(async (patch: SettingsShape) => {
    setSettings(prev => {
      // optimistic merge
      const next = JSON.parse(JSON.stringify(prev))
      for (const [k, v] of Object.entries(patch)) next[k] = { ...(next[k] ?? {}), ...v }
      return next
    })
    await api('/api/settings', { method: 'POST', body: JSON.stringify({ patch }) })
      .catch(() => undefined)
  }, [])

  const retryBoot = useCallback(() => {
    setBootError(null)
    setState('boot')
    setBootNonce(n => n + 1)
  }, [])

  const clearHistory = useCallback(async () => {
    await api('/api/history', { method: 'DELETE' }).catch(() => undefined)
    setHistory([])
  }, [])

  const refreshStatus = useCallback(async () => {
    try {
      const s = await api<import('../api').StatusInfo>('/api/status')
      setStatus(s)
      return s
    } catch {
      return null
    }
  }, [])

  return {
    state, stateText, actionText, connected, stats, history, plan, confirmation,
    level, settings, status, logs, bootError,
    sendCommand, confirm, stop, ptt, testMic, updateSettings, clearHistory, refreshStatus,
    retryBoot, setHistory,
  }
}

export type JarvisStore = ReturnType<typeof useJarvisStore>
