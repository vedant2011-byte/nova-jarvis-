import type { TaskPlanUI } from '../api'

export function TaskPanel({ plan, onStop }: { plan: TaskPlanUI | null; onStop: () => void }) {
  if (!plan) return null
  return (
    <aside className="panel task-panel" aria-label="Current task">
      <div className="panel-title">
        <span>CURRENT TASK</span>
        <button className="btn-mini" onClick={onStop}>STOP</button>
      </div>
      <div style={{ padding: '10px 16px', fontSize: 13, color: 'var(--text-hi)' }}>
        {plan.description}
      </div>
      {plan.steps.map((step, i) => (
        <div key={i} className={`task-step ${step.status}`}>
          <span className="mark">
            {step.status === 'done' ? '✓' : step.status === 'failed' ? '✕' : step.status === 'active' ? '●' : '○'}
          </span>
          <span>{step.title}</span>
        </div>
      ))}
    </aside>
  )
}
