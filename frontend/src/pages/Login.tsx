import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, sessionValid } from '../api'

const EXPIRE_OPTIONS = [
  { hours: 24, label: '24 小时' },
  { hours: 168, label: '7 天' },
  { hours: 720, label: '30 天' },
]

export function LoginPage() {
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [expireHours, setExpireHours] = useState(24)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    document.title = '管理登录'
    if (sessionValid()) navigate('/admin', { replace: true })
  }, [navigate])

  async function onSubmit(event: FormEvent) {
    event.preventDefault()
    setError('')
    setLoading(true)
    try {
      const result = await api.login(username.trim(), password, expireHours)
      localStorage.setItem('token', result.token)
      localStorage.setItem('username', result.username)
      localStorage.setItem('token_expires_at', String(Date.now() + result.expire_hours * 3600 * 1000))
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
        <div className="field">
          <span>登录有效期</span>
          <div className="expire-choices">
            {EXPIRE_OPTIONS.map((item) => (
              <button
                key={item.hours}
                type="button"
                className={item.hours === expireHours ? 'on' : ''}
                onClick={() => setExpireHours(item.hours)}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>
        <button className="btn primary" type="submit" disabled={loading} style={{ width: '100%' }}>
          {loading ? '登录中…' : '登录'}
        </button>
      </form>
    </div>
  )
}
