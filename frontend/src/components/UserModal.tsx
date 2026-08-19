import { useEffect, useMemo, useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { TrafficPoint, User } from '../api'
import { api } from '../api'
import { copyText, formatBytes, formatDateTime, formatLastSeen, formatPair } from '../lib/format'

type Props = {
  user: User
  onClose: () => void
  onChanged: (user: User) => void
  onDeleted: (id: number) => void
  toast: (message: string) => void
}

export function UserModal({ user, onClose, onChanged, onDeleted, toast }: Props) {
  const [current, setCurrent] = useState(user)
  const [granularity, setGranularity] = useState<'day' | 'month'>('day')
  const [points, setPoints] = useState<TrafficPoint[]>([])
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setCurrent(user)
  }, [user])

  useEffect(() => {
    let cancelled = false
    api.traffic(current.id, granularity).then((data) => {
      if (!cancelled) setPoints(data)
    })
    return () => {
      cancelled = true
    }
  }, [current.id, granularity])

  const chartData = useMemo(
    () =>
      points.map((item) => ({
        ...item,
        label: granularity === 'day' ? item.period.slice(5) : item.period,
      })),
    [points, granularity],
  )

  async function toggle() {
    setBusy(true)
    try {
      const next = await api.updateUser(current.id, { enabled: !current.enabled })
      setCurrent(next)
      onChanged(next)
      toast(next.enabled ? '已启用该用户' : '已禁用该用户')
    } catch (err) {
      toast(err instanceof Error ? err.message : '操作失败')
    } finally {
      setBusy(false)
    }
  }

  async function resetPassword() {
    if (!window.confirm('将生成新密码，旧密码会立即失效。继续？')) return
    setBusy(true)
    try {
      const next = await api.resetPassword(current.id)
      setCurrent(next)
      onChanged(next)
      toast('密码已重置')
    } catch (err) {
      toast(err instanceof Error ? err.message : '操作失败')
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    if (!window.confirm(`确认删除用户「${current.name}」？该操作不可恢复。`)) return
    setBusy(true)
    try {
      await api.deleteUser(current.id)
      onDeleted(current.id)
    } catch (err) {
      toast(err instanceof Error ? err.message : '删除失败')
      setBusy(false)
    }
  }

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <div className="modal-head">
          <h3>{current.name}</h3>
          <button className="btn ghost" type="button" onClick={onClose}>
            关闭
          </button>
        </div>
        <div className="modal-body">
          <aside className="modal-side">
            <div className="meta">
              <div>
                <em>状态</em>
                <span className="badge">
                  <i className={`dot ${current.enabled ? '' : 'off'}`} />
                  {current.enabled ? '允许访问' : '已禁用'}
                </span>
              </div>
              <div>
                <em>最近使用</em>
                <span>{formatLastSeen(current.last_seen_at)}</span>
              </div>
              <div>
                <em>创建时间</em>
                <span>{formatDateTime(current.created_at)}</span>
              </div>
              <div>
                <em>累计流量</em>
                <span className="num">{formatPair(current.total_download, current.total_upload)}</span>
              </div>
              {current.remark ? (
                <div>
                  <em>备注</em>
                  <span>{current.remark}</span>
                </div>
              ) : null}
              <div>
                <em>密码</em>
                <div className="secret">
                  <code>{current.password}</code>
                  <button className="btn" type="button" onClick={() => copyText(current.password).then(() => toast('已复制密码'))}>
                    复制
                  </button>
                </div>
              </div>
              <div>
                <em>分享链接</em>
                <div className="secret">
                  <code>{current.share_uri}</code>
                  <button className="btn" type="button" onClick={() => copyText(current.share_uri).then(() => toast('已复制链接'))}>
                    复制
                  </button>
                </div>
              </div>
            </div>
            <div className="actions">
              <button className="btn" type="button" disabled={busy} onClick={toggle}>
                {current.enabled ? '禁用访问' : '启用访问'}
              </button>
              <button className="btn" type="button" disabled={busy} onClick={resetPassword}>
                重置密码
              </button>
              <button className="btn danger" type="button" disabled={busy} onClick={remove}>
                删除用户
              </button>
            </div>
          </aside>
          <div className="modal-chart">
            <div className="tabs">
              <button className={`tab ${granularity === 'day' ? 'on' : ''}`} type="button" onClick={() => setGranularity('day')}>
                按天
              </button>
              <button className={`tab ${granularity === 'month' ? 'on' : ''}`} type="button" onClick={() => setGranularity('month')}>
                按月
              </button>
            </div>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={chartData} barGap={2}>
                <CartesianGrid stroke="#2f2c27" vertical={false} />
                <XAxis dataKey="label" tick={{ fill: '#9a9488', fontSize: 11 }} />
                <YAxis tickFormatter={(v: number) => formatBytes(v)} tick={{ fill: '#9a9488', fontSize: 11 }} width={72} />
                <Tooltip
                  formatter={(value) => formatBytes(Number(value ?? 0))}
                  contentStyle={{ background: '#191816', border: '1px solid #2f2c27', color: '#e8e4dc' }}
                />
                <Legend />
                <Bar dataKey="download" name="进流量" fill="#d4a054" radius={[2, 2, 0, 0]} />
                <Bar dataKey="upload" name="出流量" fill="#7ea37a" radius={[2, 2, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
            <p className="num" style={{ color: 'var(--admin-muted)', marginTop: 12 }}>
              今日 {formatPair(current.today_download, current.today_upload)}　本月{' '}
              {formatPair(current.month_download, current.month_upload)}
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
