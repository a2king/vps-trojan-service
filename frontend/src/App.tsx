import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AdminLayout } from './components/AdminLayout'
import { HomePage } from './pages/Home'
import { AdminPage } from './pages/Admin'
import { LoginPage } from './pages/Login'
import { RulesPage } from './pages/Rules'

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/admin/login" element={<LoginPage />} />
        <Route path="/admin" element={<AdminLayout />}>
          <Route index element={<AdminPage />} />
          <Route path="rules" element={<RulesPage />} />
        </Route>
        <Route path="/admin/*" element={<Navigate to="/admin" replace />} />
        <Route path="*" element={<HomePage />} />
      </Routes>
    </BrowserRouter>
  )
}
