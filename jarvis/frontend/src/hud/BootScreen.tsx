import { useEffect, useState } from 'react'
import type { LogLine } from '../hooks/useJarvisStore'

export function BootScreen({ logs }: { logs: LogLine[] }) {
  const [minTime, setMinTime] = useState(true)
  useEffect(() => {
    const t = setTimeout(() => setMinTime(false), 2600)
    return () => clearTimeout(t)
  }, [])

  const bootLines = logs.filter(l => l.text.includes('ONLINE') || l.text.includes('OFFLINE'))
  const show = minTime || !bootLines.some(l => l.text.includes('SECURITY'))

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
