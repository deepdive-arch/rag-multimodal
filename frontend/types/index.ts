export type FileType = "pdf" | "image" | "video" | "audio" | "text" | "docx"
export type AnswerMode = "quick" | "detailed" | "evidence"
export type HealthState = "ok" | "degraded" | "offline"
export type GoogleHealthState = "configured" | "missing_key"
export type PineconeHealthState = "ready" | "missing_key" | "index_missing" | "unavailable" | "invalid_configuration"

export interface Source {
  doc_id: string
  chunk_id: string
  file_name: string
  file_type: FileType
  content_modality: "text" | "image" | "video" | "audio"
  page_number: number
  text_preview: string
  media_url: string | null
  score: number
}

export interface Message {
  id: string
  role: "user" | "assistant"
  content: string
  created_at: string
  sources?: Source[]
  insufficient_context?: boolean
  feedback?: "useful" | "not_useful" | null
}

export interface IngestedFile {
  doc_id: string
  name: string
  file_type: FileType
  mime_type: string
  chunks: number
  size_bytes: number
  status: "processing" | "ready" | "failed" | "deleting"
  warnings: string[]
  ingested_at: string
}

export interface QueryFilters {
  file_type?: FileType
  doc_id?: string
}

export interface HealthStatus {
  status: HealthState
  services: {
    database: "ok" | "unavailable"
    google: GoogleHealthState
    pinecone: PineconeHealthState
  }
  models: { embedding: string; generation: string }
}

export interface Stats {
  files: number
  chunks: number
  by_type: Record<string, number>
}

export interface QueryPayload {
  question: string
  top_k: number
  history: Array<{ role: "user" | "assistant"; content: string }>
  filters: QueryFilters
  answer_mode: AnswerMode
}

export interface QueryResult {
  answer: string
  sources: Source[]
  insufficient_context: boolean
}

export type QuerySubmissionResult = "completed" | "cancelled"
