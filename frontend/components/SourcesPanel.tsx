import { ChevronDown, ChevronUp, FileAudio, FileImage, FileText, FileVideo } from "lucide-react"
import Image from "next/image"
import { useId, useState } from "react"
import { mediaUrl } from "@/lib/api"
import type { Source } from "@/types"

interface SourcesPanelProps { sources: Source[] }

function SourceIcon({ fileType }: { fileType: Source["file_type"] }) {
  if (fileType === "image") return <FileImage aria-hidden="true" size={14} />
  if (fileType === "video") return <FileVideo aria-hidden="true" size={14} />
  if (fileType === "audio") return <FileAudio aria-hidden="true" size={14} />
  return <FileText aria-hidden="true" size={14} />
}

export function SourcesPanel({ sources }: SourcesPanelProps) {
  const [isOpen, setIsOpen] = useState(false)
  const [expanded, setExpanded] = useState<string | null>(null)
  const sourcesId = useId()
  const uniqueSources = [...new Map(sources.map((source) => [source.chunk_id, source])).values()]
  return (
    <section className="sources-panel" aria-label="Fontes utilizadas">
      <button type="button" className="sources-trigger" aria-expanded={isOpen} aria-controls={sourcesId} onClick={() => setIsOpen((value) => !value)}>
        <span className="sources-trigger-copy">Fontes utilizadas ({uniqueSources.length})</span>
        {isOpen ? <ChevronUp aria-hidden="true" size={15} /> : <ChevronDown aria-hidden="true" size={15} />}
      </button>
      {!isOpen && <div className="source-chips" aria-hidden="true">{uniqueSources.slice(0, 3).map((source, index) => <span key={source.chunk_id} className="source-chip">[{index + 1}] {source.file_name}</span>)}</div>}
      <div id={sourcesId} className="sources-list" hidden={!isOpen}>
        {isOpen && uniqueSources.map((source, index) => {
          const url = mediaUrl(source.media_url)
          const isExpanded = expanded === source.chunk_id
          return <article key={source.chunk_id} className="source-card">
            <div className="source-card-header">
              <span className="source-index">[{index + 1}]</span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5"><SourceIcon fileType={source.file_type} /><span className="source-name" title={source.file_name}>{source.file_name}</span></div>
                {source.page_number > 0 && <p className="source-detail">Página {source.page_number}</p>}
              </div>
            </div>
            {source.content_modality === "image" && url && <Image src={url} alt={`Prévia de ${source.file_name}`} loading="lazy" sizes="(max-width: 700px) 100vw, 800px" width={800} height={520} unoptimized className="source-media max-h-64 object-contain" />}
            {source.content_modality === "video" && url && <video src={url} controls preload="none" className="source-media max-h-64 w-full" aria-label={`Vídeo de ${source.file_name}`} />}
            {source.content_modality === "audio" && url && <audio src={url} controls preload="none" className="source-media w-full" aria-label={`Áudio de ${source.file_name}`} />}
            {source.text_preview && <div><p className={`source-preview ${isExpanded ? "" : "line-clamp-3"}`}>{source.text_preview}</p>{source.text_preview.length > 220 && <button type="button" className="source-more" onClick={() => setExpanded(isExpanded ? null : source.chunk_id)}>{isExpanded ? "Ver menos" : "Ver mais"}</button>}</div>}
          </article>
        })}
      </div>
    </section>
  )
}
