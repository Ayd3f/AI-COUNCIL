import { useState } from 'react'

import type { AgentOutcome, ArgumentRef, ConsensusReport, RoundResult } from '../lib/types'
import { AGENT_COLOR } from './AgentStatusBar'
import { CopyButton, Markdown } from './Markdown'

function pct(v: number): string {
  return `${Math.round(v * 100)}%`
}

/** English readings of the diagnosis codes the backend derives. */
const ADVICE: Record<string, string> = {
  no_credits: 'No credits left on this provider — top up the account, or untick this agent',
  model_not_found: 'That model is not available to this key — set a current one in Settings',
  auth: 'The provider rejected the API key',
  permission_denied: 'This key has no access to that model',
  rate_limit: 'Rate limited by the provider',
  timeout: 'The provider did not answer in time — raise the timeout',
  provider_down: 'Provider-side outage — the other agents carried on',
  invalid_output: 'The model would not return valid JSON, even when re-asked',
  not_configured: 'No API key configured for this provider',
  unknown: 'The provider returned an error',
}

function StanceList({
  title,
  kind,
  items,
}: {
  title: string
  kind: 'accepted' | 'rejected' | 'uncertain'
  items: ArgumentRef[]
}) {
  if (!items?.length) return null
  return (
    <>
      <div className="section-label">
        <span className={`badge ${kind}`}>{title}</span>
      </div>
      <ul className="stance-list">
        {items.map((it, i) => (
          <li key={i} className={`stance ${kind}`}>
            <span className="who" style={{ color: AGENT_COLOR[it.agent] ?? 'inherit' }}>
              {it.agent}
            </span>{' '}
            {it.argument}
            <span className="why">↳ {it.reason}</span>
          </li>
        ))}
      </ul>
    </>
  )
}

function AgentCard({ outcome, isInitial }: { outcome: AgentOutcome; isInitial: boolean }) {
  const p = outcome.payload
  const body = isInitial ? p?.answer : p?.position

  return (
    <article className="card">
      <header className="card-head">
        <span className="dot" style={{ ['--dot' as string]: AGENT_COLOR[outcome.agent] }} />
        <span className="agent-name" style={{ color: AGENT_COLOR[outcome.agent] }}>
          {outcome.agent}
        </span>
        {p?.confidence !== undefined && (
          <span className="confidence" title="Self-reported confidence">
            {pct(p.confidence)}
          </span>
        )}
        {body ? <CopyButton text={body} /> : null}
      </header>

      {!outcome.payload ? (
        <div className="error-box">
          <strong>{ADVICE[outcome.diagnosis?.code ?? ''] ?? outcome.status.replace('_', ' ')}</strong>
          {outcome.diagnosis?.url && (
            <>
              {' '}
              <a href={outcome.diagnosis.url} target="_blank" rel="noreferrer">
                open
              </a>
            </>
          )}
          <span className="k">{outcome.error ?? 'no data'}</span>
          <span className="k">
            {outcome.error_kind ?? 'unknown'} · {outcome.attempts} attempt
            {outcome.attempts === 1 ? '' : 's'}
          </span>
        </div>
      ) : (
        <>
          <Markdown>{body}</Markdown>

          {isInitial && !!p?.key_points?.length && (
            <>
              <div className="section-label">Key points</div>
              <ul className="bullets">
                {p.key_points.map((k, i) => (
                  <li key={i}>{k}</li>
                ))}
              </ul>
            </>
          )}
          {isInitial && !!p?.assumptions?.length && (
            <>
              <div className="section-label">Assumptions</div>
              <ul className="bullets">
                {p.assumptions.map((k, i) => (
                  <li key={i} className="muted">
                    {k}
                  </li>
                ))}
              </ul>
            </>
          )}

          {!isInitial && (
            <>
              <StanceList title="Accepted" kind="accepted" items={p?.accepted_arguments ?? []} />
              <StanceList title="Rejected" kind="rejected" items={p?.rejected_arguments ?? []} />
              <StanceList
                title="Uncertain"
                kind="uncertain"
                items={p?.uncertain_arguments ?? []}
              />

              {!!p?.persuasion?.length && (
                <>
                  <div className="section-label">
                    <span className="badge persuading">Persuading</span>
                  </div>
                  <ul className="stance-list">
                    {p.persuasion.map((it, i) => (
                      <li key={i} className="stance persuading">
                        <span className="who" style={{ color: AGENT_COLOR[it.agent] }}>
                          → {it.agent}
                        </span>
                        <span className="why">
                          <em>their objection:</em> {it.their_objection}
                        </span>
                        <span className="why">
                          <em>counter:</em> {it.my_counter}
                        </span>
                        {it.concession && (
                          <span className="why">
                            <em>conceded:</em> {it.concession}
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                </>
              )}

              {p?.what_would_change_my_mind && (
                <p className="small muted" style={{ marginTop: 10, marginBottom: 0 }}>
                  <strong>Would change my mind:</strong> {p.what_would_change_my_mind}
                </p>
              )}
              {p?.proposed_common_answer && (
                <p className="small muted" style={{ marginTop: 6, marginBottom: 0 }}>
                  <strong>Proposed common answer:</strong> {p.proposed_common_answer}
                </p>
              )}

              <div className="row" style={{ marginTop: 12 }}>
                <span className={`badge ${p?.changed_my_position ? 'changed' : 'held'}`}>
                  {p?.changed_my_position ? 'Changed position' : 'Held position'}
                </span>
                {!!p?.persuaded_by?.length && (
                  <span className="small muted">
                    persuaded by <strong>{p.persuaded_by.join(', ')}</strong>
                  </span>
                )}
                <span className="small muted">{p?.why_changed}</span>
              </div>
            </>
          )}

          <div className="small faint" style={{ marginTop: 12 }}>
            {outcome.model} · {outcome.latency_ms}ms ·{' '}
            {(outcome.usage.input_tokens + outcome.usage.output_tokens).toLocaleString()} tokens
            {outcome.attempts > 1 ? ` · ${outcome.attempts} attempts` : ''}
          </div>
        </>
      )}
    </article>
  )
}

export function ConsensusStrip({ report }: { report: ConsensusReport }) {
  return (
    <div className={`consensus-strip ${report.reached ? 'reached' : ''}`}>
      <div>
        <div className={`consensus-score ${report.reached ? 'reached' : ''}`}>
          {pct(report.score)}
        </div>
        <div className="small faint">
          {report.agree_count}/{report.participant_count} agree
        </div>
      </div>
      <div className="consensus-reason">
        <strong>{report.reached ? 'Consensus reached' : 'No consensus yet'}</strong> ·{' '}
        <code className="mono small">{report.rule}</code>
        <div style={{ marginTop: 4 }}>{report.reason}</div>
        {report.unsubstantiated_agents.length > 0 && (
          <div style={{ marginTop: 6 }} className="small">
            <span className="badge uncertain">Discarded</span> unsubstantiated agreement from{' '}
            {report.unsubstantiated_agents.join(', ')} — an agent must give a specific
            reason, not just say &ldquo;I agree&rdquo;.
          </div>
        )}
      </div>
      {Object.keys(report.agreement_by_agent).length > 0 && (
        <div className="agreement-grid" style={{ flex: '1 1 260px', minWidth: 220 }}>
          {Object.entries(report.agreement_by_agent).map(([agent, level]) => (
            <div className="agreement-row" key={agent}>
              <span className="name" style={{ color: AGENT_COLOR[agent] }}>
                {agent}
              </span>
              <span className="track">
                <span className="fill" style={{ width: `${Math.round(level * 100)}%` }} />
              </span>
              <span className="val">{pct(level)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export function RoundView({ round, defaultOpen }: { round: RoundResult; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  const isInitial = round.kind === 'INITIAL'
  const entries = Object.entries(round.outcomes)
  const okCount = entries.filter(([, o]) => o.status === 'OK').length

  return (
    <section className="round">
      <button className="round-head" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className="round-title">
          {isInitial ? 'ROUND 0 — INDEPENDENT ANSWERS' : `ROUND ${round.index} — DEBATE`}
        </span>
        <span className="round-sub">
          {okCount}/{entries.length} responded
          {round.consensus
            ? ` · consensus ${pct(round.consensus.score)}${
                round.consensus.reached ? ' ✓' : ''
              }`
            : ''}
        </span>
        <span className={`chev ${open ? 'open' : ''}`}>›</span>
      </button>

      {open && (
        <div className="round-body">
          <div className="cards">
            {entries
              .sort(([a], [b]) => a.localeCompare(b))
              .map(([agent, outcome]) => (
                <AgentCard key={agent} outcome={outcome} isInitial={isInitial} />
              ))}
          </div>
          {round.consensus && <ConsensusStrip report={round.consensus} />}
        </div>
      )}
    </section>
  )
}
