import { useEffect, useState } from 'react'
import type { LogLine } from '../hooks/useJarvisStore'

export function BootScreen({
  logs,
  error,
  onRetry,
}: {
  logs: LogLine[]
  error?: string | null
  onRetry?: () => void
}) {
  const [minTime, setMinTime] = useState(true)
  useEffect(() => {
    const t = setTimeout(() => setMinTime(false), 2600)
    return () => clearTimeout(t)
  }, [])

  const bootLines = logs.filter(l => l.text.includes('ONLINE') || l.text.includes('OFFLINE'))
  const show = minTime || !bootLines.some(l => l.text.includes('SECURITY'))

  // watchdog fired: initialization cannot complete — say so honestly
  // instead of spinning forever (never fake a ready state)
  if (error) {
    return (
      <div className="boot-screen" role="status">
        <div className="boot-title">CONNECTION FAILED</div>
        <div className="boot-lines">
          <div className="boot-line">
            <span>JARVIS BACKEND</span>
            <span className="bad">OFFLINE</span>
          </div>
          <div className="boot-detail">{error}</div>
          <div className="boot-detail muted">
            The HUD needs the backend API on this domain. The local Windows app is unaffected.
          </div>
          <button className="retry-btn" onClick={onRetry} aria-label="Retry connection">
            RETRY
          </button>
        </div>
      </div>
    )
  }

  if (!show) return null
  return (
    <div className="boot-screen" role="status">
      <div className="boot-title">INITIALIZING JARVIS</div>
      <div className="boot-lines">
        {bootLines.map((l, i) => (
          <div key={i} className="boot-line">
            <span>{l.text.replace(/ (ONLINE|OFFLINE)$/, '')}</span>
            <span className={l.text.includes('OFFLINE') ? 'bad' : 'ok'}>
              {l.text.includes('OFFLINE') ? 'OFFLINE' : 'ONLINE'}
            </span>
          </div>
          ))}
        <div className="boot-line" style={{ color: 'var(--cyan)' }}>
          <span>SYSTEM READY</span>
          <span>▊</span>
        </div>
      </div>
    </div>
  )
}
