import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'

export function LoginPage() {
  const navigate = useNavigate()
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    document.title = '管理登录'
    if (localStorage.getItem('token')) navigate('/admin', { replace: true })
  }, [navigate])

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    setError('')
    setLoading(true)
    try {
      const result = await api.login(username.trim(), password)
      localStorage.setItem('token', result.token)
      localStorage.setItem('username', result.username)
      navigate('/admin', { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : '登录失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={onSubmit}>
        <h1>登录</h1>
        <p>使用管理员账号继续。</p>
        {error ? <p className="error">{error}</p> : null}
        <label className="field">
          <span>用户名</span>
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
        </label>
        <label className="field">
          <span>密码</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
          />
        </label>
        <button className="btn primary" type="submit" disabled={loading} style={{ width: '100%' }}>
          {loading ? '登录中…' : '登录'}
        </button>
      </form>
    </div>
  )
}
