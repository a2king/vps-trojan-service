export type User = {
  id: number
  name: string
  password: string
  share_uri: string
  clash_subscribe_url: string
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
  group_nodes: Record<string, string[]>
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

export type UserIp = {
  ip: string
  last_seen_at: string
}

export type ClashRule = {
  id: number
  line: string
  sort_order: number
}

export type SystemCpu = {
  percent: number
  per_core: number[]
  model: string
  cores_physical: number | null
  cores_logical: number
  load_1: number | null
  load_5: number | null
  load_15: number | null
}

export type SystemMemory = {
  total: number
  used: number
  available: number
  free: number
  cached: number
  buffers: number
  percent: number
  swap_total: number
  swap_used: number
  swap_percent: number
}

export type SystemDisk = {
  device: string
  mountpoint: string
  fstype: string
  total: number
  used: number
  free: number
  percent: number
}

export type SystemInfo = {
  hostname: string
  os_name: string
  kernel: string
  arch: string
  timezone: string
  boot_time: string | null
  uptime_seconds: number
  collected_at: string | null
  process_count: number
  ips: string[]
  cpu: SystemCpu
  memory: SystemMemory
  disks: SystemDisk[]
}

export type ClashSettings = {
  node_name?: string
  mixed_port?: number
  socks_port?: number
  allow_lan?: boolean
  bind_address?: string
  mode?: string
  log_level?: string
  external_controller?: string
  udp?: boolean
  alpn?: string[]
  dns?: Record<string, unknown>
  proxy_groups?: { name: string; type: string; proxies: string[] }[]
  cfw_bypass?: string[]
  cfw_latency_timeout?: number
}

export type VpsNode = {
  id: string
  name: string
  ssh_host: string
  ssh_port: number
  ssh_user: string
  is_local: boolean
  hidden: boolean
  status: string
  last_error: string
  created_at: string
}

export type VpsLog = {
  id: string
  status: string
  log: string
}

export type TunnelProxy = {
  id: number
  assign_id: string
  name: string
  protocol: 'http' | 'https' | 'socks5' | string
  server: string
  port: number
  username: string
  vps_id: string
  vps_name: string
  created_at: string
}

export type ClashImportPreview = {
  total: number
  duplicated: number
  invalid: string[]
  new_rules: { line: string; selected: boolean }[]
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
  system: (vpsId?: string) =>
    request<SystemInfo>(`/api/system${vpsId ? `?vps_id=${encodeURIComponent(vpsId)}` : ''}`),
  vps: () => request<VpsNode[]>('/api/vps'),
  createVps: (payload: {
    id: string
    ssh_host: string
    ssh_port?: number
    ssh_user?: string
    ssh_password: string
    name?: string
    hidden?: boolean
  }) => request<VpsNode>('/api/vps', { method: 'POST', body: JSON.stringify(payload) }),
  updateVps: (id: string, payload: { name?: string; hidden?: boolean }) =>
    request<VpsNode>(`/api/vps/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteVps: (id: string) => request<void>(`/api/vps/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  vpsLogs: (id: string) => request<VpsLog>(`/api/vps/${encodeURIComponent(id)}/logs`),
  redeployVps: (id: string, sshPassword?: string) =>
    request<VpsNode>(`/api/vps/${encodeURIComponent(id)}/redeploy`, {
      method: 'POST',
      body: JSON.stringify({ ssh_password: sshPassword || '' }),
    }),
  saveUserNodes: (id: number, groups: { group_name: string; vps_ids: string[] }[]) =>
    request<User>(`/api/users/${id}/nodes`, { method: 'PUT', body: JSON.stringify({ groups }) }),
  users: () => request<User[]>('/api/users'),
  user: (id: number) => request<User>(`/api/users/${id}`),
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
  userIps: (id: number) => request<UserIp[]>(`/api/users/${id}/ips`),
  clashSettings: () => request<{ settings: ClashSettings }>('/api/clash/settings'),
  saveClashSettings: (settings: ClashSettings) =>
    request<{ settings: ClashSettings }>('/api/clash/settings', {
      method: 'PUT',
      body: JSON.stringify({ settings }),
    }),
  clashRules: (q = '') =>
    request<ClashRule[]>(`/api/clash/rules${q ? `?q=${encodeURIComponent(q)}` : ''}`),
  createClashRule: (line: string) =>
    request<ClashRule>('/api/clash/rules', { method: 'POST', body: JSON.stringify({ line }) }),
  updateClashRule: (id: number, line: string) =>
    request<ClashRule>(`/api/clash/rules/${id}`, { method: 'PATCH', body: JSON.stringify({ line }) }),
  deleteClashRule: (id: number) => request<void>(`/api/clash/rules/${id}`, { method: 'DELETE' }),
  reorderClashRules: (ids: number[]) =>
    request<ClashRule[]>('/api/clash/rules/reorder', { method: 'POST', body: JSON.stringify({ ids }) }),
  previewClashImport: (yaml: string) =>
    request<ClashImportPreview>('/api/clash/rules/import/preview', {
      method: 'POST',
      body: JSON.stringify({ yaml }),
    }),
  confirmClashImport: (rules: string[]) =>
    request<ClashRule[]>('/api/clash/rules/import', { method: 'POST', body: JSON.stringify({ rules }) }),
  tunnels: () => request<TunnelProxy[]>('/api/tunnels'),
  createTunnel: (payload: {
    name: string
    protocol: string
    server: string
    port: number
    username: string
    password: string
    vps_id: string
  }) => request<TunnelProxy>('/api/tunnels', { method: 'POST', body: JSON.stringify(payload) }),
  updateTunnel: (
    id: number,
    payload: {
      name?: string
      protocol?: string
      server?: string
      port?: number
      username?: string
      password?: string
      vps_id?: string
    },
  ) => request<TunnelProxy>(`/api/tunnels/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  deleteTunnel: (id: number) => request<void>(`/api/tunnels/${id}`, { method: 'DELETE' }),
}
