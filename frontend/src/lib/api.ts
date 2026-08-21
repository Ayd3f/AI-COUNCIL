import type {
  AppSettings,
  DebateDetail,
  DebateSummary,
  RoundResult,
  StartDebatePayload,
} from './types'

const BASE = '/api'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body?.detail ?? detail
    } catch {
      /* keep statusText */
    }
    throw new ApiError(res.status, detail)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export const api = {
  settings: () => request<AppSettings>('/settings'),

  startDebate: (payload: StartDebatePayload) =>
    request<{ debate_id: string; status: string; events_url: string }>('/debate', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  debate: (id: string) => request<DebateDetail>(`/debate/${id}`),

  rounds: (id: string) =>
    request<{ rounds: RoundResult[] }>(`/debate/${id}/rounds`).then((r) => r.rounds),

  list: () => request<{ debates: DebateSummary[] }>('/debates').then((r) => r.debates),

  remove: (id: string) => request<{ deleted: string }>(`/debate/${id}`, { method: 'DELETE' }),

  clearAll: () => request<{ deleted: number }>('/debates', { method: 'DELETE' }),

  cancel: (id: string) =>
    request<{ cancelled: boolean }>(`/debate/${id}/cancel`, { method: 'POST' }),

  exportUrl: (id: string, fmt: 'markdown' | 'json') =>
    `${BASE}/debate/${id}/export?fmt=${fmt}`,
}

export const EVENT_TYPES = [
  'debate_started',
  'round_started',
  'agent_started',
  'agent_finished',
  'agent_failed',
  'consensus_check',
  'round_finished',
  'synthesis_started',
  'debate_finished',
  'debate_error',
  'stream_end',
  'stream_error',
] as const

export type KnownEventType = (typeof EVENT_TYPES)[number]

/** Subscribe to a debate's SSE stream. Returns a disposer. */
export function subscribeToDebate(
  debateId: string,
  onEvent: (type: KnownEventType, data: Record<string, unknown>) => void,
  onClose?: () => void,
): () => void {
  const source = new EventSource(`${BASE}/debate/${debateId}/events`)
  let closed = false

  const close = () => {
    if (closed) return
    closed = true
    source.close()
    onClose?.()
  }

  for (const type of EVENT_TYPES) {
    source.addEventListener(type, (raw) => {
      const message = raw as MessageEvent<string>
      let payload: Record<string, unknown> = {}
      try {
        payload = JSON.parse(message.data)
      } catch {
        payload = {}
      }
      onEvent(type, payload)
      if (type === 'stream_end' || type === 'debate_error' || type === 'stream_error') {
        close()
      }
    })
  }

  source.onerror = () => {
    // EventSource auto-reconnects; the backend replays from seq 0 so
    // duplicate events are harmless. Only give up if the stream is closed.
    if (source.readyState === EventSource.CLOSED) close()
  }

  return close
}

export function downloadFile(url: string, filename: string): void {
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
}
