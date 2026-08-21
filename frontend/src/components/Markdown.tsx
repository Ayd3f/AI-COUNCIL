import ReactMarkdown from 'react-markdown'
import rehypeHighlight from 'rehype-highlight'
import remarkGfm from 'remark-gfm'

export function Markdown({ children }: { children?: string | null }) {
  if (!children) return null
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
      >
        {children}
      </ReactMarkdown>
    </div>
  )
}

export function CopyButton({ text, label = 'Copy' }: { text: string; label?: string }) {
  return (
    <button
      className="btn btn-sm btn-ghost"
      title="Copy to clipboard"
      onClick={async (e) => {
        const btn = e.currentTarget
        try {
          await navigator.clipboard.writeText(text)
          const old = btn.textContent
          btn.textContent = 'Copied'
          setTimeout(() => {
            btn.textContent = old
          }, 1200)
        } catch {
          btn.textContent = 'Copy failed'
        }
      }}
    >
      {label}
    </button>
  )
}
