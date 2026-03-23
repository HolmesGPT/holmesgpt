const BASE = '';

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
    credentials: 'include',
  });

  if (res.status === 401) {
    // Only redirect to login if we're not already on the login page
    const onLoginPage = window.location.pathname === '/auth/login' || window.location.pathname === '/login';
    if (!onLoginPage) {
      window.location.href = '/auth/login';
    }
    throw new Error('Unauthorized');
  }

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status}: ${text}`);
  }

  return res.json();
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface ChatRequest {
  ask: string;
  conversation_history?: ChatMessage[];
  stream?: boolean;
}

export interface ChatResponse {
  analysis: string;
  tool_calls?: ToolCall[];
}

export interface ToolCall {
  tool_name: string;
  description: string;
  result: string;
}

export interface InvestigateRequest {
  source: string;
  title: string;
  description: string;
  subject: Record<string, string>;
  context: Record<string, string>;
  include_tool_calls?: boolean;
  include_tool_call_results?: boolean;
  project_id?: string | null;
}

export interface InvestigateResponse {
  analysis: string;
  tool_calls?: ToolCall[];
}

export interface ModelInfo {
  model_name: string;
}

export interface HealthStatus {
  status: string;
}

export interface ConfigField {
  name: string;
  type: 'str' | 'int' | 'float' | 'bool' | 'dict' | 'list';
  required: boolean;
  default: unknown;
  description: string;
  sensitive: boolean;
}

export interface Integration {
  name: string;
  description: string;
  status: 'enabled' | 'disabled' | 'failed';
  type: 'built-in' | 'custom' | 'mcp' | 'http' | 'database';
  error: string | null;
  icon_url: string | null;
  docs_url: string | null;
  tool_count: number;
  enabled: boolean;
  config: Record<string, unknown>;
  config_schema: ConfigField[];
}

export interface IntegrationsResponse {
  integrations: Integration[];
}

export interface AwsAccount {
  name: string;
  account_id: string;
  region: string;
  role_arn: string;
}

export interface AwsAccountsResponse {
  accounts: AwsAccount[];
  irsa_role: string;
}

export interface WebhookInfo {
  id: string;
  name: string;
  url: string;
  auth_type: string;
  trigger: string;
  configured: boolean;
  vars: Record<string, boolean>;
  write_back_enabled: boolean;
  write_back_capable: boolean;
}

export interface WebhooksResponse {
  webhooks: WebhookInfo[];
  webhook_dev_mode: boolean;
}

export interface AppSettings {
  webhook_dev_mode: boolean;
  system_prompt_additions: string;
}

export interface LlmInstructionsEntry {
  name: string;
  description: string;
  type: string;
  icon_url: string | null;
  enabled: boolean;
  instructions: string;
  has_default: boolean;
  is_overridden: boolean;
}

export interface LlmInstructionsResponse {
  integrations: LlmInstructionsEntry[];
}

export interface UpdateLlmInstructionsResponse {
  name: string;
  instructions: string;
  is_overridden: boolean;
}

export interface ToolsetInstance {
  type: string;
  name: string;
  secret_arn: string | null;
  /** For MCP toolsets: override the MCP server URL (null = use global URL) */
  mcp_url?: string | null;
  /** For aws_api: restrict to these account profile names (null = all configured accounts) */
  aws_accounts?: string[] | null;
}

export interface TagFilter {
  logic: 'AND' | 'OR';
  tags: Record<string, string>;
}

export interface Instance {
  id: string;
  type: string;
  name: string;
  tags: Record<string, string>;
  secret_arn: string | null;
  mcp_url?: string | null;
  aws_accounts?: string[] | null;
  aws_account_name?: string | null;
  aws_account_id?: string | null;
  aws_role_arn?: string | null;
  aws_connection_status?: string | null;
  aws_connection_error?: string | null;
  created_at: string;
}

export interface TestConnectionResponse {
  ok: boolean;
  status: string;
  assumed_role?: string;
  error?: string;
}

export interface InstancesResponse {
  instances: Instance[];
}

export interface ProjectPreview {
  project_id: string;
  tag_filter: TagFilter | null;
  resolved_instances: Instance[];
  total_instances: number;
  resolved_count: number;
}

export interface WebhookRouting {
  ado: string[];
  pagerduty: string[];
  salesforce: string[];
}

export interface Project {
  id: string;
  name: string;
  description: string;
  tag_filter: TagFilter | null;
  webhook_write_back: Record<string, boolean | null> | null;
  webhook_routing: WebhookRouting | null;
  created_at: string;
}

export interface ProjectsResponse {
  projects: Project[];
}

export interface ToolCallRecord {
  tool_name: string;
  tool_input: Record<string, unknown>;
  tool_output: string;
  called_at: string;
}

export interface Investigation {
  id: string;
  started_at: string;
  finished_at: string;
  trigger: string;
  source: string;
  source_id: string;
  source_url: string;
  question: string;
  answer: string;
  tool_calls: ToolCallRecord[];
  project_id: string;
  status: 'running' | 'completed' | 'failed';
  error: string;
  feedback: string | null;
  resolution_summary: string | null;
  metadata?: {
    model?: string;
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
    duration_ms?: number;
  };
}

export interface SimilarInvestigation {
  id: string;
  question: string;
  answer_summary: string;
  source: string;
  started_at: string;
  score: number;
  tools_used: string[];
  feedback: string | null;
  resolution_summary: string | null;
}

export const api = {
  chat(data: ChatRequest): Promise<ChatResponse> {
    return request('/api/chat', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  async chatStream(
    data: ChatRequest,
    onChunk: (text: string) => void,
    signal?: AbortSignal,
  ): Promise<void> {
    const res = await fetch(`${BASE}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ ...data, stream: true }),
      signal,
    });

    if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
    if (!res.body) throw new Error('No response body');

    const reader = res.body.getReader();
    const decoder = new TextDecoder();

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      onChunk(decoder.decode(value, { stream: true }));
    }
  },

  investigate(data: InvestigateRequest): Promise<InvestigateResponse> {
    return request('/api/investigate', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  /**
   * Like investigate() but reads the SSE stream so keepalive comments
   * prevent the ALB 300s idle-timeout from killing long investigations.
   * Resolves with the parsed JSON from the final `data:` event.
   */
  async investigateStream(data: InvestigateRequest): Promise<InvestigateResponse> {
    const res = await fetch(`${BASE}/api/investigate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify(data),
    });

    if (res.status === 401) {
      const onLoginPage = window.location.pathname === '/auth/login' || window.location.pathname === '/login';
      if (!onLoginPage) window.location.href = '/auth/login';
      throw new Error('Unauthorized');
    }

    if (!res.ok) {
      const text = await res.text();
      throw new Error(`${res.status}: ${text}`);
    }

    if (!res.body) throw new Error('No response body');

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Process complete SSE lines
      const lines = buffer.split('\n');
      // Keep the last (potentially incomplete) line in the buffer
      buffer = lines.pop() ?? '';

      for (const line of lines) {
        const trimmed = line.trim();
        // Skip keepalive comments and blank lines
        if (!trimmed || trimmed.startsWith(':')) continue;

        if (trimmed.startsWith('data: ')) {
          const jsonStr = trimmed.slice(6);
          const parsed = JSON.parse(jsonStr) as { error?: string; analysis?: string; tool_calls?: ToolCall[] };
          if (parsed.error) throw new Error(parsed.error);
          return { analysis: parsed.analysis ?? '', tool_calls: parsed.tool_calls };
        }
      }
    }

    // Flush remaining buffer
    if (buffer.trim().startsWith('data: ')) {
      const jsonStr = buffer.trim().slice(6);
      const parsed = JSON.parse(jsonStr) as { error?: string; analysis?: string; tool_calls?: ToolCall[] };
      if (parsed.error) throw new Error(parsed.error);
      return { analysis: parsed.analysis ?? '', tool_calls: parsed.tool_calls };
    }

    throw new Error('Investigation stream ended without a result');
  },

  getModel(): Promise<ModelInfo> {
    return request('/api/model');
  },

  async getHealth(): Promise<HealthStatus> {
    return request('/healthz');
  },

  async getReadiness(): Promise<HealthStatus> {
    return request('/readyz');
  },

  getIntegrations(): Promise<IntegrationsResponse> {
    return request('/api/integrations');
  },

  getAwsAccounts(): Promise<AwsAccountsResponse> {
    return request('/api/aws/accounts');
  },

  getWebhooks(): Promise<WebhooksResponse> {
    return request('/api/webhooks');
  },

  updateWebhookSettings(webhookId: string, settings: { write_back_enabled: boolean }): Promise<{ ok: boolean; webhook_id: string; write_back_enabled: boolean }> {
    return request(`/api/webhooks/${webhookId}/settings`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    });
  },

  getAppSettings(): Promise<AppSettings> {
    return request('/api/app-settings');
  },

  updateAppSettings(settings: Partial<AppSettings>): Promise<AppSettings> {
    return request('/api/app-settings', {
      method: 'PUT',
      body: JSON.stringify(settings),
    });
  },

  getLlmInstructions(): Promise<LlmInstructionsResponse> {
    return request('/api/llm-instructions');
  },

  updateLlmInstructions(name: string, instructions: string): Promise<UpdateLlmInstructionsResponse> {
    return request(`/api/llm-instructions/${encodeURIComponent(name)}`, {
      method: 'PUT',
      body: JSON.stringify({ instructions }),
    });
  },

  resetLlmInstructions(name: string): Promise<UpdateLlmInstructionsResponse> {
    return request(`/api/llm-instructions/${encodeURIComponent(name)}`, {
      method: 'DELETE',
    });
  },

  toggleIntegration(name: string, enabled: boolean): Promise<Integration> {
    return request(`/api/integrations/${encodeURIComponent(name)}/toggle`, {
      method: 'PUT',
      body: JSON.stringify({ enabled }),
    });
  },

  updateIntegrationConfig(name: string, config: Record<string, unknown>, enabled?: boolean): Promise<Integration> {
    return request(`/api/integrations/${encodeURIComponent(name)}/config`, {
      method: 'PUT',
      body: JSON.stringify({ config, enabled }),
    });
  },

  getProjects(): Promise<ProjectsResponse> {
    return request('/api/projects');
  },

  createProject(data: {
    name: string;
    description?: string;
    tag_filter?: TagFilter | null;
    webhook_routing?: WebhookRouting | null;
  }): Promise<Project> {
    return request('/api/projects', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  updateProject(id: string, data: Partial<{
    name: string;
    description: string;
    tag_filter: TagFilter | null;
    webhook_routing: WebhookRouting | null;
  }>): Promise<Project> {
    return request(`/api/projects/${encodeURIComponent(id)}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  deleteProject(id: string): Promise<{ ok: boolean }> {
    return request(`/api/projects/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    });
  },

  previewProject(id: string): Promise<ProjectPreview> {
    return request(`/api/projects/${encodeURIComponent(id)}/preview`);
  },

  getProjectWebhookSettings(id: string): Promise<Record<string, { write_back_enabled: boolean; is_override: boolean; global_default: boolean }>> {
    return request(`/api/projects/${encodeURIComponent(id)}/webhook-settings`);
  },

  listInstances(): Promise<Instance[]> {
    return request<InstancesResponse>('/api/instances').then((r) => r.instances);
  },

  getInstance(id: string): Promise<Instance> {
    return request(`/api/instances/${encodeURIComponent(id)}`);
  },

  createInstance(data: Omit<Instance, 'id' | 'created_at'>): Promise<Instance> {
    return request('/api/instances', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  updateInstance(id: string, data: Partial<Omit<Instance, 'id' | 'created_at'>>): Promise<Instance> {
    return request(`/api/instances/${encodeURIComponent(id)}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  deleteInstance(id: string): Promise<{ ok: boolean }> {
    return request(`/api/instances/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    });
  },

  testInstanceConnection(id: string): Promise<TestConnectionResponse> {
    return request(`/api/instances/${encodeURIComponent(id)}/test-connection`, {
      method: 'POST',
    });
  },

  getInvestigations(params?: {
    limit?: number;
    source?: string;
    project_id?: string;
    start_date?: string;
    end_date?: string;
  }): Promise<Investigation[]> {
    const qs = new URLSearchParams();
    if (params?.limit) qs.set('limit', String(params.limit));
    if (params?.source) qs.set('source', params.source);
    if (params?.project_id) qs.set('project_id', params.project_id);
    if (params?.start_date) qs.set('start_date', params.start_date);
    if (params?.end_date) qs.set('end_date', params.end_date);
    const query = qs.toString();
    return request(`/api/investigations${query ? `?${query}` : ''}`);
  },

  getInvestigation(id: string): Promise<Investigation> {
    return request(`/api/investigations/${encodeURIComponent(id)}`);
  },

  deleteInvestigation(id: string): Promise<{ ok: boolean }> {
    return request(`/api/investigations/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    });
  },

  getSimilarInvestigations(query: string, projectId?: string, limit?: number): Promise<SimilarInvestigation[]> {
    const qs = new URLSearchParams({ q: query });
    if (projectId) qs.set('project_id', projectId);
    if (limit) qs.set('limit', String(limit));
    return request(`/api/investigations/similar?${qs.toString()}`);
  },

  updateInvestigationFeedback(id: string, feedback: 'helpful' | 'not_helpful', resolutionSummary?: string): Promise<{ id: string; feedback: string; resolution_summary: string | null }> {
    return request(`/api/investigations/${encodeURIComponent(id)}/feedback`, {
      method: 'PUT',
      body: JSON.stringify({ feedback, resolution_summary: resolutionSummary }),
    });
  },

  async login(username: string, password: string): Promise<{ ok: boolean }> {
    const res = await fetch(`${BASE}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ username, password }),
    });
    if (!res.ok) throw new Error('Invalid credentials');
    return { ok: true };
  },

  async logout(): Promise<void> {
    await fetch(`${BASE}/auth/logout`, {
      method: 'POST',
      credentials: 'include',
    });
  },

  async checkAuth(): Promise<boolean> {
    try {
      const res = await fetch(`${BASE}/auth/check`, { credentials: 'include' });
      return res.ok;
    } catch {
      return false;
    }
  },
};
