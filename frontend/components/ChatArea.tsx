import { Bot, CircleStop, Loader2, Send, Wifi, WifiOff } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { EmptyState, type EmptyStateMode } from "@/components/EmptyState"
import { MessageBubble } from "@/components/MessageBubble"
import { deriveHealthView } from "@/lib/health"
import { sourceLimitLabel } from "@/lib/source-limit"
import type { AnswerMode, HealthStatus, Message, QuerySubmissionResult } from "@/types"

const CANCELLED_FEEDBACK = "Você parou de esperar. A consulta pode continuar no servidor."

interface ChatAreaProps {
  messages: Message[]
  healthStatus: HealthStatus | null
  stats: { files: number; chunks: number } | null
  canQuery: boolean
  emptyStateMode: EmptyStateMode
  isQuerying: boolean
  activeFilterLabel?: string
  topK: number
  answerMode: AnswerMode
  catalogError: string
  actionError: string
  isRefreshingWorkspace: boolean
  onRetryWorkspace: () => void
  onOpenLibrary: () => void
  onSubmit: (question: string) => Promise<QuerySubmissionResult>
  onCancel: () => void
  onFeedback: (messageId: string, useful: boolean) => Promise<void>
}

function answerModeLabel(answerMode: AnswerMode) {
  if (answerMode === "quick") return "Resposta rápida"
  if (answerMode === "evidence") return "Somente evidências"
  return "Resposta detalhada"
}

export function ChatArea({ messages, healthStatus, stats, canQuery, emptyStateMode, isQuerying, activeFilterLabel, topK, answerMode, catalogError, actionError, isRefreshingWorkspace, onRetryWorkspace, onOpenLibrary, onSubmit, onCancel, onFeedback }: ChatAreaProps) {
  const [input, setInput] = useState("")
  const [error, setError] = useState("")
  const [status, setStatus] = useState("")
  const [isCancelling, setIsCancelling] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const endRef = useRef<HTMLDivElement>(null)
  const restoreFocusRef = useRef(false)
  useEffect(() => { if (!messages.length && !isQuerying) return; const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches; endRef.current?.scrollIntoView({ behavior: reduced ? "auto" : "smooth" }) }, [messages, isQuerying])
  useEffect(() => { if (isQuerying || !restoreFocusRef.current) return; restoreFocusRef.current = false; textareaRef.current?.focus() }, [isQuerying])
  const resize = (element: HTMLTextAreaElement) => { element.style.height = "auto"; element.style.height = `${Math.min(element.scrollHeight, 128)}px` }
  const submit = async () => {
    const question = input.trim()
    if (!question || isQuerying || !canQuery || isCancelling) return
    setError("")
    setStatus("")
    try {
      const result = await onSubmit(question)
      if (result === "completed") {
        setInput("")
        if (textareaRef.current) textareaRef.current.style.height = "auto"
        return
      }
      setStatus(CANCELLED_FEEDBACK)
      restoreFocusRef.current = true
    } catch (submissionError) {
      setError(submissionError instanceof Error ? submissionError.message : "Não foi possível consultar a base.")
    } finally {
      setIsCancelling(false)
    }
  }
  const cancel = () => { if (isCancelling) return; setIsCancelling(true); setStatus(CANCELLED_FEEDBACK); restoreFocusRef.current = true; onCancel() }
  const selectedQuestion = (message: Message) => { const index = messages.findIndex((item) => item.id === message.id); return messages.slice(0, index).toReversed().find((item) => item.role === "user") }
  const health = deriveHealthView(healthStatus, isRefreshingWorkspace)
  const filterLabel = activeFilterLabel ?? "Todos os arquivos"
  const composerContext = canQuery ? `Consultando: ${filterLabel} · ${sourceLimitLabel(topK)} · ${answerModeLabel(answerMode)}` : "Adicione um arquivo pronto para começar"
  const composerHelp = canQuery ? "Enter envia · Shift + Enter quebra linha." : "Adicione um arquivo e aguarde o processamento para liberar perguntas."
  return (
    <main className="chat-shell">
      <header className="chat-header">
        <div className="min-w-0">
          <p className="chat-eyebrow">Espaço de consulta</p>
          <h1 className="chat-title">Conversa</h1>
          <p className="chat-subtitle">Pergunte sobre os arquivos indexados</p>
        </div>
        <div className="chat-status-bar" aria-live="polite">
          {stats && <span className="header-chip">{stats.files} arquivos · {stats.chunks} trechos</span>}
          {activeFilterLabel && <span className="header-chip">Filtro: {activeFilterLabel}</span>}
          <span className={`health-pill ${health.tone === "warning" || health.tone === "danger" ? "is-warning" : ""} ${health.tone === "pending" ? "is-pending" : ""}`}><span className="health-dot" aria-hidden="true" />{health.tone === "pending" ? <Loader2 aria-hidden="true" className="animate-spin" size={13} /> : health.tone === "warning" || health.tone === "danger" ? <WifiOff aria-hidden="true" size={13} /> : <Wifi aria-hidden="true" size={13} />}{health.label}</span>
          {isQuerying && <button type="button" className="action-button" disabled={isCancelling} aria-label="Parar de esperar pela resposta" onClick={cancel}><CircleStop aria-hidden="true" size={14} /> Parar de esperar</button>}
        </div>
      </header>
      <div id="conversation-content" className="message-scroll" role="region" tabIndex={-1} aria-label="Mensagens da conversa" aria-busy={isQuerying || isRefreshingWorkspace}>
        <div className="message-list">
          {catalogError && <div className="error-alert catalog-alert" role="alert"><span>{catalogError}</span><button type="button" className="action-button error-retry" disabled={isRefreshingWorkspace} aria-busy={isRefreshingWorkspace} onClick={onRetryWorkspace}>{isRefreshingWorkspace ? "Atualizando…" : "Tentar novamente"}</button></div>}
          {actionError && <p className="error-alert" role="alert">{actionError}</p>}
          {status && <p className="query-status" role="status" aria-live="polite">{status}</p>}
          {messages.length === 0 ? <EmptyState mode={emptyStateMode} selectedFileName={activeFilterLabel} onSuggestion={(suggestion) => { setInput(suggestion); textareaRef.current?.focus() }} onOpenLibrary={onOpenLibrary} /> : messages.map((message) => <MessageBubble key={message.id} message={message} question={message.role === "assistant" ? selectedQuestion(message) : undefined} onFeedback={(useful) => onFeedback(message.id, useful)} />)}
          {isQuerying && !isCancelling && <div className="query-loading" role="status" aria-live="polite"><div className="message-avatar" aria-hidden="true"><Bot size={16} /></div><Loader2 aria-hidden="true" className="animate-spin" size={17} /> Consultando os arquivos…</div>}
          {error && <p className="error-alert" role="alert">{error}</p>}
          <div ref={endRef} aria-hidden="true" />
        </div>
      </div>
      <div className="composer-shell">
        <div className="composer-inner">
          <p className={`composer-context ${canQuery ? "" : "is-blocked"}`}>{composerContext}</p>
          <label className="composer-label" htmlFor="question">Pergunta</label>
          <div className="composer">
            <textarea id="question" name="question" ref={textareaRef} value={input} disabled={isQuerying || !canQuery} onChange={(event) => { setInput(event.target.value); resize(event.currentTarget) }} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void submit() } }} placeholder={canQuery ? "O que você quer encontrar…" : "Adicione um arquivo para liberar a pergunta"} aria-describedby="composer-help" autoComplete="off" rows={1} className="composer-textarea" />
            <button type="button" className="composer-send" disabled={!input.trim() || isQuerying || !canQuery} onClick={() => void submit()} aria-label="Enviar pergunta"><Send aria-hidden="true" size={17} /></button>
          </div>
          <p id="composer-help" className="composer-help">{composerHelp}</p>
        </div>
      </div>
    </main>
  )
}
