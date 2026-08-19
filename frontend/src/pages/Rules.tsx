import { useEffect, useMemo, useState, type ChangeEvent, type FormEvent } from 'react'
import { useOutletContext } from 'react-router-dom'
import type { ClashImportPreview, ClashRule, ClashSettings } from '../api'
import { api } from '../api'

type AdminContext = { username: string; domain: string }

function policyOf(line: string): string {
  const parts = line.split(',').map((item) => item.trim()).filter(Boolean)
  if (parts.length < 2) return ''
  if (parts[parts.length - 1].toLowerCase() === 'no-resolve' && parts.length >= 3) {
    return parts[parts.length - 2]
  }
  return parts[parts.length - 1]
}

function policyClass(policy: string): string {
  const value = policy.toUpperCase()
  if (value === 'DIRECT') return 'direct'
  if (value === 'REJECT') return 'reject'
  if (value.includes('AI')) return 'ai'
  if (value.includes('VIDEO')) return 'video'
  return 'proxy'
}

export function RulesPage() {
  const { domain } = useOutletContext<AdminContext>()
  const [rules, setRules] = useState<ClashRule[]>([])
  const [settings, setSettings] = useState<ClashSettings | null>(null)
  const [query, setQuery] = useState('')
  const [newLine, setNewLine] = useState('')
  const [nodeName, setNodeName] = useState('')
  const [mixedPort, setMixedPort] = useState(7890)
  const [socksPort, setSocksPort] = useState(7891)
  const [bypass, setBypass] = useState('')
  const [error, setError] = useState('')
  const [toast, setToast] = useState('')
  const [busy, setBusy] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [importText, setImportText] = useState('')
  const [preview, setPreview] = useState<ClashImportPreview | null>(null)
  const [selected, setSelected] = useState<Record<string, boolean>>({})
  const [parsing, setParsing] = useState(false)

  function notify(message: string) {
    setToast(message)
    window.setTimeout(() => setToast(''), 2200)
  }

  async function refresh() {
    const [list, profile] = await Promise.all([api.clashRules(), api.clashSettings()])
    setRules(list)
    setSettings(profile.settings)
    setNodeName(String(profile.settings.node_name || ''))
    setMixedPort(Number(profile.settings.mixed_port || 7890))
    setSocksPort(Number(profile.settings.socks_port || 7891))
    setBypass((profile.settings.cfw_bypass || []).join('\n'))
  }

  useEffect(() => {
    document.title = '规则'
    refresh().catch((err: unknown) => {
      setError(err instanceof Error ? err.message : '加载失败')
    })
  }, [])

  const filtered = useMemo(() => {
    const keyword = query.trim().toLowerCase()
    return rules
      .map((item, index) => ({ item, index }))
      .filter(({ item }) => !keyword || item.line.toLowerCase().includes(keyword))
  }, [rules, query])

  async function onCreate(event: FormEvent) {
    event.preventDefault()
    if (!newLine.trim()) return
    setBusy(true)
    setError('')
    try {
      const created = await api.createClashRule(newLine.trim())
      setRules((prev) => {
        const matchIndex = prev.findIndex((item) => item.line.toUpperCase().startsWith('MATCH,'))
        if (matchIndex === -1) return [...prev, created]
        return [...prev.slice(0, matchIndex), created, ...prev.slice(matchIndex)]
      })
      setNewLine('')
      notify('规则已添加')
    } catch (err) {
      setError(err instanceof Error ? err.message : '添加失败')
    } finally {
      setBusy(false)
    }
  }

  async function onDelete(id: number) {
    if (!window.confirm('删除这条规则？')) return
    setBusy(true)
    try {
      await api.deleteClashRule(id)
      setRules((prev) => prev.filter((item) => item.id !== id))
      notify('规则已删除')
    } catch (err) {
      notify(err instanceof Error ? err.message : '删除失败')
    } finally {
      setBusy(false)
    }
  }

  async function move(index: number, delta: number) {
    const nextIndex = index + delta
    if (nextIndex < 0 || nextIndex >= rules.length) return
    const next = [...rules]
    const [item] = next.splice(index, 1)
    next.splice(nextIndex, 0, item)
    setRules(next)
    try {
      const sorted = await api.reorderClashRules(next.map((row) => row.id))
      setRules(sorted)
    } catch (err) {
      notify(err instanceof Error ? err.message : '排序失败')
      refresh().catch(() => undefined)
    }
  }

  async function saveSettings() {
    if (!settings) return
    setBusy(true)
    try {
      const saved = await api.saveClashSettings({
        ...settings,
        node_name: nodeName.trim(),
        mixed_port: mixedPort,
        socks_port: socksPort,
        cfw_bypass: bypass
          .split('\n')
          .map((item) => item.trim())
          .filter(Boolean),
      })
      setSettings(saved.settings)
      notify('客户端参数已保存')
    } catch (err) {
      notify(err instanceof Error ? err.message : '保存失败')
    } finally {
      setBusy(false)
    }
  }

  async function parseImport() {
    if (!importText.trim()) return
    setParsing(true)
    setError('')
    try {
      const result = await api.previewClashImport(importText)
      setPreview(result)
      const next: Record<string, boolean> = {}
      for (const item of result.new_rules) next[item.line] = true
      setSelected(next)
    } catch (err) {
      notify(err instanceof Error ? err.message : '解析失败')
    } finally {
      setParsing(false)
    }
  }

  async function onPickFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (!file) return
    const text = await file.text()
    setImportText(text)
    setPreview(null)
  }

  async function confirmImport() {
    if (!preview) return
    const lines = preview.new_rules.map((item) => item.line).filter((line) => selected[line])
    if (lines.length === 0) {
      notify('请至少勾选一条规则')
      return
    }
    setBusy(true)
    try {
      await api.confirmClashImport(lines)
      await refresh()
      setImportOpen(false)
      setImportText('')
      setPreview(null)
      notify(`已添加 ${lines.length} 条规则`)
    } catch (err) {
      notify(err instanceof Error ? err.message : '导入失败')
    } finally {
      setBusy(false)
    }
  }

  const selectedCount = preview ? preview.new_rules.filter((item) => selected[item.line]).length : 0
  const allSelected = Boolean(preview && preview.new_rules.length > 0 && selectedCount === preview.new_rules.length)

  return (
    <>
      <main className="admin-main">
        <p className="page-kicker">{domain || '节点'}</p>
        <h1 className="page-title">规则</h1>
        <section className="panel">
          <div className="panel-head">
            <h2>客户端参数</h2>
            <button className="btn primary" type="button" disabled={busy} onClick={saveSettings}>
              保存参数
            </button>
          </div>
          <div className="settings-grid">
            <label className="field">
              <span>节点名称</span>
              <input value={nodeName} onChange={(e) => setNodeName(e.target.value)} placeholder="例如 vps-jp" />
            </label>
            <label className="field">
              <span>mixed-port</span>
              <input type="number" value={mixedPort} onChange={(e) => setMixedPort(Number(e.target.value))} />
            </label>
            <label className="field">
              <span>socks-port</span>
              <input type="number" value={socksPort} onChange={(e) => setSocksPort(Number(e.target.value))} />
            </label>
            <label className="field settings-span">
              <span>cfw-bypass（每行一条）</span>
              <textarea rows={5} value={bypass} onChange={(e) => setBypass(e.target.value)} />
            </label>
          </div>
          {settings?.proxy_groups?.length ? (
            <p className="settings-hint">
              策略组：{settings.proxy_groups.map((item) => item.name).join(' / ')}
              。导入规则时若出现新策略名，会自动补一个指向本节点的 select 组。
            </p>
          ) : null}
        </section>

        <section className="panel" style={{ marginTop: 18 }}>
          <div className="panel-head">
            <h2>规则列表 · {filtered.length}/{rules.length}</h2>
            <button className="btn" type="button" onClick={() => { setImportOpen(true); setPreview(null) }}>
              导入 Clash 配置
            </button>
          </div>
          <form className="create-form rules-form" onSubmit={onCreate}>
            <label className="field">
              <span>搜索</span>
              <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="域名 / 策略 / 关键字" />
            </label>
            <label className="field">
              <span>新增规则</span>
              <input
                value={newLine}
                onChange={(e) => setNewLine(e.target.value)}
                placeholder="DOMAIN-SUFFIX,example.com,PROXY"
              />
            </label>
            <button className="btn primary" type="submit" disabled={busy}>
              添加
            </button>
          </form>
          {error ? <p className="error" style={{ padding: '0 18px' }}>{error}</p> : null}
          {filtered.length === 0 ? (
            <div className="empty">没有匹配的规则。</div>
          ) : (
            <div className="table-wrap rules-table">
              <table className="users">
                <thead>
                  <tr>
                    <th style={{ width: 52 }}>#</th>
                    <th>规则</th>
                    <th style={{ width: 120 }}>策略</th>
                    <th style={{ width: 168 }}>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map(({ item, index }) => {
                    const policy = policyOf(item.line)
                    return (
                      <tr key={item.id} className="rule-row">
                        <td className="num">{index + 1}</td>
                        <td>
                          <code className="rule-line">{item.line}</code>
                        </td>
                        <td>
                          <span className={`rule-policy ${policyClass(policy)}`}>{policy || '—'}</span>
                        </td>
                        <td>
                          <div className="rule-actions">
                            <button className="btn ghost" type="button" disabled={index <= 0} onClick={() => move(index, -1)}>
                              上移
                            </button>
                            <button
                              className="btn ghost"
                              type="button"
                              disabled={index < 0 || index >= rules.length - 1}
                              onClick={() => move(index, 1)}
                            >
                              下移
                            </button>
                            <button className="btn danger" type="button" disabled={busy} onClick={() => onDelete(item.id)}>
                              删除
                            </button>
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </main>

      {importOpen ? (
        <div className="modal-mask" onClick={() => setImportOpen(false)}>
          <div className="modal import-modal" onClick={(event) => event.stopPropagation()}>
            <div className="modal-head">
              <h3>导入配置</h3>
              <button className="icon-btn" type="button" onClick={() => setImportOpen(false)} aria-label="关闭">
                ×
              </button>
            </div>
            <div className="import-body">
              <label className="field">
                <span>上传 YAML 或粘贴全文</span>
                <input type="file" accept=".yml,.yaml,.txt" onChange={onPickFile} />
              </label>
              <textarea
                rows={10}
                value={importText}
                onChange={(e) => { setImportText(e.target.value); setPreview(null) }}
                placeholder="paste clash.yaml here"
              />
              <div className="import-actions">
                <button className="btn primary" type="button" disabled={parsing || !importText.trim()} onClick={parseImport}>
                  {parsing ? '解析中…' : '解析新增规则'}
                </button>
                {preview ? (
                  <span className="settings-hint" style={{ margin: 0 }}>
                    共 {preview.total} 条，已有 {preview.duplicated} 条，新增 {preview.new_rules.length} 条
                    {preview.invalid.length ? `，无法识别 ${preview.invalid.length} 条` : ''}
                  </span>
                ) : null}
              </div>
              {preview ? (
                preview.new_rules.length === 0 ? (
                  <div className="empty">没有新增规则，全部已存在。</div>
                ) : (
                  <>
                    <label className="check-all">
                      <input
                        type="checkbox"
                        checked={allSelected}
                        onChange={(e) => {
                          const next: Record<string, boolean> = {}
                          for (const item of preview.new_rules) next[item.line] = e.target.checked
                          setSelected(next)
                        }}
                      />
                      全选新增规则（已选 {selectedCount}/{preview.new_rules.length}）
                    </label>
                    <div className="import-list">
                      {preview.new_rules.map((item) => (
                        <label key={item.line} className="import-item">
                          <input
                            type="checkbox"
                            checked={Boolean(selected[item.line])}
                            onChange={(e) => setSelected((prev) => ({ ...prev, [item.line]: e.target.checked }))}
                          />
                          <code>{item.line}</code>
                          <span className={`rule-policy ${policyClass(policyOf(item.line))}`}>{policyOf(item.line)}</span>
                        </label>
                      ))}
                    </div>
                    <button className="btn primary" type="button" disabled={busy} onClick={confirmImport}>
                      添加选中规则
                    </button>
                  </>
                )
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
      {toast ? <div className="toast">{toast}</div> : null}
    </>
  )
}
