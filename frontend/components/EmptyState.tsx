import { FileSearch, Sparkles } from "lucide-react"

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
    <div className="flex min-h-[55vh] flex-col items-center justify-center px-6 py-16 text-center">
      <div className="mb-5 flex size-16 items-center justify-center rounded-2xl border border-[#3a4946] bg-[#1a2523] text-[#e8b16b] shadow-[0_0_40px_rgba(232,177,107,0.12)]">
        <Sparkles size={28} strokeWidth={1.5} />
      </div>
      <p className="mb-2 font-mono text-[10px] uppercase tracking-[0.24em] text-[#7ccbc4]">Evidence desk / ready</p>
      <h2 className="max-w-lg font-display text-3xl font-semibold tracking-tight text-[#f1eadf]">Pergunte aos seus arquivos.</h2>
      <p className="mt-3 max-w-md text-sm leading-6 text-[#8e9998]">Respostas fundamentadas em trechos, páginas e mídias indexadas — sem completar lacunas com conhecimento externo.</p>
      {selectedFileName && <p className="mt-4 flex items-center gap-2 rounded-full border border-[#2b3637] bg-[#151b1c] px-3 py-1.5 text-xs text-[#c2cbc8]"><FileSearch size={13} /> Filtrando por {selectedFileName}</p>}
      <div className="mt-9 grid w-full max-w-2xl gap-2 md:grid-cols-3">
        {suggestions.map((suggestion) => <button key={suggestion} type="button" onClick={() => onSuggestion(suggestion)} className="rounded-xl border border-[#2b3637] bg-[#14191b] p-4 text-left text-xs leading-5 text-[#b4bfbc] transition hover:-translate-y-0.5 hover:border-[#7ccbc4]/60 hover:bg-[#192221] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#e8b16b]">{suggestion}</button>)}
      </div>
    </div>
  )
}
