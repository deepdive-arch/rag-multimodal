import type { HealthStatus, IngestedFile, QueryPayload, QueryResult, Stats } from "@/types"

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"
const REQUEST_TIMEOUT_MS = 90_000

async function parseResponse<T>(response: Response): Promise<T> {
  const text = await response.text()
  let data: unknown = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = null
  }
  if (!response.ok) {
    const detail = typeof data === "object" && data && "detail" in data ? String(data.detail) : "A API não retornou uma resposta válida."
    throw new Error(detail)
  }
  return data as T
}

async function request<T>(path: string, options?: RequestInit, signal?: AbortSignal): Promise<T> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  const onAbort = () => controller.abort()
  signal?.addEventListener("abort", onAbort, { once: true })
  try {
    const response = await fetch(`${BASE}${path}`, { ...options, signal: controller.signal })
    return await parseResponse<T>(response)
  } catch (error) {
    if (error instanceof TypeError) throw new Error(`NÃ£o foi possÃ­vel conectar ao backend em ${BASE}. Verifique se a API estÃ¡ em execuÃ§Ã£o.`)
    throw error
  } finally {
    window.clearTimeout(timeout)
    signal?.removeEventListener("abort", onAbort)
  }
}

export function checkHealth(): Promise<HealthStatus> {
  return request<HealthStatus>("/api/health")
}

export function getStats(): Promise<Stats> {
  return request<Stats>("/api/stats")
}

export async function listFiles(): Promise<IngestedFile[]> {
  const response = await request<{ files: IngestedFile[] }>("/api/files")
  return response.files
}

export function ingestFile(file: File): Promise<{ doc_id: string; name: string; file_type: string; chunks: number; duplicate: boolean; warnings: string[] }> {
  const body = new FormData()
  body.append("file", file)
  return request("/api/ingest", { method: "POST", body })
}

export function queryRag(payload: QueryPayload, signal?: AbortSignal): Promise<QueryResult> {
  return request<QueryResult>("/api/query", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }, signal)
}

export function deleteFile(docId: string): Promise<{ status: string }> {
  return request(`/api/files/${encodeURIComponent(docId)}`, { method: "DELETE" })
}

export function clearIndex(confirmation: "DELETE_ALL"): Promise<{ status: string }> {
  return request("/api/index", { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirmation }) })
}

export function sendFeedback(payload: { question: string; answer: string; useful: boolean; source_ids: string[] }): Promise<{ id: string }> {
  return request("/api/feedback", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })
}

export function mediaUrl(relativeUrl: string | null): string | null {
  if (!relativeUrl) return null
  if (relativeUrl.startsWith("http")) return relativeUrl
  return `${BASE}${relativeUrl.startsWith("/") ? relativeUrl : `/${relativeUrl}`}`
}
