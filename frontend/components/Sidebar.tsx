import { ChevronDown, ChevronUp, FileArchive, FileAudio, FileImage, FileText, FileVideo, Network, Trash2 } from "lucide-react"
import type { Ref } from "react"
import { useState } from "react"
import { FileFilters } from "@/components/FileFilters"
import { UploadDropzone } from "@/components/UploadDropzone"
import type { AnswerMode, FileType, IngestedFile, QueryFilters } from "@/types"

interface SidebarProps {
  files: IngestedFile[]
  filters: QueryFilters
  topK: number
  answerMode: AnswerMode
  isUploading: boolean
  uploadSummary: { success: number; errors: string[]; catalogError: string }
  sidebarRef?: Ref<HTMLElement>
  onFiles: (files: File[]) => Promise<void>
  onFilters: (filters: QueryFilters) => void
  onTopK: (value: number) => void
  onAnswerMode: (mode: AnswerMode) => void
  onDelete: (file: IngestedFile) => void
  onClearChat: () => void
  onClearIndex: () => void
}

function FileIcon({ type }: { type: FileType }) {
  if (type === "image") return <FileImage size={16} />
  if (type === "video") return <FileVideo size={16} />
  if (type === "audio") return <FileAudio size={16} />
  return type === "text" || type === "docx" ? <FileText size={16} /> : <FileArchive size={16} />
}

function formatSize(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function statusLabel(status: IngestedFile["status"]) {
  if (status === "processing") return "Processando…"
  if (status === "failed") return "Falha"
  if (status === "deleting") return "Removendo…"
  return "Pronto"
}

export function Sidebar({ sidebarRef, ...props }: SidebarProps) {
  const [settingsOpen, setSettingsOpen] = useState(false)
  return (
    <aside id="rag-sidebar" ref={sidebarRef} tabIndex={-1} aria-label="Biblioteca de arquivos" className="sidebar-shell">
      <header className="brand-block">
        <div className="brand-mark" aria-hidden="true"><Network size={20} /></div>
        <div className="brand-copy">
          <p className="brand-eyebrow">RAG multimodal</p>
          <p className="brand-title">Biblioteca semântica</p>
          <p className="brand-subtitle">Perguntas ancoradas nos seus arquivos</p>
        </div>
      </header>
      <div className="sidebar-scroll-area">
        <UploadDropzone isUploading={props.isUploading} uploadSummary={props.uploadSummary} onFiles={props.onFiles} />
        <section className="sidebar-section files-section" aria-labelledby="files-heading">
          <div className="section-heading">
            <p id="files-heading" className="section-kicker">Arquivos</p>
            <span className="count-badge" aria-label={`${props.files.length} arquivos`}>{props.files.length}</span>
          </div>
          <div className="file-list">
            {props.files.length === 0 ? <p className="file-list-empty">A biblioteca está vazia. Adicione um arquivo para começar.</p> : props.files.map((file) => {
              const selected = props.filters.doc_id === file.doc_id
              return <div key={file.doc_id} className={`file-row ${selected ? "is-selected" : ""}`}>
                <button type="button" className="file-row-main" aria-pressed={selected} onClick={() => props.onFilters(selected ? {} : { doc_id: file.doc_id })}>
                  <span className="file-icon" aria-hidden="true"><FileIcon type={file.file_type} /></span>
                  <span className="file-copy">
                    <span className="file-name" title={file.name}>{file.name}</span>
                    <span className="file-meta">{file.chunks} trechos · {formatSize(file.size_bytes)}</span>
                    <span className={`file-state ${file.status === "ready" ? "ready" : file.status === "failed" ? "failed" : "processing"}`} role={file.status === "processing" ? "status" : undefined}>{statusLabel(file.status)}</span>
                  </span>
                </button>
                <button type="button" className="file-delete" aria-label={`Excluir ${file.name}`} onClick={() => props.onDelete(file)}><Trash2 aria-hidden="true" size={15} /></button>
              </div>
            })}
          </div>
        </section>
        <FileFilters files={props.files} filters={props.filters} onChange={props.onFilters} />
        <section className="sidebar-section" aria-labelledby="settings-heading">
          <button type="button" className="settings-trigger" aria-expanded={settingsOpen} aria-controls="query-settings" onClick={() => setSettingsOpen((current) => !current)}>
            <span id="settings-heading">Ajustes da consulta</span>
            {settingsOpen ? <ChevronUp aria-hidden="true" size={16} /> : <ChevronDown aria-hidden="true" size={16} />}
          </button>
          {settingsOpen && <div id="query-settings" className="settings-panel">
            <div>
              <label className="field-label" htmlFor="top-k"><span>Quantidade de fontes</span><span className="range-output">{props.topK}</span></label>
              <p className="field-help">Define quantos trechos relevantes serão considerados.</p>
              <input id="top-k" className="mt-2 w-full accent-[var(--accent)]" type="range" min="1" max="20" value={props.topK} onChange={(event) => props.onTopK(Number(event.target.value))} />
            </div>
            <div>
              <label className="field-label" htmlFor="answer-mode">Modo de resposta</label>
              <select id="answer-mode" className="settings-select mt-2" value={props.answerMode} onChange={(event) => props.onAnswerMode(event.target.value as AnswerMode)}>
                <option value="quick">Rápida</option>
                <option value="detailed">Detalhada</option>
                <option value="evidence">Somente evidências</option>
              </select>
            </div>
          </div>}
        </section>
      </div>
      <footer className="sidebar-footer">
        <p className="section-kicker">Mais ações</p>
        <button type="button" className="secondary-button" onClick={props.onClearChat}>Limpar conversa</button>
        <button type="button" className="danger-button" onClick={props.onClearIndex}>Limpar base</button>
      </footer>
    </aside>
  )
}
