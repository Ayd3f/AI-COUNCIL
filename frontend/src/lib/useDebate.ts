import { useCallback, useEffect, useRef, useState } from 'react'

import { api, subscribeToDebate, type KnownEventType } from './api'
import type {
  AgentName,
  AgentStatus,
  AppSettings,
  ConsensusReport,
  DebateDetail,
  DebateSummary,
  RoundResult,
  StartDebatePayload,
} from './types'

export type RunPhase = 'idle' | 'running' | 'synthesizing' | 'completed' | 'failed'

export interface LiveAgentState {
  status: AgentStatus
  phase: string
  latencyMs?: number
  attempts?: number
  error?: string
  tokens?: number
}

export interface LogLine {
  seq: number
  type: KnownEventType
  text: string
  at: string
}

export interface DebateState {
  debateId: string | null
  phase: RunPhase
  currentRound: number
  maxRounds: number
  agents: AgentName[]
  agentState: Record<string, LiveAgentState>
  rounds: RoundResult[]
  consensus: ConsensusReport | null
  detail: DebateDetail | null
  log: LogLine[]
  error: string | null
}

const EMPTY: DebateState = {
  debateId: null,
  phase: 'idle',
  currentRound: 0,
  maxRounds: 0,
  agents: [],
  agentState: {},
  rounds: [],
  consensus: null,
  detail: null,
  log: [],
  error: null,
}

function describe(type: KnownEventType, d: Record<string, unknown>): string {
  const agent = d.agent as string | undefined
  switch (type) {
    case 'debate_started':
      return `Debate started with ${(d.agents as string[] | undefined)?.join(', ')}`
    case 'round_started':
      return d.round === 0
        ? 'Round 0 — independent answers (agents cannot see each other)'
        : `Round ${d.round} — debate`
    case 'agent_started':
      return `${agent} thinking…`
    case 'agent_finished':
      return `${agent} finished in ${d.latency_ms}ms (${d.tokens} tokens)`
    case 'agent_failed':
      return `${agent} failed: ${d.reason}`
    case 'consensus_check':
      return `Consensus check: ${d.reached ? 'REACHED' : 'not reached'} — score ${(
        (d.score as number) * 100
      ).toFixed(0)}% (${d.rule})`
    case 'round_finished':
      return `Round ${d.round} finished — ${(d.ok as string[] | undefined)?.length ?? 0} ok, ${
        (d.failed as string[] | undefined)?.length ?? 0
      } failed`
    case 'synthesis_started':
      return 'Compiling the final synthesis…'
    case 'debate_finished':
      return `Debate complete after ${d.rounds_used}/${d.max_rounds} rounds`
    case 'debate_error':
      return `Debate error: ${d.error}`
    default:
      return type
  }
}

/** Rebuild the per-agent status chips from stored rounds (no live events). */
function finalAgentState(rounds: RoundResult[]): Record<string, LiveAgentState> {
  const out: Record<string, LiveAgentState> = {}
  for (const round of rounds) {
    for (const [agent, outcome] of Object.entries(round.outcomes)) {
      out[agent] = {
        status: outcome.status,
        phase: round.kind === 'INITIAL' ? 'initial' : 'debate',
        latencyMs: outcome.latency_ms,
        attempts: outcome.attempts,
        error: outcome.error ?? undefined,
        tokens: outcome.usage.input_tokens + outcome.usage.output_tokens,
      }
    }
  }
  return out
}

export function useSettings() {
  const [settings, setSettings] = useState<AppSettings | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    api
      .settings()
      .then((s) => alive && setSettings(s))
      .catch((e) => alive && setError(String(e.message ?? e)))
    return () => {
      alive = false
    }
  }, [])

  return { settings, error }
}

export function useHistory() {
  const [items, setItems] = useState<DebateSummary[]>([])
  const refresh = useCallback(async () => {
    try {
      setItems(await api.list())
    } catch {
      /* history is non-critical */
    }
  }, [])
  useEffect(() => {
    void refresh()
  }, [refresh])
  return { items, refresh, setItems }
}

export function useDebate() {
  const [state, setState] = useState<DebateState>(EMPTY)
  const disposer = useRef<null | (() => void)>(null)
  const debateIdRef = useRef<string | null>(null)

  const stop = useCallback(() => {
    disposer.current?.()
    disposer.current = null
  }, [])

  useEffect(() => () => stop(), [stop])

  const reset = useCallback(() => {
    stop()
    debateIdRef.current = null
    setState(EMPTY)
  }, [stop])

  const refreshRounds = useCallback(async (id: string) => {
    try {
      const rounds = await api.rounds(id)
      setState((s) => (s.debateId === id ? { ...s, rounds } : s))
    } catch {
      /* transient */
    }
  }, [])

  const loadDetail = useCallback(async (id: string) => {
    const detail = await api.debate(id)
    setState((s) => ({
      ...s,
      detail,
      rounds: detail.rounds,
      maxRounds: detail.max_rounds || s.maxRounds,
      consensus:
        [...detail.rounds].reverse().find((r) => r.consensus)?.consensus ?? s.consensus,
      phase: detail.status === 'FAILED' ? 'failed' : 'completed',
      error: detail.error,
    }))
  }, [])

  const attach = useCallback(
    (id: string, seedAgents: AgentName[], maxRounds: number) => {
      stop()
      debateIdRef.current = id
      setState({
        ...EMPTY,
        debateId: id,
        phase: 'running',
        agents: seedAgents,
        maxRounds,
        agentState: Object.fromEntries(
          seedAgents.map((a) => [a, { status: 'IDLE' as AgentStatus, phase: 'queued' }]),
        ),
      })

      disposer.current = subscribeToDebate(id, (type, data) => {
        setState((s) => {
          if (s.debateId !== id) return s
          const next: DebateState = { ...s }
          const seq = Number(data.seq ?? s.log.length)
          next.log = [
            ...s.log.slice(-200),
            {
              seq,
              type,
              text: describe(type, (data.data as Record<string, unknown>) ?? data),
              at: String(data.created_at ?? ''),
            },
          ]
          const payload = (data.data as Record<string, unknown>) ?? {}

          switch (type) {
            case 'debate_started': {
              const agents = (payload.agents as AgentName[]) ?? s.agents
              next.agents = agents
              next.agentState = Object.fromEntries(
                agents.map((a) => [a, { status: 'IDLE' as AgentStatus, phase: 'queued' }]),
              )
              const cfg = payload.config as { max_rounds?: number } | undefined
              if (cfg?.max_rounds) next.maxRounds = cfg.max_rounds
              break
            }
            case 'round_started': {
              next.currentRound = Number(payload.round ?? 0)
              next.agentState = Object.fromEntries(
                next.agents.map((a) => [
                  a,
                  { status: 'IDLE' as AgentStatus, phase: 'queued' },
                ]),
              )
              break
            }
            case 'agent_started': {
              const a = String(payload.agent)
              next.agentState = {
                ...next.agentState,
                [a]: { status: 'THINKING', phase: String(payload.phase ?? '') },
              }
              break
            }
            case 'agent_finished': {
              const a = String(payload.agent)
              next.agentState = {
                ...next.agentState,
                [a]: {
                  status: 'OK',
                  phase: String(payload.phase ?? ''),
                  latencyMs: Number(payload.latency_ms ?? 0),
                  attempts: Number(payload.attempts ?? 1),
                  tokens: Number(payload.tokens ?? 0),
                },
              }
              break
            }
            case 'agent_failed': {
              const a = String(payload.agent)
              next.agentState = {
                ...next.agentState,
                [a]: {
                  status: (payload.status as AgentStatus) ?? 'ERROR',
                  phase: String(payload.phase ?? ''),
                  error: String(payload.reason ?? 'unknown error'),
                  attempts: Number(payload.attempts ?? 1),
                },
              }
              break
            }
            case 'consensus_check':
              next.consensus = payload as unknown as ConsensusReport
              break
            case 'synthesis_started':
              next.phase = 'synthesizing'
              break
            case 'debate_error':
              next.phase = 'failed'
              next.error = String(payload.error ?? 'unknown error')
              break
            default:
              break
          }
          return next
        })

        if (type === 'round_finished') void refreshRounds(id)
        // `stream_end` also covers the replay case (a debate that had already
        // finished before this client connected).
        if (type === 'debate_finished' || type === 'stream_end') {
          void loadDetail(id).catch(() => undefined)
        }
      })
    },
    [loadDetail, refreshRounds, stop],
  )

  const start = useCallback(
    async (payload: StartDebatePayload) => {
      const res = await api.startDebate(payload)
      const cfg = (res as unknown as { config: { agents: AgentName[]; max_rounds: number } })
        .config
      attach(res.debate_id, cfg.agents, cfg.max_rounds)
      return res.debate_id
    },
    [attach],
  )

  const open = useCallback(
    async (id: string) => {
      stop()
      debateIdRef.current = id
      const detail = await api.debate(id)
      if (detail.running) {
        attach(id, detail.config?.agents ?? [], detail.config?.max_rounds ?? 0)
        return
      }
      setState({
        ...EMPTY,
        debateId: id,
        phase: detail.status === 'FAILED' ? 'failed' : 'completed',
        agents: detail.config?.agents ?? [],
        maxRounds: detail.max_rounds,
        currentRound: detail.rounds_used,
        rounds: detail.rounds,
        detail,
        // Replayed debates have no live events, so rebuild the status chips
        // from the last round each agent actually took part in.
        agentState: finalAgentState(detail.rounds),
        consensus:
          [...detail.rounds].reverse().find((r) => r.consensus)?.consensus ?? null,
        error: detail.error,
      })
    },
    [attach, stop],
  )

  const cancel = useCallback(async () => {
    const id = debateIdRef.current
    if (!id) return
    await api.cancel(id).catch(() => undefined)
  }, [])

  return { state, start, open, reset, cancel }
}
