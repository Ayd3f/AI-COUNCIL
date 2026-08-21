import { useEffect, useMemo, useState } from 'react'

import { AgentStatusBar } from './components/AgentStatusBar'
import { EventLog } from './components/EventLog'
import { FinalReport } from './components/FinalReport'
import { HistoryPanel } from './components/HistoryPanel'
import { QuestionForm } from './components/QuestionForm'
import { ConsensusStrip, RoundView } from './components/RoundView'
import { SettingsPanel, initialSettings, type DebateSettings } from './components/SettingsPanel'
import { TokenPanel } from './components/TokenPanel'
import { api, downloadFile } from './lib/api'
import { useDebate, useHistory, useSettings } from './lib/useDebate'

export default function App() {
  const { settings, error: settingsError } = useSettings()
  const { state, start, open, reset, cancel } = useDebate()
  const history = useHistory()

  const [form, setForm] = useState<DebateSettings | null>(null)
  const [showSettings, setShowSettings] = useState(false)
  const [toast, setToast] = useState<string | null>(null)

  useEffect(() => {
    if (settings && !form) setForm(initialSettings(settings))
  }, [settings, form])

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 3200)
    return () => clearTimeout(t)
  }, [toast])

  useEffect(() => {
    if (state.phase === 'completed' || state.phase === 'failed') void history.refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.phase])

  const busy = state.phase === 'running' || state.phase === 'synthesizing'
  const canDebate = (form?.agents.length ?? 0) >= 2

  const models = useMemo(() => {
    if (state.detail?.config?.models) return state.detail.config.models
    return form?.models ?? {}
  }, [state.detail, form])

  const progress = useMemo(() => {
    if (state.phase === 'completed' || state.phase === 'failed') return 1
    const total = Math.max(1, state.maxRounds + 1)
    return Math.min(0.97, (state.currentRound + 1) / (total + 0.2))
  }, [state.phase, state.currentRound, state.maxRounds])

  async function handleStart(question: string) {
    if (!form) return
    try {
      await start({
        question,
        agents: form.agents,
        min_rounds: form.minRounds,
        max_rounds: form.maxRounds,
        consensus_threshold: form.threshold,
        timeout: form.timeout,
        temperature: form.temperature,
        max_retries: form.maxRetries,
        models: form.models,
      })
      void history.refresh()
    } catch (e) {
      setToast(e instanceof Error ? e.message : String(e))
    }
  }

  function handleExport(fmt: 'markdown' | 'json') {
    if (!state.debateId) return
    downloadFile(
      api.exportUrl(state.debateId, fmt),
      `ai-council-${state.debateId}.${fmt === 'json' ? 'json' : 'md'}`,
    )
  }

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">⚖️</div>
          <div>
            <h1>AI COUNCIL</h1>
            <p>
              {settings
                ? `${settings.agents.filter((a) => a.configured).length} of ${
                    settings.agents.length
                  } providers connected — they answer independently, then debate.`
                : 'Independent answers first, then a debate.'}
            </p>
          </div>
        </div>
        <div className="topbar-actions">
          <button className="btn" onClick={() => setShowSettings((v) => !v)}>
            {showSettings ? 'Hide settings' : 'Settings'}
          </button>
          {state.debateId && (
            <button className="btn" onClick={reset} disabled={busy}>
              New question
            </button>
          )}
        </div>
      </header>

      {settingsError && (
        <div className="panel">
          <div className="error-box">
            Cannot reach the backend: {settingsError}
            <span className="k">
              Start it with: uvicorn backend.main:app --reload --port 8000
            </span>
          </div>
        </div>
      )}

      {!state.debateId && (
        <div className="hero">
          <h2>Ask several AI models to solve a problem</h2>
          <p>
            Every model answers on its own first. Then each one reads the others, accepts
            what holds up, rejects what does not, and says why. A deterministic consensus
            engine decides when they are actually done.
          </p>
        </div>
      )}

      {settings && form && (
        <>
          <QuestionForm
            maxLength={settings.limits.max_question_length}
            disabled={!canDebate}
            busy={busy}
            onSubmit={handleStart}
            onCancel={() => void cancel()}
          />

          {showSettings && (
            <SettingsPanel
              settings={settings}
              value={form}
              onChange={setForm}
              disabled={busy}
            />
          )}
        </>
      )}

      {state.debateId && (
        <section className="panel">
          <h3 className="panel-title">
            <span>
              {busy
                ? state.phase === 'synthesizing'
                  ? 'Compiling final synthesis…'
                  : `Round ${state.currentRound} of at most ${state.maxRounds}`
                : state.phase === 'failed'
                  ? 'Debate failed'
                  : 'Debate complete'}
            </span>
            <span className="mono faint small" style={{ letterSpacing: 0, textTransform: 'none' }}>
              {state.debateId}
            </span>
          </h3>

          <div className="progress-wrap" style={{ marginBottom: 16 }}>
            <span className="small faint">
              {state.phase === 'completed' ? 'Done' : state.phase === 'failed' ? 'Stopped' : 'Running'}
            </span>
            <div className="progress">
              <i style={{ width: `${Math.round(progress * 100)}%` }} />
            </div>
          </div>

          <AgentStatusBar
            agents={state.agents}
            models={models}
            agentState={state.agentState}
            roles={state.detail?.config?.roles}
          />

          {state.consensus && busy && <ConsensusStrip report={state.consensus} />}

          {state.error && (
            <div className="error-box" style={{ marginTop: 14 }}>
              {state.error}
            </div>
          )}
        </section>
      )}

      {state.detail?.synthesis && (
        <FinalReport
          detail={state.detail}
          consensus={state.consensus}
          onExport={handleExport}
        />
      )}

      {state.rounds.length > 0 && (
        <section className="panel">
          <h3 className="panel-title">
            <span>Rounds</span>
            <span className="faint small" style={{ letterSpacing: 0, textTransform: 'none' }}>
              Click a round to expand it
            </span>
          </h3>
          {state.rounds.map((r) => (
            <RoundView
              key={r.index}
              round={r}
              defaultOpen={r.index === state.rounds.length - 1}
            />
          ))}
        </section>
      )}

      {state.detail?.cost && <TokenPanel cost={state.detail.cost} />}

      <EventLog lines={state.log} />

      <HistoryPanel
        items={history.items}
        activeId={state.debateId}
        onOpen={(id) => void open(id).catch((e) => setToast(String(e)))}
        onDelete={async (id) => {
          await api.remove(id).catch(() => undefined)
          if (id === state.debateId) reset()
          void history.refresh()
        }}
        onClear={async () => {
          if (!window.confirm('Delete every stored debate? This cannot be undone.')) return
          await api.clearAll().catch(() => undefined)
          reset()
          void history.refresh()
          setToast('History cleared')
        }}
      />

      {toast && <div className="toast">{toast}</div>}
    </div>
  )
}
