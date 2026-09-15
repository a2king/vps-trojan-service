import { useEffect, useState, type FormEvent } from 'react'
import type { TunnelProxy, VpsNode } from '../api'
import { api } from '../api'
import { formatDateTime } from '../lib/format'

const PROTOCOLS = [
  { id: 'http', label: 'HTTP' },
  { id: 'https', label: 'HTTPS' },
  { id: 'socks5', label: 'SOCKS5' },
] as const

function protocolLabel(value: string): string {
  return PROTOCOLS.find((item) => item.id === value)?.label || value.toUpperCase()
}

export function TunnelsPage() {
  const [rows, setRows] = useState<TunnelProxy[]>([])
  const [nodes, setNodes] = useState<VpsNode[]>([])
  const [error, setError] = useState('')
  const [toast, setToast] = useState('')
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState<TunnelProxy | null>(null)
  const [name, setName] = useState('')
  const [protocol, setProtocol] = useState('http')
  const [server, setServer] = useState('')
  const [port, setPort] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [vpsId, setVpsId] = useState('')

  function notify(message: string) {
    setToast(message)
    window.setTimeout(() => setToast(''), 2200)
  }

  function resetForm() {
    setEditing(null)
    setName('')
    setProtocol('http')
    setServer('')
    setPort('')
    setUsername('')
    setPassword('')
    setVpsId(nodes.find((item) => item.is_local)?.id || nodes[0]?.id || '')
  }

  function fillForm(item: TunnelProxy) {
    setEditing(item)
    setName(item.name)
    setProtocol(item.protocol)
    setServer(item.server)
    setPort(String(item.port))
    setUsername(item.username)
    setPassword('')
    setVpsId(item.vps_id)
  }

  async function refresh() {
    const [list, vps] = await Promise.all([api.tunnels(), api.vps()])
    setRows(list)
    setNodes(vps)
    setVpsId((prev) => prev || vps.find((item) => item.is_local)?.id || vps[0]?.id || '')
  }

  useEffect(() => {
    document.title = '隧道代理'
    refresh().catch((err: unknown) => {
      setError(err instanceof Error ? err.message : '加载失败')
    })
  }, [])

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    if (!name.trim() || !server.trim() || !port || !username.trim() || !vpsId) return
    if (!editing && !password) return
    setBusy(true)
    setError('')
    try {
      if (editing) {
        const payload: Parameters<typeof api.updateTunnel>[1] = {
          name: name.trim(),
          protocol,
          server: server.trim(),
          port: Number(port),
          username: username.trim(),
          vps_id: vpsId,
        }
        if (password) payload.password = password
        const next = await api.updateTunnel(editing.id, payload)
        setRows((prev) => prev.map((item) => (item.id === next.id ? next : item)))
        notify('隧道代理已保存')
      } else {
        const created = await api.createTunnel({
          name: name.trim(),
          protocol,
          server: server.trim(),
          port: Number(port),
          username: username.trim(),
          password,
          vps_id: vpsId,
        })
        setRows((prev) => [created, ...prev.filter((item) => item.id !== created.id)])
        notify('隧道代理已添加，可在用户详情里勾选')
      }
      resetForm()
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败')
    } finally {
      setBusy(false)
    }
  }

  async function remove(item: TunnelProxy) {
    if (!window.confirm(`删除隧道代理「${item.name}」？已分配给用户的勾选会一并去掉。`)) return
    try {
      await api.deleteTunnel(item.id)
      setRows((prev) => prev.filter((row) => row.id !== item.id))
      if (editing?.id === item.id) resetForm()
      notify('已删除')
    } catch (err) {
      notify(err instanceof Error ? err.message : '删除失败')
    }
  }

  return (
    <>
      <main className="admin-main">
        <section className="panel">
          <div className="panel-head">
            <h2>隧道代理</h2>
          </div>
          <p className="settings-hint" style={{ padding: '0 20px 8px' }}>
            把家用 HTTP / HTTPS / SOCKS5 写进来，客户端会先连指定 VPS，再由 VPS 跳转到该代理。添加后可在用户详情里当节点勾选。
          </p>
          <form className="create-form nodes-form" onSubmit={onSubmit}>
            <label className="field">
              <span>名称</span>
              <input value={name} onChange={(e) => setName(e.target.value)} placeholder="hk" />
            </label>
            <label className="field">
              <span>协议</span>
              <select value={protocol} onChange={(e) => setProtocol(e.target.value)}>
                {PROTOCOLS.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>服务器</span>
              <input value={server} onChange={(e) => setServer(e.target.value)} placeholder="149.119.180.248" />
            </label>
            <label className="field">
              <span>端口</span>
              <input value={port} onChange={(e) => setPort(e.target.value)} placeholder="34896" />
            </label>
            <label className="field">
              <span>用户名</span>
              <input value={username} onChange={(e) => setUsername(e.target.value)} />
            </label>
            <label className="field">
              <span>{editing ? '密码（不改请留空）' : '密码'}</span>
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
            </label>
            <label className="field">
              <span>跳转节点</span>
              <select value={vpsId} onChange={(e) => setVpsId(e.target.value)}>
                {nodes.length === 0 ? <option value="">暂无节点</option> : null}
                {nodes.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name || item.id}
                    {item.is_local ? ' · 本机' : ''}
                  </option>
                ))}
              </select>
            </label>
            <div className="field" style={{ display: 'flex', gap: 8, alignItems: 'end' }}>
              <button className="btn primary" type="submit" disabled={busy || !nodes.length}>
                {busy ? '保存中…' : editing ? '保存修改' : '添加隧道'}
              </button>
              {editing ? (
                <button className="btn ghost" type="button" onClick={resetForm}>
                  取消
                </button>
              ) : null}
            </div>
          </form>
          {error ? <p className="error" style={{ padding: '0 18px' }}>{error}</p> : null}
          {rows.length === 0 ? (
            <div className="empty">还没有隧道代理。填入家用代理和跳转 VPS 后，就能在用户里勾选。</div>
          ) : (
            <div className="table-wrap">
              <table className="users">
                <thead>
                  <tr>
                    <th>名称</th>
                    <th>协议</th>
                    <th>服务器</th>
                    <th>用户名</th>
                    <th>跳转节点</th>
                    <th>创建时间</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((item) => (
                    <tr key={item.id} className="rule-row">
                      <td>{item.name}</td>
                      <td>{protocolLabel(item.protocol)}</td>
                      <td className="num">{item.server}:{item.port}</td>
                      <td className="num">{item.username}</td>
                      <td>{item.vps_name || item.vps_id}</td>
                      <td>{item.created_at ? formatDateTime(item.created_at) : '—'}</td>
                      <td>
                        <div className="rule-actions">
                          <button className="btn ghost" type="button" onClick={() => fillForm(item)}>
                            编辑
                          </button>
                          <button className="btn danger" type="button" onClick={() => remove(item)}>
                            删除
                          </button>
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
      {toast ? <div className="toast">{toast}</div> : null}
    </>
  )
}
