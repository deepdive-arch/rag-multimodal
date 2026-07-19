import { CheckCircle2, CircleAlert, CircleX, CloudUpload, Loader2, XCircle } from "lucide-react"
import { useRef, useState } from "react"
import type { PublicDemoConfig, UploadProgress } from "@/types"

interface UploadDropzoneProps {
  isUploading: boolean
  uploadSummary: { success: number; errors: string[]; catalogError: string }
  uploadStates: UploadProgress[]
  publicDemo: PublicDemoConfig | null
  onFiles: (files: File[]) => Promise<boolean>
  onCancel: () => void
}

const phaseLabels = { preparando: "Preparando", enviando: "Enviando", validando: "Validando", processando: "Processando", indexando: "Indexando", pronto: "Pronto", falhou: "Falhou" }

export function UploadDropzone({ isUploading, uploadSummary, uploadStates, publicDemo, onFiles, onCancel }: UploadDropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [isDragging, setIsDragging] = useState(false)
  const handleFiles = async (files: FileList | null) => { if (files?.length) await onFiles(Array.from(files)) }
  const hasFeedback = uploadSummary.success > 0 || uploadSummary.errors.length > 0 || Boolean(uploadSummary.catalogError)
  const showPublicNotice = publicDemo?.enabled !== false
  return (
    <section className={`upload-dropzone ${isDragging ? "is-dragging" : ""}`} aria-labelledby="upload-heading">
      {showPublicNotice && <div className="public-demo-notice" role="note">
        <strong>Ambiente público de demonstração.</strong> Não envie documentos confidenciais, pessoais ou sigilosos. Os arquivos podem ser removidos automaticamente após o período de retenção.
        <p>{publicDemo ? `Formatos permitidos: ${publicDemo.formats.join(", ")}.` : "Formatos permitidos: limites carregados pelo servidor."}</p>
        <p>{publicDemo ? `Tamanho máximo: ${publicDemo.max_upload_size_mb} MB · até ${publicDemo.max_daily_uploads} upload(s) e ${publicDemo.max_daily_queries} consulta(s) por dia · retenção de ${publicDemo.retention_days} dias.` : "Carregando tamanho máximo, retenção e limite diário…"}</p>
      </div>}
      <input ref={inputRef} id="file-upload" name="files" type="file" multiple accept=".pdf,.png,.jpg,.jpeg,.gif,.webp,.txt,.md,.docx,.mp4,.mov,.mp3,.wav" aria-label="Selecionar arquivos" className="sr-only" onChange={async (event) => { const input = event.currentTarget; await handleFiles(input.files); input.value = "" }} />
      <button type="button" className="upload-button" disabled={isUploading} aria-busy={isUploading} onClick={() => inputRef.current?.click()} onDragOver={(event) => { event.preventDefault(); setIsDragging(true) }} onDragLeave={() => setIsDragging(false)} onDrop={async (event) => { event.preventDefault(); setIsDragging(false); await handleFiles(event.dataTransfer.files) }}>
        {isUploading ? <Loader2 className="animate-spin" aria-hidden="true" size={21} /> : <CloudUpload aria-hidden="true" size={21} />}
        <span id="upload-heading" className="upload-title">{isUploading ? "Enviando arquivos…" : "Adicionar arquivos"}</span>
        <span className="upload-help">PDF, DOCX, TXT, imagens, áudio e vídeo</span>
      </button>
      {isUploading && <button type="button" className="upload-cancel" onClick={onCancel}><CircleX aria-hidden="true" size={14} /> Cancelar upload</button>}
      {uploadStates.length > 0 && <div className="upload-progress-list" aria-live="polite">
        {uploadStates.map((state) => <div className="upload-progress-item" key={state.id}>
          <div className="upload-progress-heading"><span className="upload-progress-name">{state.fileName}</span><span>{phaseLabels[state.phase]}{state.duplicate ? " · duplicado" : ""}</span></div>
          <progress value={state.progress} max={100} aria-label={`${state.fileName}: ${phaseLabels[state.phase]}`} />
          {state.error && <p className="feedback-error"><XCircle aria-hidden="true" size={13} /> {state.error}</p>}
        </div>)}
      </div>}
      {hasFeedback && <div className="upload-feedback" aria-live="polite">
        {uploadSummary.success > 0 && <p className="feedback-success"><CheckCircle2 aria-hidden="true" size={13} /> {uploadSummary.success} arquivo(s) pronto(s)</p>}
        {uploadSummary.errors.map((error, index) => <p key={`${error}-${index}`} className="feedback-error" role="alert"><XCircle aria-hidden="true" size={13} /> {error}</p>)}
        {uploadSummary.catalogError && <p className="feedback-warning"><CircleAlert aria-hidden="true" size={13} /> {uploadSummary.catalogError}</p>}
      </div>}
    </section>
  )
}
