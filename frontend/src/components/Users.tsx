import { useState, useEffect } from 'react'
import { api, type UserWithRoles, type Project } from '../lib/api'

// ── Add User Modal ───────────────────────────────────────────────────────────

function AddUserModal({
  onClose,
  onSuccess,
}: {
  onClose: () => void
  onSuccess: () => void
}) {
  const [email, setEmail] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSend = async () => {
    const trimmed = email.trim()
    if (!trimmed) {
      setError('Email is required')
      return
    }
    setSending(true)
    setError(null)
    try {
      await api.inviteUser(trimmed)
      onSuccess()
      onClose()
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Failed to add user'
      if (msg.startsWith('409')) {
        setError('A user with this email already exists.')
      } else {
        setError(msg)
      }
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-md mx-4 overflow-hidden">
        <div className="px-6 py-4 border-b border-pdi-cool-gray">
          <h2 className="text-lg font-semibold text-pdi-granite">Add User</h2>
        </div>
        <div className="px-6 py-5 space-y-4">
          <div>
            <label className="block text-sm font-medium text-pdi-granite mb-1">
              Email address
            </label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSend()}
              placeholder="user@example.com"
              autoFocus
              className="w-full text-sm border border-pdi-cool-gray rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-pdi-sky"
            />
          </div>
          {error && <p className="text-sm text-pdi-orange">{error}</p>}
        </div>
        <div className="px-6 py-4 border-t border-pdi-cool-gray flex justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-pdi-granite bg-white border border-pdi-cool-gray rounded-lg hover:bg-gray-50 transition-colors"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSend}
            disabled={sending}
            className="px-4 py-2 text-sm font-medium text-white bg-pdi-sky rounded-lg hover:bg-pdi-indigo transition-colors disabled:opacity-50"
          >
            {sending ? 'Adding…' : 'Add User'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── User Detail Slide-out Panel ───────────────────────────────────────────────

function UserPanel({
  user,
  projects,
  onClose,
  onChanged,
}: {
  user: UserWithRoles
  projects: Project[]
  onClose: () => void
  onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)

  // Add-project row state
  const [addProjectId, setAddProjectId] = useState('')
  const [addRole, setAddRole] = useState<'project-admin' | 'read-only'>('read-only')
  const [addingProject, setAddingProject] = useState(false)

  const assignedProjectIds = Object.keys(user.project_roles)
  const unassignedProjects = projects.filter((p) => !assignedProjectIds.includes(p.id))

  const displayName = user.name ?? user.email

  const formatDate = (iso: string | null) => {
    if (!iso) return '—'
    return new Date(iso).toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    })
  }

  const handleToggleSuperAdmin = async () => {
    setBusy(true)
    setError(null)
    try {
      const newRole = user.global_role === 'super-admin' ? null : 'super-admin'
      await api.updateUserGlobalRole(user.sub, newRole)
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to update role')
    } finally {
      setBusy(false)
    }
  }

  const handleRoleChange = async (projectId: string, role: 'project-admin' | 'read-only') => {
    setBusy(true)
    setError(null)
    try {
      await api.updateUserProjectRole(user.sub, projectId, role)
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to update project role')
    } finally {
      setBusy(false)
    }
  }

  const handleRemoveProject = async (projectId: string) => {
    setBusy(true)
    setError(null)
    try {
      await api.updateUserProjectRole(user.sub, projectId, null)
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to remove project')
    } finally {
      setBusy(false)
    }
  }

  const handleAddProject = async () => {
    if (!addProjectId) return
    setAddingProject(true)
    setError(null)
    try {
      await api.updateUserProjectRole(user.sub, addProjectId, addRole)
      setAddProjectId('')
      setAddRole('read-only')
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to add project')
    } finally {
      setAddingProject(false)
    }
  }

  const handleDelete = async () => {
    setBusy(true)
    setError(null)
    try {
      await api.deleteUser(user.sub)
      onClose()
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to delete user')
      setBusy(false)
    }
  }

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-30 bg-black/20"
        onClick={onClose}
      />

      {/* Panel */}
      <div className="fixed right-0 top-0 bottom-0 z-40 w-96 bg-white shadow-2xl flex flex-col">
        {/* Header */}
        <div className="px-5 py-4 border-b border-pdi-cool-gray flex items-center justify-between shrink-0">
          <h2 className="text-base font-semibold text-pdi-granite truncate pr-2">{displayName}</h2>
          <button
            type="button"
            onClick={onClose}
            className="text-gray-400 hover:text-pdi-granite transition-colors shrink-0"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-5 space-y-6">

          {/* Basic info */}
          <div className="space-y-2">
            <div className="flex justify-between text-sm">
              <span className="text-pdi-slate">Email</span>
              <span className="text-pdi-granite font-medium truncate max-w-[200px]">{user.email}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-pdi-slate">Status</span>
              <StatusBadge status={user.status} />
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-pdi-slate">Joined</span>
              <span className="text-pdi-granite">{formatDate(user.created_at)}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-pdi-slate">Last login</span>
              <span className="text-pdi-granite">{formatDate(user.last_login)}</span>
            </div>
          </div>

          <div className="border-t border-pdi-cool-gray" />

          {/* Global role */}
          <div>
            <h3 className="text-sm font-semibold text-pdi-granite mb-3">Global Role</h3>
            <div className="flex items-center justify-between py-2 px-3 bg-gray-50 rounded-lg">
              <div>
                <p className="text-sm font-medium text-pdi-granite">Super Admin</p>
                <p className="text-xs text-pdi-slate mt-0.5">
                  Full access to all projects and settings
                </p>
              </div>
              <button
                type="button"
                onClick={handleToggleSuperAdmin}
                disabled={busy}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors disabled:opacity-50 ${
                  user.global_role === 'super-admin' ? 'bg-pdi-sky' : 'bg-gray-300'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                    user.global_role === 'super-admin' ? 'translate-x-6' : 'translate-x-1'
                  }`}
                />
              </button>
            </div>
          </div>

          <div className="border-t border-pdi-cool-gray" />

          {/* Project assignments */}
          <div>
            <h3 className="text-sm font-semibold text-pdi-granite mb-3">Project Access</h3>

            {assignedProjectIds.length === 0 ? (
              <p className="text-xs text-pdi-slate italic mb-3">No project assignments yet.</p>
            ) : (
              <div className="space-y-2 mb-3">
                {assignedProjectIds.map((pid) => {
                  const assignment = user.project_roles[pid]
                  const project = projects.find((p) => p.id === pid)
                  const projectName = project?.name ?? pid
                  return (
                    <div
                      key={pid}
                      className="flex items-center gap-2 py-2 px-3 bg-gray-50 rounded-lg"
                    >
                      <span className="flex-1 text-sm text-pdi-granite truncate" title={projectName}>
                        {projectName}
                      </span>
                      <select
                        value={assignment.role}
                        onChange={(e) =>
                          handleRoleChange(pid, e.target.value as 'project-admin' | 'read-only')
                        }
                        disabled={busy}
                        className="text-xs border border-pdi-cool-gray rounded-md px-2 py-1 focus:outline-none focus:ring-1 focus:ring-pdi-sky bg-white text-pdi-granite disabled:opacity-50"
                      >
                        <option value="project-admin">Project Admin</option>
                        <option value="read-only">Read Only</option>
                      </select>
                      <button
                        type="button"
                        onClick={() => handleRemoveProject(pid)}
                        disabled={busy}
                        className="text-gray-400 hover:text-pdi-orange transition-colors disabled:opacity-40 shrink-0"
                        title="Remove"
                      >
                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                        </svg>
                      </button>
                    </div>
                  )
                })}
              </div>
            )}

            {/* Add project row */}
            {unassignedProjects.length > 0 && (
              <div className="flex items-center gap-2 pt-1">
                <select
                  value={addProjectId}
                  onChange={(e) => setAddProjectId(e.target.value)}
                  className="flex-1 text-xs border border-pdi-cool-gray rounded-md px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-pdi-sky bg-white text-pdi-granite"
                >
                  <option value="">Add project…</option>
                  {unassignedProjects.map((p) => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
                <select
                  value={addRole}
                  onChange={(e) => setAddRole(e.target.value as 'project-admin' | 'read-only')}
                  disabled={!addProjectId}
                  className="text-xs border border-pdi-cool-gray rounded-md px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-pdi-sky bg-white text-pdi-granite disabled:opacity-40"
                >
                  <option value="read-only">Read Only</option>
                  <option value="project-admin">Project Admin</option>
                </select>
                <button
                  type="button"
                  onClick={handleAddProject}
                  disabled={!addProjectId || addingProject}
                  className="px-2.5 py-1.5 text-xs font-medium text-white bg-pdi-sky rounded-md hover:bg-pdi-indigo transition-colors disabled:opacity-40"
                >
                  Add
                </button>
              </div>
            )}
          </div>

          {error && (
            <p className="text-sm text-pdi-orange bg-red-50 rounded-lg px-3 py-2">{error}</p>
          )}
        </div>

        {/* Footer: delete */}
        <div className="px-5 py-4 border-t border-pdi-cool-gray shrink-0">
          {confirmDelete ? (
            <div className="flex items-center gap-3">
              <span className="text-sm text-pdi-granite flex-1">Delete this user?</span>
              <button
                type="button"
                onClick={() => setConfirmDelete(false)}
                className="px-3 py-1.5 text-xs font-medium text-pdi-granite bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleDelete}
                disabled={busy}
                className="px-3 py-1.5 text-xs font-medium text-white bg-red-500 rounded-lg hover:bg-red-600 transition-colors disabled:opacity-50"
              >
                {busy ? 'Deleting…' : 'Delete'}
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setConfirmDelete(true)}
              className="w-full px-4 py-2 text-sm font-medium text-red-600 border border-red-200 rounded-lg hover:bg-red-50 transition-colors"
            >
              Delete User
            </button>
          )}
        </div>
      </div>
    </>
  )
}

// ── Status Badge ──────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: 'active' | 'invited' | 'pending' }) {
  if (status === 'active') {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ring-1 ring-inset bg-emerald-50 text-emerald-700 ring-emerald-600/20">
        Active
      </span>
    )
  }
  // 'invited' or 'pending'
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ring-1 ring-inset bg-amber-50 text-amber-700 ring-amber-600/20">
      Pending
    </span>
  )
}

// ── Role Badge ────────────────────────────────────────────────────────────────

function RoleBadge({ role }: { role: 'super-admin' | null }) {
  if (role !== 'super-admin') return null
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-pdi-sky/10 text-pdi-sky">
      Super Admin
    </span>
  )
}

// ── Main Component ────────────────────────────────────────────────────────────

export default function Users() {
  const [users, setUsers] = useState<UserWithRoles[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [showAddUser, setShowAddUser] = useState(false)
  const [selectedUser, setSelectedUser] = useState<UserWithRoles | null>(null)

  const loadData = async () => {
    try {
      const [usersData, projectsData] = await Promise.all([
        api.getUsers(),
        api.getProjects().then((r) => r.projects),
      ])
      setUsers(usersData)
      setProjects(projectsData)

      // Keep selected user in sync after reload
      if (selectedUser) {
        const refreshed = usersData.find((u) => u.sub === selectedUser.sub)
        setSelectedUser(refreshed ?? null)
      }
    } catch {
      // errors are non-fatal; table stays empty
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const filtered = users.filter((u) => {
    const q = search.toLowerCase()
    if (!q) return true
    return (
      u.email.toLowerCase().includes(q) ||
      (u.name ?? '').toLowerCase().includes(q)
    )
  })

  const formatDate = (iso: string | null) => {
    if (!iso) return '—'
    return new Date(iso).toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    })
  }

  return (
    <div className="h-full overflow-y-auto bg-gray-50">
      <div className="max-w-5xl mx-auto px-6 py-8">

        {/* Page header */}
        <div className="relative flex items-center justify-between mb-6 px-6 py-4 border-b border-pdi-cool-gray bg-white -mx-6 -mt-8">
          <div>
            <h2 className="text-lg font-bold text-pdi-granite">Users</h2>
            <p className="text-xs text-pdi-slate mt-1">
              Manage user access, global roles, and project assignments.
            </p>
          </div>
          <button
            onClick={() => setShowAddUser(true)}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-pdi-sky rounded-lg hover:bg-pdi-indigo transition-colors"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
            </svg>
            Add User
          </button>
          <div className="absolute bottom-0 left-0 right-0 h-[2px] bg-gradient-to-r from-pdi-sky via-pdi-ocean to-pdi-indigo opacity-60" />
        </div>

        {/* Search */}
        <div className="mb-4">
          <div className="relative max-w-sm">
            <svg
              className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-4.35-4.35M17 11A6 6 0 115 11a6 6 0 0112 0z" />
            </svg>
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by name or email…"
              className="w-full pl-9 pr-3 py-2 text-sm border border-pdi-cool-gray rounded-lg focus:outline-none focus:ring-2 focus:ring-pdi-sky bg-white"
            />
          </div>
        </div>

        {/* Table card */}
        <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
          {loading ? (
            <div className="py-16 text-center text-pdi-slate text-sm">Loading users…</div>
          ) : filtered.length === 0 ? (
            <div className="py-16 text-center text-pdi-slate">
              <svg className="w-10 h-10 mx-auto mb-3 opacity-40" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M17 20h5v-2a4 4 0 00-5.356-3.712M9 20H4v-2a4 4 0 015.356-3.712M15 7a4 4 0 11-8 0 4 4 0 018 0zm6 3a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
              <p className="text-sm">
                {search ? 'No users match your search.' : 'No users yet. Add a user to get started.'}
              </p>
            </div>
          ) : (
            <table className="min-w-full divide-y divide-gray-100">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-5 py-3 text-left text-xs font-semibold text-pdi-slate uppercase tracking-wide">
                    Name / Email
                  </th>
                  <th className="px-5 py-3 text-left text-xs font-semibold text-pdi-slate uppercase tracking-wide">
                    Role
                  </th>
                  <th className="px-5 py-3 text-left text-xs font-semibold text-pdi-slate uppercase tracking-wide">
                    Projects
                  </th>
                  <th className="px-5 py-3 text-left text-xs font-semibold text-pdi-slate uppercase tracking-wide">
                    Status
                  </th>
                  <th className="px-5 py-3 text-left text-xs font-semibold text-pdi-slate uppercase tracking-wide">
                    Last Login
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {filtered.map((u) => {
                  const displayName = u.name ?? u.email
                  const projectCount = Object.keys(u.project_roles).length
                  return (
                    <tr
                      key={u.sub}
                      onClick={() => setSelectedUser(u)}
                      className="hover:bg-gray-50 cursor-pointer transition-colors"
                    >
                      <td className="px-5 py-3.5">
                        <div className="text-sm font-medium text-pdi-granite truncate max-w-[220px]">
                          {displayName}
                        </div>
                        {u.name && (
                          <div className="text-xs text-pdi-slate truncate max-w-[220px]">{u.email}</div>
                        )}
                      </td>
                      <td className="px-5 py-3.5">
                        <RoleBadge role={u.global_role} />
                      </td>
                      <td className="px-5 py-3.5">
                        <span className="text-sm text-pdi-slate">{projectCount}</span>
                      </td>
                      <td className="px-5 py-3.5">
                        <StatusBadge status={u.status} />
                      </td>
                      <td className="px-5 py-3.5 text-sm text-pdi-slate whitespace-nowrap">
                        {formatDate(u.last_login)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* Add user modal */}
      {showAddUser && (
        <AddUserModal
          onClose={() => setShowAddUser(false)}
          onSuccess={loadData}
        />
      )}

      {/* User detail panel */}
      {selectedUser && (
        <UserPanel
          user={selectedUser}
          projects={projects}
          onClose={() => setSelectedUser(null)}
          onChanged={loadData}
        />
      )}
    </div>
  )
}
