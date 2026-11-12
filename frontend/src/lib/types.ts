// ── 聊天消息 ──────────────────────────────────────
export type Role = 'user' | 'assistant' | 'system'

export interface TextContentBlock {
  type: 'text'
  text: string
}

export interface ImageContentBlock {
  type: 'image'
  source: {
    type: 'base64'
    media_type: string
    data: string
  }
}

export type ContentBlock = TextContentBlock | ImageContentBlock

export interface ChatMessage {
  id: string
  role: Role
  content: string | ContentBlock[]
  ts: number
  runId?: string
  streaming?: boolean
  thinking?: string
  toolCalls?: import('../hooks/useChat').ToolCall[]
  steps?: AssistantStep[]
  summary?: { input_tokens: number; output_tokens: number; tool_rounds: number; file_operations: number }
}

export interface AssistantStep {
  type: 'thinking' | 'tool_call'
  text?: string
  toolCall?: import('../hooks/useChat').ToolCall
}

// ── 会话 ──────────────────────────────────────
export interface SessionInfo {
  id: string
  title: string
  created_at: number
  updated_at: number
  message_count: number
  workdir: string
  running?: boolean
}

export interface SessionSnapshot {
  session_id: string
  history: Array<{ role: string; content: unknown }>
  history_run_ids?: Array<string | null>
  total?: number
  title?: string
  workdir: string
  mode?: AgentMode
  running: boolean
  run_id: string | null
  phase?: 'running' | 'reviewing' | 'finishing' | null
  events: AgentEvent[]
  event_seq: number
  accepted_requests?: Record<string, string>
  delivery_reports: DeliveryReport[]
}

export type DeliveryOutcome = 'completed' | 'failed' | 'interrupted' | 'incomplete'

export interface DeliveryFile {
  path: string
  status: string
}

export interface DeliveryCommand {
  tool_use_id: string
  command: string
  exit_code: number | null
  duration?: number
  timed_out?: boolean
  cancelled?: boolean
  stdout?: string
  stderr?: string
}

export type A2AReviewFinalStatus =
  | 'completed' | 'failed' | 'cancelled' | 'unknown'
  | 'cancel_unknown' | 'stale' | 'skipped'

export type A2AReviewStatus = A2AReviewFinalStatus | 'working' | 'submitted'

export interface A2AReviewFinding {
  severity: 'high' | 'medium' | 'low'
  file: string
  line: number
  reason: string
  evidence: string
  suggestion: string
}

export interface A2AReviewUsage {
  input_tokens: number | null
  output_tokens: number | null
}

export interface A2AReviewArtifact {
  schema_version: 1
  session_id: string
  run_id: string
  invocation_id: string
  patch_hash: string
  snapshot_hash: string
  verdict: 'no_findings' | 'changes_requested'
  findings: A2AReviewFinding[]
  context_limits: string[]
  usage: A2AReviewUsage
}

interface A2AReviewBase {
  session_id?: string
  run_id?: string
  invocation_id?: string
  patch_hash?: string
  snapshot_hash?: string
  task_id?: string | null
  context_id?: string | null
  agent_url?: string
  message_id?: string
  artifact?: A2AReviewArtifact | null
  error?: string | null
  started_at?: number
  finished_at?: number | null
  duration?: number
  usage?: A2AReviewUsage
}

export interface A2AReview extends A2AReviewBase {
  status: A2AReviewStatus
}

export interface A2AReviewEvent extends A2AReviewBase {
  status: A2AReviewStatus
}

export interface DeliveryReport {
  run_id: string
  session_id: string
  outcome: DeliveryOutcome
  started_at: number
  finished_at: number
  base_sha: string | null
  files: DeliveryFile[]
  diff: string
  commands: DeliveryCommand[]
  usage: {
    input_tokens: number
    output_tokens: number
  }
  warnings: string[]
  review?: A2AReview
}

// ── 图片附件（本地状态）─────────────────────────
export interface ImageAttachment {
  id: string
  dataUrl: string
  base64Data: string
  mediaType: string
  fileName?: string
}

// ── Agent 模式 ──────────────────────────────────
export type AgentMode = 'plan' | 'auto' | 'manual'

// ── 文件上下文 ──────────────────────────────────
export interface FileContext {
  path: string
  content: string
  included: boolean
}

// ── 斜杠命令 ───────────────────────────────────
export interface SlashCommand {
  trigger: string
  label: string
  description: string
}

// ── 模型信息 ───────────────────────────────────
export interface ModelInfo {
  id: string
  label: string
  provider: string
  configured: boolean
}

// ── 工具事件 ──────────────────────────────────────
export type AgentEventType =
  | 'run_start' | 'run_end' | 'delivery_report'
  | 'agent_state' | 'a2a_review'
  | 'chunk'
  | 'tool_start' | 'tool_result'
  | 'tool_approval_request'
  | 'plan_proposed'
  | 'options_presented'
  | 'usage'
  | 'thinking'
  | 'system'
  | 'interrupted'
  | 'error'

export interface AgentEvent {
  type: AgentEventType
  session_id?: string
  event_seq?: number
  run_id?: string
  ts: number
  text?: string
  name?: string
  input?: Record<string, unknown>
  output?: string
  tool_use_id?: string
  content?: string
  state?: 'idle' | 'thinking' | 'executing_tool' | 'reviewing' | 'cancelling'
  message?: string
  is_error?: boolean
  cancelled?: boolean
  timed_out?: boolean
  exit_code?: number
  duration?: number
  report?: DeliveryReport
  review?: A2AReviewEvent
}

// ── Todo ──────────────────────────────────────────
export type TodoStatus = 'pending' | 'in_progress' | 'completed'

export interface TodoItem {
  content: string
  status: TodoStatus
  activeForm: string
}

// ── Token usage ────────────────────────────────────
export interface TokenUsage {
  estimated_tokens: number
  threshold: number
  usage_percent: number
}

export interface TokenBreakdown {
  estimated_tokens: number
  threshold: number
  usage_percent: number
  system_tokens: number
  tool_tokens: number
  messages_tokens: number
  user_context_tokens: number
}

// ── API 响应包装 ───────────────────────────────────
export interface ApiResponse<T> {
  success: boolean
  data?: T
  error?: string
  ts: number
}

// ── 状态 ──────────────────────────────────────────
export interface AgentStatus {
  session_id?: string
  running_session_id?: string | null
  running_run_id?: string | null
  agent_state: 'idle' | 'thinking' | 'executing_tool' | 'reviewing' | 'cancelling'
  run_id: string | null
  workdir: string
  model: string
  history_length: number
  token_usage?: TokenUsage
  token_breakdown?: TokenBreakdown
  memory_stats: Record<string, unknown>
  safety_stats: Record<string, unknown>
}
