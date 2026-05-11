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
}

// ── Plan ──────────────────────────────────────────
export interface PlanOption {
  id: string
  label: string
  description?: string | null
}

// ── 工具事件 ──────────────────────────────────────
export type AgentEventType =
  | 'run_start' | 'run_end'
  | 'agent_state'
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
  run_id?: string
  ts: number
  text?: string
  name?: string
  input?: Record<string, unknown>
  output?: string
  tool_use_id?: string
  content?: string
  state?: 'idle' | 'thinking' | 'executing_tool'
  message?: string
}

// ── 文件变更 ──────────────────────────────────────
export interface FileChange {
  path: string
  op: 'create' | 'modify' | 'delete'
}

// ── 任务 ──────────────────────────────────────────
export type TaskStatus = 'pending' | 'in_progress' | 'completed' | 'deleted'

export interface Task {
  id: number
  subject: string
  description: string
  status: TaskStatus
  owner: string
  blockedBy: number[]
}

// ── Todo ──────────────────────────────────────────
export type TodoStatus = 'pending' | 'in_progress' | 'completed'

export interface TodoItem {
  content: string
  status: TodoStatus
  activeForm: string
}

// ── 团队 ──────────────────────────────────────────
export type MemberStatus = 'working' | 'idle' | 'shutdown'

export interface TeamMember {
  name: string
  role: string
  status: MemberStatus
}

// ── 安全快照 ──────────────────────────────────────
export interface Checkpoint {
  id: string
  name: string
  description: string
  created_at: number
  datetime: string
  size: number
  paths: string[]
}

// ── 文件树 ──────────────────────────────────────
export interface FileNode {
  name: string
  type: 'file' | 'dir'
  path: string
  size?: number
  children?: FileNode[]
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

// ── 总结卡片 ──────────────────────────────────────
export interface SummaryCard {
  id: string
  type: 'summary'
  run_id: string
  input_tokens: number
  output_tokens: number
  tool_rounds: number
  file_operations: number
  ts: number
}

// ── 文件差异 ──────────────────────────────────────
export interface FileDiff {
  path: string
  diff: string
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
  agent_state: 'idle' | 'thinking' | 'executing_tool'
  run_id: string | null
  workdir: string
  model: string
  history_length: number
  token_usage?: TokenUsage
  token_breakdown?: TokenBreakdown
  memory_stats: Record<string, unknown>
  safety_stats: Record<string, unknown>
}
