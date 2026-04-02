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

  // Load projects once authentication is confirmed
  useEffect(() => {
    if (isAuthenticated) {
      reloadProjects()
    }
  }, [isAuthenticated]) // eslint-disable-line react-hooks/exhaustive-deps

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
      <AppContent />
    </AuthProvider>
  )
}
