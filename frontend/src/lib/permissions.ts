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
