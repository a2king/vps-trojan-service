import { Link, useLocation, useNavigate } from 'react-router-dom'

type Props = {
  username: string
  domain: string
}

export function AdminNav({ username, domain }: Props) {
  const location = useLocation()
  const navigate = useNavigate()
  const onDashboard = location.pathname === '/admin' || location.pathname === '/admin/'
  const onUsers = location.pathname.startsWith('/admin/users')
  const onNodes = location.pathname.startsWith('/admin/nodes')
  const onRules = location.pathname.startsWith('/admin/rules')

  function go(path: string) {
    if (location.pathname !== path) navigate(path)
  }

  function logout() {
    localStorage.removeItem('token')
    localStorage.removeItem('username')
    navigate('/admin/login', { replace: true })
  }

  return (
    <header className="admin-top">
      <div className="admin-brand">
        <b>管理</b>
        <span>{domain || '面板'}</span>
      </div>
      <nav className="admin-nav">
        <Link
          className={onDashboard ? 'on' : ''}
          to="/admin"
          onPointerDown={(event) => {
            if (event.button === 0) go('/admin')
          }}
        >
          看板
        </Link>
        <Link
          className={onUsers ? 'on' : ''}
          to="/admin/users"
          onPointerDown={(event) => {
            if (event.button === 0) go('/admin/users')
          }}
        >
          用户
        </Link>
        <Link
          className={onNodes ? 'on' : ''}
          to="/admin/nodes"
          onPointerDown={(event) => {
            if (event.button === 0) go('/admin/nodes')
          }}
        >
          节点
        </Link>
        <Link
          className={onRules ? 'on' : ''}
          to="/admin/rules"
          onPointerDown={(event) => {
            if (event.button === 0) go('/admin/rules')
          }}
        >
          规则
        </Link>
      </nav>
      <div className="admin-top-actions">
        <span>{username}</span>
        <button className="btn ghost" type="button" onClick={logout}>
          退出
        </button>
      </div>
    </header>
  )
}
