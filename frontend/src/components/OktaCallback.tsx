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
          // Update URL without full page reload — tokens persist in sessionStorage
          window.history.replaceState(null, '', '/')
          // Force React to re-render by reloading (sessionStorage survives this)
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
