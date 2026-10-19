import { useEffect, useState } from 'react'
import { Outlet, useNavigate } from 'react-router-dom'
import { api, sessionValid } from '../api'
import { AdminNav } from './AdminNav'

export function AdminLayout() {
  const navigate = useNavigate()
  const [username, setUsername] = useState(localStorage.getItem('username') || '')
  const [domain, setDomain] = useState('')

  useEffect(() => {
    if (!sessionValid()) {
      navigate('/admin/login', { replace: true })
      return
    }
    api.me()
      .then((me) => {
        setUsername(me.username)
        setDomain(me.domain)
      })
      .catch(() => {
        navigate('/admin/login', { replace: true })
      })
  }, [navigate])

  return (
    <div className="admin-shell">
      <AdminNav username={username} domain={domain} />
      <Outlet context={{ username, domain }} />
    </div>
  )
}
