import type { AnswerMode, QueryFilters } from "@/types"

const CHAT_KEY = "rag-multimodal:chat:v1"
const CONVERSATION_KEY = "rag-multimodal:conversation:v1"
const PREFERENCES_KEY = "rag-multimodal:preferences:v1"

export interface Preferences {
  topK: number
  answerMode: AnswerMode
  filters: QueryFilters
}

export function clearMessages(): void {
  if (typeof window !== "undefined") window.localStorage.removeItem(CHAT_KEY)
}

export function loadConversationId(): string | null {
  if (typeof window === "undefined") return null
  const value = window.localStorage.getItem(CONVERSATION_KEY)
  return value && /^[0-9a-f-]{36}$/i.test(value) ? value : null
}

export function saveConversationId(value: string): void {
  if (typeof window !== "undefined") window.localStorage.setItem(CONVERSATION_KEY, value)
}

export function clearConversationId(): void {
  if (typeof window !== "undefined") window.localStorage.removeItem(CONVERSATION_KEY)
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
