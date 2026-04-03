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
  scopes: ['openid', 'profile', 'email'],
  pkce: true,
  responseType: 'code',
  tokenManager: {
    storage: 'sessionStorage',
  },
})

export function getOktaClient(): OktaAuth {
  return oktaAuth
}

export async function getIdToken(): Promise<string | null> {
  try {
    // First try the tokenManager
    const tokenManager = oktaAuth.tokenManager
    const idToken = await tokenManager.get('idToken')
    if (idToken && 'idToken' in idToken) {
      return idToken.idToken
    }

    // Fallback: read directly from sessionStorage (tokenManager may not be synced)
    const raw = sessionStorage.getItem('okta-token-storage')
    if (raw) {
      const parsed = JSON.parse(raw)
      if (parsed?.idToken?.idToken) {
        return parsed.idToken.idToken
      }
    }

    return null
  } catch {
    return null
  }
}

export async function isAuthenticated(): Promise<boolean> {
  try {
    // First try tokenManager
    const idToken = await oktaAuth.tokenManager.get('idToken')
    if (idToken && 'expiresAt' in idToken) {
      const now = Math.floor(Date.now() / 1000)
      return idToken.expiresAt > now + 60
    }

    // Fallback: read directly from sessionStorage
    const raw = sessionStorage.getItem('okta-token-storage')
    if (raw) {
      const parsed = JSON.parse(raw)
      if (parsed?.idToken?.expiresAt) {
        const now = Math.floor(Date.now() / 1000)
        return parsed.idToken.expiresAt > now + 60
      }
    }

    return false
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
    oktaAuth.tokenManager.clear()
    window.location.href = '/'
  }
}
