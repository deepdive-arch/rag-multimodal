"use client"

import { Menu, X } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { ChatArea } from "@/components/ChatArea"
import { Sidebar } from "@/components/Sidebar"
import { checkHealth, clearIndex, deleteFile, getStats, ingestFile, listFiles, queryRag, sendFeedback } from "@/lib/api"
import { clearMessages, loadMessages, loadPreferences, saveMessages, savePreferences } from "@/lib/storage"
import type { AnswerMode, HealthStatus, IngestedFile, Message, QueryFilters, Stats } from "@/types"

function messageId() { return `${Date.now()}-${Math.random().toString(36).slice(2)}` }
function errorMessage(error: unknown, fallback: string) { return error instanceof Error ? error.message : fallback }

export default function Page() {
  const [messages, setMessages] = useState<Message[]>([])
  const [files, setFiles] = useState<IngestedFile[]>([])
  const [topK, setTopK] = useState(5)
  const [answerMode, setAnswerMode] = useState<AnswerMode>("detailed")
  const [filters, setFilters] = useState<QueryFilters>({})
  const [healthStatus, setHealthStatus] = useState<HealthStatus | null>(null)
  const [stats, setStats] = useState<Stats | null>(null)
  const [isQuerying, setIsQuerying] = useState(false)
  const [isUploading, setIsUploading] = useState(false)
  const [uploadSummary, setUploadSummary] = useState({ success: 0, errors: [] as string[], catalogError: "" })
  const [isSidebarOpen, setIsSidebarOpen] = useState(false)
  const [isHydrated, setIsHydrated] = useState(false)
  const controllerRef = useRef<AbortController | null>(null)
  const menuButtonRef = useRef<HTMLButtonElement>(null)
  const sidebarRef = useRef<HTMLElement>(null)

  useEffect(() => {
    const preferences = loadPreferences()
    window.setTimeout(() => { setMessages(loadMessages()); setTopK(preferences.topK); setAnswerMode(preferences.answerMode); setFilters(preferences.filters); setIsHydrated(true) }, 0)
    void Promise.all([checkHealth(), listFiles(), getStats()]).then(([health, loadedFiles, loadedStats]) => { setHealthStatus(health); setFiles(loadedFiles); setStats(loadedStats) }).catch(() => setHealthStatus(null))
  }, [])
  useEffect(() => { if (isHydrated) saveMessages(messages) }, [messages, isHydrated])
  useEffect(() => { if (isHydrated) savePreferences({ topK, answerMode, filters }) }, [topK, answerMode, filters, isHydrated])
  useEffect(() => {
    if (!isSidebarOpen) return
    const previousOverflow = document.body.style.overflow
    const menuButton = menuButtonRef.current
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") setIsSidebarOpen(false) }
    document.body.style.overflow = "hidden"
    document.addEventListener("keydown", closeOnEscape)
    sidebarRef.current?.focus()
    return () => { document.body.style.overflow = previousOverflow; document.removeEventListener("keydown", closeOnEscape); menuButton?.focus() }
  }, [isSidebarOpen])

  const refreshCatalog = async () => { const [loadedFiles, loadedStats] = await Promise.all([listFiles(), getStats()]); setFiles(loadedFiles); setStats(loadedStats) }
  const handleFiles = async (incoming: File[]) => { setIsUploading(true); setUploadSummary({ success: 0, errors: [], catalogError: "" }); let success = 0; const errors: string[] = []; for (const file of incoming) { try { await ingestFile(file); success += 1; setUploadSummary({ success, errors: [...errors], catalogError: "" }) } catch (error) { errors.push(`${file.name}: ${errorMessage(error, "falha na ingestão")}`); setUploadSummary({ success, errors: [...errors], catalogError: "" }); continue } try { await refreshCatalog(); setUploadSummary({ success, errors: [...errors], catalogError: "" }) } catch (error) { setUploadSummary({ success, errors: [...errors], catalogError: `Upload concluído, mas a lista não pôde ser atualizada: ${errorMessage(error, "erro desconhecido")}` }) } } setIsUploading(false) }
  const handleQuery = async (question: string) => { const userMessage: Message = { id: messageId(), role: "user", content: question, created_at: new Date().toISOString() }; setMessages((current) => [...current, userMessage]); setIsQuerying(true); const controller = new AbortController(); controllerRef.current = controller; try { const result = await queryRag({ question, top_k: topK, history: messages.slice(-6).map(({ role, content }) => ({ role, content })), filters, answer_mode: answerMode }, controller.signal); setMessages((current) => [...current, { id: messageId(), role: "assistant", content: result.answer, created_at: new Date().toISOString(), sources: result.sources, insufficient_context: result.insufficient_context, feedback: null }]) } finally { setIsQuerying(false); controllerRef.current = null } }
  const handleFeedback = async (messageIdValue: string, useful: boolean) => { const answer = messages.find((message) => message.id === messageIdValue); if (!answer) return; const index = messages.findIndex((message) => message.id === messageIdValue); const question = messages.slice(0, index).toReversed().find((message) => message.role === "user"); if (!question) return; await sendFeedback({ question: question.content, answer: answer.content, useful, source_ids: (answer.sources ?? []).map((source) => source.chunk_id) }); setMessages((current) => current.map((message) => message.id === messageIdValue ? { ...message, feedback: useful ? "useful" : "not_useful" } : message)) }
  const handleDelete = async (file: IngestedFile) => { if (!window.confirm(`Excluir ${file.name} da base?`)) return; await deleteFile(file.doc_id); setFilters((current) => current.doc_id === file.doc_id ? {} : current); await refreshCatalog() }
  const handleClearIndex = async () => { if (window.prompt("Digite DELETE_ALL para limpar a base inteira.") !== "DELETE_ALL") return; await clearIndex("DELETE_ALL"); setMessages([]); clearMessages(); setFilters({}); await refreshCatalog() }
  const activeFilterLabel = filters.doc_id ? files.find((file) => file.doc_id === filters.doc_id)?.name ?? "Arquivo específico" : filters.file_type ?? undefined
  return (
    <div className="flex h-dvh overflow-hidden">
      {isSidebarOpen && <button type="button" className="mobile-scrim md:hidden" aria-label="Fechar biblioteca" onClick={() => setIsSidebarOpen(false)} />}
      <div className={`fixed inset-y-0 left-0 z-50 w-[min(90vw,348px)] transition-transform duration-200 md:static md:z-auto md:w-[348px] md:translate-x-0 ${isSidebarOpen ? "translate-x-0" : "-translate-x-full"}`}>
        <Sidebar sidebarRef={sidebarRef} files={files} filters={filters} topK={topK} answerMode={answerMode} isUploading={isUploading} uploadSummary={uploadSummary} onFiles={async (incoming) => { await handleFiles(incoming); setIsSidebarOpen(false) }} onFilters={setFilters} onTopK={setTopK} onAnswerMode={setAnswerMode} onDelete={(file) => void handleDelete(file)} onClearChat={() => { setMessages([]); clearMessages() }} onClearIndex={() => void handleClearIndex()} />
      </div>
      <div className="flex min-w-0 flex-1 flex-col" aria-hidden={isSidebarOpen ? "true" : undefined}>
        <button ref={menuButtonRef} type="button" className="mobile-menu-button md:hidden" aria-label={isSidebarOpen ? "Fechar biblioteca" : "Abrir biblioteca"} aria-controls="rag-sidebar" aria-expanded={isSidebarOpen} onClick={() => setIsSidebarOpen((value) => !value)}>{isSidebarOpen ? <X aria-hidden="true" size={18} /> : <Menu aria-hidden="true" size={18} />}</button>
        <ChatArea messages={messages} healthStatus={healthStatus} stats={stats} isQuerying={isQuerying} activeFilterLabel={activeFilterLabel} topK={topK} answerMode={answerMode} onSubmit={handleQuery} onCancel={() => controllerRef.current?.abort()} onFeedback={handleFeedback} />
      </div>
    </div>
  )
}
