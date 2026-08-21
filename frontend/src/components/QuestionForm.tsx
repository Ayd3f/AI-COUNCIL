import { useState } from 'react'

interface Props {
  maxLength: number
  disabled: boolean
  busy: boolean
  onSubmit: (question: string) => void
  onCancel: () => void
}

const EXAMPLES = [
  'Is it better to use a monolith or microservices for a 6-person startup in its first year?',
  'Does a daily standup improve delivery speed, or is it mostly overhead?',
  'What is the strongest argument against using LLMs for code review?',
]

export function QuestionForm({ maxLength, disabled, busy, onSubmit, onCancel }: Props) {
  const [question, setQuestion] = useState('')
  const over = question.length > maxLength
  const canSubmit = question.trim().length > 0 && !over && !disabled && !busy

  return (
    <section className="panel">
      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (canSubmit) onSubmit(question.trim())
        }}
      >
        <label htmlFor="question">Your question</label>
        <textarea
          id="question"
          value={question}
          placeholder="Ask one question. Five AI models will answer independently, then debate it."
          disabled={busy}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && canSubmit) {
              e.preventDefault()
              onSubmit(question.trim())
            }
          }}
        />

        <div className="form-footer">
          <div className="row">
            {EXAMPLES.map((ex, i) => (
              <button
                key={i}
                type="button"
                className="btn btn-sm btn-ghost"
                disabled={busy}
                onClick={() => setQuestion(ex)}
                title={ex}
              >
                Example {i + 1}
              </button>
            ))}
          </div>

          <div className="row">
            <span className={`char-count ${over ? 'over' : ''}`}>
              {question.length.toLocaleString()} / {maxLength.toLocaleString()}
            </span>
            {busy ? (
              <button type="button" className="btn btn-danger" onClick={onCancel}>
                Stop debate
              </button>
            ) : (
              <button type="submit" className="btn btn-primary" disabled={!canSubmit}>
                Start debate
              </button>
            )}
          </div>
        </div>

        {disabled && !busy && (
          <p className="notice" style={{ marginTop: 14, marginBottom: 0 }}>
            At least two providers must be configured. Copy <code>.env.example</code> to{' '}
            <code>.env</code>, add API keys, and restart the backend.
          </p>
        )}
      </form>
    </section>
  )
}
