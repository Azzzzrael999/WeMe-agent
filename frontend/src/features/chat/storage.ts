import type { AgentCitation, AgentUnsatAnalysis } from '../../api/types'
export interface ConversationTurn {
  id: string
  runId: string | null
  question: string
  answer: string
  status: string
  unsatAnalysis?: AgentUnsatAnalysis | null
  citations?: AgentCitation[]
}

export interface StoredConversation {
  history: ConversationTurn[]
  current: ConversationTurn | null
}

export interface StoredRunContext {
  threadId: string
  question: string
  status?: string
  updatedAt?: number
}

export const CHAT_HISTORY_STORAGE_KEY = 'weme.chat-history.v1'
export const CHAT_ACTIVE_RUN_STORAGE_KEY = 'weme.chat-active-run.v1'
export const CHAT_ACTIVE_THREAD_STORAGE_KEY = 'weme.chat-active-thread.v1'
export const CHAT_SUPPRESS_RESTORE_STORAGE_KEY = 'weme.chat-suppress-restore.v1'
export const CHAT_RUN_CONTEXT_STORAGE_KEY = 'weme.chat-run-context.v1'
export const CHAT_SHEET_OPENED_STORAGE_KEY = 'weme.chat-sheet-opened.v1'
export const CHAT_SHEET_DISMISSED_STORAGE_KEY = 'weme.chat-sheet-dismissed.v1'
export const CHAT_CONTEXT_EVENT = 'weme:chat-context-updated'
export const NEW_CONVERSATION_EVENT = 'weme:new-conversation'
export const SAFE_RUN_ID = /^[A-Za-z0-9_-]{1,64}$/
export const MAX_PREFILL_LENGTH = 2000

export function readStoredRunSet(key: string): Set<string> {
  try {
    const raw = window.sessionStorage.getItem(key)
    const parsed: unknown = raw === null ? [] : JSON.parse(raw)
    return new Set(Array.isArray(parsed) ? parsed.filter((id): id is string => typeof id === 'string' && SAFE_RUN_ID.test(id)) : [])
  } catch {
    return new Set()
  }
}

export function readReplanPrefill(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const normalized = value.trim()
  if (
    normalized.length === 0
    || normalized.length > MAX_PREFILL_LENGTH
    || /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/.test(normalized)
  ) {
    return null
  }
  return normalized
}

export function persistStoredRunSet(key: string, values: Set<string>): void {
  window.sessionStorage.setItem(key, JSON.stringify([...values].slice(-50)))
}

export function readStoredConversations(): Record<string, StoredConversation> {
  try {
    const raw = window.sessionStorage.getItem(CHAT_HISTORY_STORAGE_KEY)
    if (raw === null) {
      return {}
    }
    const parsed: unknown = JSON.parse(raw)
    return typeof parsed === 'object' && parsed !== null
      ? parsed as Record<string, StoredConversation>
      : {}
  } catch {
    return {}
  }
}

export function readStoredRunContexts(): Record<string, StoredRunContext> {
  try {
    const raw = window.sessionStorage.getItem(CHAT_RUN_CONTEXT_STORAGE_KEY)
    if (raw === null) {
      return {}
    }
    const parsed: unknown = JSON.parse(raw)
    return typeof parsed === 'object' && parsed !== null
      ? parsed as Record<string, StoredRunContext>
      : {}
  } catch {
    return {}
  }
}
