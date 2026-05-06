import { ReactNode } from 'react'
import type { Page } from '../App'
import type { Project } from '../lib/api'
import type { AuthUser } from '../lib/auth'
import { isSuperAdmin, isProjectAdminOnAny, canManageSettings, canManageUsers } from '../lib/permissions'

interface LayoutProps {
  currentPage: Page
  onNavigate: (page: Page) => void
  onLogout: () => void
  user: AuthUser
  children: ReactNode
  projects: Project[]
  selectedProjectId: string | null
  selectedProject: Project | null
  onSelectProject: (id: string | null) => void
}

type NavSection = {
  label: string
  items: { page: Page; label: string; icon: string }[]
}

function getNavSections(user: AuthUser): NavSection[] {
  const sections: NavSection[] = []

  // Workspace -- always visible
  sections.push({
    label: 'Workspace',
    items: [
      { page: 'chat', label: 'Chat', icon: 'M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z' },
      { page: 'investigate', label: 'Investigate', icon: 'M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z' },
      { page: 'history', label: 'History', icon: 'M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2' },
      { page: 'analytics', label: 'Analytics', icon: 'M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 013 19.875v-6.75zM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V8.625zM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V4.125z' },
    ],
  })

  // Configure -- super-admin or project-admin only
  const configureItems: NavSection['items'] = []
  if (isSuperAdmin(user) || isProjectAdminOnAny(user)) {
    configureItems.push(
      { page: 'integrations', label: 'Integrations', icon: 'M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1' },
      { page: 'instances', label: 'Instances', icon: 'M5 12h14M5 12a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 01-2 2M5 12a2 2 0 00-2 2v4a2 2 0 002 2h14a2 2 0 002-2v-4a2 2 0 00-2-2m-2-4h.01M17 16h.01' },
    )
  }
  if (isSuperAdmin(user)) {
    configureItems.push({ page: 'projects', label: 'Projects', icon: 'M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z' })
  }
  if (configureItems.length > 0) {
    sections.push({ label: 'Configure', items: configureItems })
  }

  // System
  const systemItems: NavSection['items'] = [
    { page: 'docs', label: 'Docs', icon: 'M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253' },
  ]
  if (canManageSettings(user)) {
    systemItems.push({ page: 'settings', label: 'Settings', icon: 'M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z M15 12a3 3 0 11-6 0 3 3 0 016 0z' })
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

export default function Layout({
  currentPage,
  onNavigate,
  onLogout,
  user,
  children,
  projects,
  selectedProjectId,
  selectedProject,
  onSelectProject,
}: LayoutProps) {
  const navSections = getNavSections(user)
  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar */}
      <aside className="w-64 bg-pdi-indigo flex flex-col shrink-0">
        {/* Logo */}
        <div className="px-5 py-5 border-b border-white/10 relative">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-pdi-sky to-pdi-ocean flex items-center justify-center shadow-lg shadow-pdi-sky/20">
              {/* Magnifying glass icon */}
              <svg className="w-5 h-5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
            </div>
            <div>
              <h1 className="text-white font-bold text-base leading-tight tracking-tight">HolmesGPT</h1>
              <p className="text-pdi-sky/70 text-[11px] font-medium tracking-wide">PDI Technologies</p>
            </div>
          </div>
          <div className="absolute bottom-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-pdi-sky/30 to-transparent" />
        </div>

        {/* Project selector */}
        <div className="px-3 pt-3 pb-2 border-b border-white/10">
          <label className="block text-white/30 text-[10px] font-semibold uppercase tracking-widest mb-1.5 px-1">
            Project scope
          </label>
          <div className="relative">
            <select
              value={selectedProjectId ?? ''}
              onChange={(e) => onSelectProject(e.target.value || null)}
              className={`w-full text-sm bg-white/[0.08] text-white border border-white/[0.15] rounded-lg ${selectedProjectId ? 'pl-6' : 'pl-3'} py-2 pr-8 focus:outline-none focus:ring-2 focus:ring-pdi-sky/50 focus:border-pdi-sky/50 appearance-none cursor-pointer transition-colors hover:bg-white/[0.12]`}
            >
              <option value="" className="bg-pdi-indigo text-white">All integrations</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id} className="bg-pdi-indigo text-white">
                  {p.name}
                </option>
              ))}
            </select>
            {selectedProjectId && (
              <div className="pointer-events-none absolute inset-y-0 left-3 flex items-center">
                <div className="w-1.5 h-1.5 rounded-full bg-pdi-sky" />
              </div>
            )}
            <div className="pointer-events-none absolute inset-y-0 right-2.5 flex items-center">
              <svg className="w-3.5 h-3.5 text-white/40" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
              </svg>
            </div>
          </div>
          {selectedProject && selectedProject.tag_filter && Object.keys(selectedProject.tag_filter.tags).length > 0 && (
            <p className="text-white/30 text-[11px] mt-1.5 px-1 truncate">
              {selectedProject.tag_filter.logic}: {Object.entries(selectedProject.tag_filter.tags).map(([k, v]) => `${k}=${v}`).join(', ')}
            </p>
          )}
        </div>

        {/* Navigation */}
        <nav className="flex-1 px-3 py-3 overflow-y-auto space-y-4">
          {navSections.map((section) => (
            <div key={section.label}>
              <p className="text-[10px] font-semibold uppercase tracking-widest text-white/25 px-3 mb-1">
                {section.label}
              </p>
              <div className="space-y-0.5">
                {section.items.map(({ page, label, icon }) => {
                  const isActive = currentPage === page
                  return (
                    <button
                      key={page}
                      onClick={() => onNavigate(page)}
                      title={label}
                      className={`relative w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all ${
                        isActive
                          ? 'bg-gradient-to-r from-pdi-sky/20 to-transparent text-pdi-sky'
                          : 'text-white/60 hover:bg-white/[0.08] hover:text-white/90'
                      }`}
                    >
                      {/* Active left border indicator */}
                      {isActive && (
                        <span className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-5 bg-pdi-sky rounded-r-full" />
                      )}
                      <svg
                        className={`w-4 h-4 shrink-0 ${isActive ? 'text-pdi-sky' : 'text-white/40'}`}
                        fill="none"
                        viewBox="0 0 24 24"
                        stroke="currentColor"
                        strokeWidth={isActive ? 2 : 1.5}
                      >
                        <path strokeLinecap="round" strokeLinejoin="round" d={icon} />
                      </svg>
                      {label}
                    </button>
                  )
                })}
              </div>
            </div>
          ))}
        </nav>

        {/* Profile footer */}
        <div className="px-3 py-3 border-t border-white/10">
          <div className="group flex items-center gap-3 px-2 py-2 rounded-lg hover:bg-white/[0.06] transition-colors cursor-pointer" onClick={onLogout}>
            {/* Avatar */}
            <div className="w-7 h-7 rounded-full bg-gradient-to-br from-pdi-sky/60 to-pdi-ocean/80 flex items-center justify-center shrink-0">
              <span className="text-white text-xs font-bold">
                {user.name
                  ? user.name.split(' ').map((n) => n[0]).join('').toUpperCase().slice(0, 2)
                  : user.email[0].toUpperCase()}
              </span>
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-white/80 text-xs font-medium truncate">{user.name || user.email}</p>
              <p className="text-white/30 text-[10px] truncate">
                {user.globalRole === 'super-admin' ? 'Super Admin' : user.email}
              </p>
            </div>
            {/* Sign out icon — always visible, orange on hover */}
            <svg
              className="w-4 h-4 text-white/30 group-hover:text-pdi-orange transition-colors shrink-0"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={1.5}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 9V5.25A2.25 2.25 0 0013.5 3h-6a2.25 2.25 0 00-2.25 2.25v13.5A2.25 2.25 0 007.5 21h6a2.25 2.25 0 002.25-2.25V15m3 0l3-3m0 0l-3-3m3 3H9" />
            </svg>
          </div>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-hidden">
        {children}
      </main>
    </div>
  )
}
