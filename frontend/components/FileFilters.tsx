import type { FileType, IngestedFile, QueryFilters } from "@/types"

interface FileFiltersProps {
  files: IngestedFile[]
  filters: QueryFilters
  onChange: (filters: QueryFilters) => void
}

const typeOptions: Array<{ value: FileType; label: string }> = [
  { value: "pdf", label: "PDFs" },
  { value: "image", label: "Imagens" },
  { value: "video", label: "Vídeos" },
  { value: "audio", label: "Áudios" },
  { value: "text", label: "Textos" },
  { value: "docx", label: "DOCX" },
]

export function FileFilters({ files, filters, onChange }: FileFiltersProps) {
  return <div className="space-y-3"><p className="font-mono text-[10px] uppercase tracking-[0.2em] text-[#6f7d7a]">Filtros</p><div className="grid grid-cols-2 gap-1.5"><button type="button" onClick={() => onChange({})} className={`rounded-lg px-2 py-2 text-left text-xs ${!filters.file_type && !filters.doc_id ? "bg-[#2b3834] text-[#f4ebde]" : "text-[#87938f] hover:bg-[#1d2524]"}`}>Todos</button>{typeOptions.map((option) => <button key={option.value} type="button" onClick={() => onChange({ file_type: option.value })} className={`rounded-lg px-2 py-2 text-left text-xs ${filters.file_type === option.value ? "bg-[#2b3834] text-[#f4ebde]" : "text-[#87938f] hover:bg-[#1d2524]"}`}>{option.label}</button>)}</div><select aria-label="Filtrar por arquivo" value={filters.doc_id ?? ""} onChange={(event) => onChange(event.target.value ? { doc_id: event.target.value } : {})} className="w-full rounded-lg border border-[#2b3637] bg-[#141a1b] px-3 py-2 text-xs text-[#bdc8c4] outline-none focus:border-[#7ccbc4]"><option value="">Arquivo específico</option>{files.filter((file) => file.status === "ready").map((file) => <option key={file.doc_id} value={file.doc_id}>{file.name}</option>)}</select></div>
}
