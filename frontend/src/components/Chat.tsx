import { useState, useRef, useEffect } from 'react'
import { useChat } from '../hooks/useChat'
import MessageBubble from './MessageBubble'
import ToolCallCard from './ToolCallCard'
import { api, AwsAccount } from '../lib/api'
import SimilarInvestigationsPanel from './SimilarInvestigations'

const K8S_SUGGESTIONS = [
  'List all pods with errors',
  'Show namespaces in this cluster',
  'Check node resource usage',
  'Find recent crash loops',
]

const AWS_SUGGESTIONS = [
  'List EC2 instances in logistics-prod',
  'Check CloudWatch alarms across all logistics accounts',
  'Find Lambda errors in logistics-dev in the last hour',
  'Compare RDS status across logistics accounts',
]

interface ChatProps {
  projectId?: string | null
}

export default function Chat({ projectId }: ChatProps) {
  const { messages, loading, sendMessage, clearMessages } = useChat(projectId ?? null)
  const [input, setInput] = useState('')
  const [awsAccounts, setAwsAccounts] = useState<AwsAccount[]>([])
  const [showAccountPill, setShowAccountPill] = useState(true)
  const [confirmClear, setConfirmClear] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  useEffect(() => {
    api.getAwsAccounts(projectId).then((data) => setAwsAccounts(data.accounts)).catch(() => {})
  }, [])

  useEffect(() => {
    if (inputRef.current) {
      inputRef.current.style.height = 'auto'
      inputRef.current.style.height = `${Math.min(inputRef.current.scrollHeight, 160)}px`
    }
  }, [input])

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const text = input.trim()
    if (!text || loading) return
    setInput('')
    sendMessage(text)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit(e)
    }
  }

  return (
    <div className="flex h-full">
    <div className="flex flex-col flex-1 min-w-0">
      {/* Header */}
      <div className="relative flex items-center justify-between px-6 py-4 border-b border-pdi-cool-gray bg-white">
        <div>
          <h2 className="text-lg font-bold text-pdi-granite">Chat</h2>
          <p className="text-xs text-pdi-slate">Ask questions about your infrastructure</p>
        </div>
        {messages.length > 0 && (
          confirmClear ? (
            <div className="flex items-center gap-2">
              <span className="text-xs text-pdi-slate">Clear all messages?</span>
              <button
                onClick={() => { clearMessages(); setConfirmClear(false) }}
                className="text-xs font-medium text-white bg-pdi-orange px-2.5 py-1 rounded-lg hover:bg-pdi-orange/90 transition-colors"
              >
                Yes, clear
              </button>
              <button
                onClick={() => setConfirmClear(false)}
                className="text-xs text-pdi-slate px-2.5 py-1 rounded-lg border border-pdi-cool-gray hover:bg-gray-50 transition-colors"
              >
                Cancel
              </button>
            </div>
          ) : (
            <button
              onClick={() => setConfirmClear(true)}
              className="text-xs text-pdi-slate hover:text-pdi-granite px-3 py-1.5 rounded-lg border border-pdi-cool-gray hover:border-pdi-slate transition-colors"
            >
              Clear chat
            </button>
          )
        )}
        <div className="absolute bottom-0 left-0 right-0 h-[2px] bg-gradient-to-r from-pdi-sky via-pdi-ocean to-pdi-indigo opacity-60" />
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <div className="w-16 h-16 rounded-2xl bg-pdi-sky/10 flex items-center justify-center mb-4">
              <svg className="w-8 h-8 text-pdi-sky" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
              </svg>
            </div>
            <h3 className="text-pdi-granite font-semibold text-lg mb-1">How can I help?</h3>
            <p className="text-pdi-slate text-sm max-w-md mb-6">
              Ask me anything about your Kubernetes cluster, infrastructure, or application health.
            </p>

            {/* Kubernetes suggestions */}
            <div className="w-full max-w-lg mb-4">
              <p className="text-xs font-medium text-pdi-slate mb-2 text-left flex items-center gap-1.5">
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5 12h14M12 5l7 7-7 7" />
                </svg>
                Kubernetes
              </p>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {K8S_SUGGESTIONS.map((suggestion) => (
                  <button
                    key={suggestion}
                    onClick={() => { if (!loading) sendMessage(suggestion) }}
                    disabled={loading}
                    className="text-left text-sm px-3 py-2 rounded-lg border border-pdi-cool-gray text-pdi-granite hover:border-pdi-sky hover:bg-pdi-sky/5 transition-colors"
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            </div>

            {/* AWS suggestions */}
            {awsAccounts.length > 0 && (
              <div className="w-full max-w-lg">
                <p className="text-xs font-medium text-pdi-slate mb-2 text-left flex items-center gap-1.5">
                  <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M6.763 10.036c0 .296.032.535.088.71.064.176.144.368.256.576.04.064.056.128.056.184 0 .08-.048.16-.152.24l-.504.336a.383.383 0 01-.208.072c-.08 0-.16-.04-.24-.112a2.47 2.47 0 01-.288-.376 6.18 6.18 0 01-.248-.472c-.624.736-1.408 1.104-2.352 1.104-.672 0-1.208-.192-1.6-.576-.392-.384-.592-.896-.592-1.536 0-.68.24-1.232.728-1.648.488-.416 1.136-.624 1.96-.624.272 0 .552.024.848.064.296.04.6.104.92.176v-.584c0-.608-.128-1.032-.376-1.28-.256-.248-.688-.368-1.304-.368-.28 0-.568.032-.864.104-.296.072-.584.16-.864.272a2.294 2.294 0 01-.28.104.488.488 0 01-.128.024c-.112 0-.168-.08-.168-.248v-.392c0-.128.016-.224.056-.28a.597.597 0 01.224-.168c.28-.144.616-.264 1.008-.36A4.84 4.84 0 014.8 6.4c.96 0 1.664.216 2.12.656.448.432.68 1.088.68 1.96v2.584l-.837-.564zm-3.24 1.2c.264 0 .536-.048.824-.144.288-.096.544-.272.76-.512.128-.152.224-.32.272-.512.048-.192.08-.424.08-.696v-.336a6.8 6.8 0 00-.736-.136 6.03 6.03 0 00-.752-.048c-.536 0-.928.104-1.192.32-.264.216-.392.52-.392.92 0 .376.096.656.296.848.192.2.472.296.84.296zm6.44.88c-.144 0-.24-.024-.304-.08-.064-.048-.12-.16-.168-.312L7.684 6.4c-.048-.16-.072-.264-.072-.32 0-.128.064-.2.192-.2h.784c.152 0 .256.024.312.08.064.048.112.16.16.312l1.48 5.824 1.376-5.824c.04-.16.088-.264.152-.312a.53.53 0 01.32-.08h.64c.152 0 .256.024.32.08.064.048.12.16.152.312l1.392 5.896 1.528-5.896c.048-.16.104-.264.16-.312a.488.488 0 01.312-.08h.744c.128 0 .2.064.2.2 0 .04-.008.08-.016.128-.008.048-.024.112-.056.2l-2.128 5.824c-.048.16-.104.264-.168.312-.064.048-.168.08-.304.08h-.688c-.152 0-.256-.024-.32-.08-.064-.056-.12-.16-.152-.32L12.36 7.2l-1.36 5.512c-.04.16-.088.264-.152.32-.064.056-.176.08-.32.08h-.688zm11.348.256c-.416 0-.832-.048-1.232-.144-.4-.096-.712-.2-.92-.32-.128-.072-.216-.152-.248-.224a.56.56 0 01-.048-.224v-.408c0-.168.064-.248.184-.248.048 0 .096.008.144.024.048.016.12.048.2.08.272.12.568.216.888.28.328.064.648.096.976.096.52 0 .92-.088 1.2-.272.28-.184.424-.448.424-.8 0-.232-.072-.424-.224-.576-.152-.152-.44-.288-.864-.416l-1.24-.384c-.624-.192-1.088-.48-1.376-.864-.288-.376-.432-.792-.432-1.24 0-.36.08-.68.24-.952.16-.272.376-.512.648-.704.272-.192.576-.336.928-.432.352-.096.72-.144 1.104-.144.192 0 .392.008.584.04.2.024.384.064.568.104.176.04.344.088.504.144.16.056.288.112.384.168.128.072.216.152.264.24.048.08.072.184.072.312v.376c0 .168-.064.256-.184.256a.836.836 0 01-.304-.096 3.652 3.652 0 00-1.544-.312c-.472 0-.84.072-1.096.224-.256.152-.384.384-.384.704 0 .232.08.432.248.592.168.16.48.32.928.456l1.216.384c.616.192 1.064.464 1.336.816.272.352.408.752.408 1.192 0 .368-.072.704-.216.992-.152.288-.36.544-.632.752-.272.208-.592.36-.96.464-.384.112-.8.168-1.248.168z"/>
                  </svg>
                  AWS
                </p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  {AWS_SUGGESTIONS.map((suggestion) => (
                    <button
                      key={suggestion}
                      onClick={() => { if (!loading) sendMessage(suggestion) }}
                      disabled={loading}
                      className="text-left text-sm px-3 py-2 rounded-lg border border-pdi-cool-gray text-pdi-granite hover:border-pdi-sun hover:bg-pdi-sun/5 transition-all hover:-translate-y-0.5 active:scale-[0.97]"
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {messages.map((msg) => (
          <div key={msg.id}>
            {msg.loading ? (
              <div className="space-y-2">
                {/* Live tool call feed — show completed tools as they arrive */}
                {msg.toolCalls && msg.toolCalls.length > 0 && (
                  <div className="ml-0 max-w-[80%]">
                    {msg.toolCalls.map((tc, i) => (
                      <ToolCallCard
                        key={i}
                        toolName={tc.tool_name}
                        description={tc.description}
                        result={tc.result}
                      />
                    ))}
                  </div>
                )}
                {/* Current activity indicator */}
                <div className="flex justify-start">
                  <div className="bg-white border border-gray-200 rounded-2xl rounded-bl-md px-4 py-3 shadow-sm">
                    <div className="flex items-center gap-2 text-pdi-slate text-sm">
                      <div className="flex gap-1">
                        <span className="w-2 h-2 bg-pdi-sky rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                        <span className="w-2 h-2 bg-pdi-sky rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                        <span className="w-2 h-2 bg-pdi-sky rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                      </div>
                      <span>
                        {msg.currentToolName
                          ? <span>Running <span className="font-mono text-pdi-sky text-xs">{msg.currentToolName}</span></span>
                          : msg.toolCalls && msg.toolCalls.length > 0
                            ? `Analyzing results (${msg.toolCalls.length} tools called)...`
                            : 'Investigating...'}
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            ) : (
              <>
                <MessageBubble role={msg.role} content={msg.content} />
                {msg.toolCalls && msg.toolCalls.length > 0 && (
                  <div className="ml-0 mt-1 max-w-[80%]">
                    {msg.toolCalls.map((tc, i) => (
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
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* AWS account context pill */}
      {awsAccounts.length > 0 && showAccountPill && (
        <div className="px-6 pt-2 flex items-center gap-2 bg-white">
          <span className="inline-flex items-center gap-1.5 text-xs bg-pdi-sun/10 text-pdi-sun px-2.5 py-1 rounded-full border border-pdi-sun/30">
            <svg className="w-3 h-3 shrink-0" viewBox="0 0 24 24" fill="currentColor">
              <path d="M6.763 10.036c0 .296.032.535.088.71.064.176.144.368.256.576.04.064.056.128.056.184 0 .08-.048.16-.152.24l-.504.336a.383.383 0 01-.208.072c-.08 0-.16-.04-.24-.112a2.47 2.47 0 01-.288-.376 6.18 6.18 0 01-.248-.472c-.624.736-1.408 1.104-2.352 1.104-.672 0-1.208-.192-1.6-.576-.392-.384-.592-.896-.592-1.536 0-.68.24-1.232.728-1.648.488-.416 1.136-.624 1.96-.624.272 0 .552.024.848.064.296.04.6.104.92.176v-.584c0-.608-.128-1.032-.376-1.28-.256-.248-.688-.368-1.304-.368-.28 0-.568.032-.864.104-.296.072-.584.16-.864.272a2.294 2.294 0 01-.28.104.488.488 0 01-.128.024c-.112 0-.168-.08-.168-.248v-.392c0-.128.016-.224.056-.28a.597.597 0 01.224-.168c.28-.144.616-.264 1.008-.36A4.84 4.84 0 014.8 6.4c.96 0 1.664.216 2.12.656.448.432.68 1.088.68 1.96v2.584l-.837-.564zm-3.24 1.2c.264 0 .536-.048.824-.144.288-.096.544-.272.76-.512.128-.152.224-.32.272-.512.048-.192.08-.424.08-.696v-.336a6.8 6.8 0 00-.736-.136 6.03 6.03 0 00-.752-.048c-.536 0-.928.104-1.192.32-.264.216-.392.52-.392.92 0 .376.096.656.296.848.192.2.472.296.84.296zm6.44.88c-.144 0-.24-.024-.304-.08-.064-.048-.12-.16-.168-.312L7.684 6.4c-.048-.16-.072-.264-.072-.32 0-.128.064-.2.192-.2h.784c.152 0 .256.024.312.08.064.048.112.16.16.312l1.48 5.824 1.376-5.824c.04-.16.088-.264.152-.312a.53.53 0 01.32-.08h.64c.152 0 .256.024.32.08.064.048.12.16.152.312l1.392 5.896 1.528-5.896c.048-.16.104-.264.16-.312a.488.488 0 01.312-.08h.744c.128 0 .2.064.2.2 0 .04-.008.08-.016.128-.008.048-.024.112-.056.2l-2.128 5.824c-.048.16-.104.264-.168.312-.064.048-.168.08-.304.08h-.688c-.152 0-.256-.024-.32-.08-.064-.056-.12-.16-.152-.32L12.36 7.2l-1.36 5.512c-.04.16-.088.264-.152.32-.064.056-.176.08-.32.08h-.688zm11.348.256c-.416 0-.832-.048-1.232-.144-.4-.096-.712-.2-.92-.32-.128-.072-.216-.152-.248-.224a.56.56 0 01-.048-.224v-.408c0-.168.064-.248.184-.248.048 0 .096.008.144.024.048.016.12.048.2.08.272.12.568.216.888.28.328.064.648.096.976.096.52 0 .92-.088 1.2-.272.28-.184.424-.448.424-.8 0-.232-.072-.424-.224-.576-.152-.152-.44-.288-.864-.416l-1.24-.384c-.624-.192-1.088-.48-1.376-.864-.288-.376-.432-.792-.432-1.24 0-.36.08-.68.24-.952.16-.272.376-.512.648-.704.272-.192.576-.336.928-.432.352-.096.72-.144 1.104-.144.192 0 .392.008.584.04.2.024.384.064.568.104.176.04.344.088.504.144.16.056.288.112.384.168.128.072.216.152.264.24.048.08.072.184.072.312v.376c0 .168-.064.256-.184.256a.836.836 0 01-.304-.096 3.652 3.652 0 00-1.544-.312c-.472 0-.84.072-1.096.224-.256.152-.384.384-.384.704 0 .232.08.432.248.592.168.16.48.32.928.456l1.216.384c.616.192 1.064.464 1.336.816.272.352.408.752.408 1.192 0 .368-.072.704-.216.992-.152.288-.36.544-.632.752-.272.208-.592.36-.96.464-.384.112-.8.168-1.248.168z"/>
            </svg>
            {awsAccounts.length} AWS {awsAccounts.length === 1 ? 'account' : 'accounts'} in scope
            <button
              onClick={() => setShowAccountPill(false)}
              className="ml-0.5 hover:text-pdi-sun/80 transition-colors"
              aria-label="Dismiss"
            >
              ×
            </button>
          </span>
        </div>
      )}

      {/* Input */}
      <div className="px-6 py-4 border-t border-gray-200 bg-white">
        <form onSubmit={handleSubmit} className="flex gap-3">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask Holmes a question..."
            style={{ resize: 'none', overflow: 'hidden' }}
            className="flex-1 min-h-[42px] max-h-40 px-4 py-2.5 border border-pdi-cool-gray rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-pdi-sky focus:border-transparent placeholder:text-pdi-slate/60"
            disabled={loading}
          />
          <button
            type="submit"
            disabled={loading || !input.trim()}
            className="px-4 py-2.5 bg-pdi-sky text-white rounded-xl font-medium text-sm hover:bg-pdi-ocean active:scale-95 transition-colors disabled:bg-pdi-cool-gray disabled:text-pdi-slate disabled:cursor-not-allowed shrink-0"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 12L3.269 3.126A59.768 59.768 0 0121.485 12 59.77 59.77 0 013.27 20.876L5.999 12zm0 0h7.5" />
            </svg>
          </button>
        </form>
      </div>
    </div>
    <SimilarInvestigationsPanel query={input} projectId={projectId ?? undefined} />
    </div>
  )
}
