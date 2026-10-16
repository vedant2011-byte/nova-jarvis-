import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { StatusInfo } from '../api'
import type { JarvisStore } from '../hooks/useJarvisStore'

type Tab = 'voice' | 'ai' | 'wake' | 'memory' | 'permissions' | 'appearance' | 'system'

const TABS: { id: Tab; label: string }[] = [
  { id: 'ai', label: 'AI' },
  { id: 'voice', label: 'VOICE' },
  { id: 'wake', label: 'WAKE WORD' },
  { id: 'memory', label: 'MEMORY' },
  { id: 'permissions', label: 'PERMISSIONS' },
  { id: 'appearance', label: 'APPEARANCE' },
  { id: 'system', label: 'SYSTEM' },
]

export function SettingsModal({ store, onClose }: { store: JarvisStore; onClose: () => void }) {
  const [tab, setTab] = useState<Tab>('ai')
  const s = store.settings

  return (
    <div
      style={{
        position: 'fixed', inset: 0, zIndex: 50,
        background: 'rgba(2,5,10,0.8)', backdropFilter: 'blur(4px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
      onClick={e => e.target === e.currentTarget && onClose()}
    >
      <div
        style={{
          width: 'min(860px, 94vw)', maxHeight: '88vh',
          background: 'rgba(8,14,24,0.98)',
          border: '1px solid var(--panel-border)', borderRadius: 12,
          display: 'grid', gridTemplateColumns: '170px 1fr',
          overflow: 'hidden',
        }}
        role="dialog" aria-label="Settings"
      >
        <nav style={{ borderRight: '1px solid rgba(56,189,248,0.12)', padding: '18px 0' }}>
          {TABS.map(t => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              style={{
                display: 'block', width: '100%', textAlign: 'left',
                padding: '11px 22px', fontSize: 12, letterSpacing: '0.18em',
                fontFamily: 'var(--font-mono)',
                color: tab === t.id ? 'var(--cyan)' : 'var(--text-mid)',
                background: tab === t.id ? 'rgba(34,211,238,0.07)' : 'transparent',
                borderLeft: tab === t.id ? '2px solid var(--cyan)' : '2px solid transparent',
              }}
            >
              {t.label}
            </button>
          ))}
        </nav>
        <div style={{ padding: '22px 26px', overflowY: 'auto' }}>
          <Header title={TABS.find(t => t.id === tab)?.label ?? ''} onClose={onClose} />
          {tab === 'ai' && <AITab store={store} />}
          {tab === 'voice' && <VoiceTab store={store} />}
          {tab === 'wake' && <WakeTab store={store} />}
          {tab === 'memory' && <MemoryTab store={store} />}
          {tab === 'permissions' && <PermissionsTab store={store} />}
          {tab === 'appearance' && <AppearanceTab store={store} />}
          {tab === 'system' && <SystemTab store={store} />}
          <div style={{ height: 30 }} />
        </div>
      </div>
    </div>
  )
}

function Header({ title, onClose }: { title: string; onClose: () => void }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 18 }}>
      <h2 style={{ fontFamily: 'var(--font-mono)', fontSize: 14, letterSpacing: '0.3em', color: 'var(--cyan)' }}>
        {title}
      </h2>
      <button className="btn btn-cancel" onClick={onClose} style={{ padding: '5px 14px' }}>CLOSE</button>
    </div>
  )
}

function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <label style={{ display: 'block', marginBottom: 16 }}>
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, letterSpacing: '0.15em', color: 'var(--text-low)', marginBottom: 5 }}>
        {label}
      </div>
      {children}
      {hint && <div style={{ fontSize: 11, color: 'var(--text-low)', marginTop: 4 }}>{hint}</div>}
    </label>
  )
}

function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <label style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12, cursor: 'pointer', fontSize: 14 }}>
      <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} />
      <span>{label}</span>
    </label>
  )
}

// ---------------------------------------------------------------------------
function AITab({ store }: { store: JarvisStore }) {
  const ai = (store.settings.ai ?? {}) as Record<string, any>
  const [models, setModels] = useState<string[]>([])
  const [online, setOnline] = useState<boolean | null>(null)
  const [provider, setProvider] = useState<string>('')
  const [llmError, setLlmError] = useState<string>('')

  useEffect(() => {
    api<StatusInfo>('/api/status').then(st => {
      setModels(st.llm?.models ?? [])
      setOnline(st.llm?.online ?? false)
      setProvider(st.llm?.provider ?? '')
      setLlmError(st.llm?.error ?? '')
    }).catch(() => setOnline(false))
  }, [])

  const isGemini = provider === 'gemini'
  const modelKey = isGemini ? 'gemini_model' : 'model'
  const currentModel = String(ai[modelKey] ?? ai.model ?? '')

  const statusLine = online
    ? (isGemini ? 'Gemini API authenticated' : 'Ollama detected')
    : (llmError || (isGemini
        ? 'Gemini unreachable — the backend API key may be missing or invalid'
        : 'Ollama not reachable — JARVIS still controls the PC offline'))

  return (
    <div>
      <Field label={`RUNTIME STATUS — ${online ? 'ONLINE' : 'OFFLINE (offline intent mode active)'}`}>
        <div style={{ fontSize: 13, color: online ? 'var(--ok)' : 'var(--warn)' }}>
          {statusLine}
        </div>
      </Field>
      <Field
        label={`MODEL${isGemini ? ' (GEMINI_MODEL)' : ''}`}
        hint={models.length ? '' : 'Install models with: ollama pull llama3.2:3b'}
      >
        <select
          value={currentModel}
          onChange={e => store.updateSettings({ ai: { [modelKey]: e.target.value } })}
          style={{ width: '100%' }}
        >
          {(models.length ? models : [currentModel || 'llama3.2:3b']).map(m => (
            <option key={m} value={m}>{m}</option>
          ))}
        </select>
      </Field>
      <Field label="OLLAMA HOST">
        <input
          style={{ width: '100%' }} value={String(ai.host ?? '')}
          onChange={e => store.updateSettings({ ai: { host: e.target.value } })}
        />
      </Field>
      <Field label="RESPONSE LENGTH">
        <select
          value={String(ai.response_length ?? 'concise')}
          onChange={e => store.updateSettings({ ai: { response_length: e.target.value } })}
        >
          <option value="concise">Concise (voice-friendly)</option>
          <option value="normal">Normal</option>
          <option value="detailed">Detailed</option>
        </select>
      </Field>
      <Toggle
        label="Fall back to offline intent engine when the model is unavailable"
        checked={ai.fallback_to_intent_engine !== false}
        onChange={v => store.updateSettings({ ai: { fallback_to_intent_engine: v } })}
      />
    </div>
  )
}

function VoiceTab({ store }: { store: JarvisStore }) {
  const voice = (store.settings.voice ?? {}) as Record<string, any>
  const wake = (store.settings.wake_word ?? {}) as Record<string, any>
  const tts = (store.settings.tts ?? {}) as Record<string, any>
  const [devices, setDevices] = useState<{ index: number; name: string; default: boolean }[]>([])
  const [voices, setVoices] = useState<{ id: string; name: string }[]>([])
  const [testing, setTesting] = useState(false)
  const [micResult, setMicResult] = useState<string>('')
  const [stt, setStt] = useState<{ available: boolean; engine: string; install_hint: string | null } | null>(null)
  const [wwAvailable, setWwAvailable] = useState<boolean | null>(null)

  useEffect(() => {
    api<StatusInfo>('/api/status').then(st => {
      setDevices(st.voice?.devices ?? [])
      setVoices(((st as any).tts_voices ?? []) as { id: string; name: string }[])
      setStt((st.voice as any)?.stt ?? null)
    }).catch(() => undefined)
    // wake-word engine availability is honest, not assumed
    api<{ available: boolean }>('/api/wakeword/status')
      .then(r => setWwAvailable(r.available))
      .catch(() => setWwAvailable(false))
  }, [])

  const runMicTest = async () => {
    setTesting(true)
    setMicResult('Listening...')
    const r = await store.testMic()
    setMicResult(r ? (r.detected ? `Signal detected (level ${r.level})` : (r.error ?? 'No signal')) : 'Test failed')
    setTesting(false)
  }

  return (
    <div>
      <Field label="MICROPHONE">
        <select
          value={String(voice.input_device ?? '')}
          onChange={e => store.updateSettings({ voice: { input_device: e.target.value === '' ? null : Number(e.target.value) } })}
          style={{ width: '100%' }}
        >
          <option value="">System default</option>
          {devices.map(d => (
            <option key={d.index} value={d.index}>{d.name}{d.default ? ' (default)' : ''}</option>
          ))}
        </select>
      </Field>
      <Field label="MICROPHONE TEST" hint="Speaks into your mic for 1.5 s and measures the real signal">
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <button className="btn-mini" style={{ border: '1px solid rgba(34,211,238,0.3)', padding: '5px 14px', borderRadius: 3 }}
            onClick={runMicTest} disabled={testing}>
            {testing ? 'TESTING...' : 'TEST MIC'}
          </button>
          {micResult && <span style={{ fontSize: 11, color: 'var(--text-mid)' }}>{micResult}</span>}
        </div>
      </Field>
      {stt && !stt.available && (
        <Field label="SPEECH RECOGNITION" hint="Voice input needs this local package">
          <span style={{ fontSize: 11, color: 'var(--warn, #fbbf24)' }}>
            Unavailable — run: {stt.install_hint ?? 'pip install faster-whisper'}
          </span>
        </Field>
      )}
      <Field label="LISTENING MODE">
        <select
          value={String(voice.mode ?? 'push-to-talk')}
          onChange={e => store.updateSettings({ voice: { mode: e.target.value } })}
        >
          <option value="push-to-talk">Push-to-talk (CTRL+SPACE)</option>
          <option value="continuous">Continuous + wake word</option>
        </select>
      </Field>
      <Field label="SPEECH MODEL" hint="tiny.en = fastest; base.en = balanced; small.en = most accurate">
        <select
          value={String(voice.stt_model ?? 'base.en')}
          onChange={e => store.updateSettings({ voice: { stt_model: e.target.value } })}
        >
          <option value="tiny.en">tiny.en (fastest)</option>
          <option value="base.en">base.en (recommended)</option>
          <option value="small.en">small.en (accurate)</option>
        </select>
      </Field>
      <Field label={`TTS VOLUME — ${Math.round(Number(tts.volume ?? 0.9) * 100)}%`}>
        <input type="range" min={0} max={1} step={0.05} style={{ width: '100%' }}
          value={Number(tts.volume ?? 0.9)}
          onChange={e => store.updateSettings({ tts: { volume: Number(e.target.value) } })} />
      </Field>
      <Field label={`TTS SPEED — ${tts.rate ?? 175} wpm`}>
        <input type="range" min={120} max={260} step={5} style={{ width: '100%' }}
          value={Number(tts.rate ?? 175)}
          onChange={e => store.updateSettings({ tts: { rate: Number(e.target.value) } })} />
      </Field>
      <Toggle label="Voice output enabled" checked={tts.enabled !== false}
        onChange={v => store.updateSettings({ tts: { enabled: v } })} />
      <Toggle label="Speak on startup" checked={tts.speak_startup !== false}
        onChange={v => store.updateSettings({ tts: { speak_startup: v } })} />
      <button
        className="btn btn-cancel"
        disabled={testing}
        onClick={async () => {
          setTesting(true)
          try {
            const r = await api<{ success: boolean; error: string | null }>('/api/tts/test', { method: 'POST' })
            if (!r.success) setMicResult(`TTS failed: ${r.error}`)
          } catch { setMicResult('TTS test failed') }
          setTesting(false)
        }}
      >
        {testing ? '...' : 'TEST SPEAKERS'}
      </button>
    </div>
  )
}

function WakeTab({ store }: { store: JarvisStore }) {
  const ww = (store.settings.wake_word ?? {}) as Record<string, any>
  const [wwAvailable, setWwAvailable] = useState<boolean | null>(null)

  useEffect(() => {
    api<{ available: boolean }>('/api/wakeword/status')
      .then(r => setWwAvailable(r.available))
      .catch(() => setWwAvailable(false))
  }, [])

  return (
    <div>
      <Toggle label="Wake word enabled (continuous mode)" checked={ww.enabled !== false}
        onChange={v => store.updateSettings({ wake_word: { enabled: v } })} />
      <Field label="WAKE WORD">
        <input value={String(ww.word ?? 'jarvis')}
          onChange={e => store.updateSettings({ wake_word: { word: e.target.value.toLowerCase() } })} />
      </Field>
      <Field label="SENSITIVITY">
        <select value={String(ww.sensitivity ?? 'medium')}
          onChange={e => store.updateSettings({ wake_word: { sensitivity: e.target.value } })}>
          <option value="low">Low (fewer false triggers)</option>
          <option value="medium">Medium</option>
          <option value="high">High (more triggers)</option>
        </select>
      </Field>
      {wwAvailable === false && (
        <p style={{ fontSize: 12, color: 'var(--warn, #fbbf24)' }}>
          Wake-word engine unavailable — push-to-talk still works. Run: pip install faster-whisper
        </p>
      )}
      <p style={{ fontSize: 12, color: 'var(--text-low)', lineHeight: 1.6 }}>
        In continuous mode JARVIS transcribes speech and matches it against the wake word
        (including common mis-hearings). Push-to-talk (CTRL+SPACE) always works regardless
        of this setting.
      </p>
    </div>
  )
}

type MemoryItem = { id: number; category: string; key: string; value: string }

function MemoryTab({ store }: { store: JarvisStore }) {
  const mem = (store.settings.memory ?? {}) as Record<string, any>
  const [items, setItems] = useState<MemoryItem[]>([])
  const load = () => api<{ items: MemoryItem[] }>('/api/memory').then(r => setItems(r.items)).catch(() => undefined)
  useEffect(() => { load() }, [])

  return (
    <div>
      <Toggle label="Memory system enabled" checked={mem.enabled !== false}
        onChange={v => store.updateSettings({ memory: { enabled: v } })} />
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', margin: '14px 0 8px' }}>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, letterSpacing: '0.2em', color: 'var(--text-low)' }}>
          STORED INFORMATION ({items.length})
        </div>
        <button className="btn btn-cancel" style={{ padding: '4px 12px', fontSize: 11 }}
          onClick={async () => { await api('/api/memory/clear', { method: 'POST' }); load() }}>
          CLEAR ALL
        </button>
      </div>
      <div style={{ maxHeight: 320, overflowY: 'auto' }}>
        {items.length === 0 && <div style={{ color: 'var(--text-low)', fontSize: 13 }}>No memories stored yet. Try: "Remember my Blackgrid project is on the Desktop."</div>}
        {items.map(item => (
          <div key={item.id} style={{
            display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10,
            padding: '8px 10px', marginBottom: 6,
            background: 'rgba(8,18,32,0.6)', border: '1px solid rgba(56,189,248,0.12)', borderRadius: 6,
            fontSize: 13,
          }}>
            <div>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--cyan)', marginRight: 8 }}>
                {item.category.toUpperCase()}
              </span>
              <strong>{item.key}</strong>
              <span style={{ color: 'var(--text-mid)' }}> — {item.value}</span>
            </div>
            <button className="btn btn-cancel" style={{ padding: '2px 10px', fontSize: 10 }}
              onClick={async () => { await api(`/api/memory/${item.id}`, { method: 'DELETE' }); load() }}>
              REMOVE
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}

function PermissionsTab({ store }: { store: JarvisStore }) {
  const p = (store.settings.permissions ?? {}) as Record<string, any>
  return (
    <div>
      <Toggle label="Auto-approve SAFE actions (open apps, system info, searches)"
        checked={p.auto_approve_safe !== false}
        onChange={v => store.updateSettings({ permissions: { auto_approve_safe: v } })} />
      <Toggle label="Auto-approve recognized LOW-RISK terminal commands"
        checked={!!p.auto_approve_low_risk_terminal}
        onChange={v => store.updateSettings({ permissions: { auto_approve_low_risk_terminal: v } })} />
      <Toggle label="Confirm file edits" checked={p.confirm_edits !== false}
        onChange={v => store.updateSettings({ permissions: { confirm_edits: v } })} />
      <Toggle label="Confirm file deletions (recommended)" checked={p.confirm_file_deletes !== false}
        onChange={v => store.updateSettings({ permissions: { confirm_file_deletes: v } })} />
      <Toggle label="Allow browser automation" checked={p.allow_browser_automation !== false}
        onChange={v => store.updateSettings({ permissions: { allow_browser_automation: v } })} />
      <Field label="ADDITIONAL ALLOWED ROOTS (one per line)" hint="Folders JARVIS may modify. System locations always stay protected.">
        <textarea
          style={{ width: '100%', minHeight: 90, background: 'rgba(8,18,32,0.8)', border: '1px solid rgba(56,189,248,0.25)', color: 'var(--text-hi)', borderRadius: 4, padding: 8, fontFamily: 'var(--font-mono)', fontSize: 12 }}
          value={((p.allowed_roots as string[]) ?? []).join('\n')}
          onChange={e => store.updateSettings({ permissions: { allowed_roots: e.target.value.split('\n').map(x => x.trim()).filter(Boolean) } })}
        />
      </Field>
    </div>
  )
}

function AppearanceTab({ store }: { store: JarvisStore }) {
  const ap = (store.settings.appearance ?? {}) as Record<string, any>
  const intensity = Number(ap.animation_intensity ?? 1.0)
  return (
    <div>
      <Field label={`ANIMATION INTENSITY — ${intensity.toFixed(1)}x`}>
        <input type="range" min={0} max={1.5} step={0.1} style={{ width: '100%' }}
          value={intensity}
          onChange={e => store.updateSettings({ appearance: { animation_intensity: Number(e.target.value) } })} />
      </Field>
      <Field label="HUD DENSITY">
        <select value={String(ap.hud_density ?? 'normal')}
          onChange={e => store.updateSettings({ appearance: { hud_density: e.target.value } })}>
          <option value="compact">Compact</option>
          <option value="normal">Normal</option>
          <option value="roomy">Roomy</option>
        </select>
      </Field>
      <Field label={`BRIGHTNESS — ${Math.round(Number(ap.brightness ?? 1) * 100)}%`}>
        <input type="range" min={0.6} max={1.3} step={0.05} style={{ width: '100%' }}
          value={Number(ap.brightness ?? 1)}
          onChange={e => store.updateSettings({ appearance: { brightness: Number(e.target.value) } })} />
      </Field>
      <Toggle label="Startup animation" checked={store.settings.general?.startup_animation !== false}
        onChange={v => store.updateSettings({ general: { startup_animation: v } })} />
    </div>
  )
}

function SystemTab({ store }: { store: JarvisStore }) {
  const [logs, setLogs] = useState<string[]>([])
  const [startup, setStartup] = useState((store.settings.startup ?? {}) as Record<string, any>)
  const logRef = useRef<HTMLPreElement>(null)

  const loadLogs = () => api<{ lines: string[] }>('/api/logs?tail=120').then(r => setLogs(r.lines)).catch(() => undefined)
  useEffect(() => { loadLogs() }, [])

  const setRunKey = async (enable: boolean) => {
    await api('/api/settings', {
      method: 'POST',
      body: JSON.stringify({ patch: { startup: { launch_with_windows: enable } } }),
    }).catch(() => undefined)
    setStartup((s: any) => ({ ...s, launch_with_windows: enable }))
  }

  return (
    <div>
      <Toggle label="Start JARVIS with Windows"
        checked={!!startup.launch_with_windows}
        onChange={setRunKey} />
      <Toggle label="Start minimized" checked={!!startup.start_minimized}
        onChange={v => store.updateSettings({ startup: { start_minimized: v } })} />
      <div style={{ display: 'flex', gap: 10, margin: '14px 0' }}>
        <button className="btn btn-cancel" onClick={loadLogs}>REFRESH LOGS</button>
        <button className="btn btn-cancel" onClick={() => {
          if (confirm('Reset all settings to defaults?')) {
            api('/api/settings', { method: 'POST', body: JSON.stringify({ patch: { __reset: true } }) })
          }
        }}>RESET CONFIG</button>
      </div>
      <pre ref={logRef} style={{
        background: 'rgba(0,0,0,0.5)', border: '1px solid rgba(56,189,248,0.15)',
        borderRadius: 6, padding: 12, fontSize: 11, fontFamily: 'var(--font-mono)',
        maxHeight: 300, overflow: 'auto', color: 'var(--text-mid)',
      }}>
        {logs.join('\n') || 'No log lines.'}
      </pre>
    </div>
  )
}
