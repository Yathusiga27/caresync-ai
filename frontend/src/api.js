const baseUrl = (import.meta.env.VITE_API_BASE_URL?.trim() || '/api').replace(/\/+$/, '')

export class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === 'string' ? detail : `API request failed (${status})`)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

export async function apiRequest(path, { body, headers, ...options } = {}) {
  if (!path.startsWith('/') || path.startsWith('//')) {
    throw new TypeError('Pass an API path starting with a single slash.')
  }

  const requestHeaders = new Headers(headers)
  if (!requestHeaders.has('Accept')) requestHeaders.set('Accept', 'application/json')
  if (body !== undefined) requestHeaders.set('Content-Type', 'application/json')

  const response = await fetch(`${baseUrl}${path}`, {
    ...options,
    headers: requestHeaders,
    body: body === undefined ? undefined : JSON.stringify(body),
  })

  if (response.status === 204) return null
  const isJson = response.headers.get('content-type')?.includes('json')
  const data = isJson ? await response.json() : await response.text()
  if (!response.ok) throw new ApiError(response.status, isJson ? data?.detail : undefined)
  return data
}
