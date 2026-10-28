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
import type { TrafficPoint, TunnelProxy, User, UserIp, VpsNode } from '../api'
import { api } from '../api'
import { copyText, formatBytes, formatDateTime, formatLastSeen, formatPair } from '../lib/format'

type CopyBlockProps = {
  value: string
  label: string
  onCopied: (message: string) => void
}

function CopyIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <rect x="5.25" y="5.25" width="8.5" height="8.5" rx="1.6" stroke="currentColor" strokeWidth="1.4" />
      <path d="M10.5 5.25V3.9A1.65 1.65 0 0 0 8.85 2.25H3.9A1.65 1.65 0 0 0 2.25 3.9v4.95A1.65 1.65 0 0 0 3.9 10.5h1.35" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  )
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M3.5 8.2 6.4 11.1 12.5 4.9" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function CopyBlock({ value, label, onCopied }: CopyBlockProps) {
  const [copied, setCopied] = useState(false)

  async function copy() {
    await copyText(value)
    setCopied(true)
    onCopied(`已复制${label}`)
    window.setTimeout(() => setCopied(false), 1600)
  }

  return (
    <div className="copy-block">
      <code>{value}</code>
      <button
        className={`copy-block-btn${copied ? ' ok' : ''}`}
        type="button"
        onClick={copy}
        aria-label={`复制${label}`}
        title={copied ? '已复制' : '复制'}
      >
        {copied ? <CheckIcon /> : <CopyIcon />}
      </button>
    </div>
  )
}

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
  const [ips, setIps] = useState<UserIp[]>([])
  const [ipError, setIpError] = useState('')
  const [busy, setBusy] = useState(false)
  const [nodes, setNodes] = useState<VpsNode[]>([])
  const [tunnels, setTunnels] = useState<TunnelProxy[]>([])
  const [groupNames, setGroupNames] = useState<string[]>([])
  const [groupNodes, setGroupNodes] = useState<Record<string, string[]>>(user.group_nodes || {})

  useEffect(() => {
    setCurrent(user)
    setGroupNodes(user.group_nodes || {})
  }, [user])

  useEffect(() => {
    Promise.all([api.vps(), api.tunnels(), api.clashSettings()])
      .then(([list, tunnelList, profile]) => {
        setNodes(list)
        setTunnels(tunnelList)
        const names = (profile.settings.proxy_groups || [])
          .filter((item) => (item.proxies || []).includes('__NODE__'))
          .map((item) => item.name)
        setGroupNames(names)
      })
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    let cancelled = false
    api.traffic(current.id, granularity).then((data) => {
      if (!cancelled) setPoints(data)
    })
    return () => {
      cancelled = true
    }
  }, [current.id, granularity])

  useEffect(() => {
    let cancelled = false
    setIpError('')
    api.userIps(current.id)
      .then(async (data) => {
        if (cancelled) return
        setIps(data)
        const next = await api.user(current.id)
        if (!cancelled) onChanged(next)
      })
      .catch((err: unknown) => {
        if (!cancelled) setIpError(err instanceof Error ? err.message : '采集源 IP 失败')
      })
    return () => {
      cancelled = true
    }
  }, [current.id])

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

  function toggleNode(group: string, vpsId: string) {
    setGroupNodes((prev) => {
      const currentIds = prev[group] || []
      const next = currentIds.includes(vpsId)
        ? currentIds.filter((item) => item !== vpsId)
        : [...currentIds, vpsId]
      return { ...prev, [group]: next }
    })
  }

  async function saveNodes() {
    setBusy(true)
    try {
      const names = groupNames.length ? groupNames : Object.keys(groupNodes)
      const next = await api.saveUserNodes(
        current.id,
        names.map((name) => ({ group_name: name, vps_ids: groupNodes[name] || [] })),
      )
      setCurrent(next)
      onChanged(next)
      const pushErrors = next.replica_push_errors || []
      if (pushErrors.length) {
        toast(`节点策略已保存，但备机同步失败：${pushErrors.map((item) => item.vps_id).join('、')}`)
      } else {
        toast('节点策略已保存')
      }
    } catch (err) {
      toast(err instanceof Error ? err.message : '保存失败')
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
          <button className="icon-btn" type="button" onClick={onClose} aria-label="关闭">
            ×
          </button>
        </div>
        <div className="modal-body">
          <aside className="modal-side scroll-inset">
            <div className="meta">
              <div className="meta-kv">
                <em>状态</em>
                <span className="badge">
                  <i className={`dot ${current.enabled ? '' : 'off'}`} />
                  {current.enabled ? '允许访问' : '已禁用'}
                </span>
                <em>最近使用</em>
                <span>{formatLastSeen(current.last_seen_at)}</span>
                <em>创建时间</em>
                <span>{formatDateTime(current.created_at)}</span>
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
                <CopyBlock value={current.password} label="密码" onCopied={toast} />
              </div>
              <div>
                <em>Clash 订阅</em>
                <CopyBlock value={current.clash_subscribe_url} label="Clash 订阅地址" onCopied={toast} />
              </div>
              <div>
                <em>分享链接</em>
                <CopyBlock value={current.share_uri} label="链接" onCopied={toast} />
              </div>
            </div>
            {(nodes.length > 0 || tunnels.length > 0) && groupNames.length > 0 ? (
              <div className="group-nodes">
                <em>策略组节点</em>
                {groupNames.map((group) => (
                  <div key={group}>
                    <strong>{group}</strong>
                    <div className="node-picks">
                      {nodes.map((item) => (
                        <label key={item.id} className="node-pick">
                          <input
                            type="checkbox"
                            checked={(groupNodes[group] || []).includes(item.id)}
                            onChange={() => toggleNode(group, item.id)}
                          />
                          <span>
                            {item.name || item.id}
                            {item.hidden ? ' · 高级' : ''}
                            {item.is_local ? ' · 本机' : ''}
                          </span>
                        </label>
                      ))}
                      {tunnels.map((item) => (
                        <label key={item.assign_id} className="node-pick">
                          <input
                            type="checkbox"
                            checked={(groupNodes[group] || []).includes(item.assign_id)}
                            onChange={() => toggleNode(group, item.assign_id)}
                          />
                          <span>
                            {item.name} · 隧道 · {item.protocol.toUpperCase()} → {item.vps_name || item.vps_id}
                          </span>
                        </label>
                      ))}
                    </div>
                  </div>
                ))}
                <button className="btn primary" type="button" disabled={busy} onClick={saveNodes}>
                  保存节点
                </button>
              </div>
            ) : null}
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
                <CartesianGrid stroke="rgba(0,0,0,0.06)" vertical={false} />
                <XAxis dataKey="label" tick={{ fill: '#6e6e73', fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis tickFormatter={(v: number) => formatBytes(v)} tick={{ fill: '#6e6e73', fontSize: 11 }} width={72} axisLine={false} tickLine={false} />
                <Tooltip
                  cursor={false}
                  isAnimationActive={false}
                  allowEscapeViewBox={{ x: false, y: false }}
                  wrapperStyle={{ outline: 'none', pointerEvents: 'none', zIndex: 2 }}
                  formatter={(value) => formatBytes(Number(value ?? 0))}
                  contentStyle={{ background: '#fff', border: '0', borderRadius: 12, boxShadow: '0 8px 24px rgba(0,0,0,0.12)', color: '#1d1d1f' }}
                />
                <Legend />
                <Bar dataKey="download" name="进流量" fill="#0071e3" radius={[6, 6, 0, 0]} activeBar={false} />
                <Bar dataKey="upload" name="出流量" fill="#34c759" radius={[6, 6, 0, 0]} activeBar={false} />
              </BarChart>
            </ResponsiveContainer>
            <p className="num" style={{ color: 'var(--admin-muted)', marginTop: 12 }}>
              今日 {formatPair(current.today_download, current.today_upload)}　本月{' '}
              {formatPair(current.month_download, current.month_upload)}
            </p>
            <div className="ip-block">
              <h4>源 IP</h4>
              {ipError ? (
                <p className="error" style={{ padding: 0 }}>{ipError}</p>
              ) : ips.length === 0 ? (
                <p className="settings-hint" style={{ padding: 0 }}>暂无记录。用户连上代理后会出现在这里。</p>
              ) : (
                <div className="ip-scroll scroll-inset">
                  <table className="ip-table">
                    <thead>
                      <tr>
                        <th>IP</th>
                        <th>最近访问</th>
                      </tr>
                    </thead>
                    <tbody>
                      {ips.map((item) => (
                        <tr key={item.ip}>
                          <td className="num">{item.ip}</td>
                          <td>{formatDateTime(item.last_seen_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
