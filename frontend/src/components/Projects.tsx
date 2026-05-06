import { useState, useEffect, useMemo } from 'react'
import { api, type Project, type Instance, type TagFilter, type WebhookInfo, type WebhookRouting } from '../lib/api'

interface ProjectsProps {
  projects: Project[]
  onReload: () => void
}

function TagFilterEditor({
  tagFilter,
  onChange,
}: {
  tagFilter: TagFilter
  onChange: (tf: TagFilter) => void
}) {
  const entries = Object.entries(tagFilter.tags)

  const addTag = () => {
    onChange({ ...tagFilter, tags: { ...tagFilter.tags, '': '' } })
  }

  const removeTag = (key: string) => {
    const next = { ...tagFilter.tags }
    delete next[key]
    onChange({ ...tagFilter, tags: next })
  }

  const updateKey = (oldKey: string, newKey: string) => {
    const next: Record<string, string> = {}
    for (const [k, v] of Object.entries(tagFilter.tags)) {
      next[k === oldKey ? newKey : k] = v
    }
    onChange({ ...tagFilter, tags: next })
  }

  const updateValue = (key: string, value: string) => {
    onChange({ ...tagFilter, tags: { ...tagFilter.tags, [key]: value } })
  }

  return (
    <div className="space-y-3">
      {/* AND / OR toggle */}
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium text-pdi-slate">Match logic:</span>
        {(['AND', 'OR'] as const).map((logic) => (
          <button
            key={logic}
            type="button"
            onClick={() => onChange({ ...tagFilter, logic })}
            className={`px-3 py-1 text-xs font-medium rounded-full border transition-colors ${
              tagFilter.logic === logic
                ? 'bg-pdi-sky text-white border-pdi-sky'
                : 'bg-white text-pdi-slate border-pdi-cool-gray hover:border-pdi-sky'
            }`}
          >
            {logic}
          </button>
        ))}
        <span className="text-xs text-pdi-slate">
          {tagFilter.logic === 'AND' ? '— all tags must match' : '— any tag must match'}
        </span>
      </div>

      {/* Tag rows */}
      <div className="space-y-2">
        {entries.map(([k, v], i) => (
          <div key={i} className="flex items-center gap-2">
            <input
              type="text"
              placeholder="key"
              value={k}
              onChange={(e) => updateKey(k, e.target.value)}
              className="w-28 text-sm border border-pdi-cool-gray rounded-md px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-pdi-sky font-mono"
            />
            <span className="text-pdi-slate text-sm">=</span>
            <input
              type="text"
              placeholder="value"
              value={v}
              onChange={(e) => updateValue(k, e.target.value)}
              className="flex-1 text-sm border border-pdi-cool-gray rounded-md px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-pdi-sky font-mono"
            />
            <button
              type="button"
              onClick={() => removeTag(k)}
              className="text-gray-400 hover:text-pdi-orange transition-colors"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        ))}
        <button
          type="button"
          onClick={addTag}
          className="flex items-center gap-1.5 text-sm text-pdi-sky hover:text-pdi-indigo transition-colors"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
          </svg>
          Add tag filter
        </button>
      </div>

      {entries.length === 0 && (
        <p className="text-xs text-pdi-slate bg-gray-50 border border-pdi-cool-gray rounded px-2 py-1.5">
          Empty filter — only global (untagged) instances will be used.
        </p>
      )}
    </div>
  )
}

function computePreview(tagFilter: TagFilter | null, allInstances: Instance[]): Instance[] {
  if (tagFilter === null) {
    return allInstances.filter((i) => Object.keys(i.tags).length === 0)
  }
  return allInstances.filter((i) => {
    if (Object.keys(i.tags).length === 0) return true // global always included
    if (!tagFilter.tags || Object.keys(tagFilter.tags).length === 0) return false
    if (tagFilter.logic === 'AND') {
      return Object.entries(tagFilter.tags).every(([k, v]) => i.tags[k] === v)
    }
    return Object.entries(tagFilter.tags).some(([k, v]) => i.tags[k] === v)
  })
}

function InstancePreviewPanel({
  tagFilter,
  allInstances,
}: {
  tagFilter: TagFilter | null
  allInstances: Instance[]
}) {
  const resolved = useMemo(() => computePreview(tagFilter, allInstances), [tagFilter, allInstances])

  return (
    <div className="border border-pdi-cool-gray rounded-lg p-3 bg-gray-50">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium text-pdi-slate">Preview</span>
        <span className="text-xs text-pdi-slate">
          {resolved.length} of {allInstances.length} instance{allInstances.length !== 1 ? 's' : ''} will be used
        </span>
      </div>
      {resolved.length === 0 ? (
        <p className="text-xs text-pdi-slate italic">No instances match this filter.</p>
      ) : (
        <div className="space-y-1.5">
          {resolved.map((inst) => (
            <div key={inst.id} className="flex items-center gap-2">
              <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium bg-pdi-sky/10 text-pdi-indigo">
                {inst.type}
              </span>
              <span className="text-xs font-medium text-pdi-granite">{inst.name}</span>
              {Object.keys(inst.tags).length === 0 ? (
                <span className="text-xs text-pdi-sun bg-pdi-sun/10 px-1.5 py-0.5 rounded-full">global</span>
              ) : (
                <div className="flex gap-1">
                  {Object.entries(inst.tags).map(([k, v]) => (
                    <span key={k} className="text-xs font-mono text-pdi-slate bg-gray-100 px-1.5 py-0.5 rounded">
                      {k}={v}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function ProjectModal({
  project,
  onClose,
  onSave,
}: {
  project: Project | null
  onClose: () => void
  onSave: () => void
}) {
  const [name, setName] = useState(project?.name ?? '')
  const [description, setDescription] = useState(project?.description ?? '')
  const [tagFilter, setTagFilter] = useState<TagFilter>(
    project?.tag_filter ?? { logic: 'AND', tags: {} }
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [allInstances, setAllInstances] = useState<Instance[]>([])
  const [webhookWriteBack, setWebhookWriteBack] = useState<Record<string, boolean | null>>(
    project?.webhook_write_back ?? {}
  )
  const [webhooks, setWebhooks] = useState<WebhookInfo[]>([])
  const [webhookRouting, setWebhookRouting] = useState<WebhookRouting>(
    project?.webhook_routing ?? { ado: [], pagerduty: [], salesforce: [] }
  )

  useEffect(() => {
    api.listInstances().then(setAllInstances).catch(() => {})
    api.getWebhooks().then((r) => setWebhooks(r.webhooks ?? [])).catch(() => {})
  }, [])

  const handleSave = async () => {
    if (!name.trim()) {
      setError('Project name is required')
      return
    }
    setSaving(true)
    setError(null)
    try {
      const payload = {
        name: name.trim(),
        description: description.trim(),
        tag_filter: tagFilter,
        webhook_write_back: Object.keys(webhookWriteBack).length > 0 ? webhookWriteBack : null,
        webhook_routing: (webhookRouting.ado.length || webhookRouting.pagerduty.length || webhookRouting.salesforce.length)
          ? webhookRouting
          : null,
      }
      if (project) {
        await api.updateProject(project.id, payload)
      } else {
        await api.createProject(payload)
      }
      onSave()
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save project')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl mx-4 overflow-hidden max-h-[90vh] flex flex-col">
        <div className="px-6 py-4 border-b border-pdi-cool-gray">
          <h2 className="text-lg font-semibold text-pdi-granite">
            {project ? 'Edit Project' : 'Create Project'}
          </h2>
        </div>
        <div className="px-6 py-5 space-y-5 overflow-y-auto flex-1">
          <div>
            <label className="block text-sm font-medium text-pdi-granite mb-1">Name</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Logistics Cloud"
              className="w-full text-sm border border-pdi-cool-gray rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-pdi-sky"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-pdi-granite mb-1">Description</label>
            <input
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Optional description"
              className="w-full text-sm border border-pdi-cool-gray rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-pdi-sky"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-pdi-granite mb-2">
              Instance Tag Filter
            </label>
            <p className="text-xs text-pdi-slate mb-3">
              Define which tagged instances this project uses. Global (untagged) instances are always included.
              Manage instances on the <a href="/instances" className="text-pdi-sky hover:underline">Instances page</a>.
            </p>
            <TagFilterEditor tagFilter={tagFilter} onChange={setTagFilter} />
          </div>
          <div>
            <label className="block text-sm font-medium text-pdi-granite mb-2">
              Resolved Instances
            </label>
            <InstancePreviewPanel tagFilter={tagFilter} allInstances={allInstances} />
          </div>

          {/* ── Write-Back Settings ─────────────────────────────────── */}
          {webhooks.length > 0 && (
            <div>
              <label className="block text-sm font-medium text-pdi-granite mb-1">
                Write-Back Settings
              </label>
              <p className="text-xs text-pdi-slate mb-3">
                Control whether Holmes writes investigation results back to each source system for this project.
                "Inherit" uses the global default from the Integrations page.
              </p>
              <div className="space-y-3">
                {webhooks.map((wh) => {
                  const current = webhookWriteBack[wh.id] ?? null
                  return (
                    <div key={wh.id} className="flex items-center justify-between py-2 px-3 bg-gray-50 rounded-lg">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium text-pdi-granite">{wh.name}</span>
                        {!wh.write_back_capable && (
                          <span className="text-xs text-pdi-slate bg-gray-200 px-1.5 py-0.5 rounded">
                            credentials not configured
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-1">
                        {([
                          { value: null, label: 'Inherit' },
                          { value: true, label: 'Enabled' },
                          { value: false, label: 'Disabled' },
                        ] as const).map(({ value, label }) => (
                          <button
                            key={String(value)}
                            type="button"
                            onClick={() => {
                              const next = { ...webhookWriteBack }
                              if (value === null) {
                                delete next[wh.id]
                              } else {
                                next[wh.id] = value
                              }
                              setWebhookWriteBack(next)
                            }}
                            className={`px-2.5 py-1 text-xs font-medium rounded-full border transition-colors ${
                              current === value
                                ? value === true
                                  ? 'bg-emerald-500 text-white border-emerald-500'
                                  : value === false
                                    ? 'bg-pdi-orange text-white border-pdi-orange'
                                    : 'bg-pdi-sky text-white border-pdi-sky'
                                : 'bg-white text-pdi-slate border-pdi-cool-gray hover:border-pdi-sky'
                            }`}
                          >
                            {label}
                          </button>
                        ))}
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
          {/* Webhook Routing */}
          <div>
            <label className="block text-sm font-medium text-pdi-granite mb-1">
              Webhook Routing
            </label>
            <p className="text-xs text-pdi-slate mb-3">
              Map incoming webhook events to this project. Events that don't match any project use global integrations.
            </p>
            <div className="space-y-3">
              {([
                { key: 'ado' as const, label: 'ADO Projects', placeholder: 'e.g. PDI Dispatch' },
                { key: 'pagerduty' as const, label: 'PagerDuty Services', placeholder: 'e.g. checkout-api' },
                { key: 'salesforce' as const, label: 'Salesforce Accounts', placeholder: 'e.g. Acme Corp' },
              ]).map(({ key, label, placeholder }) => (
                <div key={key} className="py-2 px-3 bg-gray-50 rounded-lg">
                  <span className="text-xs font-medium text-pdi-slate block mb-1.5">{label}</span>
                  <div className="flex flex-wrap gap-1.5 mb-2">
                    {webhookRouting[key].map((val, idx) => (
                      <span
                        key={idx}
                        className="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium bg-pdi-sky/10 text-pdi-indigo border border-pdi-sky/30 rounded-full"
                      >
                        {val}
                        <button
                          type="button"
                          onClick={() => {
                            const next = { ...webhookRouting, [key]: webhookRouting[key].filter((_: string, i: number) => i !== idx) }
                            setWebhookRouting(next)
                          }}
                          className="text-pdi-slate hover:text-pdi-orange ml-0.5"
                        >
                          x
                        </button>
                      </span>
                    ))}
                  </div>
                  <input
                    type="text"
                    placeholder={placeholder}
                    className="w-full text-xs border border-pdi-cool-gray rounded-md px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-pdi-sky"
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault()
                        const input = e.currentTarget
                        const value = input.value.trim()
                        if (value && !webhookRouting[key].includes(value)) {
                          setWebhookRouting({ ...webhookRouting, [key]: [...webhookRouting[key], value] })
                          input.value = ''
                        }
                      }
                    }}
                  />
                </div>
              ))}
            </div>
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
            onClick={handleSave}
            disabled={saving}
            className="px-4 py-2 text-sm font-medium text-white bg-pdi-sky rounded-lg hover:bg-pdi-indigo transition-colors disabled:opacity-50"
          >
            {saving ? 'Saving…' : 'Save Project'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default function Projects({ projects: projectsProp, onReload }: ProjectsProps) {
  const projects = projectsProp ?? []
  const [editingProject, setEditingProject] = useState<Project | null | undefined>(undefined)
  const [deleting, setDeleting] = useState<string | null>(null)
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)

  const handleDeleteConfirmed = async (id: string) => {
    setConfirmDeleteId(null)
    setDeleting(id)
    try {
      await api.deleteProject(id)
      onReload()
    } catch {
      // silently ignore — project may already be gone
    } finally {
      setDeleting(null)
    }
  }

  const filterSummary = (p: Project) => {
    if (!p.tag_filter || Object.keys(p.tag_filter.tags).length === 0) {
      return 'Global instances only'
    }
    const parts = Object.entries(p.tag_filter.tags).map(([k, v]) => `${k}=${v}`)
    return `${p.tag_filter.logic}: ${parts.join(', ')}`
  }

  return (
    <div className="h-full overflow-y-auto bg-gray-50">
      <div className="max-w-4xl mx-auto px-6 py-8">
        <div className="relative flex items-center justify-between mb-6 px-6 py-4 border-b border-pdi-cool-gray bg-white -mx-6 -mt-8">
          <div>
            <h2 className="text-lg font-bold text-pdi-granite">Projects</h2>
            <p className="text-xs text-pdi-slate mt-1">
              Projects use tag filters to select which instances to use. Select a project in the sidebar to scope your chat.
            </p>
          </div>
          <button
            onClick={() => setEditingProject(null)}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-pdi-sky rounded-lg hover:bg-pdi-indigo transition-colors"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
            </svg>
            New Project
          </button>
          <div className="absolute bottom-0 left-0 right-0 h-[2px] bg-gradient-to-r from-pdi-sky via-pdi-ocean to-pdi-indigo opacity-60" />
        </div>

        {projects.length === 0 ? (
          <div className="text-center py-16 text-pdi-slate">
            <svg className="w-12 h-12 mx-auto mb-3 opacity-40" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
            </svg>
            <p className="text-sm">No projects yet. Create one to get started.</p>
          </div>
        ) : (
          <div className="grid gap-4">
            {projects.map((p) => (
              <div
                key={p.id}
                className="bg-white rounded-xl border border-pdi-cool-gray px-5 py-4 flex items-start justify-between gap-4 hover:border-pdi-sky/30 hover:shadow-md transition-all"
              >
                <div className="min-w-0">
                  <h3 className="font-semibold text-pdi-granite truncate">{p.name}</h3>
                  {p.description && (
                    <p className="text-sm text-pdi-slate mt-0.5 truncate">{p.description}</p>
                  )}
                  <div className="mt-2">
                    {p.tag_filter && Object.keys(p.tag_filter.tags).length > 0 ? (
                      <div className="flex flex-wrap gap-1.5">
                        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-pdi-slate">
                          {p.tag_filter.logic}
                        </span>
                        {Object.entries(p.tag_filter.tags).map(([k, v]) => (
                          <span
                            key={k}
                            className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-mono bg-pdi-sky/10 text-pdi-indigo"
                          >
                            {k}={v}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <span className="text-xs text-pdi-sun bg-pdi-sun/10 px-2 py-0.5 rounded-full">
                        {filterSummary(p)}
                      </span>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <button
                    onClick={() => setEditingProject(p)}
                    className="p-1.5 text-gray-400 hover:text-pdi-sky transition-colors rounded"
                    title="Edit"
                  >
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931zm0 0L19.5 7.125" />
                    </svg>
                  </button>
                  {confirmDeleteId === p.id ? (
                    <>
                      <span className="text-xs text-gray-600">Delete?</span>
                      <button
                        onClick={() => handleDeleteConfirmed(p.id)}
                        className="px-2 py-1 text-xs font-medium text-white bg-pdi-orange rounded hover:bg-pdi-orange/90 transition-colors"
                      >
                        Yes
                      </button>
                      <button
                        onClick={() => setConfirmDeleteId(null)}
                        className="px-2 py-1 text-xs font-medium text-gray-600 bg-gray-100 rounded hover:bg-gray-200 transition-colors"
                      >
                        No
                      </button>
                    </>
                  ) : (
                    <button
                      onClick={() => setConfirmDeleteId(p.id)}
                      disabled={deleting === p.id}
                      className="p-1.5 text-gray-400 hover:text-pdi-orange transition-colors rounded disabled:opacity-40"
                      title="Delete"
                    >
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0" />
                      </svg>
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {editingProject !== undefined && (
        <ProjectModal
          project={editingProject}
          onClose={() => setEditingProject(undefined)}
          onSave={onReload}
        />
      )}
    </div>
  )
}
