export type FileType = "pdf" | "image" | "video" | "audio" | "text" | "docx"
export type AnswerMode = "quick" | "detailed" | "evidence"
export type HealthState = "ok" | "degraded" | "offline"
export type GoogleHealthState = "configured" | "missing_key"
export type R2HealthState = "ready" | "missing_config" | "unavailable"
export type PineconeHealthState = "ready" | "missing_key" | "index_missing" | "unavailable" | "invalid_configuration"
export type UploadPhase = "preparando" | "enviando" | "validando" | "processando" | "indexando" | "pronto" | "falhou"

export interface PublicDemoConfig {
  enabled: boolean
  formats: string[]
  max_upload_size_mb: number
  max_daily_uploads: number
  max_daily_queries: number
  retention_days: number
  max_pdf_pages: number
  max_audio_duration_seconds: number
  max_video_duration_seconds: number
}

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
  response_id?: string | null
  conversation_id?: string
}

export interface IngestedFile {
  doc_id: string
  name: string
  file_type: FileType
  mime_type: string
  chunks: number
  size_bytes: number
  status: "pending_upload" | "uploaded" | "processing" | "indexing" | "ready" | "failed" | "deleting"
  warnings: string[]
  error?: string | null
  ingested_at: string
}

export interface UploadProgress {
  id: string
  fileName: string
  phase: UploadPhase
  progress: number
  error?: string
  duplicate?: boolean
}

export interface UploadPresignResponse {
  doc_id: string
  upload_url: string | null
  headers: Record<string, string>
  expires_in: number
  duplicate: boolean
  status: IngestedFile["status"]
}

export interface UploadCompleteResponse {
  doc_id: string
  duplicate: boolean
  status: IngestedFile["status"]
  chunks: number
  warnings: string[]
  error?: string | null
}

export interface QueryFilters {
  file_type?: FileType
  doc_id?: string
}

export interface HealthStatus {
  status: HealthState
  services: {
    database: "ok" | "unavailable"
    r2: R2HealthState
    google: GoogleHealthState
    gemini: GoogleHealthState
    pinecone: PineconeHealthState
  }
  models: { embedding: string; generation: string }
  public_demo: PublicDemoConfig
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
  conversation_id?: string
}

export interface QueryResult {
  answer: string
  sources: Source[]
  insufficient_context: boolean
  response_id?: string | null
  conversation_id?: string | null
  message_id?: string | null
}

export interface ConversationResult {
  conversation_id: string
  messages: Array<{
    message_id: string
    response_id?: string | null
    conversation_id: string
    question: string
    answer: string
    source_ids: string[]
    sources: Source[]
    insufficient_context: boolean
    created_at: string | null
  }>
}

export type QuerySubmissionResult = "completed" | "cancelled"
