import { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react'
import { getOktaClient, getIdToken, signOut as oktaSignOut, isAuthenticated as oktaIsAuthenticated } from './okta'
import { api } from './api'

export interface AuthUser {
  sub: string
  email: string
  name: string
  globalRole: 'super-admin' | null
  projectRoles: Record<string, 'project-admin' | 'read-only'>
  status: 'active' | 'invited' | 'pending'
}

interface AuthContextType {
  user: AuthUser | null
  isLoading: boolean
  isAuthenticated: boolean
  login: () => void
  logout: () => Promise<void>
  refreshUser: () => Promise<void>
}

const AuthContext = createContext<AuthContextType>({
  user: null,
  isLoading: true,
  isAuthenticated: false,
  login: () => {},
  logout: async () => {},
  refreshUser: async () => {},
})

interface AuthProviderProps {
  children: ReactNode
}

export function AuthProvider({ children }: AuthProviderProps) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  const refreshUser = useCallback(async () => {
    try {
      const token = await getIdToken()
      if (!token) {
        setUser(null)
        return
      }
      const me = await api.getMe()
      setUser(me)
    } catch (err) {
      console.error('Failed to fetch user profile:', err)
      setUser(null)
    }
  }, [])

  useEffect(() => {
    let cancelled = false

    async function checkAuth() {
      try {
        const oktaClient = getOktaClient()

        // Handle redirect callback if we're on the callback path
        if (window.location.pathname === '/login/callback') {
          setIsLoading(false)
          return
        }

        // Check if we have a valid Okta session
        const authenticated = await oktaIsAuthenticated()
        if (!authenticated) {
          // Try to restore tokens from Okta session
          try {
            const tokenResponse = await oktaClient.token.getWithoutPrompt()
            if (tokenResponse.tokens.idToken) {
              oktaClient.tokenManager.setTokens(tokenResponse.tokens)
            }
          } catch {
            // No valid Okta session
            if (!cancelled) {
              setUser(null)
              setIsLoading(false)
            }
            return
          }
        }

        if (!cancelled) {
          await refreshUser()
        }
      } catch {
        if (!cancelled) {
          setUser(null)
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false)
        }
      }
    }

    checkAuth()
    return () => { cancelled = true }
  }, [refreshUser])

  const login = useCallback(() => {
    const oktaClient = getOktaClient()
    oktaClient.signInWithRedirect()
  }, [])

  const logout = useCallback(async () => {
    setUser(null)
    await oktaSignOut()
  }, [])

  const isAuthenticated = user !== null

  return (
    <AuthContext.Provider value={{ user, isLoading, isAuthenticated, login, logout, refreshUser }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextType {
  return useContext(AuthContext)
}
