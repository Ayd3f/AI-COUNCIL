import type { AgentName } from '../lib/types'
import type { LiveAgentState } from '../lib/useDebate'

export const AGENT_COLOR: Record<string, string> = {
  OPENAI: 'var(--openai)',
  CLAUDE: 'var(--claude)',
  GEMINI: 'var(--gemini)',
  GROK: 'var(--grok)',
  DEEPSEEK: 'var(--deepseek)',
  // Deliberately far from GROK's colour — the names differ by one letter.
  GROQCLOUD: 'var(--groqcloud)',
  CEREBRAS: 'var(--cerebras)',
  MISTRAL: 'var(--mistral)',
  LOCAL: 'var(--local)',
}

const STATUS_DOT: Record<string, string> = {
  IDLE: 'var(--text-faint)',
  THINKING: 'var(--info)',
  OK: 'var(--ok)',
  ERROR: 'var(--err)',
  INVALID_OUTPUT: 'var(--warn)',
  DISABLED: 'var(--text-faint)',
}

const STATUS_TEXT: Record<string, string> = {
  IDLE: 'Waiting',
  THINKING: 'Thinking…',
  OK: 'Finished',
  ERROR: 'Error',
  INVALID_OUTPUT: 'Invalid output',
  DISABLED: 'Not configured',
}

/** How each round-table role argues — shown under the agent name. */
export const ROLE_LABEL: Record<string, string> = {
  ADVOCATE: 'ADVOCATE · drives the council to one answer',
  SKEPTIC: 'SKEPTIC · attacks the weakest link',
  EVIDENCE: 'EVIDENCE · demands proof',
  PRAGMATIST: 'PRAGMATIST · tests feasibility',
  BRIDGE: 'BRIDGE · finds wording everyone can sign',
  ANALYST: 'ANALYST · maps the disagreement',
}

interface Props {
  agents: AgentName[]
  models: Record<string, string>
  agentState: Record<string, LiveAgentState>
  roles?: Record<string, string>
}

export function AgentStatusBar({ agents, models, agentState, roles }: Props) {
  return (
    <div className="agent-bar">
      {agents.map((agent) => {
        const st = agentState[agent] ?? { status: 'IDLE', phase: '' }
        const cls =
          st.status === 'THINKING'
            ? 'is-thinking'
            : st.status === 'OK'
              ? 'is-ok'
              : st.status === 'ERROR' || st.status === 'INVALID_OUTPUT'
                ? 'is-error'
                : st.status === 'DISABLED'
                  ? 'is-disabled'
                  : ''
        return (
          <div
            key={agent}
            className={`agent-chip ${cls}`}
            style={{ ['--dot' as string]: AGENT_COLOR[agent] }}
          >
            <div className="agent-chip-head">
              <span
                className={`dot ${st.status === 'THINKING' ? 'pulse' : ''}`}
                style={{ ['--dot' as string]: STATUS_DOT[st.status], color: STATUS_DOT[st.status] }}
              />
              <span className="agent-name">{agent}</span>
            </div>
            <div className="agent-model" title={models[agent]}>
              {models[agent] ?? '—'}
            </div>
            {roles?.[agent] && (
              <div className="agent-role" title={ROLE_LABEL[roles[agent]] ?? roles[agent]}>
                {roles[agent]}
              </div>
            )}
            <div className={`agent-meta ${st.error ? 'err' : ''}`}>
              {st.error
                ? `${STATUS_TEXT[st.status]}: ${st.error.slice(0, 90)}`
                : st.status === 'OK'
                  ? `${STATUS_TEXT.OK} · ${st.latencyMs ?? 0}ms${
                      (st.attempts ?? 1) > 1 ? ` · ${st.attempts} attempts` : ''
                    }`
                  : STATUS_TEXT[st.status]}
            </div>
            {st.status === 'THINKING' && <div className="thinking-bar" />}
          </div>
        )
      })}
    </div>
  )
}
