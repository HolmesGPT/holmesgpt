import { useState, useEffect, useCallback } from 'react'
import { api, type Integration, type AwsAccount, type Instance, type ConfigField, type WebhookInfo, type TestConnectionResponse } from '../lib/api'

type StatusFilter = 'all' | 'enabled' | 'disabled' | 'failed'
type TypeFilter = 'all' | 'built-in' | 'mcp' | 'custom' | 'http' | 'database'

const STATUS_COLORS: Record<string, { bg: string; text: string; dot: string }> = {
  enabled: { bg: 'bg-pdi-grass/10', text: 'text-pdi-grass', dot: 'bg-pdi-grass' },
  disabled: { bg: 'bg-gray-100', text: 'text-pdi-slate', dot: 'bg-pdi-slate' },
  failed: { bg: 'bg-pdi-orange/10', text: 'text-pdi-orange', dot: 'bg-pdi-orange' },
}

const TYPE_LABELS: Record<string, string> = {
  'built-in': 'Built-in',
  mcp: 'MCP',
  custom: 'Custom',
  http: 'HTTP',
  database: 'Database',
}

// ─── Tags Editor ────────────────────────────────────────────────────────────

function TagsEditor({
  tags,
  onChange,
}: {
  tags: Record<string, string>
  onChange: (tags: Record<string, string>) => void
}) {
  const entries = Object.entries(tags)

  const addTag = () => onChange({ ...tags, '': '' })

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
            className="w-28 text-xs border border-pdi-cool-gray rounded-md px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-pdi-sky font-mono"
          />
          <span className="text-pdi-slate text-xs">=</span>
          <input
            type="text"
            placeholder="value"
            value={v}
            onChange={(e) => updateValue(k, e.target.value)}
            className="flex-1 text-xs border border-pdi-cool-gray rounded-md px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-pdi-sky font-mono"
          />
          <button
            type="button"
            onClick={() => removeTag(k)}
            className="text-pdi-slate hover:text-pdi-orange transition-colors"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={addTag}
        className="flex items-center gap-1 text-xs text-pdi-sky hover:text-pdi-indigo transition-colors"
      >
        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
        </svg>
        Add tag
      </button>
      {entries.length === 0 && (
        <p className="text-[11px] text-pdi-sun bg-pdi-sun/10 border border-pdi-sun/20 rounded px-2 py-1">
          No tags — this instance will be <strong>global</strong> (available to all projects)
        </p>
      )}
    </div>
  )
}

// ─── Instance Form (inline sub-form) ────────────────────────────────────────

function InstanceForm({
  instance,
  schema,
  integrationType,
  onSave,
  onCancel,
}: {
  instance: Instance | null
  schema: ConfigField[]
  integrationType: string
  onSave: () => void
  onCancel: () => void
}) {
  const [name, setName] = useState(instance?.name ?? '')
  const [tags, setTags] = useState<Record<string, string>>(instance?.tags ?? {})
  const [fields, setFields] = useState<Record<string, string>>(() => {
    const initial: Record<string, string> = {}
    const instanceConfig = (instance as unknown as Record<string, unknown>)?.config as Record<string, unknown> | undefined
    for (const field of schema) {
      const currentVal = (instance as unknown as Record<string, unknown>)?.[field.name]
        ?? instanceConfig?.[field.name]
        ?? (instance?.secret_arn && field.name === 'secret_arn' ? instance.secret_arn : undefined)
      if (currentVal !== undefined && currentVal !== null) {
        initial[field.name] = typeof currentVal === 'object' ? JSON.stringify(currentVal) : String(currentVal)
      } else if (field.default !== undefined && field.default !== null) {
        initial[field.name] = String(field.default)
      } else {
        initial[field.name] = ''
      }
    }
    return initial
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // AWS-specific fields
  const isAws = integrationType === 'aws_api'
  const [awsAccountName, setAwsAccountName] = useState(instance?.aws_account_name ?? '')
  const [awsAccountId, setAwsAccountId] = useState(instance?.aws_account_id ?? '')
  const [awsRoleArn, setAwsRoleArn] = useState(instance?.aws_role_arn ?? '')
  const [awsRegions, setAwsRegions] = useState<string[]>(instance?.aws_regions ?? [])
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<TestConnectionResponse | null>(null)
  const [connectionStatus, setConnectionStatus] = useState<string | null>(instance?.aws_connection_status ?? null)
  const [connectionError, setConnectionError] = useState<string | null>(instance?.aws_connection_error ?? null)

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
      const msg = e instanceof Error ? e.message : 'Test failed'
      setTestResult({ ok: false, status: 'error', error: msg })
      setConnectionStatus('error')
      setConnectionError(msg)
    } finally {
      setTesting(false)
    }
  }

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
      if (isAws) {
        const payload = {
          type: integrationType,
          name: name.trim(),
          tags,
          secret_arn: null as string | null,
          mcp_url: null as string | null,
          aws_account_name: awsAccountName.trim() || null,
          aws_account_id: awsAccountId.trim() || null,
          aws_role_arn: awsRoleArn.trim() || null,
          aws_regions: awsRegions.length > 0 ? awsRegions : null,
        }
        if (instance) {
          await api.updateInstance(instance.id, payload)
        } else {
          await api.createInstance(payload)
        }
      } else {
        // Build config from schema fields
        const config: Record<string, unknown> = {}
        for (const [k, v] of Object.entries(fields)) {
          if (v === '') continue
          const schemaField = schema.find((f) => f.name === k)
          const fieldType = schemaField?.type || 'str'
          if (fieldType === 'bool') config[k] = v === 'true'
          else if (fieldType === 'int') config[k] = parseInt(v, 10)
          else if (fieldType === 'float') config[k] = parseFloat(v)
          else if (fieldType === 'dict' || fieldType === 'list') {
            try { config[k] = JSON.parse(v) } catch { config[k] = v }
          } else {
            config[k] = v
          }
        }

        // Extract fields that go to top-level Instance fields, rest goes to config
        const { secret_arn, mcp_url, aws_accounts, ...instanceConfig } = config as Record<string, unknown>

        const payload = {
          type: integrationType,
          name: name.trim(),
          tags,
          secret_arn: (secret_arn as string | null) ?? null,
          mcp_url: (mcp_url as string | null) ?? null,
          aws_accounts: aws_accounts
            ? (typeof aws_accounts === 'string'
                ? (aws_accounts as string).split(',').map((s) => s.trim()).filter(Boolean)
                : aws_accounts as string[])
            : null,
          config: Object.keys(instanceConfig).length > 0 ? instanceConfig : null,
        }

        if (instance) {
          await api.updateInstance(instance.id, payload)
        } else {
          await api.createInstance(payload)
        }
      }
      onSave()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save instance')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="border border-pdi-sky/30 rounded-xl p-4 bg-pdi-sky/5 space-y-4">
      <p className="text-xs font-semibold text-pdi-granite uppercase tracking-wide">
        {instance ? 'Edit Instance' : 'New Instance'}
      </p>

      {/* Name */}
      <div>
        <label className="block text-xs font-medium text-pdi-granite mb-1">Instance Name</label>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. grafana-retail-prod"
          className="w-full px-3 py-2 border border-pdi-cool-gray rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-pdi-sky"
        />
      </div>

      {/* Tags */}
      <div>
        <label className="block text-xs font-medium text-pdi-granite mb-1">Tags</label>
        <p className="text-[11px] text-pdi-slate mb-2">
          Tags let Projects scope which instances to use. Instances with no tags are global.
        </p>
        <TagsEditor tags={tags} onChange={setTags} />
      </div>

      {/* AWS-specific fields */}
      {isAws && (
        <div className="space-y-3 p-3 bg-gray-50 rounded-lg border border-pdi-cool-gray">
          <p className="text-xs font-semibold text-pdi-granite uppercase tracking-wide">AWS Account Details</p>
          <div>
            <label className="text-xs font-medium text-pdi-granite">Account Name {!instance && <span className="text-pdi-orange">*</span>}</label>
            <input
              type="text"
              value={awsAccountName}
              onChange={(e) => setAwsAccountName(e.target.value)}
              placeholder="e.g. Retail Production"
              className="w-full mt-1 px-3 py-2 border border-pdi-cool-gray rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-pdi-sky"
            />
          </div>
          <div>
            <label className="text-xs font-medium text-pdi-granite">Account Number {!instance && <span className="text-pdi-orange">*</span>}</label>
            <input
              type="text"
              value={awsAccountId}
              onChange={(e) => setAwsAccountId(e.target.value.replace(/\D/g, '').slice(0, 12))}
              placeholder="123456789012"
              className="w-full mt-1 px-3 py-2 border border-pdi-cool-gray rounded-lg text-sm font-mono focus:outline-none focus:ring-2 focus:ring-pdi-sky"
            />
            <p className="text-[11px] text-pdi-slate mt-1">12-digit AWS account ID</p>
          </div>
          <div>
            <label className="text-xs font-medium text-pdi-granite">Role ARN {!instance && <span className="text-pdi-orange">*</span>}</label>
            <input
              type="text"
              value={awsRoleArn}
              onChange={(e) => setAwsRoleArn(e.target.value)}
              placeholder="arn:aws:iam::123456789012:role/HolmesGPT-ReadOnly"
              className="w-full mt-1 px-3 py-2 border border-pdi-cool-gray rounded-lg text-sm font-mono focus:outline-none focus:ring-2 focus:ring-pdi-sky"
            />
            <p className="text-[11px] text-pdi-slate mt-1">IAM role ARN created by the setup script. See Docs &gt; AWS Account tab.</p>
          </div>

          {/* AWS Regions */}
          <div>
            <label className="text-xs font-medium text-pdi-granite">Regions</label>
            <div className="flex flex-wrap gap-1.5 mt-1 mb-2">
              {awsRegions.map((r) => (
                <span key={r} className="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-mono bg-pdi-sky/10 text-pdi-sky border border-pdi-sky/20 rounded-md">
                  {r}
                  <button type="button" onClick={() => setAwsRegions(awsRegions.filter((x) => x !== r))} className="hover:text-pdi-orange">&times;</button>
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
              className="w-full px-3 py-2 border border-pdi-cool-gray rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-pdi-sky"
            >
              <option value="">Add a region...</option>
              {['us-east-1', 'us-east-2', 'us-west-2', 'eu-central-1', 'eu-west-1', 'eu-west-2', 'ap-southeast-1', 'ap-northeast-1'].filter((r) => !awsRegions.includes(r)).map((r) => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>
            <p className="text-[11px] text-pdi-slate mt-1">Restrict investigations to these AWS regions. Leave empty to allow all regions.</p>
          </div>

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
                <span className="font-medium">{connectionStatus === 'success' ? 'Connected' : 'Connection failed'}</span>
                {connectionError && <p className="mt-0.5 text-[11px] opacity-80 break-all">{connectionError}</p>}
                {testResult?.assumed_role && <p className="mt-0.5 text-[11px] opacity-80 font-mono">{testResult.assumed_role}</p>}
              </div>
            </div>
          )}

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

      {/* Schema-driven config fields (non-AWS) */}
      {!isAws && schema.length > 0 && (
        <div className="space-y-3">
          <p className="text-xs font-semibold text-pdi-granite uppercase tracking-wide">Configuration</p>
          {schema.map((field: ConfigField) => (
            <div key={field.name}>
              <div className="flex items-center gap-2 mb-1">
                <label className="text-xs font-medium text-pdi-granite">{field.name}</label>
                {field.required && (
                  <span className="text-[9px] font-bold text-pdi-orange">REQUIRED</span>
                )}
                {field.type !== 'str' && (
                  <span className="text-[9px] text-pdi-slate bg-gray-100 px-1 rounded">{field.type}</span>
                )}
              </div>
              {field.description && (
                <p className="text-[11px] text-pdi-slate mb-1">{field.description}</p>
              )}
              {field.type === 'bool' ? (
                <select
                  value={fields[field.name] || ''}
                  onChange={(e) => setFields({ ...fields, [field.name]: e.target.value })}
                  className="w-full px-3 py-2 border border-pdi-cool-gray rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-pdi-sky"
                >
                  <option value="">— not set —</option>
                  <option value="true">true</option>
                  <option value="false">false</option>
                </select>
              ) : (
                <input
                  type={field.sensitive ? 'password' : 'text'}
                  value={fields[field.name] || ''}
                  onChange={(e) => setFields({ ...fields, [field.name]: e.target.value })}
                  placeholder={field.default !== null && field.default !== undefined ? `Default: ${field.default}` : `Enter ${field.name}`}
                  className="w-full px-3 py-2 border border-pdi-cool-gray rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-pdi-sky font-mono"
                />
              )}
            </div>
          ))}
        </div>
      )}

      {error && (
        <p className="text-xs text-pdi-orange bg-pdi-orange/5 border border-pdi-orange/20 rounded px-3 py-2">
          {error}
        </p>
      )}

      <div className="flex justify-end gap-2 pt-1">
        <button
          type="button"
          onClick={onCancel}
          className="px-3 py-1.5 text-xs text-pdi-slate border border-pdi-cool-gray rounded-lg hover:bg-gray-50"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={handleSave}
          disabled={saving}
          className="px-3 py-1.5 text-xs text-white bg-pdi-sky rounded-lg hover:bg-pdi-sky/90 disabled:opacity-50"
        >
          {saving ? 'Saving…' : 'Save Instance'}
        </button>
      </div>
    </div>
  )
}

// ─── Instances Tab ───────────────────────────────────────────────────────────

function InstancesTab({
  integration,
}: {
  integration: Integration
}) {
  const [instances, setInstances] = useState<Instance[]>([])
  const [loading, setLoading] = useState(true)
  const [editingInstance, setEditingInstance] = useState<Instance | null | undefined>(undefined)
  const [deleting, setDeleting] = useState<string | null>(null)
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)
  const [migrating, setMigrating] = useState(false)
  const [migrateError, setMigrateError] = useState<string | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    api.listInstances()
      .then((all) => setInstances((all ?? []).filter((i) => i.type === integration.name)))
      .catch(() => setInstances([]))
      .finally(() => setLoading(false))
  }, [integration.name])

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

  // Auto-migration: create a global instance from existing integration.config
  const hasLegacyConfig = integration.config && Object.keys(integration.config).length > 0
  const showMigrationPrompt = !loading && instances.length === 0 && hasLegacyConfig && editingInstance === undefined

  const handleMigrate = async () => {
    setMigrating(true)
    setMigrateError(null)
    try {
      const config = integration.config as Record<string, unknown>
      const { secret_arn, mcp_url, aws_accounts, ...instanceConfig } = config
      await api.createInstance({
        type: integration.name,
        name: `${integration.name}-default`,
        tags: {},
        secret_arn: (secret_arn as string | null) ?? null,
        mcp_url: (mcp_url as string | null) ?? null,
        aws_accounts: aws_accounts
          ? (Array.isArray(aws_accounts)
              ? aws_accounts as string[]
              : String(aws_accounts).split(',').map((s) => s.trim()).filter(Boolean))
          : null,
        config: Object.keys(instanceConfig).length > 0 ? instanceConfig : null,
      })
      load()
    } catch (e) {
      setMigrateError(e instanceof Error ? e.message : 'Failed to create instance')
    } finally {
      setMigrating(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-pdi-slate">
          Multiple instances let you connect several {integration.name} environments (e.g. prod, staging) and scope them to projects via tags.
        </p>
        <button
          onClick={() => setEditingInstance(null)}
          className="flex items-center gap-1 text-xs text-pdi-sky hover:text-pdi-indigo transition-colors shrink-0 ml-3"
        >
          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
          </svg>
          Add Instance
        </button>
      </div>

      {/* Auto-migration prompt */}
      {showMigrationPrompt && (
        <div className="bg-pdi-sun/5 border border-pdi-sun/20 rounded-xl p-4 space-y-2">
          <div className="flex items-start gap-2">
            <svg className="w-4 h-4 text-pdi-sun shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
            </svg>
            <div>
              <p className="text-xs font-semibold text-pdi-sun">Existing configuration detected</p>
              <p className="text-xs text-pdi-sun/80 mt-0.5">
                This integration has configuration but no instances yet. Create a global instance from the existing config so it works with all projects.
              </p>
            </div>
          </div>
          {migrateError && (
            <p className="text-xs text-pdi-orange bg-pdi-orange/10 border border-pdi-orange/20 rounded px-2 py-1">{migrateError}</p>
          )}
          <div className="flex gap-2 justify-end">
            <button
              onClick={() => setEditingInstance(null)}
              className="text-xs text-pdi-sun hover:text-pdi-sun/80 px-3 py-1.5 border border-pdi-sun/30 rounded-lg"
            >
              Configure manually
            </button>
            <button
              onClick={handleMigrate}
              disabled={migrating}
              className="text-xs text-white bg-pdi-sun hover:bg-pdi-sun/90 px-3 py-1.5 rounded-lg disabled:opacity-50"
            >
              {migrating ? 'Creating…' : 'Create global instance'}
            </button>
          </div>
        </div>
      )}

      {/* New instance form */}
      {editingInstance === null && (
        <InstanceForm
          instance={null}
          schema={integration.config_schema || []}
          integrationType={integration.name}
          onSave={() => { setEditingInstance(undefined); load() }}
          onCancel={() => setEditingInstance(undefined)}
        />
      )}

      {loading ? (
        <p className="text-xs text-pdi-slate text-center py-4">Loading instances…</p>
      ) : instances.length === 0 && editingInstance !== null && !showMigrationPrompt ? (
        <div className="text-center py-6 text-pdi-slate">
          <svg className="w-8 h-8 mx-auto mb-2 opacity-30" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M5 12h14M5 12a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 01-2 2M5 12a2 2 0 00-2 2v4a2 2 0 002 2h14a2 2 0 002-2v-4a2 2 0 00-2-2" />
          </svg>
          <p className="text-xs">No instances yet. Click "Add Instance" to create one.</p>
        </div>
      ) : (
        <div className="space-y-2">
          {instances.map((inst) => (
            <div key={inst.id}>
              {editingInstance?.id === inst.id ? (
                <InstanceForm
                  instance={inst}
                  schema={integration.config_schema || []}
                  integrationType={integration.name}
                  onSave={() => { setEditingInstance(undefined); load() }}
                  onCancel={() => setEditingInstance(undefined)}
                />
              ) : (
                <div className="flex items-start justify-between gap-3 bg-white border border-pdi-cool-gray rounded-lg px-3 py-2.5">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-medium text-pdi-granite">{inst.name}</span>
                      {Object.keys(inst.tags).length === 0 ? (
                        <span className="text-[10px] font-medium bg-pdi-sun/15 text-pdi-sun px-1.5 py-0.5 rounded-full">
                          global
                        </span>
                      ) : (
                        <div className="flex flex-wrap gap-1">
                          {Object.entries(inst.tags).map(([k, v]) => (
                            <span key={k} className="text-[10px] font-mono bg-pdi-sky/10 text-pdi-indigo px-1.5 py-0.5 rounded">
                              {k}={v}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                    {inst.secret_arn && (
                      <p className="text-[10px] text-pdi-slate mt-0.5 font-mono truncate">{inst.secret_arn}</p>
                    )}
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    {confirmDeleteId === inst.id ? (
                      <>
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
                      </>
                    ) : (
                      <>
                        <button
                          onClick={() => setEditingInstance(inst)}
                          className="p-1 text-pdi-slate hover:text-pdi-sky transition-colors rounded"
                          title="Edit"
                        >
                          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931zm0 0L19.5 7.125" />
                          </svg>
                        </button>
                        <button
                          onClick={() => setConfirmDeleteId(inst.id)}
                          disabled={deleting === inst.id}
                          className="p-1 text-pdi-slate hover:text-pdi-orange transition-colors rounded disabled:opacity-40"
                          title="Delete"
                        >
                          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0" />
                          </svg>
                        </button>
                      </>
                    )}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Config Modal ────────────────────────────────────────────────────────────

function ConfigModal({
  integration,
  onClose,
}: {
  integration: Integration
  onClose: () => void
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="bg-white rounded-2xl shadow-2xl w-full max-w-lg max-h-[90vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-6 py-4 border-b border-pdi-cool-gray flex items-center justify-between">
          <div className="flex items-center gap-3">
            {integration.icon_url ? (
              <img src={integration.icon_url} alt="" className="w-7 h-7 rounded-lg object-contain"
                onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }} />
            ) : (
              <div className="w-7 h-7 rounded-lg bg-pdi-indigo/10 flex items-center justify-center">
                <svg className="w-4 h-4 text-pdi-indigo" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
                </svg>
              </div>
            )}
            <div>
              <h3 className="text-sm font-bold text-pdi-granite">{integration.name}</h3>
              <span className={`inline-block text-[10px] font-medium px-1.5 py-0.5 rounded ${
                integration.type === 'mcp' ? 'bg-pdi-ocean/10 text-pdi-ocean' : 'bg-gray-100 text-pdi-slate'
              }`}>
                {TYPE_LABELS[integration.type] || integration.type}
              </span>
            </div>
          </div>
          <button onClick={onClose} className="text-pdi-slate hover:text-pdi-granite p-1">
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Body — instances only */}
        <div className="flex-1 overflow-y-auto px-6 py-4">
          <InstancesTab integration={integration} />
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-pdi-cool-gray flex items-center justify-end">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-pdi-slate hover:text-pdi-granite border border-pdi-cool-gray rounded-lg"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  )
}

// ─── Integration Card ────────────────────────────────────────────────────────

function IntegrationCard({
  integration,
  onToggle,
  onConfigure,
  awsAccounts,
  instances,
}: {
  integration: Integration
  onToggle: (name: string, enabled: boolean) => void
  onConfigure: (integration: Integration) => void
  awsAccounts?: AwsAccount[]
  instances?: Instance[]
}) {
  const colors = STATUS_COLORS[integration.status] || STATUS_COLORS.disabled
  const [toggling, setToggling] = useState(false)

  const handleToggle = async () => {
    setToggling(true)
    try {
      await onToggle(integration.name, !integration.enabled)
    } finally {
      setToggling(false)
    }
  }

  return (
    <div className="bg-white rounded-xl border border-pdi-cool-gray shadow-sm p-5 flex flex-col gap-3">
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          {integration.icon_url ? (
            <img
              src={integration.icon_url}
              alt=""
              className="w-8 h-8 rounded-lg object-contain"
              onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
            />
          ) : (
            <div className="w-8 h-8 rounded-lg bg-pdi-indigo/10 flex items-center justify-center">
              <svg className="w-4 h-4 text-pdi-indigo" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
              </svg>
            </div>
          )}
          <div>
            <h3 className="text-sm font-semibold text-pdi-granite">{integration.name}</h3>
            <span className={`inline-block text-[10px] font-medium px-1.5 py-0.5 rounded mt-0.5 ${
              integration.type === 'mcp' ? 'bg-pdi-ocean/10 text-pdi-ocean' : 'bg-gray-100 text-pdi-slate'
            }`}>
              {TYPE_LABELS[integration.type] || integration.type}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className={`inline-flex items-center gap-1.5 text-xs font-medium px-2 py-1 rounded-full ${colors.bg} ${colors.text}`}>
            <span className={`w-1.5 h-1.5 rounded-full ${colors.dot}`} />
            {integration.status}
          </span>
          <button
            onClick={handleToggle}
            disabled={toggling}
            className={`relative w-9 h-5 rounded-full transition-colors shrink-0 ${
              toggling ? 'opacity-50' : ''
            } ${integration.enabled ? 'bg-pdi-grass' : 'bg-gray-300'}`}
            title={integration.enabled ? 'Disable' : 'Enable'}
          >
            <span className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${
              integration.enabled ? 'translate-x-4' : ''
            }`} />
          </button>
        </div>
      </div>

      {integration.description && (
        <p className="text-xs text-pdi-slate leading-relaxed line-clamp-2">
          {integration.description}
        </p>
      )}

      {integration.name === 'aws_api' && awsAccounts && awsAccounts.length > 0 && (
        <div className="space-y-1.5">
          {awsAccounts.map((acc) => (
            <div key={acc.account_id} className="flex items-center justify-between text-xs">
              <span className="flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-pdi-grass shrink-0" />
                <span className="text-pdi-granite font-medium">{acc.name}</span>
              </span>
              <span className="text-pdi-slate font-mono">{acc.account_id}</span>
            </div>
          ))}
        </div>
      )}

      {integration.error && (
        <div className="text-xs text-pdi-orange bg-pdi-orange/5 border border-pdi-orange/20 rounded-lg px-3 py-2">
          {integration.error}
        </div>
      )}

      {instances && instances.length > 0 && (
        <div className="space-y-1.5">
          <div className="flex items-center gap-1.5">
            <svg className="w-3 h-3 text-pdi-slate shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 12h14M5 12a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 01-2 2M5 12a2 2 0 00-2 2v4a2 2 0 002 2h14a2 2 0 002-2v-4a2 2 0 00-2-2" />
            </svg>
            <span className="text-[11px] font-medium text-pdi-slate">
              {instances.length} instance{instances.length !== 1 ? 's' : ''}
            </span>
          </div>
          <div className="flex flex-wrap gap-1">
            {instances.map((inst) => (
              <span
                key={inst.id}
                className="inline-flex items-center gap-1 text-[10px] font-medium px-1.5 py-0.5 rounded bg-pdi-sky/10 text-pdi-indigo"
                title={Object.keys(inst.tags).length > 0
                  ? Object.entries(inst.tags).map(([k, v]) => `${k}=${v}`).join(', ')
                  : 'global'}
              >
                {inst.name}
                {Object.keys(inst.tags).length === 0 ? (
                  <span className="text-pdi-sky/60 font-normal">(global)</span>
                ) : (
                  <span className="text-pdi-slate font-mono font-normal">
                    {Object.entries(inst.tags).map(([k, v]) => `${k}=${v}`).join(' ')}
                  </span>
                )}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="flex items-center justify-between mt-auto pt-2 border-t border-pdi-cool-gray">
        <span className="text-[11px] text-pdi-slate">
          {integration.tool_count} tool{integration.tool_count !== 1 ? 's' : ''}
        </span>
        <div className="flex items-center gap-3">
          <button
            onClick={() => onConfigure(integration)}
            className="text-[11px] flex items-center gap-1 text-pdi-sky hover:underline"
          >
            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
            Configure
          </button>
          {integration.docs_url && (
            <a
              href={integration.docs_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-[11px] text-pdi-sky hover:underline flex items-center gap-1"
            >
              Docs
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
              </svg>
            </a>
          )}
        </div>
      </div>
    </div>
  )
}

// ─── Main Integrations Page ──────────────────────────────────────────────────

export default function Integrations() {
  const [integrations, setIntegrations] = useState<Integration[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [typeFilter, setTypeFilter] = useState<TypeFilter>('all')
  const [configuring, setConfiguring] = useState<Integration | null>(null)
  const [awsAccounts, setAwsAccounts] = useState<AwsAccount[]>([])
  const [allInstances, setAllInstances] = useState<Instance[]>([])
  const [webhooks, setWebhooks] = useState<WebhookInfo[]>([])
  const [webhookTogglingId, setWebhookTogglingId] = useState<string | null>(null)

  const loadInstances = () => {
    api.listInstances().then(setAllInstances).catch(() => {})
  }

  const loadWebhooks = () => {
    api.getWebhooks().then((data) => setWebhooks(data.webhooks)).catch(() => {})
  }

  useEffect(() => {
    loadIntegrations()
    api.getAwsAccounts().then((data) => setAwsAccounts(data.accounts)).catch(() => {})
    loadInstances()
    loadWebhooks()
  }, [])

  async function loadIntegrations() {
    try {
      setLoading(true)
      setError(null)
      const data = await api.getIntegrations()
      setIntegrations(data.integrations)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load integrations')
    } finally {
      setLoading(false)
    }
  }

  async function handleToggle(name: string, enabled: boolean) {
    try {
      setError(null)
      await api.toggleIntegration(name, enabled)
      await loadIntegrations()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to toggle integration')
    }
  }

  async function handleWebhookWriteBackToggle(webhookId: string, enabled: boolean) {
    setWebhookTogglingId(webhookId)
    try {
      await api.updateWebhookSettings(webhookId, { write_back_enabled: enabled })
      setWebhooks((prev) =>
        prev.map((w) => (w.id === webhookId ? { ...w, write_back_enabled: enabled } : w))
      )
    } catch {
      // reload to get accurate state on error
      loadWebhooks()
    } finally {
      setWebhookTogglingId(null)
    }
  }

  const filtered = integrations.filter((i) => {
    if (statusFilter !== 'all' && i.status !== statusFilter) return false
    if (typeFilter !== 'all' && i.type !== typeFilter) return false
    return true
  })

  const enabledCount = integrations.filter((i) => i.status === 'enabled').length
  const failedCount = integrations.filter((i) => i.status === 'failed').length
  const mcpCount = integrations.filter((i) => i.type === 'mcp').length

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="relative px-6 py-4 border-b border-pdi-cool-gray bg-white">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-bold text-pdi-granite">Integrations</h2>
            <p className="text-xs text-pdi-slate">Connected data sources and MCP servers</p>
          </div>
          <button
            onClick={loadIntegrations}
            disabled={loading}
            className="text-xs text-pdi-slate hover:text-pdi-granite px-3 py-1.5 rounded-lg border border-pdi-cool-gray hover:border-pdi-slate transition-colors disabled:opacity-50"
          >
            {loading ? 'Refreshing...' : 'Refresh'}
          </button>
        </div>
        <div className="absolute bottom-0 left-0 right-0 h-[2px] bg-gradient-to-r from-pdi-sky via-pdi-ocean to-pdi-indigo opacity-60" />
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="max-w-5xl space-y-6">
          {/* Summary cards */}
          <div className="grid grid-cols-3 gap-4">
            <div className="bg-white rounded-xl border border-pdi-cool-gray shadow-sm p-4">
              <p className="text-xs text-pdi-slate font-medium">Active</p>
              <p className="text-2xl font-bold text-pdi-grass mt-1">{enabledCount}</p>
              <p className="text-[11px] text-pdi-slate mt-0.5">of {integrations.length} total</p>
            </div>
            <div className="bg-white rounded-xl border border-pdi-cool-gray shadow-sm p-4">
              <p className="text-xs text-pdi-slate font-medium">Failed</p>
              <p className={`text-2xl font-bold mt-1 ${failedCount > 0 ? 'text-pdi-orange' : 'text-pdi-granite'}`}>{failedCount}</p>
              <p className="text-[11px] text-pdi-slate mt-0.5">need attention</p>
            </div>
            <div className="bg-white rounded-xl border border-pdi-cool-gray shadow-sm p-4">
              <p className="text-xs text-pdi-slate font-medium">MCP Servers</p>
              <p className="text-2xl font-bold text-pdi-ocean mt-1">{mcpCount}</p>
              <p className="text-[11px] text-pdi-slate mt-0.5">external integrations</p>
            </div>
          </div>

          {/* Filters */}
          <div className="flex gap-3">
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
              className="text-xs border border-pdi-cool-gray rounded-lg px-3 py-1.5 text-pdi-granite bg-white focus:outline-none focus:ring-1 focus:ring-pdi-sky"
            >
              <option value="all">All statuses</option>
              <option value="enabled">Enabled</option>
              <option value="disabled">Disabled</option>
              <option value="failed">Failed</option>
            </select>
            <select
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value as TypeFilter)}
              className="text-xs border border-pdi-cool-gray rounded-lg px-3 py-1.5 text-pdi-granite bg-white focus:outline-none focus:ring-1 focus:ring-pdi-sky"
            >
              <option value="all">All types</option>
              <option value="built-in">Built-in</option>
              <option value="mcp">MCP</option>
              <option value="custom">Custom</option>
            </select>
            {(statusFilter !== 'all' || typeFilter !== 'all') && (
              <button
                onClick={() => { setStatusFilter('all'); setTypeFilter('all') }}
                className="text-xs text-pdi-sky hover:underline"
              >
                Clear filters
              </button>
            )}
          </div>

          {/* Error */}
          {error && (
            <div className="bg-pdi-orange/10 border border-pdi-orange/30 rounded-xl p-4 text-sm text-pdi-orange">
              {error}
            </div>
          )}

          {/* Loading */}
          {loading && integrations.length === 0 && (
            <div className="text-center py-12 text-pdi-slate text-sm">Loading integrations...</div>
          )}

          {/* Cards grid */}
          {filtered.length > 0 && (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {filtered.map((integration) => (
                <IntegrationCard
                  key={integration.name}
                  integration={integration}
                  onToggle={handleToggle}
                  onConfigure={setConfiguring}
                  awsAccounts={awsAccounts}
                  instances={allInstances.filter((i) => i.type === integration.name)}
                />
              ))}
            </div>
          )}

          {/* Empty */}
          {!loading && filtered.length === 0 && integrations.length > 0 && (
            <div className="text-center py-12 text-pdi-slate text-sm">
              No integrations match the current filters.
            </div>
          )}

          {/* Webhooks section */}
          {webhooks.length > 0 && (
            <div>
              <div className="mb-3">
                <h3 className="text-sm font-semibold text-pdi-granite">Webhook Integrations</h3>
                <p className="text-xs text-pdi-slate mt-0.5">
                  External systems that trigger Holmes investigations. Toggle write-back to control whether Holmes posts results back to the originating system.
                </p>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {webhooks.map((wh) => (
                  <div key={wh.id} className="bg-white rounded-xl border border-pdi-cool-gray shadow-sm p-4 flex flex-col gap-3">
                    {/* Header row */}
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <p className="text-sm font-semibold text-pdi-granite">{wh.name}</p>
                        <p className="text-[11px] text-pdi-slate font-mono mt-0.5">{wh.url}</p>
                      </div>
                      <span className={`flex-shrink-0 inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full ${wh.configured ? 'bg-pdi-grass/10 text-pdi-grass' : 'bg-gray-100 text-pdi-slate'}`}>
                        <span className={`w-1.5 h-1.5 rounded-full ${wh.configured ? 'bg-pdi-grass' : 'bg-pdi-slate'}`} />
                        {wh.configured ? 'Configured' : 'Not configured'}
                      </span>
                    </div>

                    {/* Auth / trigger info */}
                    <div className="flex gap-2 flex-wrap">
                      <span className="text-[10px] bg-gray-50 border border-pdi-cool-gray rounded px-1.5 py-0.5 text-pdi-slate font-mono">
                        {wh.auth_type}
                      </span>
                      <span className="text-[10px] bg-gray-50 border border-pdi-cool-gray rounded px-1.5 py-0.5 text-pdi-slate font-mono">
                        {wh.trigger}
                      </span>
                    </div>

                    {/* Write-back toggle */}
                    <div className="border-t border-pdi-cool-gray pt-3">
                      <div className="flex items-center justify-between gap-2">
                        <div>
                          <p className="text-xs font-medium text-pdi-granite">Post results back</p>
                          {!wh.write_back_capable ? (
                            <p className="text-[10px] text-pdi-sun mt-0.5">
                              Requires write credentials in env vars
                            </p>
                          ) : (
                            <p className="text-[10px] text-pdi-slate mt-0.5">
                              {wh.write_back_enabled ? 'Holmes will comment on resolved items' : 'Write-back disabled'}
                            </p>
                          )}
                        </div>
                        <button
                          type="button"
                          disabled={!wh.write_back_capable || webhookTogglingId === wh.id}
                          onClick={() => handleWebhookWriteBackToggle(wh.id, !wh.write_back_enabled)}
                          className={`relative inline-flex h-5 w-9 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none disabled:opacity-40 disabled:cursor-not-allowed ${wh.write_back_enabled && wh.write_back_capable ? 'bg-pdi-sky' : 'bg-gray-200'}`}
                          title={!wh.write_back_capable ? 'Write credentials not configured' : (wh.write_back_enabled ? 'Disable write-back' : 'Enable write-back')}
                        >
                          <span
                            className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${wh.write_back_enabled && wh.write_back_capable ? 'translate-x-4' : 'translate-x-0'}`}
                          />
                        </button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Config modal */}
      {configuring && (
        <ConfigModal
          integration={configuring}
          onClose={() => { setConfiguring(null); loadInstances() }}
        />
      )}
    </div>
  )
}
