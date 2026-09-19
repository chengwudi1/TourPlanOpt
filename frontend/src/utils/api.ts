/**
 * One place that knows how the backend reports failure.
 *
 * FastAPI's `detail` field is polymorphic: a plain string for HTTPException, an object
 * for the AmapError handler (message + hint + infocode + kind), and a list for 422
 * validation failures. Normalising all three here means components can just render
 * `err.message` and `err.hint`.
 */

export class ApiError extends Error {
  readonly status: number
  readonly hint: string
  readonly kind: string
  readonly infocode: string

  constructor(status: number, message: string, hint = '', kind = '', infocode = '') {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.hint = hint
    this.kind = kind
    this.infocode = infocode
  }
}

interface ValidationError {
  loc?: (string | number)[]
  msg?: string
}

function describe(detail: unknown, status: number): { message: string; hint: string; kind: string; infocode: string } {
  if (typeof detail === 'string') {
    return { message: detail, hint: '', kind: '', infocode: '' }
  }
  if (Array.isArray(detail)) {
    const lines = detail
      .map((item: ValidationError) => {
        const field = (item.loc ?? []).slice(1).join('.')
        return field ? `${field}: ${item.msg ?? '无效'}` : (item.msg ?? '无效')
      })
    return { message: `请求参数有误（${lines.join('；')}）`, hint: '', kind: 'validation', infocode: '' }
  }
  if (detail && typeof detail === 'object') {
    const d = detail as Record<string, unknown>
    return {
      message: String(d.message ?? `请求失败（HTTP ${status}）`),
      hint: String(d.hint ?? ''),
      kind: String(d.kind ?? ''),
      infocode: String(d.infocode ?? ''),
    }
  }
  return { message: `请求失败（HTTP ${status}）`, hint: '', kind: '', infocode: '' }
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, init)
  } catch {
    // fetch itself threw: the backend is down or the dev proxy is not running.
    throw new ApiError(0, '连不上本机服务', '它没有响应。确认服务已经启动，然后重试。')
  }

  const text = await res.text()
  const parsed = text ? safeJson(text) : null

  if (!res.ok) {
    const { message, hint, kind, infocode } = describe(parsed?.detail, res.status)
    throw new ApiError(res.status, message, hint, kind, infocode)
  }
  return parsed as T
}

function safeJson(text: string): any {
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

export function postJson(body: unknown): RequestInit {
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }
}
