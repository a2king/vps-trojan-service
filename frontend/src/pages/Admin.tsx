import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import type { Overview, User } from '../api'
import { api } from '../api'
import { UserModal } from '../components/UserModal'
import { AdminNav } from '../components/AdminNav'
import { formatLastSeen, formatPair } from '../lib/format'

export function AdminPage() {
  const navigate = useNavigate()
  const [username, setUsername] = useState(localStorage.getItem('username') || '')
  const [domain, setDomain] = useState('')
  const [overview, setOverview] = useState<Overview | null>(null)
  const [users, setUsers] = useState<User[]>([])
  const [selected, setSelected] = useState<User | null>(null)
  const [toast, setToast] = useState('')
  const [error, setError] = useState('')
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [remark, setRemark] = useState('')
  const [creating, setCreating] = useState(false)

  function notify(message: string) {
    setToast(message)
    window.setTimeout(() => setToast(''), 2200)
  }

  async function refresh() {
    const [me, stats, list] = await Promise.all([api.me(), api.overview(), api.users()])
    setUsername(me.username)
    setDomain(me.domain)
    setOverview(stats)
    setUsers(list)
  }

  useEffect(() => {
    document.title = '用户管理'
    if (!localStorage.getItem('token')) {
      navigate('/admin/login', { replace: true })
      return
    }
    refresh().catch((err: unknown) => {
      setError(err instanceof Error ? err.message : '加载失败')
    })
  }, [navigate])

  async function onCreate(event: FormEvent) {
    event.preventDefault()
    if (!name.trim()) return
    setCreating(true)
    setError('')
    try {
      const created = await api.createUser({
        name: name.trim(),
        password: password.trim() || undefined,
        remark: remark.trim(),
      })
      setUsers((prev) => [created, ...prev])
      setName('')
      setPassword('')
      setRemark('')
      notify('用户已创建')
      const stats = await api.overview()
      setOverview(stats)
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建失败')
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="admin-shell">
      <AdminNav username={username} domain={domain} />

      <main className="admin-main">
        <div className="kpi-row">
          <div className="kpi">
            <em>用户</em>
            <strong>{overview?.user_count ?? '—'}</strong>
            <small>启用 {overview?.enabled_count ?? 0}</small>
          </div>
          <div className="kpi">
            <em>今日活跃</em>
            <strong>{overview?.active_today ?? '—'}</strong>
            <small>最近使用落在今天</small>
          </div>
          <div className="kpi">
            <em>今日流量</em>
            <strong>{overview ? formatPair(overview.today_download, overview.today_upload) : '—'}</strong>
            <small>进 / 出</small>
          </div>
          <div className="kpi">
            <em>本月流量</em>
            <strong>{overview ? formatPair(overview.month_download, overview.month_upload) : '—'}</strong>
            <small>进 / 出</small>
          </div>
        </div>

        <section className="panel">
          <div className="panel-head">
            <h2>用户列表</h2>
          </div>
          <form className="create-form" onSubmit={onCreate}>
            <label className="field">
              <span>用户名</span>
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="例如 alice" />
            </label>
            <label className="field">
              <span>密码（可空，自动生成）</span>
              <input value={password} onChange={(e) => setPassword(e.target.value)} placeholder="至少 8 位" />
            </label>
            <label className="field">
              <span>备注</span>
              <input value={remark} onChange={(e) => setRemark(e.target.value)} placeholder="可选" />
            </label>
            <button className="btn primary" type="submit" disabled={creating}>
              {creating ? '创建中…' : '创建用户'}
            </button>
          </form>
          {error ? <p className="error" style={{ padding: '0 18px' }}>{error}</p> : null}
          {users.length === 0 ? (
            <div className="empty">还没有用户。创建后即可用密码连接 Trojan。</div>
          ) : (
            <div className="table-wrap">
              <table className="users">
                <thead>
                  <tr>
                    <th>用户</th>
                    <th>状态</th>
                    <th>今日进出</th>
                    <th>本月进出</th>
                    <th>最近使用</th>
                    <th>备注</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((item) => (
                    <tr key={item.id} onClick={() => setSelected(item)}>
                      <td>{item.name}</td>
                      <td>
                        <span className="badge">
                          <i className={`dot ${item.enabled ? '' : 'off'}`} />
                          {item.enabled ? '启用' : '禁用'}
                        </span>
                      </td>
                      <td className="num">{formatPair(item.today_download, item.today_upload)}</td>
                      <td className="num">{formatPair(item.month_download, item.month_upload)}</td>
                      <td>{formatLastSeen(item.last_seen_at)}</td>
                      <td>{item.remark || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </main>

      {selected ? (
        <UserModal
          user={selected}
          toast={notify}
          onClose={() => setSelected(null)}
          onChanged={(next) => {
            setSelected(next)
            setUsers((prev) => prev.map((item) => (item.id === next.id ? next : item)))
          }}
          onDeleted={(id) => {
            setUsers((prev) => prev.filter((item) => item.id !== id))
            setSelected(null)
            notify('用户已删除')
            api.overview().then(setOverview)
          }}
        />
      ) : null}
      {toast ? <div className="toast">{toast}</div> : null}
    </div>
  )
}
