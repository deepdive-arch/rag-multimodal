import { FileSearch, Network } from "lucide-react"

interface EmptyStateProps {
  selectedFileName?: string
  onSuggestion: (suggestion: string) => void
}

const suggestions = [
  "Resuma os principais pontos dos arquivos.",
  "Quais prazos, datas ou valores são mencionados?",
  "Existem obrigações, riscos ou inconsistências importantes?",
]

export function EmptyState({ selectedFileName, onSuggestion }: EmptyStateProps) {
  return (
    <div className="empty-state">
      <div className="empty-mark" aria-hidden="true"><Network size={27} strokeWidth={1.6} /></div>
      <p className="chat-eyebrow mt-5">Biblioteca pronta</p>
      <h2 className="empty-title">Pergunte aos seus arquivos.</h2>
      <p className="empty-copy">Respostas ancoradas em trechos, páginas e mídias indexadas — sem completar lacunas com conhecimento externo.</p>
      {selectedFileName && <p className="mt-4 inline-flex items-center gap-2 rounded-full border border-[var(--border)] bg-[var(--surface-muted)] px-3 py-1.5 text-xs text-[var(--text-secondary)]"><FileSearch aria-hidden="true" size={13} /> Filtrando por {selectedFileName}</p>}
      <div className="suggestion-grid">
        {suggestions.map((suggestion) => <button key={suggestion} type="button" className="suggestion-button" onClick={() => onSuggestion(suggestion)}>{suggestion}</button>)}
      </div>
    </div>
  )
}
