import { Check, Clipboard, Download, ThumbsDown, ThumbsUp } from "lucide-react"
import { useState } from "react"
import { exportMarkdown } from "@/lib/export-markdown"
import type { Message } from "@/types"

interface ResponseActionsProps { question: Message; answer: Message; onFeedback: (useful: boolean) => Promise<void> }

export function ResponseActions({ question, answer, onFeedback }: ResponseActionsProps) {
  const [copied, setCopied] = useState(false)
  const [isSending, setIsSending] = useState(false)
  const copy = async () => { await navigator.clipboard.writeText(answer.content); setCopied(true); window.setTimeout(() => setCopied(false), 1600) }
  const feedback = async (useful: boolean) => { if (isSending || answer.feedback) return; setIsSending(true); try { await onFeedback(useful) } finally { setIsSending(false) } }
  return <div className="mt-3 flex flex-wrap items-center gap-1"><button type="button" onClick={copy} aria-label="Copiar resposta" className="action-button">{copied ? <Check aria-hidden="true" size={14} /> : <Clipboard aria-hidden="true" size={14} />}<span>{copied ? "Copiado" : "Copiar"}</span></button><button type="button" onClick={() => exportMarkdown(question, answer)} className="action-button"><Download aria-hidden="true" size={14} /><span>Baixar Markdown</span></button><span className="mx-1 h-4 w-px bg-[var(--border)]" aria-hidden="true" /><button type="button" disabled={isSending || Boolean(answer.feedback)} onClick={() => void feedback(true)} aria-label="Marcar resposta como útil" className={`action-button ${answer.feedback === "useful" ? "text-[var(--success)]" : ""}`}><ThumbsUp aria-hidden="true" size={14} /></button><button type="button" disabled={isSending || Boolean(answer.feedback)} onClick={() => void feedback(false)} aria-label="Marcar resposta como não útil" className={`action-button ${answer.feedback === "not_useful" ? "text-[var(--danger)]" : ""}`}><ThumbsDown aria-hidden="true" size={14} /></button></div>
}
