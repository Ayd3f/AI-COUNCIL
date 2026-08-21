import type { ConsensusReport, DebateDetail } from '../lib/types'
import { AGENT_COLOR } from './AgentStatusBar'
import { CopyButton, Markdown } from './Markdown'

function pct(v: number): string {
  return `${Math.round(v * 100)}%`
}

interface Props {
  detail: DebateDetail
  consensus: ConsensusReport | null
  onExport: (fmt: 'markdown' | 'json') => void
}

export function FinalReport({ detail, consensus, onExport }: Props) {
  const s = detail.synthesis
  if (!s) return null

  const agreement = consensus?.agreement_by_agent ?? {}

  return (
    <section className={`panel final ${detail.consensus_reached ? '' : 'no-consensus'}`}>
      <header className="final-head">
        <h2>DEBATE COMPLETE</h2>
        <div className="final-stats">
          <div className="stat">
            <span className="v" style={{ color: detail.consensus_reached ? 'var(--ok)' : 'var(--warn)' }}>
              {pct(detail.consensus_score)}
            </span>
            <span className="l">Consensus</span>
          </div>
          <div className="stat">
            <span className="v">
              {detail.rounds_used} / {detail.max_rounds}
            </span>
            <span className="l">Rounds</span>
          </div>
          <div className="stat">
            <span className="v">{Math.round(s.confidence)}%</span>
            <span className="l">Confidence</span>
          </div>
          <div className="stat">
            <span className="v" style={{ fontSize: 15, paddingTop: 6 }}>
              {detail.consensus_reached ? 'REACHED' : 'NOT REACHED'}
            </span>
            <span className="l">Status</span>
          </div>
        </div>
      </header>

      <div className="panel-title" style={{ marginTop: 20 }}>
        <span>Final answer</span>
        <span className="row">
          <CopyButton text={s.consensus} label="Copy answer" />
          <button className="btn btn-sm" onClick={() => onExport('markdown')}>
            Export .md
          </button>
          <button className="btn btn-sm" onClick={() => onExport('json')}>
            Export .json
          </button>
        </span>
      </div>
      <Markdown>{s.consensus}</Markdown>

      {s.main_reasoning && (
        <>
          <div className="section-label">Main reasoning</div>
          <Markdown>{s.main_reasoning}</Markdown>
        </>
      )}

      {Object.keys(agreement).length > 0 && (
        <>
          <div className="section-label">Agreement</div>
          <div className="agreement-grid">
            {Object.entries(agreement).map(([agent, level]) => (
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
        </>
      )}

      <div className="grid-2" style={{ marginTop: 20 }}>
        {s.key_agreements.length > 0 && (
          <div>
            <div className="section-label">Key agreements</div>
            <ul className="bullets">
              {s.key_agreements.map((k, i) => (
                <li key={i}>
                  <span style={{ color: 'var(--ok)' }}>✓</span> {k}
                </li>
              ))}
            </ul>
          </div>
        )}
        {s.disagreements.length > 0 && (
          <div>
            <div className="section-label">Disagreements</div>
            <ul className="bullets">
              {s.disagreements.map((k, i) => (
                <li key={i}>
                  <span style={{ color: 'var(--warn)' }}>⚠</span> {k}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {s.strongest_arguments.length > 0 && (
        <>
          <div className="section-label">Strongest arguments from the debate</div>
          <ul className="bullets">
            {s.strongest_arguments.map((k, i) => (
              <li key={i}>{k}</li>
            ))}
          </ul>
        </>
      )}

      {s.rejected_arguments.length > 0 && (
        <>
          <div className="section-label">Rejected arguments — and why</div>
          <ul className="stance-list">
            {s.rejected_arguments.map((r, i) => (
              <li className="stance rejected" key={i}>
                {r.argument}
                <span className="why">
                  ↳ rejected by {r.rejected_by.join(', ') || '—'}: {r.reason}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}

      {s.minority_positions.length > 0 && (
        <>
          <div className="section-label">
            <span className="badge minority">Minority opinion preserved</span>
          </div>
          {s.minority_positions.map((m, i) => (
            <div className="minority" key={i}>
              <strong style={{ color: AGENT_COLOR[m.agent] }}>{m.agent}</strong>
              <Markdown>{m.position}</Markdown>
              {m.evidence && (
                <p className="small muted" style={{ margin: '6px 0 0' }}>
                  <strong>Evidence:</strong> {m.evidence}
                </p>
              )}
              {m.assessment && (
                <p className="small muted" style={{ margin: '4px 0 0' }}>
                  <strong>Assessment:</strong> {m.assessment}
                </p>
              )}
            </div>
          ))}
        </>
      )}

      {s.individual_positions.length > 0 && (
        <>
          <div className="section-label">Individual positions</div>
          <div className="cards">
            {s.individual_positions.map((p, i) => (
              <div className="card" key={i}>
                <header className="card-head">
                  <span className="dot" style={{ ['--dot' as string]: AGENT_COLOR[p.agent] }} />
                  <span className="agent-name" style={{ color: AGENT_COLOR[p.agent] }}>
                    {p.agent}
                  </span>
                </header>
                <Markdown>{p.position}</Markdown>
              </div>
            ))}
          </div>
        </>
      )}

      <p className="small faint" style={{ marginTop: 18, marginBottom: 0 }}>
        Agreement percentages, the consensus score and the minority list are computed
        deterministically by the consensus engine — no single model decides the outcome.
        {detail.synthesis_agent
          ? ` Prose compiled by ${detail.synthesis_agent} acting as a neutral synthesiser.`
          : ' Compiled deterministically because every model failed the synthesis step.'}
      </p>
    </section>
  )
}
