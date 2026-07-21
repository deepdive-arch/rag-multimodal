import type { ConversationResult, HealthStatus, IngestedFile, QueryPayload, QueryResult, Stats, UploadCompleteResponse, UploadPhase, UploadPresignResponse } from "@/types"

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"
const REQUEST_TIMEOUT_MS = 90_000

export class RequestCancelledError extends Error {
  constructor() {
    super("A espera pela resposta foi interrompida.")
    this.name = "RequestCancelledError"
  }
}

export class RequestTimeoutError extends Error {
  constructor() {
    super("A consulta excedeu o tempo limite.")
    this.name = "RequestTimeoutError"
  }
}

export class R2UploadError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = "R2UploadError"
    this.status = status
  }
}

export function isRequestCancelledError(error: unknown): error is RequestCancelledError {
  return error instanceof RequestCancelledError
}

async function parseResponse<T>(response: Response): Promise<T> {
  const text = await response.text()
  let data: unknown = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = null
  }
  if (!response.ok) {
    const detail = typeof data === "object" && data && "detail" in data && data.detail ? String(data.detail) : statusMessage(response.status)
    throw new Error(detail)
  }
  return data as T
}

function statusMessage(status: number): string {
  if (status === 429) return "O limite diário deste cliente foi atingido. Tente novamente amanhã."
  if (status === 507) return "A capacidade pública de armazenamento foi atingida. Tente novamente mais tarde."
  if (status === 413) return "Arquivo excede o limite configurado."
  if (status === 415) return "Tipo ou assinatura de arquivo não suportada."
  if (status === 422) return "O arquivo não contém conteúdo indexável."
  if (status === 409) return "O upload ainda não foi concluído ou entrou em conflito."
  if (status >= 500) return "O backend está temporariamente indisponível."
  return "A API não retornou uma resposta válida."
}

async function request<T>(path: string, options?: RequestInit, signal?: AbortSignal): Promise<T> {
  const controller = new AbortController()
  let wasExternallyAborted = false
  let didTimeout = false
  const onAbort = () => { wasExternallyAborted = true; controller.abort() }
  const timeout = window.setTimeout(() => { didTimeout = true; controller.abort() }, REQUEST_TIMEOUT_MS)
  if (signal?.aborted) onAbort()
  else signal?.addEventListener("abort", onAbort, { once: true })
  try {
    const response = await fetch(`${BASE}${path}`, { credentials: "include", ...options, signal: controller.signal })
    return await parseResponse<T>(response)
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      if (wasExternallyAborted) throw new RequestCancelledError()
      if (didTimeout) throw new RequestTimeoutError()
    }
    if (error instanceof TypeError) throw new Error(`Não foi possível conectar ao backend em ${BASE}. Verifique se a API está em execução.`)
    throw error
  } finally {
    window.clearTimeout(timeout)
    signal?.removeEventListener("abort", onAbort)
  }
}

export function checkHealth(): Promise<HealthStatus> {
  return request<HealthStatus>("/api/health")
}

export function ensureVisitorSession(): Promise<{ status: string }> {
  return request<{ status: string }>("/api/session")
}

export function getConversation(conversationId: string): Promise<ConversationResult> {
  return request<ConversationResult>(`/api/conversations/${encodeURIComponent(conversationId)}`)
}

export function deleteConversation(conversationId: string): Promise<{ deleted: boolean }> {
  return request<{ deleted: boolean }>(`/api/conversations/${encodeURIComponent(conversationId)}`, { method: "DELETE" })
}

export function getStats(): Promise<Stats> {
  return request<Stats>("/api/stats")
}

export async function listFiles(): Promise<IngestedFile[]> {
  const response = await request<{ files: IngestedFile[] }>("/api/files")
  return response.files
}

export function getFileStatus(docId: string): Promise<IngestedFile> {
  return request<IngestedFile>(`/api/files/${encodeURIComponent(docId)}`)
}

export function deleteFile(docId: string): Promise<{ doc_id: string; status: "deleted" | "deleting"; stage: string | null; claimed: boolean }> {
  return request(`/api/files/${encodeURIComponent(docId)}`, { method: "DELETE" })
}

export function retryFile(docId: string): Promise<UploadCompleteResponse> {
  return request<UploadCompleteResponse>(`/api/files/${encodeURIComponent(docId)}/retry`, { method: "POST" })
}

export function ingestFile(file: File): Promise<{ doc_id: string; name: string; file_type: string; chunks: number; duplicate: boolean; warnings: string[] }> {
  const body = new FormData()
  body.append("file", file)
  return request("/api/ingest", { method: "POST", body })
}

export async function sha256File(file: File): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer())
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("")
}

function mimeForFile(file: File): string {
  if (file.type) return file.type
  const extension = file.name.toLowerCase().split(".").pop() ?? ""
  const knownTypes: Record<string, string> = { pdf: "application/pdf", docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", txt: "text/plain", md: "text/markdown", png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif", webp: "image/webp", mp4: "video/mp4", mov: "video/quicktime", mp3: "audio/mpeg", wav: "audio/wav" }
  return knownTypes[extension] ?? "application/octet-stream"
}

export function presignUpload(payload: { file_name: string; size_bytes: number; mime_type: string; sha256: string }, signal?: AbortSignal): Promise<UploadPresignResponse> {
  return request<UploadPresignResponse>("/api/uploads/presign", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }, signal)
}

export function completeUpload(docId: string, signal?: AbortSignal): Promise<UploadCompleteResponse> {
  return request<UploadCompleteResponse>(`/api/uploads/${encodeURIComponent(docId)}/complete`, { method: "POST" }, signal)
}

interface DirectUploadOptions {
  signal?: AbortSignal
  onState?: (phase: UploadPhase, progress: number) => void
}

export async function uploadFileDirect(file: File, options: DirectUploadOptions = {}): Promise<{ docId: string; duplicate: boolean }> {
  const notify = options.onState ?? (() => undefined)
  notify("preparando", 0)
  const payload = { file_name: file.name, size_bytes: file.size, mime_type: mimeForFile(file), sha256: await sha256File(file) }
  let authorization = await presignUpload(payload, options.signal)
  if (authorization.upload_url) authorization = await putWithRefresh(file, payload, authorization, notify, options.signal)
  notify("validando", 100)
  const complete = authorization.status === "pending_upload" || authorization.status === "uploaded" ? await completeUpload(authorization.doc_id, options.signal) : { doc_id: authorization.doc_id, duplicate: true, status: authorization.status, chunks: 0, warnings: [], error: null }
  return waitForReady(complete, notify, options.signal)
}

async function putWithRefresh(file: File, payload: { file_name: string; size_bytes: number; mime_type: string; sha256: string }, authorization: UploadPresignResponse, notify: (phase: UploadPhase, progress: number) => void, signal?: AbortSignal): Promise<UploadPresignResponse> {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      notify("enviando", 0)
      await putToR2(file, authorization, notify, signal)
      return authorization
    } catch (error) {
      if (!isExpiredR2Error(error) || attempt === 1) throw error
      notify("preparando", 0)
      authorization = await presignUpload(payload, signal)
    }
  }
  throw new R2UploadError(403, "A autorização do upload expirou.")
}

function putToR2(file: File, authorization: UploadPresignResponse, notify: (phase: UploadPhase, progress: number) => void, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (!authorization.upload_url) return reject(new R2UploadError(409, "O R2 não possui uma URL de upload válida."))
    const xhr = new XMLHttpRequest()
    const abort = () => xhr.abort()
    xhr.open("PUT", authorization.upload_url)
    Object.entries(authorization.headers).forEach(([name, value]) => xhr.setRequestHeader(name, value))
    xhr.upload.onprogress = (event) => { if (event.lengthComputable) notify("enviando", Math.round((event.loaded / event.total) * 100)) }
    xhr.onload = () => xhr.status >= 200 && xhr.status < 300 ? resolve() : reject(new R2UploadError(xhr.status, xhr.status === 403 ? "O R2 recusou o upload (403)." : `O R2 recusou o upload (${xhr.status}).`))
    xhr.onerror = () => reject(new R2UploadError(0, "Não foi possível acessar o R2. Verifique CORS e a rede."))
    xhr.ontimeout = () => reject(new R2UploadError(0, "O upload ao R2 excedeu o tempo limite."))
    xhr.onabort = () => reject(new RequestCancelledError())
    if (signal?.aborted) return abort()
    signal?.addEventListener("abort", abort, { once: true })
    xhr.onloadend = () => signal?.removeEventListener("abort", abort)
    xhr.send(file)
  })
}

function isExpiredR2Error(error: unknown): boolean {
  return error instanceof R2UploadError && (error.status === 403 || error.status === 400)
}

async function waitForReady(
  complete: UploadCompleteResponse,
  notify: (phase: UploadPhase, progress: number) => void,
  signal?: AbortSignal,
): Promise<{ docId: string; duplicate: boolean }> {
  let state = complete

  for (let attempt = 0; attempt < 300; attempt += 1) {
    if (signal?.aborted) {
      throw new RequestCancelledError()
    }

    if (state.status === "ready") {
      notify("pronto", 100)

      return {
        docId: state.doc_id,
        duplicate: state.duplicate,
      }
    }

    if (state.status === "failed") {
      notify("falhou", 100)

      throw new Error(
        state.error || "O processamento do arquivo falhou.",
      )
    }

    if (
      state.status === "deleting" ||
      state.status === "deleted"
    ) {
      notify("falhou", 100)

      throw new Error(
        "O arquivo foi removido durante o processamento. Exclua o registro antigo e envie o arquivo novamente.",
      )
    }

    notify(phaseForStatus(state.status), 100)

    await delay(1000, signal)

    try {
      const document = await getFileStatus(state.doc_id)

      state = {
        doc_id: document.doc_id,
        duplicate: state.duplicate,
        status: document.status,
        chunks: document.chunks,
        warnings: document.warnings,
        error: document.error,
      }
    } catch (error) {
      /*
       * Tolera apenas falhas temporárias nas primeiras consultas.
       * Depois disso, interrompe o polling e mostra o erro.
       */
      if (attempt >= 2) {
        throw error
      }
    }
  }

  throw new Error(
    "O processamento excedeu o tempo de acompanhamento do upload.",
  )
}

function phaseForStatus(
  status: IngestedFile["status"],
): UploadPhase {
  switch (status) {
    case "processing":
      return "processando"

    case "indexing":
      return "indexando"

    case "ready":
      return "pronto"

    case "failed":
    case "deleting":
    case "deleted":
      return "falhou"

    case "pending_upload":
    case "uploaded":
    default:
      return "validando"
  }
}

function delay(milliseconds: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(resolve, milliseconds)
    signal?.addEventListener("abort", () => { window.clearTimeout(timeout); reject(new RequestCancelledError()) }, { once: true })
  })
}

export function queryRag(payload: QueryPayload, signal?: AbortSignal): Promise<QueryResult> {
  return request<QueryResult>("/api/query", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }, signal)
}

export function sendFeedback(payload: { response_id: string; useful: boolean }): Promise<{ id: string }> {
  return request("/api/feedback", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })
}

export function mediaUrl(relativeUrl: string | null): string | null {
  if (!relativeUrl) return null
  if (relativeUrl.startsWith("http")) return relativeUrl
  return `${BASE}${relativeUrl.startsWith("/") ? relativeUrl : `/${relativeUrl}`}`
}
