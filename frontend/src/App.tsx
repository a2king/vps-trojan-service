import { lazy, Suspense, type ReactNode } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { HomePage } from './pages/Home'

const LoginPage = lazy(() => import('./pages/Login').then((mod) => ({ default: mod.LoginPage })))
const AdminPage = lazy(() => import('./pages/Admin').then((mod) => ({ default: mod.AdminPage })))
const RulesPage = lazy(() => import('./pages/Rules').then((mod) => ({ default: mod.RulesPage })))

function Boot({ children }: { children: ReactNode }) {
  return <Suspense fallback={<div className="login-page">加载中…</div>}>{children}</Suspense>
}

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route
          path="/admin/login"
          element={
            <Boot>
              <LoginPage />
            </Boot>
          }
        />
        <Route
          path="/admin"
          element={
            <Boot>
              <AdminPage />
            </Boot>
          }
        />
        <Route
          path="/admin/rules"
          element={
            <Boot>
              <RulesPage />
            </Boot>
          }
        />
        <Route path="/admin/*" element={<Navigate to="/admin" replace />} />
        <Route path="*" element={<HomePage />} />
      </Routes>
    </BrowserRouter>
  )
}

