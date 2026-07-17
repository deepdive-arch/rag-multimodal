import { ChevronDown, ChevronUp, FileAudio, FileImage, FileText, FileVideo } from "lucide-react"
import Image from "next/image"
import { useState } from "react"
import { mediaUrl } from "@/lib/api"
import type { Source } from "@/types"

interface SourcesPanelProps { sources: Source[] }

function SourceIcon({ fileType }: { fileType: Source["file_type"] }) {
  if (fileType === "image") return <FileImage size={14} />
  if (fileType === "video") return <FileVideo size={14} />
  if (fileType === "audio") return <FileAudio size={14} />
  return <FileText size={14} />
}

export function SourcesPanel({ sources }: SourcesPanelProps) {
  const [isOpen, setIsOpen] = useState(true)
  const [expanded, setExpanded] = useState<string | null>(null)
  sources = [...new Map(sources.map((source) => [source.chunk_id, source])).values()]
  return <div className="mt-4 overflow-hidden rounded-xl border border-[#2c3737] bg-[#111718]"><button type="button" onClick={() => setIsOpen((value) => !value)} className="flex w-full items-center justify-between px-3 py-2.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[#e8b16b]"><span className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.16em] text-[#7ccbc4]"><span className="flex size-5 items-center justify-center rounded bg-[#24312f] text-[#e8b16b]">{sources.length}</span> Fontes verificáveis</span>{isOpen ? <ChevronUp size={15} /> : <ChevronDown size={15} />}</button>{isOpen && <div className="space-y-2 border-t border-[#273232] p-2">{sources.map((source, index) => { const url = mediaUrl(source.media_url); const isExpanded = expanded === source.chunk_id; return <article key={source.chunk_id} className="border-l-2 border-[#e8b16b] bg-[#161d1e] p-3"><div className="flex items-start gap-2"><span className="font-mono text-[10px] text-[#e8b16b]">{String(index + 1).padStart(2, "0")}</span><div className="min-w-0 flex-1"><div className="flex items-center gap-1.5 text-xs font-medium text-[#e8eee8]"><SourceIcon fileType={source.file_type} /><span className="truncate">{source.file_name}</span></div>{source.page_number > 0 && <p className="mt-1 text-[10px] text-[#7b8985]">Página {source.page_number}</p>}<span className="mt-1 inline-flex rounded-full bg-[#293731] px-2 py-0.5 font-mono text-[9px] text-[#a9d8c7]">relevância {(source.score * 100).toFixed(0)}%</span></div></div>{source.content_modality === "image" && url && <Image src={url} alt={`Fonte ${source.file_name}`} loading="lazy" width={800} height={520} unoptimized className="mt-3 max-h-52 max-w-full rounded-lg object-contain" />}{source.content_modality === "video" && url && <video src={url} controls preload="metadata" className="mt-3 max-h-52 max-w-full rounded-lg" />}{source.content_modality === "audio" && url && <audio src={url} controls preload="metadata" className="mt-3 w-full" />}{source.text_preview && <div className="mt-3"><p className={`whitespace-pre-wrap text-xs leading-5 text-[#aebbb6] ${isExpanded ? "" : "line-clamp-3"}`}>{source.text_preview}</p>{source.text_preview.length > 220 && <button type="button" onClick={() => setExpanded(isExpanded ? null : source.chunk_id)} className="mt-1 text-[10px] font-medium text-[#e8b16b] focus-visible:outline-none focus-visible:underline">{isExpanded ? "Ver menos" : "Ver mais"}</button>}</div>}</article> })}</div>}</div>
}
