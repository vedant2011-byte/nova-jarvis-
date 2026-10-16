import { useEffect, useRef, useState } from 'react'
import { useJarvisStore } from '../hooks/useJarvisStore'
import type { JarvisStore } from '../hooks/useJarvisStore'
import { AICore } from './AICore'
import { Background } from './Background'
import { ConversationPanel } from '../panels/ConversationPanel'
import { SystemPanel } from '../panels/SystemPanel'
import { TaskPanel } from '../panels/TaskPanel'
import { ConfirmationCard } from './ConfirmationCard'
import { BootScreen } from './BootScreen'
import { SettingsModal } from '../settings/SettingsModal'

const STATE_TEXT: Record<string, string> = {
  idle: 'SYSTEM ONLINE',
  listening: 'LISTENING...',
  transcribing: 'TRANSCRIBING...',
  thinking: 'ANALYZING...',
  executing: 'EXECUTING',
  confirmation: 'APPROVAL REQUIRED',
  speaking: 'SPEAKING...',
  error: 'ACTION FAILED',
  stopped: 'TASK STOPPED',
  boot: 'INITIALIZING...',
}

export function Hud({ intensity }: { intensity: number }) {
  const store = useJarvisStore()
  const [input, setInput] = useState('')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const pttDown = useRef(false)

  // keyboard: Ctrl+Space push-to-talk; Esc closes modals
  useEffect(() => {
    const isTyping = (e: KeyboardEvent) =>
      e.target instanceof HTMLElement &&
      ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)

    const down = (e: KeyboardEvent) => {
      if (e.code === 'Space' && e.ctrlKey && !isTyping(e) && !pttDown.current) {
        e.preventDefault()
        pttDown.current = true
        store.ptt(true)
      }
      if (e.key === 'Escape') setSettingsOpen(false)
    }
    const up = (e: KeyboardEvent) => {
      if (e.code === 'Space' && pttDown.current) {
        e.preventDefault()
        pttDown.current = false
        store.ptt(false)
      }
    }
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
    }
  }, [store])

  const stateLabel = store.stateText || STATE_TEXT[store.state] || 'SYSTEM ONLINE'

  return (
    <div className="hud">
      <Background intensity={intensity} />

      <header className="topbar">
        <div style={{ display: 'flex', alignItems: 'center', gap: 18 }}>
          <div className="topbar-title">JARVIS</div>
          <button
            className="panel-title btn-mini"
            style={{ border: '1px solid rgba(34,211,238,0.3)', padding: '3px 10px', borderRadius: 3, letterSpacing: '0.15em', fontSize: 10 }}
            onClick={() => setSettingsOpen(true)}
            aria-label="Open settings"
          >
            SETTINGS
          </button>
        </div>
        <div className="topbar-status">
          <span className={`status-dot ${store.connected ? '' : 'off'}`} />
          <span>{store.connected ? 'SYSTEM ONLINE' : 'LINK OFFLINE'}</span>
          <span style={{ color: 'var(--text-low)' }}>|</span>
          <span>{store.stats?.internet ? 'NET OK' : 'LOCAL MODE'}</span>
        </div>
      </header>

      <div className="main-row">
        <ConversationPanel store={store} />

        <section className="core-area" aria-label="AI core">
          <AICore state={store.state} level={store.level} intensity={intensity} />
          <div className="core-state-label" role="status" aria-live="polite">{stateLabel}</div>
          {store.state === 'executing' && (
            <div className="core-action-label">{store.actionText}</div>
          )}
          <form
            className="core-input"
            onSubmit={e => {
              e.preventDefault()
              store.sendCommand(input)
              setInput('')
            }}
          >
            <input
              value={input}
              onChange={e => setInput(e.target.value)}
              placeholder="Type a command, or hold CTRL+SPACE to speak..."
              aria-label="Command input"
            />
            <button type="submit">SEND</button>
          </form>
        </section>

        <SystemPanel store={store} />
      </div>

      <TaskPanel plan={store.plan} onStop={store.stop} />

      {store.confirmation && (
        <ConfirmationCard request={store.confirmation} store={store} />
      )}

      <footer className="bottombar">
        <div className="chips">
          <span className="chip">
            <span className={`chip-dot ${store.status.voice?.listening ? '' : 'off'}`} /> MIC
          </span>
          <span className="chip">
            <span className={`chip-dot ${store.settings.wake_word?.enabled ? '' : 'off'}`} /> WAKE WORD
          </span>
          <span className="chip">
            <span className={`chip-dot ${store.status.llm?.online ? '' : 'off'}`} />
            {store.status.llm?.provider === 'gemini' ? 'GEMINI AI' : 'LOCAL AI'}
          </span>
          <span className="chip">
            <span className="chip-dot" /> SECURE
          </span>
        </div>
        <button
          className={`mic-btn ${store.state === 'listening' ? 'active' : ''}`}
          onClick={() => store.ptt(store.state !== 'listening')}
          aria-label={store.state === 'listening' ? 'Stop listening and transcribe' : 'Start listening'}
          title="Click to speak (or hold CTRL+SPACE)"
        >
          {store.state === 'listening' ? '■' : 'MIC'}
        </button>
        <button className="stop-btn" onClick={store.stop} aria-label="Emergency stop">STOP</button>
      </footer>

      {settingsOpen && <SettingsModal store={store} onClose={() => setSettingsOpen(false)} />}
      {store.state === 'boot' && (
        <BootScreen logs={store.logs} error={store.bootError} onRetry={store.retryBoot} />
      )}
    </div>
  )
}
