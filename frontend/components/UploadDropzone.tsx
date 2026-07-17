import { CheckCircle2, CircleAlert, CloudUpload, Loader2, XCircle } from "lucide-react"
import { useRef, useState } from "react"

interface UploadDropzoneProps {
  isUploading: boolean
  uploadSummary: { success: number; errors: string[]; catalogError: string }
  onFiles: (files: File[]) => Promise<void>
}

export function UploadDropzone({ isUploading, uploadSummary, onFiles }: UploadDropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [isDragging, setIsDragging] = useState(false)
  const handleFiles = async (files: FileList | null) => { if (files?.length) await onFiles(Array.from(files)) }
  const hasFeedback = uploadSummary.success > 0 || uploadSummary.errors.length > 0 || Boolean(uploadSummary.catalogError)
  return (
    <section className={`upload-dropzone ${isDragging ? "is-dragging" : ""}`} aria-labelledby="upload-heading">
      <input ref={inputRef} id="file-upload" name="files" type="file" multiple accept=".pdf,.png,.jpg,.jpeg,.gif,.webp,.txt,.md,.docx,.mp4,.mov,.mp3,.wav" aria-label="Selecionar arquivos" className="sr-only" onChange={async (event) => { await handleFiles(event.target.files); event.currentTarget.value = "" }} />
      <button type="button" className="upload-button" disabled={isUploading} onClick={() => inputRef.current?.click()} onDragOver={(event) => { event.preventDefault(); setIsDragging(true) }} onDragLeave={() => setIsDragging(false)} onDrop={async (event) => { event.preventDefault(); setIsDragging(false); await handleFiles(event.dataTransfer.files) }}>
        {isUploading ? <Loader2 className="animate-spin" aria-hidden="true" size={21} /> : <CloudUpload aria-hidden="true" size={21} />}
        <span id="upload-heading" className="upload-title">{isUploading ? "Processando arquivos…" : "Adicionar arquivos"}</span>
        <span className="upload-help">PDF, DOCX, TXT, imagens, áudio e vídeo</span>
      </button>
      {isUploading && <p className="sr-only" role="status">Processando arquivos enviados.</p>}
      {hasFeedback && <div className="upload-feedback" aria-live="polite">
        {uploadSummary.success > 0 && <p className="feedback-success"><CheckCircle2 aria-hidden="true" size={13} /> {uploadSummary.success} arquivo(s) pronto(s)</p>}
        {uploadSummary.errors.map((error) => <p key={error} className="feedback-error" role="alert"><XCircle aria-hidden="true" size={13} /> {error}</p>)}
        {uploadSummary.catalogError && <p className="feedback-warning"><CircleAlert aria-hidden="true" size={13} /> {uploadSummary.catalogError}</p>}
      </div>}
    </section>
  )
}
