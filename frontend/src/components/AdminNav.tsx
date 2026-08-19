import { Link, useLocation, useNavigate } from 'react-router-dom'

type Props = {
  username: string
  domain: string
}

export function AdminNav({ username, domain }: Props) {
  const location = useLocation()
  const navigate = useNavigate()

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
        <Link className={location.pathname === '/admin' ? 'on' : ''} to="/admin">
          用户
        </Link>
        <Link className={location.pathname.startsWith('/admin/rules') ? 'on' : ''} to="/admin/rules">
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
