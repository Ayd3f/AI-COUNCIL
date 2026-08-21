import type { CostReport } from '../lib/types'
import { AGENT_COLOR } from './AgentStatusBar'

export function TokenPanel({ cost }: { cost: CostReport }) {
  if (!cost.lines.length) return null
  return (
    <section className="panel">
      <h3 className="panel-title">
        <span>Tokens &amp; estimated cost</span>
        <span className="faint small" style={{ letterSpacing: 0, textTransform: 'none' }}>
          Prices come from backend/pricing.json
        </span>
      </h3>

      <div className="table-scroll">
        <table className="table">
          <thead>
            <tr>
              <th>Agent</th>
              <th>Model</th>
              <th className="num">Input</th>
              <th className="num">Output</th>
              <th className="num">Total</th>
              <th className="num">Est. cost</th>
            </tr>
          </thead>
          <tbody>
            {cost.lines.map((l) => (
              <tr key={l.agent}>
                <td style={{ color: AGENT_COLOR[l.agent], fontWeight: 700, letterSpacing: '0.08em', fontSize: 12 }}>
                  {l.agent}
                </td>
                <td className="mono small faint">{l.model}</td>
                <td className="num">{l.input_tokens.toLocaleString()}</td>
                <td className="num">{l.output_tokens.toLocaleString()}</td>
                <td className="num">{l.total_tokens.toLocaleString()}</td>
                <td className="num">
                  {l.pricing_known ? `$${(l.estimated_cost_usd ?? 0).toFixed(4)}` : '—'}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td>TOTAL</td>
              <td />
              <td className="num" />
              <td className="num" />
              <td className="num">{cost.total_tokens.toLocaleString()}</td>
              <td className="num">${cost.estimated_cost_usd.toFixed(4)}</td>
            </tr>
          </tfoot>
        </table>
      </div>

      {!cost.complete && (
        <p className="notice" style={{ marginTop: 12, marginBottom: 0 }}>
          Cost is partial. No price is configured for:{' '}
          <span className="mono">{cost.models_without_pricing.join(', ')}</span>. Add them to{' '}
          <code>backend/pricing.json</code> — models without a price are never counted as $0.
        </p>
      )}
    </section>
  )
}
