import { Clock3, FileSearch, Network, TriangleAlert, UploadCloud } from "lucide-react"

export type EmptyStateMode = "loading" | "error" | "no-files" | "processing" | "ready"

interface EmptyStateProps {
  mode: EmptyStateMode
  selectedFileName?: string
  onSuggestion: (suggestion: string) => void
  onOpenLibrary: () => void
}

const suggestions = [
  "Resuma os principais pontos dos arquivos.",
  "Quais prazos, datas ou valores são mencionados?",
  "Existem obrigações, riscos ou inconsistências importantes?",
]

export function EmptyState({ mode, selectedFileName, onSuggestion, onOpenLibrary }: EmptyStateProps) {
  const StateIcon = mode === "no-files" ? UploadCloud : mode === "processing" ? Clock3 : mode === "error" ? TriangleAlert : Network
  const copy = {
    loading: { eyebrow: "Carregando biblioteca", title: "Preparando seus arquivos.", description: "Aguarde enquanto verificamos os arquivos disponíveis para consulta." },
    error: { eyebrow: "Biblioteca indisponível", title: "Não foi possível carregar os arquivos.", description: "Tente novamente ou verifique se a API está em execução." },
    "no-files": { eyebrow: "Comece pela biblioteca", title: "Adicione arquivos para começar.", description: "As respostas usam apenas trechos, páginas e mídias indexadas nos seus arquivos." },
    processing: { eyebrow: "Processando arquivos", title: "Quase lá.", description: "Aguarde o processamento terminar para liberar as perguntas." },
    ready: { eyebrow: "Biblioteca pronta", title: "Pergunte aos seus arquivos.", description: "Respostas ancoradas em trechos, páginas e mídias indexadas — sem completar lacunas com conhecimento externo." },
  }[mode]
  return (
    <div className="empty-state">
      <div className={`empty-mark is-${mode}`} aria-hidden="true"><StateIcon size={27} strokeWidth={1.6} /></div>
      <p className="chat-eyebrow mt-5">{copy.eyebrow}</p>
      <h2 className="empty-title">{copy.title}</h2>
      <p className="empty-copy">{copy.description}</p>
      {mode === "no-files" && <button type="button" className="empty-upload-button" onClick={onOpenLibrary}><UploadCloud aria-hidden="true" size={16} /> Abrir biblioteca e adicionar arquivo</button>}
      {mode === "ready" && selectedFileName && <p className="filter-context"><FileSearch aria-hidden="true" size={13} /> Filtrando por {selectedFileName}</p>}
      {mode === "ready" && <div className="suggestion-grid">
        {suggestions.map((suggestion) => <button key={suggestion} type="button" className="suggestion-button" onClick={() => onSuggestion(suggestion)}>{suggestion}</button>)}
      </div>}
    </div>
  )
}
