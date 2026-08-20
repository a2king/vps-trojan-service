import { useEffect, useState, type FormEvent } from 'react'
import type { VpsNode } from '../api'
import { api } from '../api'
import { formatDateTime } from '../lib/format'

function statusLabel(status: string): string {
  if (status === 'online') return '在线'
  if (status === 'installing') return '安装中'
  if (status === 'error') return '失败'
  return '待处理'
}

export function NodesPage() {
  const [nodes, setNodes] = useState<VpsNode[]>([])
  const [error, setError] = useState('')
  const [toast, setToast] = useState('')
  const [busy, setBusy] = useState(false)
  const [domain, setDomain] = useState('')
  const [name, setName] = useState('')
  const [sshHost, setSshHost] = useState('')
  const [sshPort, setSshPort] = useState('22')
  const [sshUser, setSshUser] = useState('root')
  const [sshPassword, setSshPassword] = useState('')
  const [hidden, setHidden] = useState(false)
  const [logNode, setLogNode] = useState<VpsNode | null>(null)
  const [logText, setLogText] = useState('')
  const [logStatus, setLogStatus] = useState('')

  function notify(message: string) {
    setToast(message)
    window.setTimeout(() => setToast(''), 2200)
  }

  async function refresh() {
    const list = await api.vps()
    setNodes(list)
  }

  useEffect(() => {
    document.title = '节点'
    refresh().catch((err: unknown) => {
      setError(err instanceof Error ? err.message : '加载失败')
    })
  }, [])

  useEffect(() => {
    if (!logNode) return undefined
    let cancelled = false
    async function load() {
      if (!logNode) return
      try {
        const data = await api.vpsLogs(logNode.id)
        if (cancelled) return
        setLogText(data.log)
        setLogStatus(data.status)
        setNodes((prev) => prev.map((item) => (item.id === data.id ? { ...item, status: data.status } : item)))
      } catch {
        return
      }
    }
    load()
    const timer = window.setInterval(load, 1500)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [logNode])

  async function onCreate(event: FormEvent) {
    event.preventDefault()
    if (!domain.trim() || !sshHost.trim() || !sshPassword) return
    setBusy(true)
    setError('')
    try {
      const created = await api.createVps({
        id: domain.trim(),
        ssh_host: sshHost.trim(),
        ssh_port: Number(sshPort) || 22,
        ssh_user: sshUser.trim() || 'root',
        ssh_password: sshPassword,
        name: name.trim(),
        hidden,
      })
      setNodes((prev) => [...prev.filter((item) => item.id !== created.id), created])
      setDomain('')
      setName('')
      setSshHost('')
      setSshPort('22')
      setSshUser('root')
      setSshPassword('')
      setHidden(false)
      setLogNode(created)
      notify('已开始远程安装')
    } catch (err) {
      setError(err instanceof Error ? err.message : '添加失败')
    } finally {
      setBusy(false)
    }
  }

  async function toggleHidden(item: VpsNode) {
    try {
      const next = await api.updateVps(item.id, { hidden: !item.hidden })
      setNodes((prev) => prev.map((row) => (row.id === next.id ? next : row)))
    } catch (err) {
      notify(err instanceof Error ? err.message : '更新失败')
    }
  }

  async function saveName(item: VpsNode, value: string) {
    const nameValue = value.trim()
    if (!nameValue || nameValue === item.name) return
    try {
      const next = await api.updateVps(item.id, { name: nameValue })
      setNodes((prev) => prev.map((row) => (row.id === next.id ? next : row)))
      notify('节点名称已保存')
    } catch (err) {
      notify(err instanceof Error ? err.message : '保存失败')
    }
  }

  async function remove(item: VpsNode) {
    if (item.is_local) return
    if (!window.confirm(`删除节点「${item.id}」？远端 Trojan 不会自动卸载。`)) return
    try {
      await api.deleteVps(item.id)
      setNodes((prev) => prev.filter((row) => row.id !== item.id))
      if (logNode?.id === item.id) setLogNode(null)
      notify('节点已删除')
    } catch (err) {
      notify(err instanceof Error ? err.message : '删除失败')
    }
  }

  return (
    <>
      <main className="admin-main">
        <section className="panel">
          <div className="panel-head">
            <h2>节点</h2>
          </div>
          <form className="create-form nodes-form" onSubmit={onCreate}>
            <label className="field">
              <span>域名（节点 ID）</span>
              <input value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="jp.example.com" />
            </label>
            <label className="field">
              <span>展示名</span>
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="可选，默认取域名前缀" />
            </label>
            <label className="field">
              <span>SSH IP</span>
              <input value={sshHost} onChange={(e) => setSshHost(e.target.value)} placeholder="1.2.3.4" />
            </label>
            <label className="field">
              <span>SSH 端口</span>
              <input value={sshPort} onChange={(e) => setSshPort(e.target.value)} />
            </label>
            <label className="field">
              <span>SSH 用户</span>
              <input value={sshUser} onChange={(e) => setSshUser(e.target.value)} />
            </label>
            <label className="field">
              <span>root 密码（只用于写入公钥）</span>
              <input type="password" value={sshPassword} onChange={(e) => setSshPassword(e.target.value)} />
            </label>
            <label className="check-all" style={{ paddingBottom: 8 }}>
              <input type="checkbox" checked={hidden} onChange={(e) => setHidden(e.target.checked)} />
              高级节点（新用户默认不分配）
            </label>
            <button className="btn primary" type="submit" disabled={busy}>
              {busy ? '提交中…' : '添加并安装'}
            </button>
          </form>
          {error ? <p className="error" style={{ padding: '0 18px' }}>{error}</p> : null}
          {nodes.length === 0 ? (
            <div className="empty">还没有节点。本机节点会在后端启动后自动出现。</div>
          ) : (
            <div className="table-wrap">
              <table className="users">
                <thead>
                  <tr>
                    <th>域名</th>
                    <th>名称</th>
                    <th>SSH</th>
                    <th>类型</th>
                    <th>状态</th>
                    <th>创建时间</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {nodes.map((item) => (
                    <tr key={item.id} className="rule-row">
                      <td className="num">{item.id}</td>
                      <td>
                        <input
                          className="inline-input"
                          defaultValue={item.name}
                          onBlur={(event) => saveName(item, event.target.value)}
                        />
                      </td>
                      <td className="num">{item.is_local ? '本机' : `${item.ssh_user}@${item.ssh_host}:${item.ssh_port}`}</td>
                      <td>{item.is_local ? '主控' : item.hidden ? '高级' : '普通'}</td>
                      <td>
                        <span className="badge">
                          <i className={`dot ${item.status === 'online' ? '' : item.status === 'error' ? 'off' : 'warn'}`} />
                          {statusLabel(item.status)}
                        </span>
                      </td>
                      <td>{item.created_at ? formatDateTime(item.created_at) : '—'}</td>
                      <td>
                        <div className="rule-actions">
                          {item.is_local ? null : (
                            <button className="btn ghost" type="button" onClick={() => setLogNode(item)}>
                              日志
                            </button>
                          )}
                          <button className="btn ghost" type="button" onClick={() => toggleHidden(item)}>
                            {item.hidden ? '取消隐藏' : '设为高级'}
                          </button>
                          {item.is_local ? null : (
                            <button className="btn danger" type="button" onClick={() => remove(item)}>
                              删除
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </main>

      {logNode ? (
        <div className="modal-mask" onClick={() => setLogNode(null)}>
          <div className="modal import-modal" onClick={(event) => event.stopPropagation()}>
            <div className="modal-head">
              <h3>{logNode.id} · {statusLabel(logStatus || logNode.status)}</h3>
              <button className="icon-btn" type="button" onClick={() => setLogNode(null)} aria-label="关闭">
                ×
              </button>
            </div>
            <div className="import-body">
              {logNode.last_error && (logStatus || logNode.status) === 'error' ? (
                <p className="error">{logNode.last_error}</p>
              ) : null}
              <pre className="install-log">{logText || '暂无日志'}</pre>
            </div>
          </div>
        </div>
      ) : null}
      {toast ? <div className="toast">{toast}</div> : null}
    </>
  )
}
