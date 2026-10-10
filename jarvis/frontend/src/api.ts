export type ConnectionState = 'connecting' | 'open' | 'closed'

export type SystemStats = {
  cpu: number
  ram_percent: number
  ram_used_gb: number
  ram_total_gb: number
  battery: { present: boolean; percent?: number; plugged_in?: boolean; detail?: string }
  internet: boolean
}

export type StatusInfo = {
  state: { app_state: string; last_action?: string }
  llm: { online: boolean; models: string[]; configured: string; runtime: string }
  voice: { listening: boolean; devices: { index: number; name: string; default: boolean }[]; mode: string }
  tts: { sapi: boolean; piper: boolean }
  browser: { playwright: boolean }
  tools: { name: string; risk: string; description: string }[]
  pending_confirmations: ConfirmationRequest[]
}

export type ConfirmationRequest = {
  id: string
  tool: string
  title: string
  description: string
  detail: Record<string, unknown>
  risk: string
}

export type HistoryEntry = { id?: number; role: string; content: string; ts: number; source: string }

export type TaskStepUI = { title: string; status: string; detail?: string }

export type TaskPlanUI = {
  description: string
  status: string
  steps: TaskStepUI[]
}

export type SettingsShape = Record<string, Record<string, unknown>>

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json() as Promise<T>
}
