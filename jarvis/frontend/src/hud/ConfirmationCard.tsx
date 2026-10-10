import type { ConfirmationRequest } from '../api'
import type { JarvisStore } from '../hooks/useJarvisStore'

export function ConfirmationCard({ request, store }: { request: ConfirmationRequest; store: JarvisStore }) {
  const detail = JSON.stringify(request.detail, null, 2)
  return (
    <div className="confirm-card" role="alertdialog" aria-label="Action approval">
      <h3>ACTION REQUIRES APPROVAL — {request.risk.toUpperCase()}</h3>
      <div className="confirm-desc">{request.title}</div>
      <div style={{ fontSize: 13, color: 'var(--text-mid)' }}>{request.description}</div>
      <pre className="confirm-detail">{detail}</pre>
      <div className="confirm-buttons">
        <button className="btn btn-cancel" onClick={() => store.confirm(false)}>CANCEL</button>
        <button className="btn btn-approve" onClick={() => store.confirm(true)}>APPROVE</button>
      </div>
      <div style={{ marginTop: 10, fontSize: 11, color: 'var(--text-low)', fontFamily: 'var(--font-mono)' }}>
        Voice: say "approve" or "cancel"
      </div>
    </div>
  )
}
