const SUGGESTIONS = [
  'What does osc~ do?',
  'How does message passing work in Pd?',
  'How do I use tabread4~ for wavetable synthesis?',
  'What are the inlets of pack?',
]

export function EmptyState({ onSuggest }: { onSuggest: (s: string) => void }) {
  return (
    <div className="space-y-6 text-center">
      <div className="space-y-2">
        <p className="text-sm font-medium text-foreground">Ask a question to get started</p>
        <p className="text-xs text-muted-foreground">
          Searches the{' '}
          <a href="https://msp.ucsd.edu/Pd_documentation/" target="_blank" rel="noreferrer"
             className="underline underline-offset-2 hover:text-foreground transition-colors">
            Pure Data Manual
          </a>
          {', '}
          <a href="https://pd.iem.sh/objects/" target="_blank" rel="noreferrer"
             className="underline underline-offset-2 hover:text-foreground transition-colors">
            IEM Object Reference
          </a>
          {', and '}
          <a href="http://msp.ucsd.edu/techniques.htm" target="_blank" rel="noreferrer"
             className="underline underline-offset-2 hover:text-foreground transition-colors">
            The Theory and Techniques of Electronic Music
          </a>
        </p>
        <p className="text-xs text-muted-foreground/70 max-w-sm mx-auto">
          Answers may be incomplete or incorrect. Always verify against the original documentation.
        </p>
      </div>
      <div className="flex flex-wrap justify-center gap-2">
        {SUGGESTIONS.map(s => (
          <button
            key={s}
            onClick={() => onSuggest(s)}
            className="text-xs px-3 py-1.5 rounded-full border border-border bg-muted/50
                       text-muted-foreground hover:bg-muted hover:text-foreground
                       transition-colors cursor-pointer"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  )
}
