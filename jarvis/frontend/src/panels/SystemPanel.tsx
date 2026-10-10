import type { JarvisStore } from '../hooks/useJarvisStore'

function Meter({ label, value, suffix = '%' }: { label: string; value: number; suffix?: string }) {
  const hot = value > 85
  return (
    <div className="meter">
      <div className="meter-label">
        <span>{label}</span>
        <span>{value.toFixed(0)}{suffix}</span>
      </div>
      <div className="meter-bar">
        <div className={`meter-fill ${hot ? 'hot' : ''}`} style={{ width: `${Math.min(100, value)}%` }} />
      </div>
    </div>
  )
}

function Row({ k, v, ok }: { k: string; v: string; ok?: boolean }) {
  return (
    <div className="status-row">
      <span className="k">{k}</span>
      <span className={`v ${ok === undefined ? '' : ok ? 'ok' : 'bad'}`}>{v}</span>
    </div>
  )
}

export function SystemPanel({ store }: { store: JarvisStore }) {
  const s = store.stats
  const st = store.status
  const batt = s?.battery

  return (
    <aside className="panel" aria-label="System status">
      <div className="panel-title"><span>SYSTEM STATUS</span></div>
      <div className="status-list">
        <Row k="AI ENGINE" v={st.llm?.online ? 'ONLINE' : 'OFFLINE'} ok={st.llm?.online} />
        <Row k="VOICE ENGINE" v={st.voice?.listening ? 'ONLINE' : 'OFFLINE'} ok={st.voice?.listening} />
        <Row k="TTS" v={st.tts?.sapi || st.tts?.piper ? 'ONLINE' : 'OFFLINE'} ok={!!(st.tts?.sapi || st.tts?.piper)} />
        <Row k="MEMORY" v="ONLINE" ok />
        <Row k="TOOLS" v={`${st.tools?.length ?? 0} READY`} ok={(st.tools?.length ?? 0) > 0} />
        <Row k="SECURITY" v="ACTIVE" ok />
        <Row k="NETWORK" v={s?.internet ? 'ONLINE' : 'OFFLINE'} ok={s?.internet} />

        <div style={{ height: 10 }} />
        {s && <Meter label="CPU" value={s.cpu} />}
        {s && <Meter label="RAM" value={s.ram_percent} />}
        {batt?.present && <Meter label="BATTERY" value={batt.percent ?? 0} />}
        {batt && !batt.present && <Row k="BATTERY" v={batt.detail?.slice(0, 26) ?? 'N/A'} />}
        {s && (
          <div className="status-row" style={{ color: 'var(--text-low)' }}>
            <span>RAM USED</span>
            <span>{s.ram_used_gb} / {s.ram_total_gb} GB</span>
          </div>
        )}
      </div>
    </aside>
  )
}
