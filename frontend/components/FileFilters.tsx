import type { FileType, IngestedFile, QueryFilters } from "@/types"
import { ChevronDown } from "lucide-react"

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
  const counts = Object.fromEntries(typeOptions.map(({ value }) => [value, files.filter((file) => file.file_type === value).length])) as Record<FileType, number>
  const isAllActive = !filters.file_type && !filters.doc_id
  return (
    <section className="sidebar-section" aria-labelledby="filter-heading">
      <div className="section-heading">
        <p id="filter-heading" className="section-kicker">Filtros</p>
      </div>
      <div className="filter-list">
        <button type="button" className={`filter-chip ${isAllActive ? "active" : ""}`} aria-pressed={isAllActive} onClick={() => onChange({})}>Todos{files.length ? ` ${files.length}` : ""}</button>
        {typeOptions.map((option) => {
          const active = filters.file_type === option.value
          return <button key={option.value} type="button" className={`filter-chip ${active ? "active" : ""}`} aria-pressed={active} disabled={counts[option.value] === 0} onClick={() => onChange({ file_type: option.value })}>{option.label}{counts[option.value] ? ` ${counts[option.value]}` : ""}</button>
        })}
      </div>
      <label className="field-label mt-2" htmlFor="file-filter">Arquivo específico</label>
      <div className="select-control mt-2">
        <select id="file-filter" className="file-select" value={filters.doc_id ?? ""} onChange={(event) => onChange(event.target.value ? { doc_id: event.target.value } : {})}>
          <option value="">Arquivo específico</option>
          {files.filter((file) => file.status === "ready").map((file) => <option key={file.doc_id} value={file.doc_id}>{file.name}</option>)}
        </select>
        <ChevronDown className="select-chevron" aria-hidden="true" size={16} />
      </div>
    </section>
  )
}
