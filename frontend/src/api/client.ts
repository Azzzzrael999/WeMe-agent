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

export async function apiRequest<T>(
  path: `/${string}`,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers)
  const token = readAccessToken()

  headers.set('Accept', 'application/json')
  if (init.body !== undefined && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  if (token !== null) {
    headers.set('Authorization', `Bearer ${token}`)
  }

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
    if (response.status === 401) {
      clearAccessToken()
      unauthorizedHandler?.()
    }

    if (isApiFailure(payload)) {
      throw new ApiError({
        code: payload.code,
        message: payload.message,
        status: response.status,
        details: Array.isArray(payload.details) ? payload.details : [],
        traceId: payload.traceId,
      })
    }

    throw new ApiError({
      code: response.status === 401 ? 'UNAUTHORIZED' : 'HTTP_ERROR',
      message: response.status === 401 ? '登录状态已失效，请重新登录。' : '请求失败，请稍后重试。',
      status: response.status,
    })
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
