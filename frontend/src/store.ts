import { create } from 'zustand'
type State = { token: string | null; setToken: (token: string | null) => void }
export const useAuth = create<State>((set) => ({ token: localStorage.getItem('access'), setToken: (token) => {
  token ? localStorage.setItem('access', token) : localStorage.removeItem('access')
  set({ token })
} }))
