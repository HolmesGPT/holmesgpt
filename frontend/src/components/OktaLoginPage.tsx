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
