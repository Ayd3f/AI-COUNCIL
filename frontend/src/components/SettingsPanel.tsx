import type { AgentName, AppSettings } from '../lib/types'

export interface DebateSettings {
  agents: AgentName[]
  minRounds: number
  maxRounds: number
  threshold: number
  timeout: number
  temperature: number
  maxRetries: number
  models: Record<string, string>
}

export function initialSettings(s: AppSettings): DebateSettings {
  return {
    agents: s.agents.filter((a) => a.configured).map((a) => a.agent),
    minRounds: s.defaults.min_rounds,
    maxRounds: s.defaults.max_rounds,
    threshold: s.defaults.consensus_threshold,
    timeout: s.defaults.timeout,
    temperature: s.defaults.temperature,
    maxRetries: s.defaults.max_retries,
    models: Object.fromEntries(s.agents.map((a) => [a.agent, a.model])),
  }
}

interface Props {
  settings: AppSettings
  value: DebateSettings
  onChange: (next: DebateSettings) => void
  disabled?: boolean
}

export function SettingsPanel({ settings, value, onChange, disabled }: Props) {
  const patch = (p: Partial<DebateSettings>) => onChange({ ...value, ...p })

  const toggleAgent = (agent: AgentName) => {
    const set = new Set(value.agents)
    if (set.has(agent)) set.delete(agent)
    else set.add(agent)
    patch({ agents: settings.agents.map((a) => a.agent).filter((a) => set.has(a)) })
  }

  return (
    <section className="panel">
      <h3 className="panel-title">
        <span>Settings</span>
        <span className="faint small" style={{ letterSpacing: 0, textTransform: 'none' }}>
          Applied to the next debate only
        </span>
      </h3>

      <div className="section-label">AI participants</div>
      <div className="agent-bar">
        {settings.agents.map((a) => {
          const checked = value.agents.includes(a.agent)
          return (
            <label
              key={a.agent}
              className={`agent-chip ${a.configured ? '' : 'is-disabled'}`}
              style={{ ['--dot' as string]: 'var(--border)', cursor: a.configured ? 'pointer' : 'not-allowed', marginBottom: 0 }}
              title={
                a.configured
                  ? `${a.provider} · ${a.model}`
                  : `Not configured — set ${a.key_env} in .env`
              }
            >
              <div className="agent-chip-head">
                <input
                  type="checkbox"
                  checked={checked && a.configured}
                  disabled={disabled || !a.configured}
                  onChange={() => toggleAgent(a.agent)}
                />
                <span className="agent-name">{a.agent}</span>
              </div>
              <input
                className="agent-model"
                style={{ marginTop: 8, padding: '5px 8px', fontSize: 11 }}
                value={value.models[a.agent] ?? ''}
                disabled={disabled || !a.configured}
                spellCheck={false}
                onChange={(e) =>
                  patch({ models: { ...value.models, [a.agent]: e.target.value } })
                }
                aria-label={`${a.agent} model`}
              />
              {!a.configured && (
                <div className="agent-meta err">Set {a.key_env} in .env</div>
              )}
            </label>
          )
        })}
      </div>

      <div className="field-row" style={{ marginTop: 18 }}>
        <div>
          <label htmlFor="min-rounds">Min rounds</label>
          <input
            id="min-rounds"
            type="number"
            min={0}
            max={settings.limits.max_rounds_allowed}
            value={value.minRounds}
            disabled={disabled}
            onChange={(e) => patch({ minRounds: Number(e.target.value) })}
          />
        </div>
        <div>
          <label htmlFor="max-rounds">Max rounds</label>
          <input
            id="max-rounds"
            type="number"
            min={1}
            max={settings.limits.max_rounds_allowed}
            value={value.maxRounds}
            disabled={disabled}
            onChange={(e) => patch({ maxRounds: Number(e.target.value) })}
          />
        </div>
        <div>
          <label htmlFor="threshold">
            Consensus threshold — {(value.threshold * 100).toFixed(0)}%
          </label>
          <input
            id="threshold"
            type="range"
            min={0.5}
            max={1}
            step={0.01}
            value={value.threshold}
            disabled={disabled}
            onChange={(e) => patch({ threshold: Number(e.target.value) })}
          />
        </div>
        <div>
          <label htmlFor="timeout">Timeout (seconds)</label>
          <input
            id="timeout"
            type="number"
            min={settings.limits.timeout_range[0]}
            max={settings.limits.timeout_range[1]}
            value={value.timeout}
            disabled={disabled}
            onChange={(e) => patch({ timeout: Number(e.target.value) })}
          />
        </div>
        <div>
          <label htmlFor="temperature">Temperature — {value.temperature.toFixed(2)}</label>
          <input
            id="temperature"
            type="range"
            min={0}
            max={1.5}
            step={0.05}
            value={value.temperature}
            disabled={disabled}
            onChange={(e) => patch({ temperature: Number(e.target.value) })}
          />
        </div>
        <div>
          <label htmlFor="retries">Max retries</label>
          <input
            id="retries"
            type="number"
            min={0}
            max={5}
            value={value.maxRetries}
            disabled={disabled}
            onChange={(e) => patch({ maxRetries: Number(e.target.value) })}
          />
        </div>
      </div>

      <p className="small faint" style={{ marginTop: 12, marginBottom: 0 }}>
        Temperature is sent only to providers that accept it; models that reject the
        parameter are detected automatically and the request is retried without it.
        {!settings.pricing_configured &&
          ' Cost estimates need prices in backend/pricing.json.'}
      </p>
    </section>
  )
}
