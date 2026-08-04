import { clearAccessToken, readAccessToken } from '../auth/token'
import type { ApiErrorDetail, ApiFailure, ApiSuccess } from './types'

const API_PREFIX = '/api/v1'

let unauthorizedHandler: (() => void) | undefined

export class ApiError extends Error {
  readonly code: string
  readonly details: ApiErrorDetail[]
  readonly status: number
  readonly traceId?: string

  constructor(options: {
    code: string
    message: string
    status: number
    details?: ApiErrorDetail[]
    traceId?: string
  }) {
    super(options.message)
    this.name = 'ApiError'
    this.code = options.code
    this.status = options.status
    this.details = options.details ?? []
    this.traceId = options.traceId
  }
}

export function setUnauthorizedHandler(handler: () => void): void {
  unauthorizedHandler = handler
}

function isApiFailure(value: unknown): value is ApiFailure {
  if (typeof value !== 'object' || value === null) {
    return false
  }

  const candidate = value as Partial<ApiFailure>
  return typeof candidate.code === 'string' && typeof candidate.message === 'string'
}

function isApiSuccess<T>(value: unknown): value is ApiSuccess<T> {
  return typeof value === 'object' && value !== null && 'data' in value
}

async function parseJson(response: Response): Promise<unknown> {
  const contentType = response.headers.get('content-type') ?? ''
  if (!contentType.includes('application/json')) {
    return undefined
  }

  try {
    return await response.json()
  } catch {
    return undefined
  }
}

function apiErrorFromResponse(response: Response, payload: unknown): ApiError {
  if (response.status === 401) {
    clearAccessToken()
    unauthorizedHandler?.()
  }

  if (isApiFailure(payload)) {
    return new ApiError({
      code: payload.code,
      message: payload.message,
      status: response.status,
      details: Array.isArray(payload.details) ? payload.details : [],
      traceId: payload.traceId,
    })
  }

  return new ApiError({
    code: response.status === 401 ? 'UNAUTHORIZED' : 'HTTP_ERROR',
    message: response.status === 401 ? '登录状态已失效，请重新登录。' : '请求失败，请稍后重试。',
    status: response.status,
  })
}

function authenticatedHeaders(init: RequestInit, accept: string): Headers {
  const headers = new Headers(init.headers)
  const token = readAccessToken()

  headers.set('Accept', accept)
  if (init.body !== undefined && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  if (token !== null) {
    headers.set('Authorization', `Bearer ${token}`)
  }

  return headers
}

export async function apiRequest<T>(
  path: `/${string}`,
  init: RequestInit = {},
): Promise<T> {
  const headers = authenticatedHeaders(init, 'application/json')

  let response: Response
  try {
    response = await fetch(`${API_PREFIX}${path}`, { ...init, headers })
  } catch {
    throw new ApiError({
      code: 'NETWORK_ERROR',
      message: '无法连接服务器，请稍后重试。',
      status: 0,
    })
  }

  const payload = await parseJson(response)

  if (!response.ok) {
    throw apiErrorFromResponse(response, payload)
  }

  if (!isApiSuccess<T>(payload)) {
    throw new ApiError({
      code: 'INVALID_RESPONSE',
      message: '服务器响应格式不正确。',
      status: response.status,
    })
  }

  return payload.data
}

export interface SseMessage {
  event: string
  data: unknown
}

export interface SseConnectionMetadata {
  runId: string | null
}

function decodeSseFrame(frame: string): SseMessage | null {
  let event = 'message'
  const dataLines: string[] = []

  for (const line of frame.replaceAll('\r\n', '\n').split('\n')) {
    if (line.startsWith(':') || line.length === 0) {
      continue
    }
    const separator = line.indexOf(':')
    const field = separator === -1 ? line : line.slice(0, separator)
    const value = separator === -1 ? '' : line.slice(separator + 1).replace(/^ /, '')

    if (field === 'event') {
      event = value
    } else if (field === 'data') {
      dataLines.push(value)
    }
  }

  if (dataLines.length === 0) {
    return null
  }

  const data = dataLines.join('\n')
  try {
    return { event, data: JSON.parse(data) as unknown }
  } catch {
    throw new ApiError({
      code: 'INVALID_SSE_EVENT',
      message: '调度服务返回了无法识别的流式事件。',
      status: 200,
    })
  }
}

/**
 * Connect to the Java public SSE endpoint with fetch rather than EventSource,
 * because EventSource cannot carry the browser's Bearer access token.
 */
export async function apiSseRequest(
  path: `/${string}`,
  body: unknown,
  onMessage: (message: SseMessage) => void,
  signal?: AbortSignal,
  onOpen?: (metadata: SseConnectionMetadata) => void,
): Promise<void> {
  const init: RequestInit = {
    method: 'POST',
    body: JSON.stringify(body),
    signal,
  }
  const headers = authenticatedHeaders(init, 'text/event-stream')

  let response: Response
  try {
    response = await fetch(`${API_PREFIX}${path}`, { ...init, headers })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      return
    }
    throw new ApiError({
      code: 'NETWORK_ERROR',
      message: '无法连接调度服务，请稍后重试。',
      status: 0,
    })
  }

  if (!response.ok) {
    throw apiErrorFromResponse(response, await parseJson(response))
  }

  if (!response.headers.get('content-type')?.includes('text/event-stream')) {
    throw new ApiError({
      code: 'INVALID_STREAM_RESPONSE',
      message: '调度服务没有返回有效的事件流。',
      status: response.status,
    })
  }

  onOpen?.({ runId: response.headers.get('X-Run-Id') })

  const reader = response.body?.getReader()
  if (reader === undefined) {
    throw new ApiError({
      code: 'EMPTY_STREAM_RESPONSE',
      message: '调度服务没有返回可读取的事件流。',
      status: response.status,
    })
  }

  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value, { stream: !done })
    buffer = buffer.replaceAll('\r\n', '\n')

    let boundary = buffer.indexOf('\n\n')
    while (boundary !== -1) {
      const frame = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      const message = decodeSseFrame(frame)
      if (message !== null) {
        onMessage(message)
      }
      boundary = buffer.indexOf('\n\n')
    }

    if (done) {
      break
    }
  }

  const finalMessage = decodeSseFrame(buffer)
  if (finalMessage !== null) {
    onMessage(finalMessage)
  }
}
