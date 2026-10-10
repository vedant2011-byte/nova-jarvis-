import { useEffect, useRef } from 'react'
import type { JarvisStore } from '../hooks/useJarvisStore'

export function ConversationPanel({ store }: { store: JarvisStore }) {
  const endRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [store.history.length])

  const fmt = (ts: number) =>
    new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

  return (
    <aside className="panel" aria-label="Conversation">
      <div className="panel-title">
        <span>CONVERSATION</span>
        <button className="btn-mini" onClick={store.clearHistory} title="Clear history">CLEAR</button>
      </div>
      <div className="conversation-list">
        {store.history.length === 0 && (
          <div style={{ color: 'var(--text-low)', fontSize: 13, padding: '8px 4px' }}>
            Say "Jarvis" or type below. Try: "open Chrome", "RAM usage", "take a screenshot".
          </div>
        )}
        {store.history.map((m, i) => (
          <div key={m.id ?? `${m.ts}-${i}`} className={`msg ${m.role === 'user' ? 'user' : 'jarvis'}`}>
            <div className="who">
              {m.role === 'user' ? 'USER' : 'JARVIS'} · {fmt(m.ts)}
            </div>
            <div className="bubble">{m.content}</div>
          </div>
        ))}
        <div ref={endRef} />
      </div>
    </aside>
  )
}
