import axios from 'axios'
import { useAuth } from './store'
export const api = axios.create({ baseURL: '/api', withCredentials: true })
api.interceptors.request.use((config) => {
  const token = useAuth.getState().token
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})
let refreshRequest: Promise<string> | null = null
api.interceptors.response.use((response) => response, async (error) => {
  const original = error.config
  if (error.response?.status === 401 && original && !original._retried && !original.url?.includes('/auth/')) {
    original._retried = true
    try {
      refreshRequest ??= api.post('/auth/refresh').then((res) => {
        useAuth.getState().setToken(res.data.access_token)
        return res.data.access_token as string
      }).finally(() => { refreshRequest = null })
      original.headers.Authorization = `Bearer ${await refreshRequest}`
      return api(original)
    } catch { useAuth.getState().setToken(null) }
  }
  return Promise.reject(error)
})
