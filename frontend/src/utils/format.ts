const SHANGHAI_TIME_ZONE = 'Asia/Shanghai'
const SENSITIVE_KEY = /(authorization|token|secret|jwt|agentcontext|service[-_ ]?token)/i

export function formatDateTime(value: string | null | undefined): string {
  if (value === null || value === undefined || value.length === 0) {
    return '—'
  }

  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }

  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: SHANGHAI_TIME_ZONE,
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}

export function formatDuration(durationMs: number | null | undefined): string {
  if (durationMs === null || durationMs === undefined) {
    return '—'
  }
  if (durationMs < 1000) {
    return `${durationMs} ms`
  }
  return `${(durationMs / 1000).toFixed(1)} 秒`
}

export function toShanghaiDateTimeLocal(value: string | null | undefined): string {
  if (value === null || value === undefined || value.length === 0) {
    return ''
  }

  const shanghaiOffset = value.match(/^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})(?::\d{2})?\+08:00$/)
  if (shanghaiOffset !== null) {
    return shanghaiOffset[1]
  }

  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return ''
  }

  const parts = new Intl.DateTimeFormat('sv-SE', {
    timeZone: SHANGHAI_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(date)
  const readPart = (type: Intl.DateTimeFormatPartTypes): string =>
    parts.find((part) => part.type === type)?.value ?? ''
  return `${readPart('year')}-${readPart('month')}-${readPart('day')}T${readPart('hour')}:${readPart('minute')}`
}

/** The browser form is explicitly labelled Asia/Shanghai and accepts 30-minute values. */
export function toShanghaiOffset(value: string): string {
  return value.length === 16 ? `${value}:00+08:00` : `${value}+08:00`
}

export function parseEmployeeIds(value: string): number[] {
  const ids = value
    .split(/[，,\s]+/)
    .map((part) => Number.parseInt(part, 10))
    .filter((id) => Number.isSafeInteger(id) && id > 0)
  return [...new Set(ids)]
}

export function createClientRequestId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `web_${Date.now()}_${Math.random().toString(16).slice(2)}`
}

function redactValue(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(redactValue)
  }
  if (typeof value !== 'object' || value === null) {
    return value
  }

  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>).map(([key, nested]) => [
      key,
      SENSITIVE_KEY.test(key) ? '[已隐藏]' : redactValue(nested),
    ]),
  )
}

/** Defense in depth for the already-sanitized Java Trace payload. */
export function formatSanitizedArgs(value: Record<string, unknown>): string {
  return JSON.stringify(redactValue(value), null, 2)
}
