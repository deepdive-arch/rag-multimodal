import type { AnswerMode, Message, QueryFilters } from "@/types"

const CHAT_KEY = "rag-multimodal:chat:v1"
const PREFERENCES_KEY = "rag-multimodal:preferences:v1"
const MAX_MESSAGES = 100

export interface Preferences {
  topK: number
  answerMode: AnswerMode
  filters: QueryFilters
}

export function loadMessages(): Message[] {
  if (typeof window === "undefined") return []
  try {
    const value = JSON.parse(window.localStorage.getItem(CHAT_KEY) ?? "null")
    if (!Array.isArray(value)) return []
    return value.filter(isMessage).slice(-MAX_MESSAGES)
  } catch {
    return []
  }
}

export function saveMessages(messages: Message[]): void {
  if (typeof window === "undefined") return
  window.localStorage.setItem(CHAT_KEY, JSON.stringify(messages.slice(-MAX_MESSAGES)))
}

export function clearMessages(): void {
  if (typeof window !== "undefined") window.localStorage.removeItem(CHAT_KEY)
}

export function loadPreferences(): Preferences {
  if (typeof window === "undefined") return { topK: 5, answerMode: "detailed", filters: {} }
  try {
    const value = JSON.parse(window.localStorage.getItem(PREFERENCES_KEY) ?? "null") as Partial<Preferences> | null
    return { topK: typeof value?.topK === "number" ? Math.min(20, Math.max(1, value.topK)) : 5, answerMode: value?.answerMode === "quick" || value?.answerMode === "evidence" ? value.answerMode : "detailed", filters: value?.filters && typeof value.filters === "object" ? value.filters : {} }
  } catch {
    return { topK: 5, answerMode: "detailed", filters: {} }
  }
}

export function savePreferences(preferences: Preferences): void {
  if (typeof window !== "undefined") window.localStorage.setItem(PREFERENCES_KEY, JSON.stringify(preferences))
}

function isMessage(value: unknown): value is Message {
  if (!value || typeof value !== "object") return false
  const item = value as Partial<Message>
  return (item.role === "user" || item.role === "assistant") && typeof item.content === "string" && typeof item.created_at === "string"
}
