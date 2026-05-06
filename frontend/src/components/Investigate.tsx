import { useState, useEffect } from 'react'
import { useInvestigate } from '../hooks/useInvestigate'
import MessageBubble from './MessageBubble'
import ToolCallCard from './ToolCallCard'
import { api, AwsAccount, type Project } from '../lib/api'
import SimilarInvestigationsPanel from './SimilarInvestigations'

const SOURCES = ['Manual', 'AlertManager', 'PagerDuty', 'Jira', 'OpsGenie', 'Salesforce', 'Azure DevOps', 'AWS CloudWatch', 'Datadog', 'Grafana', 'Elasticsearch', 'DBADash']

export default function Investigate({ projectId, selectedProject }: { projectId?: string | null; selectedProject?: Project | null }) {
  const { investigations, loading, investigate, clearInvestigations } = useInvestigate(projectId)
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [source, setSource] = useState('Manual')
  const [awsAccount, setAwsAccount] = useState('')
  const [awsAccounts, setAwsAccounts] = useState<AwsAccount[]>([])

  useEffect(() => {
    api.getAwsAccounts().then((data) => setAwsAccounts(data.accounts)).catch(() => {})
  }, [])

  // Reset account selection when source changes away from AWS CloudWatch
  useEffect(() => {
    if (source !== 'AWS CloudWatch') setAwsAccount('')
  }, [source])

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!title.trim() || !description.trim() || loading) return

    const context: Record<string, string> = {}
    if (source === 'AWS CloudWatch' && awsAccount) {
      context.aws_account = awsAccount
      const found = awsAccounts.find((a) => a.name === awsAccount)
      if (found) context.aws_account_id = found.account_id
    }

    investigate(title.trim(), description.trim(), source, context)
    setTitle('')
    setDescription('')
  }

  return (
    <div className="flex h-full">
    <div className="flex flex-col flex-1 min-w-0">
      {/* Header */}
      <div className="relative flex items-center justify-between px-6 py-4 border-b border-gray-200 bg-white">
        <div>
          <h2 className="text-lg font-bold text-pdi-granite">Investigations</h2>
          <p className="text-xs text-pdi-slate">Submit alerts for AI-powered root cause analysis</p>
        </div>
        <div className="flex items-center gap-3">
          {/* Project scope indicator */}
          {selectedProject ? (
            <span className="inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full bg-pdi-sky/10 text-pdi-indigo border border-pdi-sky/20">
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
              </svg>
              {selectedProject.name}
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full bg-pdi-sun/10 text-pdi-sun border border-pdi-sun/20">
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z" />
              </svg>
              All instances
            </span>
          )}
          {investigations.length > 0 && (
            <button
              onClick={clearInvestigations}
              className="text-xs text-pdi-slate hover:text-pdi-granite px-3 py-1.5 rounded-lg border border-pdi-cool-gray hover:border-pdi-slate transition-colors"
            >
              Clear history
            </button>
          )}
        </div>
        <div className="absolute bottom-0 left-0 right-0 h-[2px] bg-gradient-to-r from-pdi-sky via-pdi-ocean to-pdi-indigo opacity-60" />
      </div>

      <div className="flex-1 overflow-y-auto">
        {/* Form */}
        <div className="px-6 py-5 border-b border-gray-200 bg-white">
          <form onSubmit={handleSubmit} className="space-y-3 max-w-2xl">
            <div className="flex gap-3 flex-wrap">
              <div className="flex-1 min-w-0">
                <label htmlFor="inv-title" className="block text-xs font-medium text-pdi-granite mb-1">
                  Alert Title
                </label>
                <input
                  id="inv-title"
                  type="text"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="e.g., High CPU usage on payment-service"
                  className="w-full px-3 py-2 border border-pdi-cool-gray rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-pdi-sky focus:border-transparent"
                  required
                />
              </div>
              <div className="w-44 shrink-0">
                <label htmlFor="inv-source" className="block text-xs font-medium text-pdi-granite mb-1">
                  Source
                </label>
                <select
                  id="inv-source"
                  value={source}
                  onChange={(e) => setSource(e.target.value)}
                  className="w-full px-3 py-2 border border-pdi-cool-gray rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-pdi-sky focus:border-transparent bg-white"
                >
                  {SOURCES.map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
              </div>

              {/* AWS account sub-selector */}
              {source === 'AWS CloudWatch' && awsAccounts.length > 0 && (
                <div className="w-44 shrink-0">
                  <label htmlFor="inv-aws-account" className="block text-xs font-medium text-pdi-granite mb-1">
                    AWS Account
                  </label>
                  <select
                    id="inv-aws-account"
                    value={awsAccount}
                    onChange={(e) => setAwsAccount(e.target.value)}
                    className="w-full px-3 py-2 border border-pdi-sun/40 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-pdi-sun/50 focus:border-transparent bg-white"
                  >
                    <option value="">All accounts</option>
                    {awsAccounts.map((acc) => (
                      <option key={acc.account_id} value={acc.name}>
                        {acc.name}
                      </option>
                    ))}
                  </select>
                </div>
              )}
            </div>

            {/* AWS CloudWatch hint */}
            {source === 'AWS CloudWatch' && (
              <div className="flex items-start gap-2 text-xs text-pdi-sun bg-pdi-sun/10 border border-pdi-sun/20 rounded-lg px-3 py-2">
                <svg className="w-3.5 h-3.5 mt-0.5 shrink-0" viewBox="0 0 24 24" fill="currentColor">
                  <path fillRule="evenodd" d="M2.25 12c0-5.385 4.365-9.75 9.75-9.75s9.75 4.365 9.75 9.75-4.365 9.75-9.75 9.75S2.25 17.385 2.25 12zm8.706-1.442c1.146-.573 2.437.463 2.126 1.706l-.709 2.836.042-.02a.75.75 0 01.67 1.34l-.04.022c-1.147.573-2.438-.463-2.127-1.706l.71-2.836-.042.02a.75.75 0 11-.671-1.34l.041-.022zM12 9a.75.75 0 100-1.5.75.75 0 000 1.5z" clipRule="evenodd" />
                </svg>
                Describe the CloudWatch alarm or metric anomaly. Holmes will query the selected AWS account to investigate.
              </div>
            )}

            <div>
              <label htmlFor="inv-desc" className="block text-xs font-medium text-pdi-granite mb-1">
                Description
              </label>
              <textarea
                id="inv-desc"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder={
                  source === 'AWS CloudWatch'
                    ? 'e.g., CPUUtilization alarm triggered on logistics-prod ECS service, threshold 80% exceeded for 15 minutes'
                    : 'Describe the alert or issue in detail...'
                }
                rows={3}
                className="w-full px-3 py-2 border border-pdi-cool-gray rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-pdi-sky focus:border-transparent resize-none"
                required
              />
            </div>

            <button
              type="submit"
              disabled={loading || !title.trim() || !description.trim()}
              className="px-5 py-2 bg-gradient-to-r from-pdi-sky to-pdi-ocean hover:from-pdi-ocean hover:to-pdi-indigo active:scale-[0.98] text-white rounded-lg font-medium text-sm transition-all disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {loading ? 'Investigating...' : 'Investigate'}
            </button>
          </form>
        </div>

        {/* Results */}
        <div className="px-6 py-4 space-y-4">
          {investigations.map((inv) => (
            <div key={inv.id} className={`bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden ${inv.loading ? 'border-l-4 border-l-pdi-sky' : inv.result ? 'border-l-4 border-l-pdi-grass' : inv.error ? 'border-l-4 border-l-pdi-orange' : ''}`}>
              {/* Investigation header */}
              <div className="px-5 py-3 border-b border-gray-100 flex items-center justify-between">
                <div>
                  <h3 className="font-semibold text-sm text-pdi-granite">{inv.title}</h3>
                  <p className="text-xs text-pdi-slate mt-0.5">
                    {inv.source} &middot; {inv.timestamp.toLocaleTimeString()}
                  </p>
                </div>
                <div>
                  {inv.loading && (
                    <span className="inline-flex items-center gap-1.5 text-xs text-pdi-sky font-medium">
                      <span className="w-2 h-2 bg-pdi-sky rounded-full animate-pulse" />
                      Analyzing
                    </span>
                  )}
                  {inv.result && (
                    <span className="inline-flex items-center gap-1.5 text-xs text-pdi-grass font-medium">
                      <span className="w-2 h-2 bg-pdi-grass rounded-full" />
                      Complete
                    </span>
                  )}
                  {inv.error && (
                    <span className="inline-flex items-center gap-1.5 text-xs text-pdi-orange font-medium">
                      <span className="w-2 h-2 bg-pdi-orange rounded-full" />
                      Failed
                    </span>
                  )}
                </div>
              </div>

              {/* Investigation body */}
              <div className="px-5 py-4">
                {inv.loading && (
                  <div className="space-y-2 py-2">
                    <div className="h-3 bg-gray-200 rounded animate-pulse w-3/4" />
                    <div className="h-3 bg-gray-200 rounded animate-pulse w-1/2" />
                    <div className="h-3 bg-gray-200 rounded animate-pulse w-5/6" />
                  </div>
                )}

                {inv.error && (
                  <div className="bg-pdi-orange/10 text-pdi-orange text-sm px-4 py-3 rounded-lg">
                    {inv.error}
                  </div>
                )}

                {inv.result && (
                  <>
                    <MessageBubble role="assistant" content={inv.result.analysis} />
                    {inv.result.tool_calls && inv.result.tool_calls.length > 0 && (
                      <div className="mt-3 pt-3 border-t border-gray-100">
                        <p className="text-xs font-medium text-pdi-slate mb-2">
                          Tools used ({inv.result.tool_calls.length})
                        </p>
                        {inv.result.tool_calls.map((tc, i) => (
                          <ToolCallCard
                            key={i}
                            toolName={tc.tool_name}
                            description={tc.description}
                            result={tc.result}
                          />
                        ))}
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
          ))}

          {investigations.length === 0 && (
            <div className="flex flex-col items-center justify-center py-16 text-center">
              <div className="w-16 h-16 rounded-2xl bg-pdi-sky/10 flex items-center justify-center mb-4">
                <svg className="w-8 h-8 text-pdi-sky" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                </svg>
              </div>
              <h3 className="text-pdi-granite font-semibold text-lg mb-1">No investigations yet</h3>
              <p className="text-pdi-slate text-sm max-w-md">
                Submit an alert above and Holmes will perform a root cause analysis using available toolsets.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
    <SimilarInvestigationsPanel query={`${title} ${description}`} projectId={projectId ?? undefined} />
    </div>
  )
}
