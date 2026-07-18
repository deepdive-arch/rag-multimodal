"use client"

import { Menu, X } from "lucide-react"
import { useCallback, useEffect, useRef, useState } from "react"
import { ChatArea } from "@/components/ChatArea"
import type { EmptyStateMode } from "@/components/EmptyState"
import { Sidebar } from "@/components/Sidebar"
import { checkHealth, clearIndex, deleteFile, getStats, ingestFile, isRequestCancelledError, listFiles, queryRag, sendFeedback } from "@/lib/api"
import { clearMessages, loadMessages, loadPreferences, saveMessages, savePreferences } from "@/lib/storage"
import type { AnswerMode, HealthStatus, IngestedFile, Message, QueryFilters, QuerySubmissionResult, Stats } from "@/types"

function messageId() { return `${Date.now()}-${Math.random().toString(36).slice(2)}` }
function errorMessage(error: unknown, fallback: string) { return error instanceof Error ? error.message : fallback }
const FOCUSABLE_SELECTOR = "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled])"

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
  const [isMobileViewport, setIsMobileViewport] = useState(false)
  const [catalogError, setCatalogError] = useState("")
  const [isRefreshingWorkspace, setIsRefreshingWorkspace] = useState(true)
  const [actionError, setActionError] = useState("")
  const [isHydrated, setIsHydrated] = useState(false)
  const controllerRef = useRef<AbortController | null>(null)
  const menuButtonRef = useRef<HTMLButtonElement>(null)
  const sidebarRef = useRef<HTMLElement>(null)

  const refreshWorkspace = useCallback(async () => {
    setIsRefreshingWorkspace(true)
    try {
      const [healthResult, filesResult, statsResult] = await Promise.allSettled([checkHealth(), listFiles(), getStats()])
      if (healthResult.status === "fulfilled") setHealthStatus(healthResult.value)
      else setHealthStatus(null)
      if (filesResult.status === "fulfilled") setFiles(filesResult.value)
      if (statsResult.status === "fulfilled") setStats(statsResult.value)
      const catalogFailure = [filesResult, statsResult].find((result): result is PromiseRejectedResult => result.status === "rejected")
      setCatalogError(catalogFailure ? errorMessage(catalogFailure.reason, "A biblioteca não pôde ser carregada.") : "")
      return !catalogFailure
    } finally {
      setIsRefreshingWorkspace(false)
    }
  }, [])
  useEffect(() => {
    const preferences = loadPreferences()
    window.setTimeout(() => { setMessages(loadMessages()); setTopK(preferences.topK); setAnswerMode(preferences.answerMode); setFilters(preferences.filters); setIsHydrated(true) }, 0)
    window.setTimeout(() => { void refreshWorkspace() }, 0)
  }, [refreshWorkspace])
  useEffect(() => {
    const mediaQuery = window.matchMedia("(max-width: 1023px)")
    const updateViewport = () => { const isMobile = mediaQuery.matches; setIsMobileViewport(isMobile); if (!isMobile) setIsSidebarOpen(false) }
    updateViewport()
    mediaQuery.addEventListener("change", updateViewport)
    return () => mediaQuery.removeEventListener("change", updateViewport)
  }, [])
  useEffect(() => { if (isHydrated) saveMessages(messages) }, [messages, isHydrated])
  useEffect(() => { if (isHydrated) savePreferences({ topK, answerMode, filters }) }, [topK, answerMode, filters, isHydrated])
  useEffect(() => {
    if (!isSidebarOpen) return
    const previousOverflow = document.body.style.overflow
    const menuButton = menuButtonRef.current
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") setIsSidebarOpen(false) }
    const keepFocusInside = (event: KeyboardEvent) => {
      if (event.key !== "Tab") return
      const focusable = Array.from(sidebarRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR) ?? [])
      if (!focusable.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    document.body.style.overflow = "hidden"
    document.addEventListener("keydown", closeOnEscape)
    document.addEventListener("keydown", keepFocusInside)
    sidebarRef.current?.focus()
    return () => { document.body.style.overflow = previousOverflow; document.removeEventListener("keydown", closeOnEscape); document.removeEventListener("keydown", keepFocusInside); menuButton?.focus() }
  }, [isSidebarOpen])

  const handleFiles = async (incoming: File[]) => {
    setIsUploading(true)
    setActionError("")
    setUploadSummary({ success: 0, errors: [], catalogError: "" })
    let success = 0
    const errors: string[] = []
    let catalogUpdated = true
    for (const file of incoming) {
      try { await ingestFile(file); success += 1 } catch (error) { errors.push(`${file.name}: ${errorMessage(error, "falha na ingestão")}`) }
      setUploadSummary({ success, errors: [...errors], catalogError: "" })
    }
    try { catalogUpdated = await refreshWorkspace(); if (!catalogUpdated) setUploadSummary({ success, errors: [...errors], catalogError: "Upload concluído, mas a lista não pôde ser atualizada." }) } catch (error) { catalogUpdated = false; setUploadSummary({ success, errors: [...errors], catalogError: `Upload concluído, mas a lista não pôde ser atualizada: ${errorMessage(error, "erro desconhecido")}` }) }
    setIsUploading(false)
    return errors.length === 0 && catalogUpdated
  }
  const handleQuery = async (question: string): Promise<QuerySubmissionResult> => {
    if (!canQuery) return "cancelled"
    const userMessage: Message = { id: messageId(), role: "user", content: question, created_at: new Date().toISOString() }
    setMessages((current) => [...current, userMessage])
    setActionError("")
    setIsQuerying(true)
    const controller = new AbortController()
    controllerRef.current = controller
    try {
      const result = await queryRag({ question, top_k: topK, history: messages.slice(-6).map(({ role, content }) => ({ role, content })), filters, answer_mode: answerMode }, controller.signal)
      setMessages((current) => [...current, { id: messageId(), role: "assistant", content: result.answer, created_at: new Date().toISOString(), sources: result.sources, insufficient_context: result.insufficient_context, feedback: null }])
      return "completed"
    } catch (error) {
      if (!isRequestCancelledError(error)) throw error
      setMessages((current) => current.filter((message) => message.id !== userMessage.id))
      return "cancelled"
    } finally {
      setIsQuerying(false)
      controllerRef.current = null
    }
  }
  const handleFeedback = async (messageIdValue: string, useful: boolean) => { const answer = messages.find((message) => message.id === messageIdValue); if (!answer) return; const index = messages.findIndex((message) => message.id === messageIdValue); const question = messages.slice(0, index).toReversed().find((message) => message.role === "user"); if (!question) return; setActionError(""); try { await sendFeedback({ question: question.content, answer: answer.content, useful, source_ids: (answer.sources ?? []).map((source) => source.chunk_id) }) } catch (error) { setActionError(errorMessage(error, "Não foi possível registrar o feedback.")); return } setMessages((current) => current.map((message) => message.id === messageIdValue ? { ...message, feedback: useful ? "useful" : "not_useful" } : message)) }
  const handleDelete = async (file: IngestedFile) => { if (!window.confirm(`Excluir ${file.name} da base?`)) return; setActionError(""); try { await deleteFile(file.doc_id) } catch (error) { setActionError(errorMessage(error, "Não foi possível excluir o arquivo.")); return } setFilters((current) => current.doc_id === file.doc_id ? {} : current); try { await refreshWorkspace() } catch { return } }
  const handleClearIndex = async () => { if (window.prompt("Digite DELETE_ALL para limpar a base inteira.") !== "DELETE_ALL") return; setActionError(""); try { await clearIndex("DELETE_ALL") } catch (error) { setActionError(errorMessage(error, "Não foi possível limpar a base.")); return } setMessages([]); clearMessages(); setFilters({}); try { await refreshWorkspace() } catch { return } }
  const activeFilterLabel = filters.doc_id ? files.find((file) => file.doc_id === filters.doc_id)?.name ?? "Arquivo específico" : filters.file_type ?? undefined
  const hasReadyFiles = files.some((file) => file.status === "ready")
  const serviceUnavailable = !isRefreshingWorkspace && (!healthStatus || healthStatus.status !== "ok")
  const canQuery = hasReadyFiles && !catalogError && healthStatus?.status === "ok"
  const emptyStateMode: EmptyStateMode = isRefreshingWorkspace && files.length === 0 ? "loading" : catalogError || serviceUnavailable ? "error" : hasReadyFiles ? "ready" : files.length ? "processing" : "no-files"
  return (
    <div className="app-shell">
      <a className="skip-link" href="#conversation-content" tabIndex={isSidebarOpen ? -1 : 0} aria-hidden={isSidebarOpen}>Pular para a conversa</a>
      {isSidebarOpen && isMobileViewport && <button type="button" className="mobile-scrim" aria-label="Fechar biblioteca" onClick={() => setIsSidebarOpen(false)} />}
      <div className={`sidebar-container ${isSidebarOpen && isMobileViewport ? "is-open" : ""}`} aria-hidden={isMobileViewport && !isSidebarOpen ? "true" : undefined} inert={isMobileViewport && !isSidebarOpen ? true : undefined}>
        <Sidebar sidebarRef={sidebarRef} isDrawerOpen={isMobileViewport && isSidebarOpen} files={files} filters={filters} topK={topK} answerMode={answerMode} isUploading={isUploading} uploadSummary={uploadSummary} onFiles={async (incoming) => { const succeeded = await handleFiles(incoming); if (succeeded) setIsSidebarOpen(false); return succeeded }} onFilters={setFilters} onTopK={setTopK} onAnswerMode={setAnswerMode} onDelete={(file) => void handleDelete(file)} onClearChat={() => { if (window.confirm("Limpar a conversa atual?")) { setMessages([]); clearMessages() } }} onClearIndex={() => void handleClearIndex()} />
      </div>
      <div className="main-panel" aria-hidden={isMobileViewport && isSidebarOpen ? "true" : undefined}>
        <button ref={menuButtonRef} type="button" className="mobile-menu-button lg:hidden" aria-label={isSidebarOpen ? "Fechar biblioteca" : "Abrir biblioteca"} aria-controls="rag-sidebar" aria-expanded={isSidebarOpen} onClick={() => setIsSidebarOpen((value) => !value)}>{isSidebarOpen ? <X aria-hidden="true" size={18} /> : <Menu aria-hidden="true" size={18} />}</button>
        <ChatArea messages={messages} healthStatus={healthStatus} stats={stats} canQuery={canQuery} emptyStateMode={emptyStateMode} isQuerying={isQuerying} activeFilterLabel={activeFilterLabel} topK={topK} answerMode={answerMode} catalogError={catalogError} actionError={actionError} isRefreshingWorkspace={isRefreshingWorkspace} onRetryWorkspace={() => void refreshWorkspace()} onOpenLibrary={() => setIsSidebarOpen(true)} onSubmit={handleQuery} onCancel={() => controllerRef.current?.abort()} onFeedback={handleFeedback} />
      </div>
    </div>
  )
}
