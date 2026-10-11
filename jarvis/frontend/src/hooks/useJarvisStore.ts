import { useCallback, useEffect, useRef, useState } from 'react'
import type { ConfirmationRequest, HistoryEntry, SettingsShape, SystemStats, TaskPlanUI } from '../api'
import { api } from '../api'
import { BOOT_TIMEOUT_MS, probeBootHealth } from '../bootHealth'

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
  const wsRef = useRef<WebSocket | null>(null)
  const levelRef = useRef(0)
  const stateRef = useRef<UIState>('boot')
  stateRef.current = state

  const pushLog = useCallback((text: string, kind: LogLine['kind'] = 'sys') => {
    setLogs(prev => [...prev.slice(-120), { ts: Date.now(), text, kind }])
  }, [])

  // -- websocket -------------------------------------------------------
  useEffect(() => {
    let closed = false
    let retryTimer: number | undefined

    const connect = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      const ws = new WebSocket(`${proto}://${location.host}/ws`)
      wsRef.current = ws

      ws.onopen = () => {
        if (closed) return
        setConnected(true)
        pushLog('link established', 'sys')
        api<{ entries: HistoryEntry[] }>('/api/history?limit=60')
          .then(r => setHistory(r.entries.slice(-40)))
          .catch(() => undefined)
        api<{ settings: SettingsShape }>('/api/settings')
          .then(r => setSettings(r.settings))
          .catch(() => undefined)
      }

      ws.onmessage = evt => {
        try {
          handle(JSON.parse(evt.data))
        } catch {
          /* ignore malformed */
        }
      }

      ws.onclose = () => {
        if (closed) return
        setConnected(false)
        retryTimer = window.setTimeout(connect, 1500)
      }
      ws.onerror = () => ws.close()
    }

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
          setHistory(prev => {
            const last = prev[prev.length - 1]
            if (last && last.role === entry.role && last.content === entry.content) return prev
            return [...prev.slice(-80), entry]
          })
          pushLog(`${d.role}: ${d.content}`, d.role === 'user' ? 'in' : 'out')
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
        default:
          break
      }
    }

    connect()
    return () => {
      closed = true
      if (retryTimer) clearTimeout(retryTimer)
      wsRef.current?.close()
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
  const sendCommand = useCallback((text: string) => {
    if (!text.trim()) return
    pushLog(`you: ${text}`, 'in')
    api('/api/command', { method: 'POST', body: JSON.stringify({ text, source: 'text' }) })
      .catch(err => pushLog(`send failed: ${err}`, 'sys'))
  }, [pushLog])

  const confirm = useCallback(async (approved: boolean) => {
    const conf = confirmation
    setConfirmation(null)
    if (conf) {
      await api('/api/confirm', { method: 'POST', body: JSON.stringify({ id: conf.id, approved }) })
        .catch(() => undefined)
    }
  }, [confirmation])

  const stop = useCallback(() => {
    api('/api/stop', { method: 'POST' }).catch(() => undefined)
    setStateText('TASK STOPPED')
    setState('idle')
    setPlan(null)
  }, [])

  const ptt = useCallback(async (start: boolean) => {
    try {
      await api(start ? '/api/ptt/start' : '/api/ptt/end', { method: 'POST' })
    } catch {
      pushLog('push-to-talk failed (mic unavailable?)', 'sys')
    }
  }, [pushLog])

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
