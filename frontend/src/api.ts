export type User = {
  id: number
  name: string
  password: string
  share_uri: string
  enabled: boolean
  remark: string
  created_at: string
  last_seen_at: string | null
  total_download: number
  total_upload: number
  today_download: number
  today_upload: number
  month_download: number
  month_upload: number
}

export type Overview = {
  user_count: number
  enabled_count: number
  active_today: number
  today_download: number
  today_upload: number
  month_download: number
  month_upload: number
}

export type TrafficPoint = {
  period: string
  download: number
  upload: number
}

function detailMessage(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (item && typeof item === 'object' && 'msg' in item) {
          return String((item as { msg: string }).msg)
        }
        return '参数错误'
      })
      .join('；')
  }
  return '请求失败'
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem('token')
  const headers = new Headers(init.headers)
  if (!headers.has('Content-Type') && init.body) {
    headers.set('Content-Type', 'application/json')
  }
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const res = await fetch(path, { ...init, headers })
  if (res.status === 401 && !path.includes('/api/auth/login')) {
    localStorage.removeItem('token')
    localStorage.removeItem('username')
    window.location.assign('/admin/login')
    throw new Error('未登录')
  }
  if (res.status === 204) return undefined as T
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new Error(detailMessage((body as { detail?: unknown }).detail))
  }
  return body as T
}

export const api = {
  login: (username: string, password: string) =>
    request<{ token: string; username: string }>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),
  me: () => request<{ username: string; domain: string }>('/api/auth/me'),
  overview: () => request<Overview>('/api/overview'),
  users: () => request<User[]>('/api/users'),
  createUser: (payload: { name: string; password?: string; remark?: string }) =>
    request<User>('/api/users', { method: 'POST', body: JSON.stringify(payload) }),
  updateUser: (id: number, payload: { name?: string; remark?: string; enabled?: boolean }) =>
    request<User>(`/api/users/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  resetPassword: (id: number, password?: string) =>
    request<User>(`/api/users/${id}/password`, {
      method: 'POST',
      body: JSON.stringify({ password: password || null }),
    }),
  deleteUser: (id: number) => request<void>(`/api/users/${id}`, { method: 'DELETE' }),
  traffic: (id: number, granularity: 'day' | 'month') =>
    request<TrafficPoint[]>(`/api/users/${id}/traffic?granularity=${granularity}`),
}
