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
  return (
    <div onDragOver={(event) => { event.preventDefault(); setIsDragging(true) }} onDragLeave={() => setIsDragging(false)} onDrop={async (event) => { event.preventDefault(); setIsDragging(false); await handleFiles(event.dataTransfer.files) }} className={`rounded-2xl border border-dashed p-4 transition ${isDragging ? "border-[#e8b16b] bg-[#2c241b]" : "border-[#344041] bg-[#141a1b]"}`}>
      <input ref={inputRef} type="file" multiple accept=".pdf,.png,.jpg,.jpeg,.gif,.webp,.txt,.md,.docx,.mp4,.mov,.mp3,.wav" className="hidden" onChange={async (event) => { await handleFiles(event.target.files); event.currentTarget.value = "" }} />
      <button type="button" disabled={isUploading} onClick={() => inputRef.current?.click()} className="flex w-full flex-col items-center gap-2 rounded-xl px-2 py-4 text-center focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#e8b16b] disabled:cursor-wait disabled:opacity-60">
        {isUploading ? <Loader2 className="animate-spin text-[#e8b16b]" size={22} /> : <CloudUpload className="text-[#7ccbc4]" size={22} />}
        <span className="text-xs font-medium text-[#e9eee9]">{isUploading ? "Indexando arquivos..." : "Solte arquivos ou escolha do dispositivo"}</span>
        <span className="text-[10px] leading-4 text-[#788482]">PDF, DOCX, texto, imagens, áudio e vídeo</span>
      </button>
      {(uploadSummary.success > 0 || uploadSummary.errors.length > 0 || uploadSummary.catalogError) && <div className="mt-2 space-y-1 border-t border-[#2b3637] pt-2 text-[10px]">{uploadSummary.success > 0 && <p className="flex items-center gap-1 text-[#9ed9a7]"><CheckCircle2 size={12} /> {uploadSummary.success} concluído(s)</p>}{uploadSummary.errors.map((error) => <p key={error} className="flex items-start gap-1 text-[#f08f7d]"><XCircle size={12} className="mt-0.5 shrink-0" /> {error}</p>)}{uploadSummary.catalogError && <p className="flex items-start gap-1 text-[#e8b16b]"><CircleAlert size={12} className="mt-0.5 shrink-0" /> {uploadSummary.catalogError}</p>}</div>}
    </div>
  )
}
