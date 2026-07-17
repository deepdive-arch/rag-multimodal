import { Bot, CircleStop, Loader2, Send, Wifi, WifiOff } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { EmptyState } from "@/components/EmptyState"
import { MessageBubble } from "@/components/MessageBubble"
import type { AnswerMode, HealthStatus, Message } from "@/types"

interface ChatAreaProps {
  messages: Message[]
  healthStatus: HealthStatus | null
  stats: { files: number; chunks: number } | null
  isQuerying: boolean
  activeFilterLabel?: string
  topK: number
  answerMode: AnswerMode
  onSubmit: (question: string) => Promise<void>
  onCancel: () => void
  onFeedback: (messageId: string, useful: boolean) => Promise<void>
}

function answerModeLabel(answerMode: AnswerMode) {
  if (answerMode === "quick") return "Resposta rápida"
  if (answerMode === "evidence") return "Somente evidências"
  return "Resposta detalhada"
}

function healthLabel(healthStatus: HealthStatus | null) {
  if (!healthStatus) return { label: "Indisponível", warning: true }
  if (healthStatus.status === "ok") return { label: "Online", warning: false }
  if (!healthStatus.services.google_configured || !healthStatus.services.pinecone_configured) return { label: "Configuração incompleta", warning: true }
  return { label: "Indisponível", warning: true }
}

export function ChatArea({ messages, healthStatus, stats, isQuerying, activeFilterLabel, topK, answerMode, onSubmit, onCancel, onFeedback }: ChatAreaProps) {
  const [input, setInput] = useState("")
  const [error, setError] = useState("")
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const endRef = useRef<HTMLDivElement>(null)
  useEffect(() => { const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches; endRef.current?.scrollIntoView({ behavior: reduced ? "auto" : "smooth" }) }, [messages, isQuerying])
  const resize = (element: HTMLTextAreaElement) => { element.style.height = "auto"; element.style.height = `${Math.min(element.scrollHeight, 128)}px` }
  const submit = async () => { const question = input.trim(); if (!question || isQuerying) return; setError(""); try { await onSubmit(question); setInput(""); if (textareaRef.current) textareaRef.current.style.height = "auto" } catch (submissionError) { setError(submissionError instanceof Error ? submissionError.message : "Não foi possível consultar a base.") } }
  const selectedQuestion = (message: Message) => { const index = messages.findIndex((item) => item.id === message.id); return messages.slice(0, index).toReversed().find((item) => item.role === "user") }
  const health = healthLabel(healthStatus)
  const filterLabel = activeFilterLabel ?? "Todos os arquivos"
  return (
    <main className="chat-shell">
      <a className="skip-link" href="#conversation-content">Pular para a conversa</a>
      <header className="chat-header">
        <div className="min-w-0">
          <p className="chat-eyebrow">Espaço de consulta</p>
          <h1 className="chat-title">Conversa</h1>
          <p className="chat-subtitle">Pergunte sobre os arquivos indexados</p>
        </div>
        <div className="chat-status-bar" aria-live="polite">
          {stats && <span className="header-chip">{stats.files} arquivos · {stats.chunks} trechos</span>}
          {activeFilterLabel && <span className="header-chip">Filtro: {activeFilterLabel}</span>}
          <span className={`health-pill ${health.warning ? "is-warning" : ""}`}><span className="health-dot" aria-hidden="true" />{health.warning ? <WifiOff aria-hidden="true" size={13} /> : <Wifi aria-hidden="true" size={13} />}{health.label}</span>
          {isQuerying && <button type="button" className="action-button" onClick={onCancel}><CircleStop aria-hidden="true" size={14} /> Parar</button>}
        </div>
      </header>
      <div id="conversation-content" className="message-scroll" tabIndex={-1} aria-label="Mensagens da conversa">
        <div className="message-list">
          {messages.length === 0 ? <EmptyState selectedFileName={activeFilterLabel} onSuggestion={(suggestion) => { setInput(suggestion); textareaRef.current?.focus() }} /> : messages.map((message) => <MessageBubble key={message.id} message={message} question={message.role === "assistant" ? selectedQuestion(message) : undefined} onFeedback={(useful) => onFeedback(message.id, useful)} />)}
          {isQuerying && <div className="query-loading" role="status" aria-live="polite"><div className="message-avatar" aria-hidden="true"><Bot size={16} /></div><Loader2 aria-hidden="true" className="animate-spin" size={17} /> Consultando os arquivos…</div>}
          {error && <p className="error-alert" role="alert">{error}</p>}
          <div ref={endRef} aria-hidden="true" />
        </div>
      </div>
      <div className="composer-shell">
        <div className="composer-inner">
          <p className="composer-context">Consultando: {filterLabel} · {topK} fontes · {answerModeLabel(answerMode)}</p>
          <label className="composer-label" htmlFor="question">Pergunta</label>
          <div className="composer">
            <textarea id="question" name="question" ref={textareaRef} value={input} disabled={isQuerying} onChange={(event) => { setInput(event.target.value); resize(event.currentTarget) }} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void submit() } }} placeholder="O que você quer encontrar?" aria-describedby="composer-help" autoComplete="off" rows={1} className="composer-textarea" />
            <button type="button" className="composer-send" disabled={!input.trim() || isQuerying} onClick={() => void submit()} aria-label="Enviar pergunta"><Send aria-hidden="true" size={17} /></button>
          </div>
          <p id="composer-help" className="composer-help">Enter envia · Shift + Enter quebra linha · respostas usam apenas evidências indexadas</p>
        </div>
      </div>
    </main>
  )
}
