import { useAuth } from '../lib/auth'

export default function PendingAccess() {
  const { user, logout } = useAuth()

  return (
    <div className="min-h-screen bg-pdi-indigo flex items-center justify-center px-4 relative overflow-hidden">
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top,_#1226aa33_0%,_transparent_60%)]" />
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_bottom_right,_#29b5e820_0%,_transparent_50%)]" />
      <div className="absolute inset-0 opacity-[0.03] bg-[linear-gradient(#29b5e8_1px,transparent_1px),linear-gradient(to_right,#29b5e8_1px,transparent_1px)] bg-[size:40px_40px]" />

      <div className="relative z-10 w-full max-w-sm">
        <div className="bg-white rounded-xl p-6 shadow-2xl text-center">
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
