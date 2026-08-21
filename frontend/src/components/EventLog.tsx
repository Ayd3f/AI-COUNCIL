import { useEffect, useRef } from 'react'

import type { LogLine } from '../lib/useDebate'

export function EventLog({ lines }: { lines: LogLine[] }) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    ref.current?.scrollTo({ top: ref.current.scrollHeight })
  }, [lines.length])

  if (!lines.length) return null

  return (
    <section className="panel">
      <h3 className="panel-title">
        <span>Live event stream</span>
        <span className="faint small" style={{ letterSpacing: 0, textTransform: 'none' }}>
          Server-Sent Events
        </span>
      </h3>
      <div className="log" ref={ref}>
        {lines.map((l) => (
          <div
            key={`${l.seq}-${l.type}-${l.text}`}
            className={`log-line ${
              l.type === 'agent_failed' || l.type === 'debate_error'
                ? 'err'
                : l.type === 'debate_finished' || l.type === 'consensus_check'
                  ? 'ok'
                  : ''
            }`}
          >
            <span className="t">{l.at ? new Date(l.at).toLocaleTimeString() : '--:--:--'}</span>
            <span>{l.text}</span>
          </div>
        ))}
      </div>
    </section>
  )
}
