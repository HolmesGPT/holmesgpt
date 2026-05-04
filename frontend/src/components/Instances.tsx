import { useState, useEffect, useCallback } from 'react'
import { api, type Instance, type TestConnectionResponse } from '../lib/api'

const TOOLSET_TYPES = [
  'grafana/dashboards',
  'grafana/loki',
  'grafana/tempo',
  'prometheus/metrics',
  'aws_api',
  'ado',
  'atlassian',
  'salesforce',
  'kubernetes',
  'dbdash',
  'pagerduty',
]

const MCP_TYPES = new Set(['ado', 'atlassian', 'salesforce'])

function TagsEditor({
  tags,
  onChange,
}: {
  tags: Record<string, string>
  onChange: (tags: Record<string, string>) => void
}) {
  const entries = Object.entries(tags)

  const addTag = () => {
    onChange({ ...tags, '': '' })
  }

  const removeTag = (key: string) => {
    const next = { ...tags }
    delete next[key]
    onChange(next)
  }

  const updateKey = (oldKey: string, newKey: string) => {
    const next: Record<string, string> = {}
    for (const [k, v] of Object.entries(tags)) {
      next[k === oldKey ? newKey : k] = v
    }
    onChange(next)
  }

  const updateValue = (key: string, value: string) => {
    onChange({ ...tags, [key]: value })
  }

  return (
    <div className="space-y-2">
      {entries.map(([k, v], i) => (
        <div key={i} className="flex items-center gap-2">
          <input
            type="text"
            placeholder="key"
            value={k}
            onChange={(e) => updateKey(k, e.target.value)}
            className="w-32 text-sm border border-pdi-cool-gray rounded-md px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-pdi-sky font-mono"
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
            className="text-pdi-slate hover:text-pdi-orange transition-colors"
            title="Remove tag"
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
        Add tag
      </button>
      {entries.length === 0 && (
        <p className="text-xs text-pdi-sun bg-pdi-sun/10 border border-pdi-sun/20 rounded px-2 py-1.5">
          No tags — this instance will be <strong>global</strong> (available to all projects)
        </p>
      )}
    </div>
  )
}

function InstanceFormDialog({
  instance,
  onClose,
  onSave,
}: {
  instance: Instance | null
  onClose: () => void
  onSave: () => void
}) {
  const [type, setType] = useState(instance?.type ?? TOOLSET_TYPES[0])
  const [name, setName] = useState(instance?.name ?? '')
  const [tags, setTags] = useState<Record<string, string>>(instance?.tags ?? {})
  const [secretArn, setSecretArn] = useState(instance?.secret_arn ?? '')
  const [mcpUrl, setMcpUrl] = useState(instance?.mcp_url ?? '')
  const [awsAccountName, setAwsAccountName] = useState(instance?.aws_account_name ?? '')
  const [awsAccountId, setAwsAccountId] = useState(instance?.aws_account_id ?? '')
  const [awsRoleArn, setAwsRoleArn] = useState(instance?.aws_role_arn ?? '')
  const [awsRegions, setAwsRegions] = useState<string[]>(instance?.aws_regions ?? [])
  const [pdServiceIds, setPdServiceIds] = useState<string[]>(
    (instance?.config as { service_ids?: string[] } | null | undefined)?.service_ids ?? []
  )
  const [pdTeamIds, setPdTeamIds] = useState<string[]>(
    (instance?.config as { team_ids?: string[] } | null | undefined)?.team_ids ?? []
  )
  const [pdServiceInput, setPdServiceInput] = useState('')
  const [pdTeamInput, setPdTeamInput] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<TestConnectionResponse | null>(null)
  const [connectionStatus, setConnectionStatus] = useState<string | null>(instance?.aws_connection_status ?? null)
  const [connectionError, setConnectionError] = useState<string | null>(instance?.aws_connection_error ?? null)

  const isMcp = MCP_TYPES.has(type)
  const isAws = type === 'aws_api'
  const isPagerDuty = type === 'pagerduty'

  const handleSave = async () => {
    if (!name.trim()) {
      setError('Instance name is required')
      return
    }
    if (isAws && !instance) {
      if (!awsAccountName.trim()) { setError('Account Name is required'); return }
      if (!awsAccountId.trim()) { setError('Account Number is required'); return }
      if (!awsRoleArn.trim()) { setError('Role ARN is required'); return }
      if (!/^arn:aws:iam::\d{12}:role\//.test(awsRoleArn.trim())) {
        setError('Role ARN must match format: arn:aws:iam::<account-id>:role/<role-name>')
        return
      }
    }
    setSaving(true)
    setError(null)
    try {
      const payload = {
        type,
        name: name.trim(),
        tags,
        secret_arn: secretArn.trim() || null,
        mcp_url: isMcp ? (mcpUrl.trim() || null) : null,
        aws_account_name: isAws ? (awsAccountName.trim() || null) : null,
        aws_account_id: isAws ? (awsAccountId.trim() || null) : null,
        aws_role_arn: isAws ? (awsRoleArn.trim() || null) : null,
        aws_regions: isAws && awsRegions.length > 0 ? awsRegions : null,
        config: isPagerDuty
          ? (pdServiceIds.length > 0 || pdTeamIds.length > 0
              ? {
                  ...(pdServiceIds.length > 0 ? { service_ids: pdServiceIds } : {}),
                  ...(pdTeamIds.length > 0 ? { team_ids: pdTeamIds } : {}),
                }
              : null)
          : (instance?.config ?? null),
      }
      if (instance) {
        await api.updateInstance(instance.id, payload)
      } else {
        await api.createInstance(payload)
      }
      onSave()
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save instance')
    } finally {
      setSaving(false)
    }
  }

  const handleTestConnection = async () => {
    if (!instance) return
    setTesting(true)
    setTestResult(null)
    try {
      const result = await api.testInstanceConnection(instance.id)
      setTestResult(result)
      setConnectionStatus(result.status)
      setConnectionError(result.error ?? null)
    } catch (e) {
      setTestResult({ ok: false, status: 'error', error: e instanceof Error ? e.message : 'Test failed' })
      setConnectionStatus('error')
      setConnectionError(e instanceof Error ? e.message : 'Test failed')
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-lg mx-4 overflow-hidden max-h-[90vh] flex flex-col">
        <div className="px-6 py-4 border-b border-pdi-cool-gray">
          <h2 className="text-lg font-semibold text-pdi-granite">
            {instance ? 'Edit Instance' : 'Create Instance'}
          </h2>
        </div>
        <div className="px-6 py-5 space-y-4 overflow-y-auto flex-1">
          <div>
            <label className="block text-sm font-medium text-pdi-granite mb-1">Type</label>
            <select
              value={type}
              onChange={(e) => setType(e.target.value)}
              className="w-full text-sm border border-pdi-cool-gray rounded-md px-3 py-2 bg-white focus:outline-none focus:ring-2 focus:ring-pdi-sky"
            >
              {TOOLSET_TYPES.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium text-pdi-granite mb-1">Name</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. grafana-retail-prod"
              className="w-full text-sm border border-pdi-cool-gray rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-pdi-sky"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-pdi-granite mb-2">Tags</label>
            <p className="text-xs text-pdi-slate mb-2">
              Tags are used to match this instance to projects. Instances with no tags are global.
            </p>
            <TagsEditor tags={tags} onChange={setTags} />
          </div>

          <div>
            <label className="block text-sm font-medium text-pdi-granite mb-1">Secret ARN</label>
            <input
              type="text"
              value={secretArn}
              onChange={(e) => setSecretArn(e.target.value)}
              placeholder="arn:aws:secretsmanager:... (optional)"
              className="w-full text-sm border border-pdi-cool-gray rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-pdi-sky"
            />
            <p className="text-xs text-pdi-slate mt-1">Leave blank to use globally configured credentials.</p>
          </div>

          {isMcp && (
            <div>
              <label className="block text-sm font-medium text-pdi-granite mb-1">MCP Server URL</label>
              <input
                type="text"
                value={mcpUrl}
                onChange={(e) => setMcpUrl(e.target.value)}
                placeholder="https://... (optional — leave blank for global)"
                className="w-full text-sm border border-pdi-cool-gray rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-pdi-sky"
              />
            </div>
          )}

          {isAws && (
            <div className="space-y-4 p-4 bg-gray-50 rounded-lg border border-pdi-cool-gray">
              <p className="text-xs font-medium text-pdi-slate uppercase tracking-wider">AWS Account Details</p>
              <div>
                <label className="block text-sm font-medium text-pdi-granite mb-1">
                  Account Name {!instance && <span className="text-pdi-orange">*</span>}
                </label>
                <input
                  type="text"
                  value={awsAccountName}
                  onChange={(e) => setAwsAccountName(e.target.value)}
                  placeholder="e.g. Retail Production"
                  className="w-full text-sm border border-pdi-cool-gray rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-pdi-sky"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-pdi-granite mb-1">
                  Account Number {!instance && <span className="text-pdi-orange">*</span>}
                </label>
                <input
                  type="text"
                  value={awsAccountId}
                  onChange={(e) => setAwsAccountId(e.target.value.replace(/\D/g, '').slice(0, 12))}
                  placeholder="123456789012"
                  className="w-full text-sm border border-pdi-cool-gray rounded-md px-3 py-2 font-mono focus:outline-none focus:ring-2 focus:ring-pdi-sky"
                />
                <p className="text-xs text-pdi-slate mt-1">12-digit AWS account ID</p>
              </div>
              <div>
                <label className="block text-sm font-medium text-pdi-granite mb-1">
                  Role ARN {!instance && <span className="text-pdi-orange">*</span>}
                </label>
                <input
                  type="text"
                  value={awsRoleArn}
                  onChange={(e) => setAwsRoleArn(e.target.value)}
                  placeholder="arn:aws:iam::123456789012:role/HolmesGPT-ReadOnly"
                  className="w-full text-sm border border-pdi-cool-gray rounded-md px-3 py-2 font-mono focus:outline-none focus:ring-2 focus:ring-pdi-sky"
                />
                <p className="text-xs text-pdi-slate mt-1">
                  IAM role ARN created by the setup script. See Docs &gt; AWS Account tab.
                </p>
              </div>

              {/* AWS Regions */}
              <div>
                <label className="block text-sm font-medium text-pdi-granite mb-1">Regions</label>
                <div className="flex flex-wrap gap-1.5 mb-2">
                  {awsRegions.map((r) => (
                    <span key={r} className="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-mono bg-pdi-sky/10 text-pdi-sky border border-pdi-sky/20 rounded-md">
                      {r}
                      <button type="button" onClick={() => setAwsRegions(awsRegions.filter((x) => x !== r))} className="hover:text-pdi-orange">
                        &times;
                      </button>
                    </span>
                  ))}
                </div>
                <select
                  value=""
                  onChange={(e) => {
                    if (e.target.value && !awsRegions.includes(e.target.value)) {
                      setAwsRegions([...awsRegions, e.target.value])
                    }
                  }}
                  className="w-full text-sm border border-pdi-cool-gray rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-pdi-sky"
                >
                  <option value="">Add a region...</option>
                  {['us-east-1', 'us-east-2', 'us-west-2', 'eu-central-1', 'eu-west-1', 'eu-west-2', 'ap-southeast-1', 'ap-northeast-1'].filter((r) => !awsRegions.includes(r)).map((r) => (
                    <option key={r} value={r}>{r}</option>
                  ))}
                </select>
                <p className="text-xs text-pdi-slate mt-1">
                  Restrict investigations to these AWS regions. Leave empty to allow all regions.
                </p>
              </div>

              {/* Connection status */}
              {connectionStatus && (
                <div className={`flex items-start gap-2 text-xs rounded-md px-3 py-2 ${
                  connectionStatus === 'success'
                    ? 'bg-pdi-grass/10 text-pdi-grass border border-pdi-grass/20'
                    : 'bg-pdi-orange/10 text-pdi-orange border border-pdi-orange/20'
                }`}>
                  <span className={`mt-0.5 w-2 h-2 rounded-full shrink-0 ${
                    connectionStatus === 'success' ? 'bg-pdi-grass' : 'bg-pdi-orange'
                  }`} />
                  <div>
                    <span className="font-medium">
                      {connectionStatus === 'success' ? 'Connected' : 'Connection failed'}
                    </span>
                    {connectionError && (
                      <p className="mt-0.5 text-[11px] opacity-80 break-all">{connectionError}</p>
                    )}
                    {testResult?.assumed_role && (
                      <p className="mt-0.5 text-[11px] opacity-80 font-mono">{testResult.assumed_role}</p>
                    )}
                  </div>
                </div>
              )}

              {/* Test Connection button — only for saved instances */}
              {instance && awsRoleArn.trim() && (
                <button
                  type="button"
                  onClick={handleTestConnection}
                  disabled={testing}
                  className="flex items-center gap-2 px-3 py-1.5 text-xs font-medium text-pdi-sky border border-pdi-sky/30 rounded-lg hover:bg-pdi-sky/5 transition-colors disabled:opacity-50"
                >
                  {testing ? (
                    <>
                      <svg className="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                      </svg>
                      Testing...
                    </>
                  ) : (
                    <>
                      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
                      </svg>
                      Test Connection
                    </>
                  )}
                </button>
              )}
            </div>
          )}

          {isPagerDuty && (
            <div className="space-y-4 p-4 bg-gray-50 rounded-lg border border-pdi-cool-gray">
              <p className="text-xs font-medium text-pdi-slate uppercase tracking-wider">
                PagerDuty Project Scope
              </p>
              <p className="text-xs text-pdi-slate">
                PagerDuty API keys are account-wide. Scope this instance to specific
                services or teams by listing IDs below. Leave empty to allow all.
              </p>

              {/* Service IDs */}
              <div>
                <label className="block text-sm font-medium text-pdi-granite mb-1">Service IDs</label>
                <div className="flex flex-wrap gap-1.5 mb-2">
                  {pdServiceIds.map((id) => (
                    <span key={id} className="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-mono bg-pdi-sky/10 text-pdi-sky border border-pdi-sky/20 rounded-md">
                      {id}
                      <button
                        type="button"
                        onClick={() => setPdServiceIds(pdServiceIds.filter((x) => x !== id))}
                        className="hover:text-pdi-orange"
                      >
                        &times;
                      </button>
                    </span>
                  ))}
                </div>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={pdServiceInput}
                    onChange={(e) => setPdServiceInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault()
                        const v = pdServiceInput.trim()
                        if (v && !pdServiceIds.includes(v)) {
                          setPdServiceIds([...pdServiceIds, v])
                          setPdServiceInput('')
                        }
                      }
                    }}
                    placeholder="e.g. PSVC123"
                    className="flex-1 text-sm border border-pdi-cool-gray rounded-md px-3 py-2 font-mono focus:outline-none focus:ring-2 focus:ring-pdi-sky"
                  />
                  <button
                    type="button"
                    onClick={() => {
                      const v = pdServiceInput.trim()
                      if (v && !pdServiceIds.includes(v)) {
                        setPdServiceIds([...pdServiceIds, v])
                        setPdServiceInput('')
                      }
                    }}
                    className="px-3 py-2 text-sm font-medium text-pdi-slate bg-white border border-pdi-cool-gray rounded-md hover:bg-gray-50 transition-colors"
                  >
                    Add
                  </button>
                </div>
              </div>

              {/* Team IDs */}
              <div>
                <label className="block text-sm font-medium text-pdi-granite mb-1">Team IDs</label>
                <div className="flex flex-wrap gap-1.5 mb-2">
                  {pdTeamIds.map((id) => (
                    <span key={id} className="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-mono bg-pdi-sky/10 text-pdi-sky border border-pdi-sky/20 rounded-md">
                      {id}
                      <button
                        type="button"
                        onClick={() => setPdTeamIds(pdTeamIds.filter((x) => x !== id))}
                        className="hover:text-pdi-orange"
                      >
                        &times;
                      </button>
                    </span>
                  ))}
                </div>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={pdTeamInput}
                    onChange={(e) => setPdTeamInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault()
                        const v = pdTeamInput.trim()
                        if (v && !pdTeamIds.includes(v)) {
                          setPdTeamIds([...pdTeamIds, v])
                          setPdTeamInput('')
                        }
                      }
                    }}
                    placeholder="e.g. PTEAM456"
                    className="flex-1 text-sm border border-pdi-cool-gray rounded-md px-3 py-2 font-mono focus:outline-none focus:ring-2 focus:ring-pdi-sky"
                  />
                  <button
                    type="button"
                    onClick={() => {
                      const v = pdTeamInput.trim()
                      if (v && !pdTeamIds.includes(v)) {
                        setPdTeamIds([...pdTeamIds, v])
                        setPdTeamInput('')
                      }
                    }}
                    className="px-3 py-2 text-sm font-medium text-pdi-slate bg-white border border-pdi-cool-gray rounded-md hover:bg-gray-50 transition-colors"
                  >
                    Add
                  </button>
                </div>
              </div>

              {/* Connection status */}
              {connectionStatus && (
                <div className={`flex items-start gap-2 text-xs rounded-md px-3 py-2 ${
                  connectionStatus === 'success'
                    ? 'bg-pdi-grass/10 text-pdi-grass border border-pdi-grass/20'
                    : 'bg-pdi-orange/10 text-pdi-orange border border-pdi-orange/20'
                }`}>
                  <span className={`mt-0.5 w-2 h-2 rounded-full shrink-0 ${
                    connectionStatus === 'success' ? 'bg-pdi-grass' : 'bg-pdi-orange'
                  }`} />
                  <div>
                    <span className="font-medium">
                      {connectionStatus === 'success' ? 'Connected' : 'Connection failed'}
                    </span>
                    {connectionError && (
                      <p className="mt-0.5 text-[11px] opacity-80 break-all">{connectionError}</p>
                    )}
                  </div>
                </div>
              )}

              {/* Test Connection button — only for saved instances with credentials */}
              {instance && (instance.secret_arn || (instance.config as { api_key?: string } | null | undefined)?.api_key) && (
                <button
                  type="button"
                  onClick={handleTestConnection}
                  disabled={testing}
                  className="flex items-center gap-2 px-3 py-1.5 text-xs font-medium text-pdi-sky border border-pdi-sky/30 rounded-lg hover:bg-pdi-sky/5 transition-colors disabled:opacity-50"
                >
                  {testing ? (
                    <>
                      <svg className="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                      </svg>
                      Testing...
                    </>
                  ) : (
                    <>
                      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
                      </svg>
                      Test Connection
                    </>
                  )}
                </button>
              )}
            </div>
          )}

          {error && <p className="text-sm text-pdi-orange">{error}</p>}
        </div>
        <div className="px-6 py-4 border-t border-pdi-cool-gray flex justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-sm font-medium text-pdi-slate bg-white border border-pdi-cool-gray rounded-lg hover:bg-gray-50 transition-colors"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            className="px-4 py-2 text-sm font-medium text-white bg-pdi-sky rounded-lg hover:bg-pdi-indigo transition-colors disabled:opacity-50"
          >
            {saving ? 'Saving…' : 'Save Instance'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default function Instances({ selectedProjectId }: { selectedProjectId: string | null }) {
  const [instances, setInstances] = useState<Instance[]>([])
  const [loading, setLoading] = useState(true)
  const [editingInstance, setEditingInstance] = useState<Instance | null | undefined>(undefined)
  const [deleting, setDeleting] = useState<string | null>(null)
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    if (selectedProjectId) {
      api.previewProject(selectedProjectId)
        .then((preview) => setInstances(preview.resolved_instances ?? []))
        .catch(() => setInstances([]))
        .finally(() => setLoading(false))
    } else {
      api.listInstances()
        .then((data) => setInstances(data ?? []))
        .catch(() => setInstances([]))
        .finally(() => setLoading(false))
    }
  }, [selectedProjectId])

  useEffect(() => { load() }, [load])

  const handleDeleteConfirmed = async (id: string) => {
    setConfirmDeleteId(null)
    setDeleting(id)
    try {
      await api.deleteInstance(id)
      load()
    } catch {
      // silently ignore — instance may already be gone
    } finally {
      setDeleting(null)
    }
  }

  const isGlobal = (inst: Instance) => Object.keys(inst.tags).length === 0

  return (
    <div className="h-full overflow-y-auto bg-gray-50">
      <div className="max-w-4xl mx-auto px-6 py-8">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold text-pdi-granite">Instances</h1>
            <p className="text-sm text-pdi-slate mt-1">
              Integration instances with tags. Projects use tag filters to select which instances to use.
              Untagged instances are global — available to all projects.
            </p>
          </div>
          <button
            onClick={() => setEditingInstance(null)}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-pdi-sky rounded-lg hover:bg-pdi-indigo transition-colors"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
            </svg>
            New Instance
          </button>
        </div>

        {loading ? (
          <div className="text-center py-16 text-pdi-slate text-sm">Loading…</div>
        ) : instances.length === 0 ? (
          <div className="text-center py-16 text-pdi-slate">
            <svg className="w-12 h-12 mx-auto mb-3 opacity-40" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M5 12h14M5 12a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 01-2 2M5 12a2 2 0 00-2 2v4a2 2 0 002 2h14a2 2 0 002-2v-4a2 2 0 00-2-2m-2-4h.01M17 16h.01" />
            </svg>
            <p className="text-sm">No instances yet. Create one to get started.</p>
          </div>
        ) : (
          <div className="bg-white rounded-xl border border-pdi-cool-gray overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-pdi-cool-gray bg-gray-50">
                  <th className="text-left px-4 py-3 font-medium text-pdi-slate">Name</th>
                  <th className="text-left px-4 py-3 font-medium text-pdi-slate">Type</th>
                  <th className="text-left px-4 py-3 font-medium text-pdi-slate">Tags</th>
                  <th className="text-left px-4 py-3 font-medium text-pdi-slate">Scope</th>
                  <th className="px-4 py-3" />
                </tr>
              </thead>
              <tbody className="divide-y divide-pdi-cool-gray">
                {instances.map((inst) => (
                  <tr key={inst.id} className="hover:bg-gray-50 transition-colors">
                    <td className="px-4 py-3">
                      <div className="font-medium text-pdi-granite">{inst.name}</div>
                      {inst.type === 'aws_api' && inst.aws_account_id && (
                        <div className="text-[11px] text-pdi-slate font-mono mt-0.5">
                          {inst.aws_account_name && <span>{inst.aws_account_name} &middot; </span>}
                          {inst.aws_account_id}
                        </div>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1.5">
                        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-pdi-sky/10 text-pdi-indigo">
                          {inst.type}
                        </span>
                        {inst.type === 'aws_api' && inst.aws_connection_status && (
                          <span className={`w-2 h-2 rounded-full ${
                            inst.aws_connection_status === 'success' ? 'bg-pdi-grass' : 'bg-pdi-orange'
                          }`} title={inst.aws_connection_status === 'success' ? 'Connected' : `Error: ${inst.aws_connection_error ?? 'unknown'}`} />
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap gap-1">
                        {Object.entries(inst.tags).map(([k, v]) => (
                          <span
                            key={k}
                            className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-mono bg-gray-100 text-pdi-granite"
                          >
                            {k}={v}
                          </span>
                        ))}
                        {Object.keys(inst.tags).length === 0 && (
                          <span className="text-xs text-pdi-slate">—</span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      {isGlobal(inst) ? (
                        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-pdi-sun/15 text-pdi-sun">
                          Global
                        </span>
                      ) : (
                        <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-pdi-sky/10 text-pdi-sky">
                          Tagged
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      {confirmDeleteId === inst.id ? (
                        <div className="flex items-center justify-end gap-2">
                          <span className="text-xs text-pdi-slate">Delete?</span>
                          <button
                            onClick={() => handleDeleteConfirmed(inst.id)}
                            className="px-2 py-1 text-xs font-medium text-white bg-pdi-orange rounded hover:bg-pdi-orange/90 transition-colors"
                          >
                            Yes
                          </button>
                          <button
                            onClick={() => setConfirmDeleteId(null)}
                            className="px-2 py-1 text-xs font-medium text-pdi-slate bg-gray-100 rounded hover:bg-gray-200 transition-colors"
                          >
                            No
                          </button>
                        </div>
                      ) : (
                        <div className="flex items-center justify-end gap-1">
                          <button
                            onClick={() => setEditingInstance(inst)}
                            className="p-1.5 text-pdi-slate hover:text-pdi-sky transition-colors rounded"
                            title="Edit"
                          >
                            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931zm0 0L19.5 7.125" />
                            </svg>
                          </button>
                          <button
                            onClick={() => setConfirmDeleteId(inst.id)}
                            disabled={deleting === inst.id}
                            className="p-1.5 text-pdi-slate hover:text-pdi-orange transition-colors rounded disabled:opacity-40"
                            title="Delete"
                          >
                            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0" />
                            </svg>
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {editingInstance !== undefined && (
        <InstanceFormDialog
          instance={editingInstance}
          onClose={() => setEditingInstance(undefined)}
          onSave={load}
        />
      )}
    </div>
  )
}
