import type { DebateSummary } from '../lib/types'

interface Props {
  items: DebateSummary[]
  activeId: string | null
  onOpen: (id: string) => void
  onDelete: (id: string) => void
  onClear: () => void
}

export function HistoryPanel({ items, activeId, onOpen, onDelete, onClear }: Props) {
  return (
    <section className="panel">
      <h3 className="panel-title">
        <span>History ({items.length})</span>
        {items.length > 0 && (
          <button className="btn btn-sm btn-danger" onClick={onClear}>
            Clear history
          </button>
        )}
      </h3>

      {items.length === 0 ? (
        <p className="muted small" style={{ margin: 0 }}>
          No debates yet. Ask a question above to start one.
        </p>
      ) : (
        items.map((d) => (
          <div
            key={d.id}
            className="history-item"
            style={
              d.id === activeId
                ? { borderColor: 'rgba(111,140,255,0.55)', background: 'var(--panel-2)' }
                : undefined
            }
            onClick={() => onOpen(d.id)}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') onOpen(d.id)
            }}
          >
            <span
              className="dot"
              style={{
                ['--dot' as string]:
                  d.status === 'COMPLETED'
                    ? d.consensus_reached
                      ? 'var(--ok)'
                      : 'var(--warn)'
                    : d.status === 'FAILED'
                      ? 'var(--err)'
                      : 'var(--info)',
              }}
            />
            <span className="q" title={d.question}>
              {d.question}
            </span>
            <span className="m">
              {d.rounds_used}/{d.max_rounds}r · {Math.round(d.consensus_score * 100)}%
            </span>
            <button
              className="btn btn-sm btn-ghost btn-danger"
              title="Delete this debate"
              onClick={(e) => {
                e.stopPropagation()
                onDelete(d.id)
              }}
            >
              ✕
            </button>
          </div>
        ))
      )}
    </section>
  )
}
