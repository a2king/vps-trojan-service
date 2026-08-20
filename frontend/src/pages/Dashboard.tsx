import { useEffect, useState } from 'react'
import type { SystemDisk, SystemInfo, VpsNode } from '../api'
import { api } from '../api'
import { formatBytes, formatDateTime, formatUptime } from '../lib/format'

function usageClass(percent: number): string {
  if (percent >= 90) return 'high'
  if (percent >= 70) return 'warn'
  return 'ok'
}

function formatLoad(value: number | null): string {
  return value == null ? '—' : value.toFixed(2)
}

function Meter({ percent, label }: { percent: number; label?: string }) {
  const value = Math.min(100, Math.max(0, percent))
  return (
    <div className="meter" title={label}>
      <span className={usageClass(value)} style={{ width: `${value}%` }} />
    </div>
  )
}

function primaryDisk(disks: SystemDisk[]): SystemDisk | null {
  return (
    disks.find((item) => item.mountpoint === '/' || /^[cC]:\\?$/.test(item.mountpoint)) ||
    disks[0] ||
    null
  )
}

export function DashboardPage() {
  const [info, setInfo] = useState<SystemInfo | null>(null)
  const [error, setError] = useState('')
  const [nodes, setNodes] = useState<VpsNode[]>([])
  const [vpsId, setVpsId] = useState('')

  async function refresh(id = vpsId) {
    const data = await api.system(id || undefined)
    setInfo(data)
  }

  useEffect(() => {
    document.title = '系统看板'
    api.vps()
      .then((list) => {
        setNodes(list)
        setVpsId((prev) => prev || list.find((item) => item.is_local)?.id || list[0]?.id || '')
      })
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    let cancelled = false
    let timer = 0
    setError('')
    refresh(vpsId)
      .then(() => {
        if (cancelled) return
        timer = window.setInterval(() => {
          refresh(vpsId).catch(() => undefined)
        }, 5000)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : '加载失败')
      })
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [vpsId])

  const disk = info ? primaryDisk(info.disks) : null
  const cores = info
    ? info.cpu.cores_physical == null
      ? `${info.cpu.cores_logical} 逻辑`
      : `${info.cpu.cores_physical} 物理 / ${info.cpu.cores_logical} 逻辑`
    : '—'
  const rows = info
    ? (
        [
          ['主机名', info.hostname],
          ['操作系统', info.os_name],
          ['内核版本', info.kernel],
          ['系统架构', info.arch],
          ['CPU 型号', info.cpu.model],
          ['CPU 核心', cores],
          info.cpu.load_1 == null
            ? null
            : ['系统负载', `${formatLoad(info.cpu.load_1)} / ${formatLoad(info.cpu.load_5)} / ${formatLoad(info.cpu.load_15)}`],
          ['运行时间', formatUptime(info.uptime_seconds)],
          ['启动时间', info.boot_time ? formatDateTime(info.boot_time) : '—'],
          ['时区', info.timezone],
          ['进程数', String(info.process_count)],
          ['IPv4 地址', info.ips.length ? info.ips.join('、') : '—'],
        ] as Array<[string, string] | null>
      ).filter((row): row is [string, string] => row != null)
    : []

  return (
    <main className="admin-main">
      {error ? <p className="error">{error}</p> : null}

      {nodes.length > 1 ? (
        <div className="tabs dash-switch">
          {nodes.map((item) => (
            <button
              key={item.id}
              className={`tab ${vpsId === item.id ? 'on' : ''}`}
              type="button"
              onClick={() => {
                setInfo(null)
                setVpsId(item.id)
              }}
            >
              {item.name || item.id}
            </button>
          ))}
        </div>
      ) : null}

      <div className="usage-row">
        <section className="panel usage-card">
          <div className="usage-head">
            <em>CPU</em>
            <strong>{info ? `${info.cpu.percent.toFixed(1)}%` : '—'}</strong>
          </div>
          <Meter percent={info?.cpu.percent ?? 0} />
          <p className="usage-meta">
            {info ? `${info.cpu.cores_logical} 逻辑核心` : '读取中'}
          </p>
        </section>

        <section className="panel usage-card">
          <div className="usage-head">
            <em>内存</em>
            <strong>{info ? `${info.memory.percent.toFixed(1)}%` : '—'}</strong>
          </div>
          <Meter percent={info?.memory.percent ?? 0} />
          <p className="usage-meta">
            {info
              ? `${formatBytes(info.memory.used)} / ${formatBytes(info.memory.total)} · 可用 ${formatBytes(info.memory.available)}`
              : '读取中'}
          </p>
          {info && info.memory.cached + info.memory.buffers > 0 ? (
            <p className="usage-meta">
              缓存 {formatBytes(info.memory.cached)} · 缓冲 {formatBytes(info.memory.buffers)}
            </p>
          ) : null}
          {info && info.memory.swap_total > 0 ? (
            <div className="swap-block">
              <div className="usage-head compact">
                <em>Swap</em>
                <strong>{info.memory.swap_percent.toFixed(1)}%</strong>
              </div>
              <Meter percent={info.memory.swap_percent} />
              <p className="usage-meta">
                {formatBytes(info.memory.swap_used)} / {formatBytes(info.memory.swap_total)}
              </p>
            </div>
          ) : null}
        </section>

        <section className="panel usage-card">
          <div className="usage-head">
            <em>磁盘</em>
            <strong>{disk ? `${disk.percent.toFixed(1)}%` : '—'}</strong>
          </div>
          <Meter percent={disk?.percent ?? 0} />
          <p className="usage-meta">
            {disk
              ? `${disk.mountpoint} · ${formatBytes(disk.used)} / ${formatBytes(disk.total)}`
              : '读取中'}
          </p>
        </section>
      </div>

      {info && info.cpu.per_core.length > 1 ? (
        <section className="panel dash-gap">
          <div className="panel-head">
            <h2>CPU 各核心</h2>
          </div>
          <div className="core-grid core-panel">
            {info.cpu.per_core.map((value, index) => (
              <div key={index} className="core-item">
                <span>核 {index + 1}</span>
                <Meter percent={value} />
                <b>{value.toFixed(0)}%</b>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <section className="panel dash-gap">
        <div className="panel-head">
          <h2>磁盘分区</h2>
        </div>
        <div className="disk-list">
          {info?.disks.length ? (
            info.disks.map((item) => (
              <div key={`${item.device}-${item.mountpoint}`} className="disk-row">
                <div className="disk-row-head">
                  <b>{item.mountpoint}</b>
                  <span>
                    {item.device} · {item.fstype || '未知'} · {item.percent.toFixed(1)}%
                  </span>
                </div>
                <Meter percent={item.percent} />
                <p className="usage-meta">
                  已用 {formatBytes(item.used)} / {formatBytes(item.total)} · 剩余 {formatBytes(item.free)}
                </p>
              </div>
            ))
          ) : (
            <p className="empty">暂无磁盘数据</p>
          )}
        </div>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>系统信息</h2>
          {info?.collected_at ? (
            <span className="usage-meta">
              更新于 {formatDateTime(info.collected_at)}
            </span>
          ) : null}
        </div>
        <div className="table-wrap">
          <table className="users info-table">
            <tbody>
              {rows.map(([label, value]) => (
                <tr key={label}>
                  <th>{label}</th>
                  <td>{value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  )
}
