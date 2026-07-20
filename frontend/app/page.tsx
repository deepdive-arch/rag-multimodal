"use client"

import { Menu, X } from "lucide-react"
import { useCallback, useEffect, useRef, useState } from "react"
import { ChatArea } from "@/components/ChatArea"
import type { EmptyStateMode } from "@/components/EmptyState"
import { Sidebar } from "@/components/Sidebar"
import { checkHealth, deleteConversation, deleteFile, ensureVisitorSession, getConversation, getStats, isRequestCancelledError, listFiles, queryRag, sendFeedback, uploadFileDirect } from "@/lib/api"
import { clearConversationId, clearMessages, loadConversationId, loadPreferences, saveConversationId, savePreferences } from "@/lib/storage"
import type { AnswerMode, HealthStatus, IngestedFile, Message, QueryFilters, QuerySubmissionResult, Stats, UploadPhase, UploadProgress } from "@/types"

function messageId() { return `${Date.now()}-${Math.random().toString(36).slice(2)}` }
function errorMessage(error: unknown, fallback: string) { return error instanceof Error ? error.message : fallback }
const FOCUSABLE_SELECTOR = "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled])"

export default function Page() {
  const [messages, setMessages] = useState<Message[]>([])
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [files, setFiles] = useState<IngestedFile[]>([])
  const [topK, setTopK] = useState(5)
  const [answerMode, setAnswerMode] = useState<AnswerMode>("detailed")
  const [filters, setFilters] = useState<QueryFilters>({})
  const [healthStatus, setHealthStatus] = useState<HealthStatus | null>(null)
  const [stats, setStats] = useState<Stats | null>(null)
  const [isQuerying, setIsQuerying] = useState(false)
  const [isUploading, setIsUploading] = useState(false)
  const [uploadStates, setUploadStates] = useState<UploadProgress[]>([])
  const [uploadSummary, setUploadSummary] = useState({ success: 0, errors: [] as string[], catalogError: "" })
  const [deletingFileId, setDeletingFileId] = useState<string | null>(null)
  const [fileActionError, setFileActionError] = useState("")
  const [isSidebarOpen, setIsSidebarOpen] = useState(false)
  const [isMobileViewport, setIsMobileViewport] = useState(false)
  const [catalogError, setCatalogError] = useState("")
  const [isRefreshingWorkspace, setIsRefreshingWorkspace] = useState(true)
  const [actionError, setActionError] = useState("")
  const [isHydrated, setIsHydrated] = useState(false)
  const controllerRef = useRef<AbortController | null>(null)
  const uploadControllerRef = useRef<AbortController | null>(null)
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
    let cancelled = false
    const bootstrap = async () => {
      const preferences = loadPreferences()
      setTopK(preferences.topK)
      setAnswerMode(preferences.answerMode)
      setFilters(preferences.filters)
      try {
        await ensureVisitorSession()
        const stored = loadConversationId()
        if (stored) {
          try {
            const history = await getConversation(stored)
            if (!cancelled) {
              setConversationId(history.conversation_id)
              setMessages(history.messages.flatMap((item) => [{ id: messageId(), role: "user" as const, content: item.question, created_at: item.created_at ?? new Date().toISOString() }, { id: messageId(), response_id: item.response_id ?? item.message_id, conversation_id: item.conversation_id, role: "assistant" as const, content: item.answer, created_at: item.created_at ?? new Date().toISOString(), sources: item.sources, insufficient_context: item.insufficient_context, feedback: null }]))
            }
          } catch { clearConversationId() }
        }
      } finally {
        if (!cancelled) setIsHydrated(true)
        if (!cancelled) await refreshWorkspace()
      }
    }
    void bootstrap()
    return () => { cancelled = true }
  }, [refreshWorkspace])
  useEffect(() => {
    const mediaQuery = window.matchMedia("(max-width: 1023px)")
    const updateViewport = () => { const isMobile = mediaQuery.matches; setIsMobileViewport(isMobile); if (!isMobile) setIsSidebarOpen(false) }
    updateViewport()
    mediaQuery.addEventListener("change", updateViewport)
    return () => mediaQuery.removeEventListener("change", updateViewport)
  }, [])
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
    const controller = new AbortController()
    uploadControllerRef.current = controller
    setUploadStates(incoming.map((file, index) => ({ id: `${index}-${file.name}`, fileName: file.name, phase: "preparando" as const, progress: 0 })))
    let success = 0
    const errors: string[] = []
    let catalogUpdated = true
    for (const [index, file] of incoming.entries()) {
      const id = `${index}-${file.name}`
      const onState = (phase: UploadPhase, progress: number) => setUploadStates((current) => current.map((state) => state.id === id ? { ...state, phase, progress } : state))
      const ingestFile = async (currentFile: File) => {
        const result = await uploadFileDirect(currentFile, { signal: controller.signal, onState })
        setUploadStates((current) => current.map((state) => state.id === id ? { ...state, duplicate: result.duplicate } : state))
        return result
      }
      try { await ingestFile(file); success += 1 } catch (error) { errors.push(`${file.name}: ${errorMessage(error, "falha na ingestão")}`) }
      setUploadSummary({ success, errors: [...errors], catalogError: "" })
    }
    try { catalogUpdated = await refreshWorkspace(); if (!catalogUpdated) setUploadSummary({ success, errors: [...errors], catalogError: "Upload concluído, mas a lista não pôde ser atualizada." }) } catch (error) { catalogUpdated = false; setUploadSummary({ success, errors: [...errors], catalogError: `Upload concluído, mas a lista não pôde ser atualizada: ${errorMessage(error, "erro desconhecido")}` }) }
    setIsUploading(false)
    uploadControllerRef.current = null
    return errors.length === 0 && catalogUpdated
  }
  const handleCancelUpload = () => uploadControllerRef.current?.abort()
  const finishFileRemoval = async (file: IngestedFile) => { if (filters.doc_id === file.doc_id) setFilters({}); setUploadStates([]); setUploadSummary({ success: 0, errors: [], catalogError: "" }); await refreshWorkspace() }
  const removeFile = async (file: IngestedFile) => { setDeletingFileId(file.doc_id); setFileActionError(""); try { await deleteFile(file.doc_id); await finishFileRemoval(file) } catch (error) { setFileActionError(errorMessage(error, "Não foi possível excluir o arquivo.")) } finally { setDeletingFileId(null) } }
  const handleRemoveFile = async (file: IngestedFile) => { if (window.confirm(`Excluir “${file.name}” do backend? Esta ação não pode ser desfeita.`)) await removeFile(file) }
  const handleQuery = async (question: string): Promise<QuerySubmissionResult> => {
    if (!canQuery) return "cancelled"
    const userMessage: Message = { id: messageId(), role: "user", content: question, created_at: new Date().toISOString() }
    setMessages((current) => [...current, userMessage])
    setActionError("")
    setIsQuerying(true)
    const controller = new AbortController()
    controllerRef.current = controller
    try {
      const result = await queryRag({ question, top_k: topK, history: messages.slice(-6).map(({ role, content }) => ({ role, content })), filters, answer_mode: answerMode, conversation_id: conversationId ?? undefined }, controller.signal)
      if (result.conversation_id) { setConversationId(result.conversation_id); saveConversationId(result.conversation_id) }
      setMessages((current) => [...current, { id: messageId(), response_id: result.response_id ?? result.message_id ?? null, conversation_id: result.conversation_id ?? conversationId ?? undefined, role: "assistant", content: result.answer, created_at: new Date().toISOString(), sources: result.sources, insufficient_context: result.insufficient_context, feedback: null }])
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
  const handleFeedback = async (messageIdValue: string, useful: boolean) => { const answer = messages.find((message) => message.id === messageIdValue); if (!answer?.response_id) return; setActionError(""); try { await sendFeedback({ response_id: answer.response_id, useful }) } catch (error) { setActionError(errorMessage(error, "Não foi possível registrar o feedback.")); return } setMessages((current) => current.map((message) => message.id === messageIdValue ? { ...message, feedback: useful ? "useful" : "not_useful" } : message)) }
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
        <Sidebar sidebarRef={sidebarRef} isDrawerOpen={isMobileViewport && isSidebarOpen} files={files} filters={filters} topK={topK} answerMode={answerMode} publicDemo={healthStatus?.public_demo ?? null} isUploading={isUploading} uploadSummary={uploadSummary} uploadStates={uploadStates} deletingFileId={deletingFileId} fileActionError={fileActionError} onCancel={handleCancelUpload} onFiles={async (incoming) => { const succeeded = await handleFiles(incoming); if (succeeded) setIsSidebarOpen(false); return succeeded }} onFilters={setFilters} onTopK={setTopK} onAnswerMode={setAnswerMode} onRemoveFile={(file) => void handleRemoveFile(file)} onClearChat={() => { if (window.confirm("Limpar a conversa atual?")) { if (conversationId) void deleteConversation(conversationId); setMessages([]); clearMessages(); clearConversationId(); setConversationId(null) } }} />
      </div>
      <div className="main-panel" aria-hidden={isMobileViewport && isSidebarOpen ? "true" : undefined}>
        <button ref={menuButtonRef} type="button" className="mobile-menu-button lg:hidden" aria-label={isSidebarOpen ? "Fechar biblioteca" : "Abrir biblioteca"} aria-controls="rag-sidebar" aria-expanded={isSidebarOpen} onClick={() => setIsSidebarOpen((value) => !value)}>{isSidebarOpen ? <X aria-hidden="true" size={18} /> : <Menu aria-hidden="true" size={18} />}</button>
        <ChatArea messages={messages} healthStatus={healthStatus} stats={stats} canQuery={canQuery} emptyStateMode={emptyStateMode} isQuerying={isQuerying} activeFilterLabel={activeFilterLabel} topK={topK} answerMode={answerMode} catalogError={catalogError} actionError={actionError} isRefreshingWorkspace={isRefreshingWorkspace} onRetryWorkspace={() => void refreshWorkspace()} onOpenLibrary={() => setIsSidebarOpen(true)} onSubmit={handleQuery} onCancel={() => controllerRef.current?.abort()} onFeedback={handleFeedback} />
      </div>
    </div>
  )
}
