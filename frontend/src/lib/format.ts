const UNITS = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'] as const

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  const exp = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), UNITS.length - 1)
  const value = bytes / 1024 ** exp
  const digits = value >= 100 || exp === 0 ? 0 : value >= 10 ? 1 : 2
  return `${value.toFixed(digits)} ${UNITS[exp]}`
}

export function formatPair(down: number, up: number): string {
  return `↓ ${formatBytes(down)}  ↑ ${formatBytes(up)}`
}

export function formatLastSeen(iso: string | null): string {
  if (!iso) return '从未使用'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '从未使用'
  const diff = Date.now() - date.getTime()
  if (diff < 60_000) return '刚刚'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function formatUptime(seconds: number): string {
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (days > 0) return `${days} 天 ${hours} 小时 ${minutes} 分钟`
  if (hours > 0) return `${hours} 小时 ${minutes} 分钟`
  return `${Math.max(0, minutes)} 分钟`
}

export async function copyText(text: string): Promise<void> {
  await navigator.clipboard.writeText(text)
}
