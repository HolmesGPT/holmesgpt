# Okta Authentication & RBAC Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace username/password auth with Okta OIDC (PKCE) and add DynamoDB-backed RBAC with super-admin, project-admin, and read-only roles.

**Architecture:** Frontend uses `@okta/okta-auth-js` for PKCE flow, sends JWT as Bearer token. Backend validates JWT against Okta JWKS, loads RBAC from DynamoDB. Single Okta group gates login; all role assignments stored in DynamoDB.

**Tech Stack:** React 18, TypeScript, @okta/okta-auth-js, FastAPI, PyJWT, DynamoDB, python-jose[cryptography]

---

## File Structure

### New files to create

| File | Purpose |
|------|---------|
| `frontend/src/lib/okta.ts` | OktaAuth client initialization and token helpers |
| `frontend/src/components/OktaLoginPage.tsx` | Okta login page with "Sign in with Okta" button |
| `frontend/src/components/OktaCallback.tsx` | Handles Okta redirect callback, exchanges code for tokens |
| `frontend/src/components/PendingAccess.tsx` | "Pending access" page for users with no roles |
| `frontend/src/components/Users.tsx` | User management page (super-admin only) |
| `frontend/src/lib/auth.ts` | Auth context provider, useAuth hook, permission helpers |
| `frontend/src/lib/permissions.ts` | Permission checking utilities (canEdit, canInvestigate, etc.) |
| `frontend/okta_jwt.py` | Backend JWT validation (JWKS fetching, signature verification, claims extraction) |
| `frontend/rbac.py` | RBAC middleware, DynamoDB user/role CRUD, permission checking |
| `frontend/users_api.py` | User management API endpoints (list users, assign roles, invite by email) |
| `tests/test_okta_jwt.py` | Unit tests for JWT validation |
| `tests/test_rbac.py` | Unit tests for RBAC logic |
| `tests/test_users_api.py` | Unit tests for user management endpoints |

### Files to modify

| File | Changes |
|------|---------|
| `frontend/package.json` | Add `@okta/okta-auth-js` dependency |
| `frontend/src/App.tsx` | Replace auth flow with Okta, add role-based routing |
| `frontend/src/lib/api.ts` | Replace cookie auth with Bearer token, add user/role endpoints |
| `frontend/src/components/Layout.tsx` | Conditional navigation based on roles |
| `frontend/server_frontend.py` | Replace AuthMiddleware with OktaJWTMiddleware, remove old auth endpoints, add user management routes |
| `frontend/projects.py` | Add project deletion cleanup for role assignments |
| `pyproject.toml` | Add `python-jose[cryptography]` dependency |

---

## Task 1: Install Okta frontend dependency

**File:** `frontend/package.json`

### Steps

- [ ] **1.1** Install the Okta auth library:

```bash
cd frontend && npm install @okta/okta-auth-js
```

- [ ] **1.2** Verify `package.json` was updated. The `dependencies` section should now include:

```json
{
  "dependencies": {
    "@okta/okta-auth-js": "^7.9.0",
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-markdown": "^9.0.1",
    "react-syntax-highlighter": "^15.6.1",
    "recharts": "^3.8.0",
    "remark-gfm": "^4.0.0"
  }
}
```

- [ ] **1.3** Commit:

```bash
git add frontend/package.json frontend/package-lock.json
git commit -s --no-verify -m "feat: add @okta/okta-auth-js dependency for OIDC PKCE auth"
```

---

## Task 2: Create Okta client initialization (`frontend/src/lib/okta.ts`)

**New file:** `frontend/src/lib/okta.ts`

### Steps

- [ ] **2.1** Create `frontend/src/lib/okta.ts` with the following content:

```typescript
import { OktaAuth } from '@okta/okta-auth-js'

const ISSUER = import.meta.env.VITE_OKTA_ISSUER as string
const CLIENT_ID = import.meta.env.VITE_OKTA_CLIENT_ID as string

if (!ISSUER || !CLIENT_ID) {
  console.error(
    'Missing Okta configuration. Set VITE_OKTA_ISSUER and VITE_OKTA_CLIENT_ID environment variables.'
  )
}

const oktaAuth = new OktaAuth({
  issuer: ISSUER,
  clientId: CLIENT_ID,
  redirectUri: `${window.location.origin}/login/callback`,
  scopes: ['openid', 'profile', 'email', 'groups'],
  pkce: true,
  responseType: 'code',
  tokenManager: {
    storage: 'memory',
  },
})

export function getOktaClient(): OktaAuth {
  return oktaAuth
}

export async function getIdToken(): Promise<string | null> {
  try {
    const tokenManager = oktaAuth.tokenManager
    const idToken = await tokenManager.get('idToken')
    if (idToken && 'idToken' in idToken) {
      return idToken.idToken
    }
    return null
  } catch {
    return null
  }
}

export async function isAuthenticated(): Promise<boolean> {
  try {
    const idToken = await oktaAuth.tokenManager.get('idToken')
    if (!idToken || !('expiresAt' in idToken)) {
      return false
    }
    // Check if token is expired (with 60s buffer)
    const now = Math.floor(Date.now() / 1000)
    return idToken.expiresAt > now + 60
  } catch {
    return false
  }
}

export async function signOut(): Promise<void> {
  try {
    await oktaAuth.signOut({
      postLogoutRedirectUri: window.location.origin,
    })
  } catch {
    // If Okta signOut fails, clear tokens locally
    oktaAuth.tokenManager.clear()
    window.location.href = '/'
  }
}
```

- [ ] **2.2** Commit:

```bash
git add frontend/src/lib/okta.ts
git commit -s --no-verify -m "feat: add OktaAuth client initialization with PKCE and memory-only token storage"
```

---

## Task 3: Create auth context and hooks (`frontend/src/lib/auth.ts`)

**New file:** `frontend/src/lib/auth.ts`

### Steps

- [ ] **3.1** Create `frontend/src/lib/auth.ts` with the following content:

```typescript
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
```

- [ ] **3.2** Commit:

```bash
git add frontend/src/lib/auth.ts
git commit -s --no-verify -m "feat: add AuthProvider context with Okta token management and user profile loading"
```

---

## Task 4: Create permission utilities (`frontend/src/lib/permissions.ts`)

**New file:** `frontend/src/lib/permissions.ts`

### Steps

- [ ] **4.1** Create `frontend/src/lib/permissions.ts` with the following content:

```typescript
import type { AuthUser } from './auth'

export function isSuperAdmin(user: AuthUser): boolean {
  return user.globalRole === 'super-admin'
}

export function hasProjectAccess(user: AuthUser, projectId: string): boolean {
  if (isSuperAdmin(user)) return true
  return projectId in user.projectRoles
}

export function isProjectAdmin(user: AuthUser, projectId: string): boolean {
  if (isSuperAdmin(user)) return true
  return user.projectRoles[projectId] === 'project-admin'
}

export function isReadOnly(user: AuthUser, projectId: string): boolean {
  return user.projectRoles[projectId] === 'read-only'
}

export function canEditProject(user: AuthUser, projectId: string): boolean {
  if (isSuperAdmin(user)) return true
  return user.projectRoles[projectId] === 'project-admin'
}

export function canInvestigate(user: AuthUser, projectId: string): boolean {
  if (isSuperAdmin(user)) return true
  return projectId in user.projectRoles
}

export function canManageIntegrations(user: AuthUser, projectId: string): boolean {
  if (isSuperAdmin(user)) return true
  return user.projectRoles[projectId] === 'project-admin'
}

export function canManageUsers(user: AuthUser): boolean {
  return isSuperAdmin(user)
}

export function canManageSettings(user: AuthUser): boolean {
  return isSuperAdmin(user)
}

export function canCreateProjects(user: AuthUser): boolean {
  return isSuperAdmin(user)
}

export function canDeleteProjects(user: AuthUser): boolean {
  return isSuperAdmin(user)
}

/**
 * Returns project IDs the user has any role on.
 * Empty array for super-admin means "all projects" (caller must handle this).
 */
export function getAccessibleProjectIds(user: AuthUser): string[] {
  if (isSuperAdmin(user)) return []
  return Object.keys(user.projectRoles)
}

/**
 * Returns true if the user has project-admin on ANY project.
 * Used for showing "Configure" nav section.
 */
export function isProjectAdminOnAny(user: AuthUser): boolean {
  if (isSuperAdmin(user)) return true
  return Object.values(user.projectRoles).some((role) => role === 'project-admin')
}

/**
 * Returns true if the user has any project access at all.
 */
export function hasAnyProjectAccess(user: AuthUser): boolean {
  if (isSuperAdmin(user)) return true
  return Object.keys(user.projectRoles).length > 0
}
```

- [ ] **4.2** Commit:

```bash
git add frontend/src/lib/permissions.ts
git commit -s --no-verify -m "feat: add permission checking utilities for RBAC role resolution"
```

---

## Task 5: Create Okta login page (`frontend/src/components/OktaLoginPage.tsx`)

**New file:** `frontend/src/components/OktaLoginPage.tsx`

### Steps

- [ ] **5.1** Create `frontend/src/components/OktaLoginPage.tsx` with the following content:

```tsx
import { useState } from 'react'
import { useAuth } from '../lib/auth'

export default function OktaLoginPage() {
  const { login } = useAuth()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleLogin = () => {
    setError('')
    setLoading(true)
    try {
      login()
    } catch {
      setError('Failed to initiate login. Please try again.')
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-pdi-indigo flex items-center justify-center px-4 relative overflow-hidden">
      {/* Radial gradient from top */}
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top,_#1226aa33_0%,_transparent_60%)]" />
      {/* Radial gradient from bottom-right */}
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_bottom_right,_#29b5e820_0%,_transparent_50%)]" />
      {/* Grid pattern */}
      <div className="absolute inset-0 opacity-[0.03] bg-[linear-gradient(#29b5e8_1px,transparent_1px),linear-gradient(to_right,#29b5e8_1px,transparent_1px)] bg-[size:40px_40px]" />

      <div className="relative z-10 w-full max-w-sm">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-br from-pdi-sky to-pdi-ocean mb-4 shadow-lg shadow-pdi-sky/30">
            <svg className="w-8 h-8 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
          </div>
          <h1 className="text-white text-2xl font-bold">HolmesGPT</h1>
          <p className="text-pdi-sky text-sm font-medium mt-1">PDI Technologies</p>
        </div>

        {/* Login card */}
        <div className="bg-white rounded-xl p-6 shadow-2xl">
          <h2 className="text-pdi-granite font-semibold text-lg mb-2">Welcome</h2>
          <p className="text-pdi-slate text-sm mb-4">Sign in to access the AI operations platform.</p>

          <div className="h-px bg-gradient-to-r from-transparent via-pdi-cool-gray to-transparent mb-4" />

          {error && (
            <div className="bg-pdi-orange/10 text-pdi-orange text-sm px-3 py-2 rounded-lg mb-4">
              {error}
            </div>
          )}

          <button
            onClick={handleLogin}
            disabled={loading}
            className="w-full bg-pdi-sky text-white font-semibold py-2.5 rounded-lg hover:bg-pdi-ocean active:scale-[0.98] transition-all disabled:opacity-50 flex items-center justify-center gap-2"
          >
            {loading ? (
              <>
                <svg className="animate-spin h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                </svg>
                Redirecting to Okta...
              </>
            ) : (
              <>
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                </svg>
                Sign in with Okta
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **5.2** Commit:

```bash
git add frontend/src/components/OktaLoginPage.tsx
git commit -s --no-verify -m "feat: add Okta login page with PKCE redirect flow"
```

---

## Task 6: Create Okta callback page (`frontend/src/components/OktaCallback.tsx`)

**New file:** `frontend/src/components/OktaCallback.tsx`

### Steps

- [ ] **6.1** Create `frontend/src/components/OktaCallback.tsx` with the following content:

```tsx
import { useEffect, useState } from 'react'
import { getOktaClient } from '../lib/okta'
import { useAuth } from '../lib/auth'

export default function OktaCallback() {
  const { refreshUser } = useAuth()
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    async function handleCallback() {
      try {
        const oktaClient = getOktaClient()
        const tokenResponse = await oktaClient.token.parseFromUrl()

        if (tokenResponse.tokens) {
          oktaClient.tokenManager.setTokens(tokenResponse.tokens)
        }

        if (!cancelled) {
          await refreshUser()
          // Navigate to main app - replace history so back button doesn't return to callback
          window.history.replaceState(null, '', '/')
          // Force a re-render by reloading (the AuthProvider will pick up the new tokens)
          window.location.replace('/')
        }
      } catch (err) {
        console.error('Okta callback error:', err)
        if (!cancelled) {
          setError(
            err instanceof Error
              ? err.message
              : 'Authentication failed. Please try again.'
          )
        }
      }
    }

    handleCallback()
    return () => { cancelled = true }
  }, [refreshUser])

  if (error) {
    return (
      <div className="min-h-screen bg-pdi-indigo flex items-center justify-center px-4 relative overflow-hidden">
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top,_#1226aa33_0%,_transparent_60%)]" />
        <div className="relative z-10 w-full max-w-sm">
          <div className="bg-white rounded-xl p-6 shadow-2xl text-center">
            {/* Error icon */}
            <div className="inline-flex items-center justify-center w-12 h-12 rounded-full bg-pdi-orange/10 mb-4">
              <svg className="w-6 h-6 text-pdi-orange" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
            </div>
            <h2 className="text-pdi-granite font-semibold text-lg mb-2">Authentication Failed</h2>
            <p className="text-pdi-slate text-sm mb-4">{error}</p>
            <button
              onClick={() => { window.location.href = '/' }}
              className="w-full bg-pdi-sky text-white font-semibold py-2.5 rounded-lg hover:bg-pdi-ocean active:scale-[0.98] transition-all"
            >
              Try Again
            </button>
          </div>
        </div>
      </div>
    )
  }

  // Loading state while processing callback
  return (
    <div className="min-h-screen bg-pdi-indigo flex items-center justify-center px-4 relative overflow-hidden">
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top,_#1226aa33_0%,_transparent_60%)]" />
      <div className="relative z-10 text-center">
        <svg className="animate-spin h-8 w-8 text-pdi-sky mx-auto mb-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
        </svg>
        <p className="text-white/70 text-sm">Completing sign-in...</p>
      </div>
    </div>
  )
}
```

- [ ] **6.2** Commit:

```bash
git add frontend/src/components/OktaCallback.tsx
git commit -s --no-verify -m "feat: add Okta callback handler for PKCE code exchange"
```

---

## Task 7: Create pending access page (`frontend/src/components/PendingAccess.tsx`)

**New file:** `frontend/src/components/PendingAccess.tsx`

### Steps

- [ ] **7.1** Create `frontend/src/components/PendingAccess.tsx` with the following content:

```tsx
import { useAuth } from '../lib/auth'

export default function PendingAccess() {
  const { user, logout } = useAuth()

  return (
    <div className="min-h-screen bg-pdi-indigo flex items-center justify-center px-4 relative overflow-hidden">
      {/* Radial gradient from top */}
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top,_#1226aa33_0%,_transparent_60%)]" />
      {/* Radial gradient from bottom-right */}
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_bottom_right,_#29b5e820_0%,_transparent_50%)]" />
      {/* Grid pattern */}
      <div className="absolute inset-0 opacity-[0.03] bg-[linear-gradient(#29b5e8_1px,transparent_1px),linear-gradient(to_right,#29b5e8_1px,transparent_1px)] bg-[size:40px_40px]" />

      <div className="relative z-10 w-full max-w-sm">
        <div className="bg-white rounded-xl p-6 shadow-2xl text-center">
          {/* Lock icon */}
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-full bg-pdi-sky/10 mb-4">
            <svg className="w-7 h-7 text-pdi-sky" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
            </svg>
          </div>

          <h2 className="text-pdi-granite font-semibold text-lg mb-2">Access Pending</h2>
          <p className="text-pdi-slate text-sm mb-1">
            Your account has been created but you don't have access to any projects yet.
          </p>
          <p className="text-pdi-slate text-sm mb-6">
            Please contact an administrator to get access.
          </p>

          {user?.email && (
            <div className="bg-gray-50 rounded-lg px-3 py-2 mb-4">
              <p className="text-xs text-pdi-slate">Signed in as</p>
              <p className="text-sm font-medium text-pdi-granite truncate">{user.email}</p>
            </div>
          )}

          <button
            onClick={logout}
            className="w-full border border-pdi-cool-gray text-pdi-slate font-medium py-2 rounded-lg hover:bg-gray-50 active:scale-[0.98] transition-all text-sm"
          >
            Sign out
          </button>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **7.2** Commit:

```bash
git add frontend/src/components/PendingAccess.tsx
git commit -s --no-verify -m "feat: add PendingAccess page for authenticated users with no role assignments"
```

---


## Task 8: Update API client (`frontend/src/lib/api.ts`)

**Modified file:** `frontend/src/lib/api.ts`

### Steps

- [ ] **8.1** Replace the entire `frontend/src/lib/api.ts`. Key changes from the current file:

**Remove** these functions from the `api` object:
- `login(username, password)` -- no longer needed
- `logout()` -- replaced by Okta signout
- `checkAuth()` -- replaced by Okta token check

**Change** the `request()` function:
- Remove `credentials: 'include'` (no more cookies)
- Import `getIdToken` from `./okta`
- Add `Authorization: Bearer <token>` header using `getIdToken()`

**Change** the `chatStream()` and `investigateStream()` functions:
- Remove `credentials: 'include'`
- Add `Authorization: Bearer <token>` header

**Add** new user management types and endpoints:

```typescript
// Add at top of file:
import { getIdToken } from './okta'
import type { AuthUser } from './auth'

// Replace the request() function:
async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const token = await getIdToken()

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options?.headers as Record<string, string> ?? {}),
  }

  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const res = await fetch(`${BASE}${path}`, {
    ...options,
    headers,
  });

  if (res.status === 401) {
    window.location.href = '/'
    throw new Error('Unauthorized');
  }

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status}: ${text}`);
  }

  return res.json();
}
```

**Add** a `streamFetch()` helper for streaming endpoints (replaces inline `credentials: 'include'`):

```typescript
async function streamFetch(path: string, body: unknown, signal?: AbortSignal): Promise<Response> {
  const token = await getIdToken()
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
    signal,
  })

  if (res.status === 401) {
    window.location.href = '/'
    throw new Error('Unauthorized')
  }
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`${res.status}: ${text}`)
  }
  return res
}
```

**Use `streamFetch` in `chatStream()`:**

```typescript
async chatStream(data: ChatRequest, onChunk: (text: string) => void, signal?: AbortSignal): Promise<void> {
  const res = await streamFetch('/api/chat', { ...data, stream: true }, signal)
  if (!res.body) throw new Error('No response body');
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    onChunk(decoder.decode(value, { stream: true }));
  }
},
```

**Use `streamFetch` in `investigateStream()`:**

```typescript
async investigateStream(data: InvestigateRequest): Promise<InvestigateResponse> {
  const res = await streamFetch('/api/investigate', data)
  if (!res.body) throw new Error('No response body');
  // ... rest of SSE parsing logic unchanged ...
},
```

**Add** new types before the `api` object:

```typescript
export interface UserRecord {
  sub: string
  email: string
  name: string | null
  global_role: 'super-admin' | null
  status: 'active' | 'invited' | 'pending'
  created_at: string
  last_login: string | null
}

export interface ProjectRoleAssignment {
  project_id: string
  role: 'project-admin' | 'read-only'
  assigned_by: string
  assigned_at: string
}

export interface UserWithRoles extends UserRecord {
  project_roles: Record<string, ProjectRoleAssignment>
}
```

**Add** new endpoints to the `api` object:

```typescript
// Auth
getMe(): Promise<AuthUser> {
  return request('/api/auth/me')
},

// Users (super-admin only)
getUsers(): Promise<UserWithRoles[]> {
  return request('/api/users')
},

inviteUser(email: string): Promise<UserRecord> {
  return request('/api/users/invite', {
    method: 'POST',
    body: JSON.stringify({ email }),
  })
},

updateUserGlobalRole(userId: string, role: 'super-admin' | null): Promise<UserRecord> {
  return request(`/api/users/${encodeURIComponent(userId)}/global-role`, {
    method: 'PUT',
    body: JSON.stringify({ role }),
  })
},

updateUserProjectRole(userId: string, projectId: string, role: 'project-admin' | 'read-only' | null): Promise<void> {
  return request(`/api/users/${encodeURIComponent(userId)}/projects/${encodeURIComponent(projectId)}/role`, {
    method: 'PUT',
    body: JSON.stringify({ role }),
  })
},

deleteUser(userId: string): Promise<{ ok: boolean }> {
  return request(`/api/users/${encodeURIComponent(userId)}`, {
    method: 'DELETE',
  })
},
```

**Keep** all existing project/investigation/integration/instance/webhook/settings endpoints unchanged (only the auth mechanism changes, not the API signatures).

- [ ] **8.2** Commit:

```bash
git add frontend/src/lib/api.ts
git commit -s --no-verify -m "feat: replace cookie-based auth with Bearer token, add user management API endpoints"
```

---


## Task 9: Update App.tsx with Okta auth flow

**Modified file:** `frontend/src/App.tsx`

### Steps

- [ ] **9.1** Replace the entire `frontend/src/App.tsx` with the following content:

```tsx
import { useState, useEffect } from 'react'
import Layout from './components/Layout'
import Chat from './components/Chat'
import Investigate from './components/Investigate'
import InvestigationHistory from './components/InvestigationHistory'
import Integrations from './components/Integrations'
import Settings from './components/Settings'
import Projects from './components/Projects'
import Instances from './components/Instances'
import Analytics from './components/Analytics'
import Docs from './components/Docs'
import Users from './components/Users'
import OktaLoginPage from './components/OktaLoginPage'
import OktaCallback from './components/OktaCallback'
import PendingAccess from './components/PendingAccess'
import { AuthProvider, useAuth } from './lib/auth'
import { hasAnyProjectAccess, isSuperAdmin, getAccessibleProjectIds } from './lib/permissions'
import { useProject } from './hooks/useProject'

export type Page = 'chat' | 'investigate' | 'history' | 'analytics' | 'integrations' | 'instances' | 'settings' | 'projects' | 'docs' | 'users'

function AppContent() {
  const [page, setPage] = useState<Page>('chat')
  const { user, isLoading, isAuthenticated, logout } = useAuth()
  const { projects, selectedProjectId, selectedProject, selectProject, reloadProjects } = useProject()

  // Handle callback route
  if (window.location.pathname === '/login/callback') {
    return <OktaCallback />
  }

  // Loading state
  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-screen bg-gray-50">
        <div className="animate-pulse text-pdi-slate text-lg">Loading...</div>
      </div>
    )
  }

  // Not authenticated -- show Okta login
  if (!isAuthenticated || !user) {
    return <OktaLoginPage />
  }

  // Authenticated but no roles -- show pending access
  if (!hasAnyProjectAccess(user)) {
    return <PendingAccess />
  }

  // Filter projects based on user permissions
  const accessibleProjectIds = getAccessibleProjectIds(user)
  const filteredProjects = isSuperAdmin(user)
    ? projects
    : projects.filter((p) => accessibleProjectIds.includes(p.id))

  return (
    <Layout
      currentPage={page}
      onNavigate={setPage}
      onLogout={async () => {
        await logout()
      }}
      user={user}
      projects={filteredProjects}
      selectedProjectId={selectedProjectId}
      selectedProject={selectedProject}
      onSelectProject={selectProject}
    >
      <div key={page} className="page-transition h-full">
        {page === 'chat' && <Chat projectId={selectedProjectId} />}
        {page === 'investigate' && <Investigate projectId={selectedProjectId} selectedProject={selectedProject} />}
        {page === 'history' && <InvestigationHistory />}
        {page === 'analytics' && <Analytics />}
        {page === 'integrations' && <Integrations />}
        {page === 'settings' && <Settings />}
        {page === 'projects' && <Projects projects={filteredProjects} onReload={reloadProjects} />}
        {page === 'instances' && <Instances selectedProjectId={selectedProjectId} />}
        {page === 'docs' && <Docs />}
        {page === 'users' && <Users />}
      </div>
    </Layout>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <AppInner />
    </AuthProvider>
  )
}

function AppInner() {
  const { isAuthenticated } = useAuth()
  const { reloadProjects } = useProject()

  // Load projects once authentication is confirmed
  useEffect(() => {
    if (isAuthenticated) {
      reloadProjects()
    }
  }, [isAuthenticated]) // eslint-disable-line react-hooks/exhaustive-deps

  return <AppContent />
}
```

- [ ] **9.2** Commit:

```bash
git add frontend/src/App.tsx
git commit -s --no-verify -m "feat: replace username/password auth flow with Okta OIDC, add role-based routing"
```

---
## Task 10: Update Layout.tsx with role-based navigation

**Modified file:** `frontend/src/components/Layout.tsx`

### Steps

- [ ] **10.1** Modify `frontend/src/components/Layout.tsx` with these changes:

**Add** imports at top:
```typescript
import type { AuthUser } from '../lib/auth'
import { isSuperAdmin, isProjectAdminOnAny, canManageSettings, canManageUsers } from '../lib/permissions'
```

**Update** `LayoutProps` -- add `user: AuthUser`, remove `onLogout` return type change:
```typescript
interface LayoutProps {
  currentPage: Page
  onNavigate: (page: Page) => void
  onLogout: () => void
  user: AuthUser          // NEW: user profile for role checks
  children: ReactNode
  projects: Project[]
  selectedProjectId: string | null
  selectedProject: Project | null
  onSelectProject: (id: string | null) => void
}
```

**Replace** the static `navSections` constant with a function that builds sections based on user role:

```typescript
function getNavSections(user: AuthUser): { label: string; items: { page: Page; label: string; icon: string }[] }[] {
  const sections: { label: string; items: { page: Page; label: string; icon: string }[] }[] = []

  // Workspace -- always visible
  sections.push({
    label: 'Workspace',
    items: [
      { page: 'chat', label: 'Chat', icon: 'M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z' },
      { page: 'investigate', label: 'Investigate', icon: 'M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z' },
      { page: 'history', label: 'History', icon: 'M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2' },
      { page: 'analytics', label: 'Analytics', icon: '...' },  // keep existing icon path
    ],
  })

  // Configure -- super-admin or project-admin only
  const configureItems: { page: Page; label: string; icon: string }[] = []
  if (isSuperAdmin(user) || isProjectAdminOnAny(user)) {
    configureItems.push(
      { page: 'integrations', label: 'Integrations', icon: '...' },
      { page: 'instances', label: 'Instances', icon: '...' },
    )
  }
  if (isSuperAdmin(user)) {
    configureItems.push({ page: 'projects', label: 'Projects', icon: '...' })
  }
  if (configureItems.length > 0) {
    sections.push({ label: 'Configure', items: configureItems })
  }

  // System
  const systemItems: { page: Page; label: string; icon: string }[] = [
    { page: 'docs', label: 'Docs', icon: '...' },
  ]
  if (canManageSettings(user)) {
    systemItems.push({ page: 'settings', label: 'Settings', icon: '...' })
  }
  if (canManageUsers(user)) {
    systemItems.push({
      page: 'users',
      label: 'Users',
      icon: 'M15 19.128a9.38 9.38 0 002.625.372 9.337 9.337 0 004.121-.952 4.125 4.125 0 00-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 018.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0111.964-3.07M12 6.375a3.375 3.375 0 11-6.75 0 3.375 3.375 0 016.75 0zm8.25 2.25a2.625 2.625 0 11-5.25 0 2.625 2.625 0 015.25 0z',
    })
  }
  sections.push({ label: 'System', items: systemItems })

  return sections
}
```

> **Note:** Keep the exact SVG icon path strings from the existing Layout.tsx. The `'...'` placeholders above mean "copy the existing icon string." Only the Users icon is new (shown in full).

**Update** the component body:
- Add `user` to destructured props
- Call `const navSections = getNavSections(user)` at the top of the component body
- **Profile footer** -- replace hardcoded "Admin" / "PDI Technologies":

```tsx
{/* Derive initials */}
const initials = user.name
  ? user.name.split(' ').map((n) => n[0]).join('').toUpperCase().slice(0, 2)
  : user.email[0].toUpperCase()

{/* In the avatar */}
<span className="text-white text-xs font-bold">{initials}</span>

{/* Name and subtitle */}
<p className="text-white/80 text-xs font-medium truncate">{user.name || user.email}</p>
<p className="text-white/30 text-[10px] truncate">
  {user.globalRole === 'super-admin' ? 'Super Admin' : user.email}
</p>
```

- [ ] **10.2** Commit:

```bash
git add frontend/src/components/Layout.tsx
git commit -s --no-verify -m "feat: add role-based navigation visibility and user profile display"
```

---

## Task 11: Backend JWT validation module (`frontend/okta_jwt.py`)

**New file:** `frontend/okta_jwt.py`

### Steps

- [ ] **11.1** Create `frontend/okta_jwt.py` with the following content:

```python
"""
Okta JWT validation for HolmesGPT frontend.

Validates ID tokens issued by Okta using JWKS (JSON Web Key Set).
Keys are fetched from the Okta OpenID Connect discovery endpoint
and cached in memory with a 24-hour TTL.
"""

import logging
import os
import threading
import time

import requests
from fastapi import HTTPException
from jose import JWTError, jwt

logger = logging.getLogger(__name__)

OKTA_ISSUER = os.environ.get("OKTA_ISSUER", "")
OKTA_CLIENT_ID = os.environ.get("OKTA_CLIENT_ID", "")
OKTA_REQUIRED_GROUP = os.environ.get("OKTA_REQUIRED_GROUP", "HolmesGPT-Users")

# JWKS cache TTL: 24 hours
_JWKS_CACHE_TTL = 86400


class JWKSClient:
    """Fetches and caches JWKS keys from the Okta discovery endpoint."""

    def __init__(self, issuer: str):
        self._issuer = issuer
        self._keys: list[dict] = []
        self._fetched_at: float = 0
        self._lock = threading.Lock()
        self._jwks_uri: str = ""

    def _discover_jwks_uri(self) -> str:
        """Fetch the JWKS URI from the OpenID Connect discovery document."""
        if self._jwks_uri:
            return self._jwks_uri
        discovery_url = f"{self._issuer}/.well-known/openid-configuration"
        resp = requests.get(discovery_url, timeout=10)
        resp.raise_for_status()
        self._jwks_uri = resp.json()["jwks_uri"]
        return self._jwks_uri

    def get_keys(self) -> list[dict]:
        """Return cached JWKS keys, refreshing if stale."""
        now = time.time()
        if self._keys and (now - self._fetched_at) < _JWKS_CACHE_TTL:
            return self._keys

        with self._lock:
            # Double-check after acquiring lock
            if self._keys and (time.time() - self._fetched_at) < _JWKS_CACHE_TTL:
                return self._keys

            try:
                jwks_uri = self._discover_jwks_uri()
                resp = requests.get(jwks_uri, timeout=10)
                resp.raise_for_status()
                self._keys = resp.json().get("keys", [])
                self._fetched_at = time.time()
                logger.info("Refreshed JWKS keys from %s (%d keys)", jwks_uri, len(self._keys))
            except Exception:
                logger.exception("Failed to fetch JWKS keys")
                if not self._keys:
                    raise
                # Use stale keys if refresh fails
                logger.warning("Using stale JWKS keys (age: %.0fs)", now - self._fetched_at)

            return self._keys

    def force_refresh(self) -> None:
        """Force a JWKS key refresh (e.g., after a key-not-found error)."""
        self._fetched_at = 0
        self.get_keys()


# Module-level singleton -- initialized lazily
_jwks_client: JWKSClient | None = None


def _get_jwks_client() -> JWKSClient:
    global _jwks_client
    if _jwks_client is None:
        if not OKTA_ISSUER:
            raise HTTPException(
                status_code=500,
                detail="OKTA_ISSUER environment variable not set",
            )
        _jwks_client = JWKSClient(OKTA_ISSUER)
    return _jwks_client


def validate_okta_token(token: str, issuer: str = "", client_id: str = "") -> dict:
    """
    Validate an Okta ID token and return its claims.

    Args:
        token: The raw JWT string
        issuer: Expected issuer (defaults to OKTA_ISSUER env var)
        client_id: Expected audience (defaults to OKTA_CLIENT_ID env var)

    Returns:
        Dict with claims: sub, email, name, groups

    Raises:
        HTTPException(401) on any validation failure
    """
    iss = issuer or OKTA_ISSUER
    aud = client_id or OKTA_CLIENT_ID

    if not iss or not aud:
        raise HTTPException(
            status_code=500,
            detail="Okta configuration missing: OKTA_ISSUER and OKTA_CLIENT_ID required",
        )

    client = _get_jwks_client()
    keys = client.get_keys()

    try:
        claims = jwt.decode(
            token,
            {"keys": keys},
            algorithms=["RS256"],
            audience=aud,
            issuer=iss,
            options={
                "verify_aud": True,
                "verify_iss": True,
                "verify_exp": True,
                "verify_iat": True,
            },
        )
    except JWTError as e:
        error_str = str(e)
        # If the key wasn't found, try refreshing JWKS (Okta may have rotated keys)
        if "signature" in error_str.lower() or "key" in error_str.lower():
            logger.info("JWT validation failed, refreshing JWKS keys and retrying")
            client.force_refresh()
            keys = client.get_keys()
            try:
                claims = jwt.decode(
                    token,
                    {"keys": keys},
                    algorithms=["RS256"],
                    audience=aud,
                    issuer=iss,
                    options={
                        "verify_aud": True,
                        "verify_iss": True,
                        "verify_exp": True,
                        "verify_iat": True,
                    },
                )
            except JWTError:
                logger.warning("JWT validation failed after JWKS refresh: %s", e)
                raise HTTPException(status_code=401, detail="Invalid token")
        else:
            logger.warning("JWT validation failed: %s", e)
            raise HTTPException(status_code=401, detail="Invalid token")

    # Verify required group membership
    groups = claims.get("groups", [])
    if OKTA_REQUIRED_GROUP and OKTA_REQUIRED_GROUP not in groups:
        logger.warning(
            "User %s not in required group '%s' (groups: %s)",
            claims.get("email", "unknown"),
            OKTA_REQUIRED_GROUP,
            groups,
        )
        raise HTTPException(
            status_code=403,
            detail=f"User is not a member of the required group: {OKTA_REQUIRED_GROUP}",
        )

    return {
        "sub": claims.get("sub", ""),
        "email": claims.get("email", ""),
        "name": claims.get("name", ""),
        "groups": groups,
    }
```

- [ ] **11.2** Commit:

```bash
git add frontend/okta_jwt.py
git commit -s --no-verify -m "feat: add Okta JWT validation with JWKS caching and group membership check"
```

---


## Task 12: Backend RBAC module (`frontend/rbac.py`)

**New file:** `frontend/rbac.py`

### Steps

- [ ] **12.1** Create `frontend/rbac.py`. This is the core RBAC module with DynamoDB operations. It follows the same single-table patterns as `frontend/projects.py` (same `_get_table()`, same `pk`/`sk` pattern).

**Data models:**

```python
class UserRecord(BaseModel):
    sub: str
    email: str
    name: Optional[str] = None
    global_role: Optional[str] = None  # "super-admin" or None
    status: str = "active"  # "active", "invited", "pending"
    created_at: str = ""
    last_login: Optional[str] = None

class ProjectRole(BaseModel):
    project_id: str
    role: str  # "project-admin" or "read-only"
    assigned_by: str = ""
    assigned_at: str = ""

class UserPermissions(BaseModel):
    user: UserRecord
    project_roles: dict[str, ProjectRole] = {}  # project_id -> ProjectRole
```

**Permission cache** -- in-memory dict with 5-minute TTL:

```python
_CACHE_TTL = 300  # 5 minutes
_permission_cache: dict[str, tuple[float, UserPermissions]] = {}

def invalidate_cache(sub: str) -> None:
    _permission_cache.pop(sub, None)
```

**DynamoDB CRUD functions** (follow patterns from `projects.py`):

- `get_user(sub: str) -> UserRecord | None` -- reads `USER#<sub> | META`
- `get_user_by_email(email: str) -> UserRecord | None` -- reads `USER#email:<email> | META`
- `create_user(sub, email, name, global_role=None) -> UserRecord` -- writes `USER#<sub> | META`
- `create_invited_user(email: str) -> UserRecord` -- writes `USER#email:<email> | META` with status="invited"
- `update_user_login(sub: str)` -- updates last_login timestamp
- `link_invited_user(sub, email, name) -> UserRecord | None`:
  1. Read `USER#email:<email>` and all its `PROJECT#` assignments
  2. Create `USER#<sub>` with same data
  3. Copy all project role assignments to new pk
  4. Delete old email-keyed records
- `get_user_permissions(sub: str) -> UserPermissions | None`:
  1. Check cache first
  2. Query all items with `pk = USER#<sub>` (META + PROJECT#*)
  3. Parse user record and project roles
  4. Cache and return
- `set_global_role(sub, role, assigned_by)` -- updates user META record
- `set_project_role(sub, project_id, role, assigned_by)`:
  - role=None removes the `USER#<sub> | PROJECT#<pid>` record
  - role="project-admin"|"read-only" creates/updates the record
  - Invalidates cache for the user
- `list_users() -> list[UserRecord]` -- scans for all `USER#* | META` items
- `get_project_users(project_id) -> list[tuple[UserRecord, str]]` -- uses GSI `gsi-sk-pk` to reverse-lookup `PROJECT#<pid>` -> all `USER#` assignments
- `delete_project_roles(project_id: str)` -- deletes all role assignments for a project (uses GSI)
- `delete_user(sub: str) -> bool` -- deletes user META and all PROJECT# records

**Bootstrap function:**

```python
def ensure_user_exists(sub: str, email: str, name: str) -> UserPermissions:
    """
    Called on every authenticated request. Ensures user exists in DynamoDB.

    Flow:
    1. Check USER#<sub> exists -> update last_login, return permissions
    2. Check USER#email:<email> exists -> link_invited_user, return permissions
    3. Create new user with status="active", role=None
    4. If email matches HOLMES_SUPER_ADMIN_EMAIL -> set role="super-admin"
    5. Return permissions
    """
```

**Environment variables used:**
- `HOLMES_DYNAMODB_TABLE` -- DynamoDB table name (same as projects.py)
- `AWS_DEFAULT_REGION` -- AWS region (same as projects.py)
- `HOLMES_SUPER_ADMIN_EMAIL` -- bootstrap email for first super-admin
- `OKTA_REQUIRED_GROUP` -- Okta group name (default: `HolmesGPT-Users`)

**Important implementation details:**
- Use `model_dump_json()` / `model_validate_json()` for DynamoDB serialization (matches projects.py pattern)
- All DynamoDB items use `pk` and `sk` as partition/sort keys with `data` as the JSON payload
- The `_get_table()` helper is the same pattern as in `projects.py`
- Thread-safe cache using simple dict (adequate for single-pod deployment)
- `set_project_role` must handle both sub-keyed and email-keyed users (for invited users)

- [ ] **12.2** Commit:

```bash
git add frontend/rbac.py
git commit -s --no-verify -m "feat: add RBAC module with DynamoDB user/role CRUD and permission caching"
```

---


## Task 13: Backend auth middleware replacement (`frontend/server_frontend.py`)

**Modified file:** `frontend/server_frontend.py`

### Steps

- [ ] **13.1** Remove old auth code from `server_frontend.py`:

**Delete** these module-level items (lines ~32-78 in current file):
```python
# DELETE all of these:
SESSION_COOKIE = "holmes_session"
SESSION_MAX_AGE = 86400
_sessions: dict[str, str] = {}
_login_failures: dict[str, list[float]] = defaultdict(list)
_LOGIN_WINDOW = 300
_LOGIN_MAX_ATTEMPTS = 10

def _check_login_rate_limit(ip: str) -> bool: ...
def _record_login_failure(ip: str) -> None: ...
def get_credentials() -> tuple[str, str]: ...
def verify_session(session_id: str | None) -> bool: ...
def verify_api_key(request: Request) -> bool: ...
class AuthMiddleware(BaseHTTPMiddleware): ...
```

**Delete** these endpoints (inside `mount_frontend`):
```python
# DELETE:
@app.get("/auth/check")
@app.post("/auth/login")
@app.post("/auth/logout")
@app.get("/auth/login")
```

**Delete** the password check in `mount_frontend`:
```python
# DELETE:
_, password = get_credentials()
if not password:
    logging.warning(...)
else:
    app.add_middleware(AuthMiddleware)
```

- [ ] **13.2** Add new imports at the top of the file:

```python
# ADD these imports (at top of file, with existing imports):
from okta_jwt import validate_okta_token, OKTA_ISSUER, OKTA_CLIENT_ID
from rbac import ensure_user_exists, UserPermissions
```

- [ ] **13.3** Add the new `OktaAuthMiddleware` class (replace the old `AuthMiddleware`):

```python
class OktaAuthMiddleware(BaseHTTPMiddleware):
    """Authenticate requests via Okta JWT or API key."""

    EXEMPT_PATHS = ("/healthz", "/readyz", "/login/callback")
    EXEMPT_PREFIXES = ("/assets/", "/favicon", "/api/webhook/")

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Health/readiness probes and callback are exempt
        if path in self.EXEMPT_PATHS:
            return await call_next(request)

        if any(path.startswith(prefix) for prefix in self.EXEMPT_PREFIXES):
            return await call_next(request)

        # Extract Authorization header
        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            # No auth header -> serve SPA for browser requests, 401 for API
            if request.headers.get("accept", "").startswith("text/html"):
                # Serve the SPA (it will show the login page)
                index = STATIC_DIR / "index.html"
                if index.exists():
                    return FileResponse(index, media_type="text/html")
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)

        token = auth_header[7:]

        # Detect token type: JWT has 2+ dots, API key is a plain string
        if token.count(".") >= 2:
            # JWT token -> validate with Okta
            try:
                claims = validate_okta_token(token)
                # Load/create user and permissions from DynamoDB
                permissions = ensure_user_exists(
                    sub=claims["sub"],
                    email=claims["email"],
                    name=claims["name"],
                )
                request.state.user = claims
                request.state.permissions = permissions
            except HTTPException:
                raise
            except Exception as e:
                logging.error("JWT validation error: %s", e)
                return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        else:
            # API key -> check against HOLMES_API_KEY env var
            api_key = os.environ.get("HOLMES_API_KEY", "")
            if not api_key or not hmac.compare_digest(token, api_key):
                return JSONResponse({"detail": "Invalid API key"}, status_code=401)

            # Synthetic admin user for API key auth
            from rbac import UserRecord, UserPermissions as UP
            synthetic_user = {
                "sub": "api-key",
                "email": "api@holmesgpt.internal",
                "name": "API Key",
                "groups": [],
            }
            synthetic_record = UserRecord(
                sub="api-key",
                email="api@holmesgpt.internal",
                name="API Key",
                global_role="super-admin",
                status="active",
            )
            request.state.user = synthetic_user
            request.state.permissions = UP(user=synthetic_record, project_roles={})

        return await call_next(request)
```

- [ ] **13.4** Update `mount_frontend()` to use the new middleware:

Replace the old password check block with:
```python
def mount_frontend(app: FastAPI, config=None) -> None:
    # Add Okta auth middleware (replaces old session-based AuthMiddleware)
    if OKTA_ISSUER and OKTA_CLIENT_ID:
        app.add_middleware(OktaAuthMiddleware)
        logging.info("Okta auth middleware enabled")
    else:
        logging.warning(
            "OKTA_ISSUER or OKTA_CLIENT_ID not set - Okta auth is DISABLED. "
            "Set both environment variables to enable authentication."
        )

    # ... rest of mount_frontend unchanged ...
```

- [ ] **13.5** Add the `/api/auth/me` endpoint (inside `mount_frontend`):

```python
@app.get("/api/auth/me")
async def auth_me(request: Request):
    """Return the current user's profile and permissions."""
    perms: UserPermissions = request.state.permissions
    user = perms.user

    # Build the response matching the frontend AuthUser type
    project_roles = {}
    for pid, pr in perms.project_roles.items():
        project_roles[pid] = pr.role

    return JSONResponse({
        "sub": user.sub,
        "email": user.email,
        "name": user.name or "",
        "globalRole": user.global_role,
        "projectRoles": project_roles,
        "status": user.status,
    })
```

- [ ] **13.6** Mount user management routes (inside `mount_frontend`, after existing endpoint definitions):

```python
from users_api import mount_users_api
mount_users_api(app)
```

- [ ] **13.7** Update the SPA catch-all route to also serve `/login/callback`:

The existing catch-all at the bottom already handles this:
```python
@app.get("/{path:path}")
async def spa_fallback(path: str):
    ...
```

This will serve `index.html` for `/login/callback`, which the React app handles.

- [ ] **13.8** Commit:

```bash
git add frontend/server_frontend.py
git commit -s --no-verify -m "feat: replace session auth with Okta JWT middleware, add /api/auth/me endpoint"
```

---


## Task 14: User management API endpoints (`frontend/users_api.py`)

**New file:** `frontend/users_api.py`

### Steps

- [ ] **14.1** Create `frontend/users_api.py` with the following content:

```python
"""
User management API endpoints for HolmesGPT (super-admin only).

Provides CRUD operations for users and their role assignments.
All endpoints require super-admin permissions.
"""

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

import rbac

logger = logging.getLogger(__name__)


def _require_super_admin(request: Request) -> None:
    """Raise 403 if the current user is not a super-admin."""
    perms: rbac.UserPermissions = request.state.permissions
    if perms.user.global_role != "super-admin":
        raise HTTPException(status_code=403, detail="Super-admin required")


def _serialize_user_with_roles(user: rbac.UserRecord) -> dict:
    """Serialize a user record with their project roles."""
    perms = rbac.get_user_permissions(user.sub)
    project_roles = {}
    if perms:
        for pid, pr in perms.project_roles.items():
            project_roles[pid] = {
                "project_id": pr.project_id,
                "role": pr.role,
                "assigned_by": pr.assigned_by,
                "assigned_at": pr.assigned_at,
            }

    return {
        "sub": user.sub,
        "email": user.email,
        "name": user.name,
        "global_role": user.global_role,
        "status": user.status,
        "created_at": user.created_at,
        "last_login": user.last_login,
        "project_roles": project_roles,
    }


def mount_users_api(app: FastAPI) -> None:
    """Register user management endpoints on the FastAPI app."""

    @app.get("/api/users")
    async def list_users(request: Request):
        """List all users with their roles (super-admin only)."""
        _require_super_admin(request)
        try:
            users = rbac.list_users()
            return JSONResponse([_serialize_user_with_roles(u) for u in users])
        except Exception as e:
            logger.error("Failed to list users: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/users/invite")
    async def invite_user(request: Request):
        """Invite a user by email (super-admin only)."""
        _require_super_admin(request)
        try:
            body = await request.json()
            email = body.get("email", "").strip().lower()
            if not email:
                raise HTTPException(status_code=400, detail="Email is required")

            # Check if user already exists
            existing = rbac.get_user_by_email(email)
            if existing:
                raise HTTPException(status_code=409, detail="User already invited")

            # Also check active users by scanning (less efficient but covers the case)
            for u in rbac.list_users():
                if u.email.lower() == email:
                    raise HTTPException(status_code=409, detail="User already exists")

            user = rbac.create_invited_user(email)
            return JSONResponse(user.model_dump(), status_code=201)
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Failed to invite user: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    @app.put("/api/users/{user_id}/global-role")
    async def update_user_global_role(user_id: str, request: Request):
        """Set or remove a user's global role (super-admin only)."""
        _require_super_admin(request)
        try:
            body = await request.json()
            role = body.get("role")  # "super-admin" or None
            if role is not None and role != "super-admin":
                raise HTTPException(status_code=400, detail="Invalid role. Must be 'super-admin' or null.")

            admin_sub = request.state.permissions.user.sub
            rbac.set_global_role(user_id, role, assigned_by=admin_sub)
            rbac.invalidate_cache(user_id)

            user = rbac.get_user(user_id)
            if not user:
                raise HTTPException(status_code=404, detail="User not found")
            return JSONResponse(user.model_dump())
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            logger.error("Failed to update global role: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    @app.put("/api/users/{user_id}/projects/{project_id}/role")
    async def update_user_project_role(user_id: str, project_id: str, request: Request):
        """Set or remove a user's role on a project (super-admin only)."""
        _require_super_admin(request)
        try:
            body = await request.json()
            role = body.get("role")  # "project-admin", "read-only", or None (remove)
            if role is not None and role not in ("project-admin", "read-only"):
                raise HTTPException(
                    status_code=400,
                    detail="Invalid role. Must be 'project-admin', 'read-only', or null.",
                )

            admin_sub = request.state.permissions.user.sub
            rbac.set_project_role(user_id, project_id, role, assigned_by=admin_sub)
            rbac.invalidate_cache(user_id)

            return JSONResponse({"ok": True})
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            logger.error("Failed to update project role: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    @app.delete("/api/users/{user_id}")
    async def delete_user_endpoint(user_id: str, request: Request):
        """Delete a user and all their role assignments (super-admin only)."""
        _require_super_admin(request)
        try:
            # Prevent self-deletion
            if user_id == request.state.permissions.user.sub:
                raise HTTPException(status_code=400, detail="Cannot delete your own account")

            if not rbac.delete_user(user_id):
                raise HTTPException(status_code=404, detail="User not found")

            return JSONResponse({"ok": True})
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Failed to delete user: %s", e)
            raise HTTPException(status_code=500, detail=str(e))
```

- [ ] **14.2** Commit:

```bash
git add frontend/users_api.py
git commit -s --no-verify -m "feat: add user management API endpoints (list, invite, role assignment, delete)"
```

---


## Task 15: Add RBAC enforcement to existing endpoints

**Modified file:** `frontend/server_frontend.py`

### Steps

- [ ] **15.1** Add RBAC helper functions inside `mount_frontend()` (at the top, before endpoint definitions):

```python
def _require_super_admin(request: Request):
    perms = request.state.permissions
    if perms.user.global_role != "super-admin":
        raise HTTPException(403, "Super-admin required")

def _require_project_access(request: Request, project_id: str, min_role: str = "read-only"):
    perms = request.state.permissions
    if perms.user.global_role == "super-admin":
        return
    project_role = perms.project_roles.get(project_id)
    if not project_role:
        raise HTTPException(403, "No access to this project")
    if min_role == "project-admin" and project_role.role != "project-admin":
        raise HTTPException(403, "Project-admin required")

def _get_accessible_project_ids(request: Request) -> list[str] | None:
    """Return list of project IDs user can access, or None for super-admin (all)."""
    perms = request.state.permissions
    if perms.user.global_role == "super-admin":
        return None  # all projects
    return list(perms.project_roles.keys())
```

- [ ] **15.2** Add permission checks to existing endpoints:

**`POST /api/projects`** (create project):
```python
@app.post("/api/projects")
async def create_project(request: Request):
    _require_super_admin(request)  # ADD this line
    # ... rest unchanged
```

**`DELETE /api/projects/{project_id}`** (delete project):
```python
@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str, request: Request):  # ADD request param
    _require_super_admin(request)  # ADD this line
    try:
        from projects import get_store
        if not get_store().delete(project_id):
            raise HTTPException(status_code=404, detail="Project not found")
        # ADD: Clean up role assignments
        from rbac import delete_project_roles
        delete_project_roles(project_id)
        return JSONResponse({"ok": True})
    # ... rest unchanged
```

**`PUT /api/projects/{project_id}`** (update project):
```python
@app.put("/api/projects/{project_id}")
async def update_project(project_id: str, request: Request):
    _require_project_access(request, project_id, min_role="project-admin")  # ADD
    # ... rest unchanged
```

**`GET /api/projects/{project_id}`** (get single project):
```python
@app.get("/api/projects/{project_id}")
async def get_project(project_id: str, request: Request):  # ADD request param
    _require_project_access(request, project_id, min_role="read-only")  # ADD
    # ... rest unchanged
```

**`GET /api/projects`** (list projects) -- filter to accessible projects:
```python
@app.get("/api/projects")
async def list_projects(request: Request):  # ADD request param
    try:
        from projects import get_store
        all_projects = get_store().list()
        accessible = _get_accessible_project_ids(request)
        if accessible is not None:
            all_projects = [p for p in all_projects if p.id in accessible]
        return JSONResponse({"projects": [p.model_dump() for p in all_projects]})
    # ... rest unchanged
```

**`PUT /api/app-settings`** (update settings):
```python
@app.put("/api/app-settings")
async def update_app_settings(request: Request):
    _require_super_admin(request)  # ADD
    # ... rest unchanged
```

**`POST /api/instances`** (create instance):
```python
@app.post("/api/instances")
async def create_instance(request: Request):
    # Require at least project-admin on some project (or super-admin)
    perms = request.state.permissions
    if perms.user.global_role != "super-admin":
        has_admin = any(pr.role == "project-admin" for pr in perms.project_roles.values())
        if not has_admin:
            raise HTTPException(403, "Project-admin required to create instances")
    # ... rest unchanged
```

**`PUT /api/instances/{instance_id}`** and **`DELETE /api/instances/{instance_id}`**:
```python
# Same pattern: require project-admin+ or super-admin
```

- [ ] **15.3** Commit:

```bash
git add frontend/server_frontend.py
git commit -s --no-verify -m "feat: add RBAC enforcement to project, instance, and settings endpoints"
```

---


## Task 16: Create Users management page (`frontend/src/components/Users.tsx`)

**New file:** `frontend/src/components/Users.tsx`

### Steps

- [ ] **16.1** Create `frontend/src/components/Users.tsx`. This is the super-admin user management page.

**Component structure:**
- Main component renders a user list table and a detail slide-out panel
- Uses `api.getUsers()` to load users on mount
- Uses `api.getProjects()` to load projects for the role assignment dropdown

**User list table columns:**
- Name (or "Invited" if no name)
- Email
- Role (super-admin badge or "User")
- Projects (count of project assignments)
- Status badge: "Active" (green), "Invited" (yellow)
- Last Login (relative time or "Never")

**Features:**
- Search/filter input at the top (filters by name and email)
- "Invite User" button opens a modal with email input
- Click a user row to open a slide-out detail panel

**Detail panel contents:**
- User info: name, email, status
- Global role toggle: a checkbox or button to toggle super-admin on/off
  - Calls `api.updateUserGlobalRole(userId, role)`
- Project assignments section:
  - Table of assigned projects with role dropdown (project-admin / read-only / remove)
  - "Add Project" button with project selector dropdown
  - Calls `api.updateUserProjectRole(userId, projectId, role)`
- Delete user button (with confirmation)

**Tailwind styling:** Follow the same patterns as `Projects.tsx`:
- White background card with shadow
- `text-pdi-granite` for headings, `text-pdi-slate` for body
- `bg-pdi-sky` for primary buttons, `border-pdi-cool-gray` for secondary
- Status badges: green for active (`bg-emerald-50 text-emerald-700`), yellow for invited (`bg-amber-50 text-amber-700`)

**Example structure:**

```tsx
import { useState, useEffect } from 'react'
import { api, type UserWithRoles, type Project } from '../lib/api'

export default function Users() {
  const [users, setUsers] = useState<UserWithRoles[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [search, setSearch] = useState('')
  const [selectedUser, setSelectedUser] = useState<UserWithRoles | null>(null)
  const [showInviteModal, setShowInviteModal] = useState(false)
  const [inviteEmail, setInviteEmail] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    loadData()
  }, [])

  async function loadData() {
    setLoading(true)
    try {
      const [usersData, projectsData] = await Promise.all([
        api.getUsers(),
        api.getProjects(),
      ])
      setUsers(usersData)
      setProjects(projectsData.projects)
    } catch (err) {
      console.error('Failed to load users', err)
    } finally {
      setLoading(false)
    }
  }

  // ... filtering, invite handler, role update handlers, etc.

  return (
    <div className="h-full overflow-y-auto bg-gray-50">
      <div className="max-w-6xl mx-auto p-6">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-xl font-bold text-pdi-granite">Users</h1>
            <p className="text-sm text-pdi-slate mt-1">Manage user access and role assignments</p>
          </div>
          <button onClick={() => setShowInviteModal(true)}
            className="px-4 py-2 bg-pdi-sky text-white text-sm font-medium rounded-lg hover:bg-pdi-ocean transition-colors">
            Invite User
          </button>
        </div>

        {/* Search input */}
        {/* User table */}
        {/* Invite modal */}
        {/* User detail slide-out */}
      </div>
    </div>
  )
}
```

The implementor should build the complete component following the patterns from `Projects.tsx` and other existing components. Key interactions:

1. **Invite flow:** Email input modal -> `api.inviteUser(email)` -> reload list
2. **Global role toggle:** Button in detail panel -> `api.updateUserGlobalRole(sub, role)` -> reload
3. **Project role assignment:** Dropdown per project -> `api.updateUserProjectRole(sub, pid, role)` -> reload
4. **Delete user:** Confirmation dialog -> `api.deleteUser(sub)` -> close panel, reload

- [ ] **16.2** Commit:

```bash
git add frontend/src/components/Users.tsx
git commit -s --no-verify -m "feat: add Users management page for super-admin role and project assignment"
```

---


## Task 17: Wire Users page into App.tsx and Layout.tsx

**Modified files:** `frontend/src/App.tsx`, `frontend/src/components/Layout.tsx`

### Steps

- [ ] **17.1** Verify that Task 9 added `'users'` to the `Page` type union in `App.tsx`:

```typescript
export type Page = 'chat' | 'investigate' | 'history' | 'analytics' | 'integrations' | 'instances' | 'settings' | 'projects' | 'docs' | 'users'
```

- [ ] **17.2** Verify that Task 9 added the Users page renderer:

```tsx
{page === 'users' && <Users />}
```

- [ ] **17.3** Verify that Task 10 added the Users nav item in Layout for super-admin:

```typescript
if (canManageUsers(user)) {
  systemItems.push({ page: 'users', label: 'Users', icon: '...' })
}
```

- [ ] **17.4** No additional changes needed if Tasks 9 and 10 were completed correctly. This task is a verification checkpoint.

---


## Task 18: Update project deletion to clean up role assignments

**Modified file:** `frontend/server_frontend.py` (or verify it was done in Task 15)

### Steps

- [ ] **18.1** Verify that the `DELETE /api/projects/{project_id}` endpoint calls `rbac.delete_project_roles(project_id)`:

This should have been added in Task 15. The delete endpoint should look like:

```python
@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str, request: Request):
    _require_super_admin(request)
    try:
        from projects import get_store
        if not get_store().delete(project_id):
            raise HTTPException(status_code=404, detail="Project not found")
        # Clean up all user-project role assignments
        from rbac import delete_project_roles
        delete_project_roles(project_id)
        return JSONResponse({"ok": True})
    except HTTPException:
        raise
    except Exception as e:
        logging.error("Failed to delete project %s: %s", project_id, e)
        raise HTTPException(status_code=500, detail=str(e))
```

- [ ] **18.2** This is a verification checkpoint. If Task 15 was completed correctly, no additional changes are needed.

---


## Task 19: Add python-jose dependency

**Modified file:** `pyproject.toml`

### Steps

- [ ] **19.1** Add `python-jose[cryptography]` to the project dependencies in `pyproject.toml`:

```bash
cd /path/to/holmesgpt-pdi
poetry add "python-jose[cryptography]"
```

This will:
1. Add `python-jose` with the `cryptography` extra to `[tool.poetry.dependencies]`
2. Update `poetry.lock`

- [ ] **19.2** Verify the dependency was added:

```bash
poetry show python-jose
```

- [ ] **19.3** Install to verify everything resolves:

```bash
poetry install
```

- [ ] **19.4** Commit:

```bash
git add pyproject.toml poetry.lock
git commit -s --no-verify -m "feat: add python-jose[cryptography] for Okta JWT validation"
```

---


## Task 20: Write backend unit tests

**New files:** `tests/test_okta_jwt.py`, `tests/test_rbac.py`, `tests/test_users_api.py`

### Steps

- [ ] **20.1** Create `tests/test_okta_jwt.py`:

Test cases:
1. **Valid JWT validation** -- mock JWKS endpoint, create a valid JWT, verify claims are extracted
2. **Expired token rejection** -- create an expired JWT, verify 401 is raised
3. **Wrong audience rejection** -- create JWT with wrong `aud`, verify 401
4. **Wrong issuer rejection** -- create JWT with wrong `iss`, verify 401
5. **Missing groups claim** -- create JWT without required group, verify 403
6. **JWKS key refresh on signature failure** -- mock first fetch returning old keys, verify retry

Use `python-jose` to create test JWTs signed with a test RSA key. Mock `requests.get` for JWKS/discovery endpoints using the `responses` library.

```python
import time
import pytest
import responses
from jose import jwt as jose_jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend

# Generate test RSA key pair
_private_key = rsa.generate_private_key(
    public_exponent=65537, key_size=2048, backend=default_backend()
)
# ... build JWKS from public key, mock endpoints, test validation
```

- [ ] **20.2** Create `tests/test_rbac.py`:

Test cases using `moto` to mock DynamoDB:
1. **User creation** -- `create_user()` then `get_user()` returns it
2. **Invited user creation** -- `create_invited_user()` creates email-keyed record
3. **Invited user linking** -- `link_invited_user()` migrates email-keyed to sub-keyed, copies project roles
4. **Permission resolution** -- super-admin bypasses project checks
5. **Project role assignment and removal** -- `set_project_role()` with role and with None
6. **Bootstrap logic** -- `ensure_user_exists()` creates user, checks HOLMES_SUPER_ADMIN_EMAIL
7. **Permission cache TTL** -- verify cache returns stale data, then expires

```python
import os
import pytest
import boto3
from moto import mock_aws

@pytest.fixture
def dynamodb_table():
    with mock_aws():
        os.environ["HOLMES_DYNAMODB_TABLE"] = "test-table"
        os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName="test-table",
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[{
                "IndexName": "gsi-sk-pk",
                "KeySchema": [
                    {"AttributeName": "sk", "KeyType": "HASH"},
                    {"AttributeName": "pk", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }],
            BillingMode="PAY_PER_REQUEST",
        )
        # Clear module-level caches in rbac
        import rbac
        rbac._permission_cache.clear()
        yield
```

- [ ] **20.3** Create `tests/test_users_api.py`:

Test cases using a FastAPI TestClient with mocked DynamoDB:
1. **List users (super-admin)** -- returns user list
2. **List users (non-admin)** -- returns 403
3. **Invite user** -- creates invited user, returns 201
4. **Invite duplicate** -- returns 409
5. **Set global role** -- updates user, returns updated record
6. **Set project role** -- creates assignment, returns ok
7. **Delete user** -- removes user and assignments
8. **Self-deletion prevented** -- returns 400

```python
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

# Create test app with users_api mounted
# Mock request.state.permissions for different user roles
```

- [ ] **20.4** Commit:

```bash
git add tests/test_okta_jwt.py tests/test_rbac.py tests/test_users_api.py
git commit -s --no-verify -m "test: add unit tests for Okta JWT validation, RBAC module, and user management API"
```

---


## Task 21: Commit and verify

### Steps

- [ ] **21.1** Run backend unit tests:

```bash
poetry run pytest tests/test_okta_jwt.py tests/test_rbac.py tests/test_users_api.py -v
```

- [ ] **21.2** Verify frontend builds:

```bash
cd frontend && npm run build
```

- [ ] **21.3** Verify TypeScript compilation:

```bash
cd frontend && npx tsc --noEmit
```

- [ ] **21.4** Run existing tests to check for regressions:

```bash
poetry run pytest tests -m "not llm" --ignore=tests/llm -v
```

- [ ] **21.5** If all tests pass, create a final verification commit:

```bash
git add -A
git commit -s --no-verify -m "feat: complete Okta RBAC integration - all tests passing"
```

---

## Environment Variable Checklist

Before deploying, ensure these are set:

### Frontend (Build-Time)

| Variable | Example |
|----------|---------|
| `VITE_OKTA_ISSUER` | `https://your-org.okta.com/oauth2/default` |
| `VITE_OKTA_CLIENT_ID` | `0oa1abc2def3ghi4j5k6` |

### Backend (Runtime)

| Variable | Example |
|----------|---------|
| `OKTA_ISSUER` | `https://your-org.okta.com/oauth2/default` |
| `OKTA_CLIENT_ID` | `0oa1abc2def3ghi4j5k6` |
| `HOLMES_SUPER_ADMIN_EMAIL` | `admin@company.com` |
| `HOLMES_API_KEY` | (existing, for programmatic access) |
| `HOLMES_DYNAMODB_TABLE` | (existing) |

### Removed (No Longer Needed)

| Variable |
|----------|
| `HOLMES_UI_USERNAME` |
| `HOLMES_UI_PASSWORD` |

## DynamoDB Changes

- **No schema migration needed** -- new `USER#*` entities are additive
- **GSI required:** Create `gsi-sk-pk` index with `sk` as partition key and `pk` as sort key, projection `ALL`
- The GSI can be added to an existing table without downtime

## First Deployment Checklist

1. Add `python-jose[cryptography]` to the Docker image (via poetry install)
2. Create Okta SPA application with PKCE, configure redirect URIs
3. Create `HolmesGPT-Users` group in Okta, add users
4. Add GSI `gsi-sk-pk` to the DynamoDB table
5. Set all environment variables listed above
6. Deploy backend and frontend
7. First login by `HOLMES_SUPER_ADMIN_EMAIL` auto-grants super-admin
8. Super-admin invites other users from the UI

## Rollback Plan

- Revert to previous deployment -- old env vars (`HOLMES_UI_USERNAME`, `HOLMES_UI_PASSWORD`) restore original auth
- No DynamoDB data is modified or deleted -- new `USER#*` entities are additive
- Remove GSI if desired (optional, doesn't affect existing data)
