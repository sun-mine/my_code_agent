import { v4 as uuidv4 } from 'uuid'
import type { AgentEvent, ChatMessage, ContentBlock, TodoItem } from './types'

export interface ToolCall {
  id: string
  run_id: string
  name: string
  input: Record<string, unknown>
  output?: string
  status: 'running' | 'done' | 'error'
  ts_start: number
  ts_end?: number
}

export interface FileChange {
  path: string
  op: 'create' | 'modify' | 'delete'
}

export interface ApprovalRequest {
  id: string
  run_id: string
  name: string
  input: Record<string, unknown>
  ts: number
}

export interface PlanProposal {
  run_id: string
  content: string
  ts: number
  status: 'pending' | 'approved' | 'rejected'
}

export interface PlanOption {
  id: string
  label: string
  description?: string | null
}

type AgentState = 'idle' | 'thinking' | 'executing_tool'

interface ChatSessionState {
  messages: ChatMessage[]
  todos: TodoItem[]
  toolCalls: ToolCall[]
  agentState: AgentState
  runId: string | null
  pendingApprovals: ApprovalRequest[]
  changedFiles: FileChange[]
  planProposal: PlanProposal | null
  planOptions: PlanOption[] | null
}

export interface HydrateSnapshot {
  events?: AgentEvent[]
  event_seq: number
  running?: boolean
  run_id?: string | null
}

type StateUpdate<T> = T | ((previous: T) => T)
type Listener = () => void

interface SessionEntry {
  state: ChatSessionState
  streamingId: string | null
  currentRunId: string | null
  pendingBash: Map<string, string>
  toolRoundCount: number
  fileOpCount: number
  runInputTokens: number
  runOutputTokens: number
  lastEventSeq: number
  hydratedBoundary: number
  lastSnapshotRunning: boolean | null
  /** Events received from the socket, retained until a later snapshot contains them. */
  liveEvents: AgentEvent[]
}

const CREATE_TOOLS = new Set(['write_file', 'create_file', 'save_file'])
const MODIFY_TOOLS = new Set(['edit_file'])
const WRITE_CMD_PATTERNS = [/\s>\s/, /\s>>\s/, /\| tee\s/, /mkdir\s/, /touch\s/, /mv\s/, /cp\s/, /rm\s/]

function isBashWrite(command: string): boolean {
  return WRITE_CMD_PATTERNS.some(pattern => pattern.test(command))
}

function initialState(messages: ChatMessage[] = []): ChatSessionState {
  return {
    messages,
    todos: [],
    toolCalls: [],
    agentState: 'idle',
    runId: null,
    pendingApprovals: [],
    changedFiles: [],
    planProposal: null,
    planOptions: null,
  }
}

const EMPTY_STATE: ChatSessionState = initialState()

function createEntry(messages: ChatMessage[] = []): SessionEntry {
  return {
    state: initialState(messages),
    streamingId: null,
    currentRunId: null,
    pendingBash: new Map(),
    toolRoundCount: 0,
    fileOpCount: 0,
    runInputTokens: 0,
    runOutputTokens: 0,
    lastEventSeq: 0,
    hydratedBoundary: -1,
    lastSnapshotRunning: null,
    liveEvents: [],
  }
}

function eventSequence(event: AgentEvent): number | null {
  return typeof event.event_seq === 'number' && Number.isFinite(event.event_seq)
    ? event.event_seq
    : null
}

function updateFileChange(state: ChatSessionState, path: string, op: FileChange['op']): ChatSessionState {
  const existing = state.changedFiles.find(file => file.path === path)
  if (!existing) {
    return { ...state, changedFiles: [...state.changedFiles, { path, op }] }
  }
  const opOrder = { create: 0, modify: 1, delete: 2 } as const
  const nextOp = opOrder[op] > opOrder[existing.op] ? op : existing.op
  return {
    ...state,
    changedFiles: state.changedFiles.map(file => file.path === path ? { ...file, op: nextOp } : file),
  }
}

function finalizeStreamingMessage(
  state: ChatSessionState,
  streamingId: string | null,
  interrupted = false,
): ChatSessionState {
  if (!streamingId) return state
  return {
    ...state,
    messages: state.messages.map(message => {
      if (message.id !== streamingId) return message
      const content = interrupted && typeof message.content === 'string'
        ? `${message.content}\n\n_[已中断]_`
        : message.content
      return { ...message, content, streaming: false }
    }),
  }
}

/**
 * Per-session chat state. Socket events are routed by their immutable session id;
 * activating another session only changes which state React subscribes to.
 */
export class ChatStore {
  private readonly sessions = new Map<string, SessionEntry>()
  private readonly listeners = new Set<Listener>()
  private activeSessionId: string | null = null

  readonly subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  readonly getSnapshot = (): ChatSessionState => {
    if (!this.activeSessionId) return EMPTY_STATE
    return this.sessions.get(this.activeSessionId)?.state ?? EMPTY_STATE
  }

  getActiveSessionId(): string | null {
    return this.activeSessionId
  }

  getSessionState(sessionId: string): ChatSessionState | undefined {
    return this.sessions.get(sessionId)?.state
  }

  activateSession(sessionId: string): void {
    if (!sessionId) return
    this.ensureEntry(sessionId)
    if (this.activeSessionId === sessionId) return
    this.activeSessionId = sessionId
    this.emit()
  }

  hydrateSession(sessionId: string, messages: ChatMessage[], snapshot: HydrateSnapshot): void {
    if (!sessionId) return
    const current = this.sessions.get(sessionId)
    const boundary = Number.isFinite(snapshot.event_seq) ? snapshot.event_seq : 0

    // A slower request must never roll a session back behind a snapshot already applied.
    if (current && boundary < current.hydratedBoundary) return
    // At an equal boundary, a completed snapshot is newer than a still-running one.
    if (current && boundary === current.hydratedBoundary &&
        current.lastSnapshotRunning === false && snapshot.running === true) return

    const pendingEvents = (current?.liveEvents ?? []).filter(event => {
      const sequence = eventSequence(event)
      return sequence === null || sequence > boundary
    })

    const entry = createEntry(messages)
    entry.hydratedBoundary = boundary
    entry.lastSnapshotRunning = snapshot.running ?? false
    if (snapshot.running) {
      entry.state = {
        ...entry.state,
        agentState: 'thinking',
        runId: snapshot.run_id ?? null,
      }
      entry.currentRunId = snapshot.run_id ?? null
    }

    const snapshotEvents = [...(snapshot.events ?? [])]
      .filter(event => {
        const sequence = eventSequence(event)
        return sequence === null || sequence <= boundary
      })
      .sort((left, right) => (eventSequence(left) ?? 0) - (eventSequence(right) ?? 0))

    for (const event of snapshotEvents) {
      // The endpoint owns these events even if an older server omitted the redundant id.
      this.applyEvent(entry, event.session_id ? event : { ...event, session_id: sessionId })
    }
    entry.lastEventSeq = Math.max(entry.lastEventSeq, boundary)
    entry.liveEvents = pendingEvents
    for (const event of pendingEvents) this.applyEvent(entry, event)

    this.sessions.set(sessionId, entry)
    if (this.activeSessionId === sessionId) this.emit()
  }

  handleEvent(event: AgentEvent): void {
    const sessionId = event.session_id
    // Runtime agent events without ownership cannot safely be shown in any conversation.
    if (!sessionId) return
    const entry = this.ensureEntry(sessionId)
    if (!this.applyEvent(entry, event)) return
    entry.liveEvents.push(event)
    if (this.activeSessionId === sessionId) this.emit()
  }

  addUserMessage(content: string | ContentBlock[]): void {
    this.updateActive(entry => {
      entry.state = {
        ...entry.state,
        messages: [...entry.state.messages, {
          id: uuidv4(),
          role: 'user',
          content,
          ts: Date.now(),
          runId: entry.currentRunId ?? undefined,
        }],
      }
    })
  }

  clearAll(): void {
    this.updateActive(entry => {
      const lastEventSeq = entry.lastEventSeq
      const hydratedBoundary = Math.max(entry.hydratedBoundary, lastEventSeq)
      Object.assign(entry, createEntry())
      entry.lastEventSeq = lastEventSeq
      entry.hydratedBoundary = hydratedBoundary
      entry.lastSnapshotRunning = false
    })
  }

  dismissApproval(id: string): void {
    this.updateActive(entry => {
      entry.state = {
        ...entry.state,
        pendingApprovals: entry.state.pendingApprovals.filter(approval => approval.id !== id),
      }
    })
  }

  dismissPlan(): void {
    this.setPlanProposal(null)
  }

  setPlanProposal(update: StateUpdate<PlanProposal | null>): void {
    this.updateActive(entry => {
      const value = typeof update === 'function'
        ? update(entry.state.planProposal)
        : update
      entry.state = { ...entry.state, planProposal: value }
    })
  }

  setPlanOptions(update: StateUpdate<PlanOption[] | null>): void {
    this.updateActive(entry => {
      const value = typeof update === 'function'
        ? update(entry.state.planOptions)
        : update
      entry.state = { ...entry.state, planOptions: value }
    })
  }

  private ensureEntry(sessionId: string): SessionEntry {
    let entry = this.sessions.get(sessionId)
    if (!entry) {
      entry = createEntry()
      this.sessions.set(sessionId, entry)
    }
    return entry
  }

  private updateActive(update: (entry: SessionEntry) => void): void {
    if (!this.activeSessionId) return
    const entry = this.ensureEntry(this.activeSessionId)
    update(entry)
    this.emit()
  }

  private emit(): void {
    for (const listener of this.listeners) listener()
  }

  private applyEvent(entry: SessionEntry, event: AgentEvent): boolean {
    const sequence = eventSequence(event)
    if (sequence !== null) {
      if (sequence <= entry.lastEventSeq) return false
      entry.lastEventSeq = sequence
    }

    // A delayed terminal event from an older run must not stop a newer run.
    if ((event.type === 'run_end' || event.type === 'interrupted' || event.type === 'error') &&
        event.run_id && entry.currentRunId && event.run_id !== entry.currentRunId) {
      return true
    }

    const isTerminal = event.type === 'run_end' || event.type === 'interrupted' || event.type === 'error'
    if (event.type !== 'run_start' && !isTerminal && event.run_id) {
      if (entry.currentRunId && event.run_id !== entry.currentRunId) return true
      // A socket can deliver the first streamed event before the HTTP/socket start
      // acknowledgement. Treat that event as sufficient evidence that the run is live.
      if (!entry.currentRunId) {
        entry.currentRunId = event.run_id
        entry.state = {
          ...entry.state,
          runId: event.run_id,
          agentState: event.type === 'tool_start' ? 'executing_tool' : 'thinking',
        }
      }
    }

    switch (event.type) {
      case 'run_start': {
        const nextRunId = event.run_id ?? null
        const sameRun = nextRunId !== null && entry.currentRunId === nextRunId
        entry.state = {
          ...entry.state,
          runId: nextRunId,
          agentState: entry.state.agentState === 'executing_tool' && sameRun ? 'executing_tool' : 'thinking',
          planOptions: sameRun ? entry.state.planOptions : null,
        }
        if (!sameRun) {
          entry.currentRunId = nextRunId
          entry.streamingId = null
          entry.toolRoundCount = 0
          entry.fileOpCount = 0
          entry.runInputTokens = 0
          entry.runOutputTokens = 0
        }
        break
      }

      case 'run_end': {
        const finishedStreamingId = entry.streamingId
        entry.streamingId = null
        entry.state = finalizeStreamingMessage(entry.state, finishedStreamingId)
        const endingRunId = event.run_id ?? entry.currentRunId
        const hasRunOutput = entry.state.messages.some(message =>
          message.role === 'assistant' && message.runId === endingRunId,
        )
        if (!finishedStreamingId && !hasRunOutput) {
          entry.state = {
            ...entry.state,
            messages: [...entry.state.messages, {
              id: uuidv4(),
              role: 'assistant',
              content: '（未收到模型输出，请重试）',
              ts: Date.now(),
              runId: event.run_id,
            }],
          }
        }
        entry.state = {
          ...entry.state,
          runId: null,
          agentState: 'idle',
          pendingApprovals: [],
          toolCalls: entry.state.toolCalls.map(toolCall =>
            toolCall.run_id === (event.run_id ?? '') && toolCall.status === 'running'
              ? { ...toolCall, status: 'done' as const, ts_end: event.ts }
              : toolCall,
          ),
        }
        const summary = {
          input_tokens: entry.runInputTokens,
          output_tokens: entry.runOutputTokens,
          tool_rounds: entry.toolRoundCount,
          file_operations: entry.fileOpCount,
        }
        for (let index = entry.state.messages.length - 1; index >= 0; index--) {
          if (entry.state.messages[index].role !== 'assistant') continue
          entry.state = {
            ...entry.state,
            messages: entry.state.messages.map((message, messageIndex) =>
              messageIndex === index ? { ...message, summary } : message,
            ),
          }
          break
        }
        entry.currentRunId = null
        break
      }

      case 'agent_state':
        if (event.state) entry.state = { ...entry.state, agentState: event.state }
        break

      case 'chunk': {
        const text = event.text ?? ''
        const existingId = entry.streamingId
        const streamingId = existingId ?? uuidv4()
        entry.streamingId = streamingId
        entry.state = {
          ...entry.state,
          messages: existingId
            ? entry.state.messages.map(message => message.id === streamingId
              ? { ...message, content: `${typeof message.content === 'string' ? message.content : ''}${text}` }
              : message)
            : [...entry.state.messages, {
              id: streamingId,
              role: 'assistant',
              content: text,
              ts: Date.now(),
              streaming: true,
              runId: entry.currentRunId ?? event.run_id ?? undefined,
            }],
        }
        break
      }

      case 'thinking': {
        const text = event.text ?? ''
        const existingId = entry.streamingId
        const streamingId = existingId ?? uuidv4()
        entry.streamingId = streamingId
        const applyThinking = (message: ChatMessage): ChatMessage => {
          const steps = [...(message.steps ?? [])]
          const lastStep = steps[steps.length - 1]
          if (lastStep?.type === 'thinking') {
            steps[steps.length - 1] = { ...lastStep, text: `${lastStep.text ?? ''}${text}` }
          } else {
            steps.push({ type: 'thinking', text })
          }
          return { ...message, thinking: `${message.thinking ?? ''}${text}`, steps }
        }
        entry.state = {
          ...entry.state,
          agentState: 'thinking',
          messages: existingId
            ? entry.state.messages.map(message => message.id === streamingId ? applyThinking(message) : message)
            : [...entry.state.messages, {
              id: streamingId,
              role: 'assistant',
              content: '',
              ts: Date.now(),
              streaming: true,
              runId: entry.currentRunId ?? event.run_id ?? undefined,
              thinking: text,
              steps: [{ type: 'thinking', text }],
            }],
        }
        break
      }

      case 'usage':
        entry.runInputTokens += (event as AgentEvent & { input_tokens?: number }).input_tokens ?? 0
        entry.runOutputTokens += (event as AgentEvent & { output_tokens?: number }).output_tokens ?? 0
        break

      case 'tool_start': {
        const toolCall: ToolCall = {
          id: event.tool_use_id ?? uuidv4(),
          run_id: event.run_id ?? entry.currentRunId ?? '',
          name: event.name ?? '',
          input: event.input ?? {},
          status: 'running',
          ts_start: event.ts,
        }
        entry.toolRoundCount += 1
        if (CREATE_TOOLS.has(toolCall.name) || MODIFY_TOOLS.has(toolCall.name) || toolCall.name === 'bash') {
          entry.fileOpCount += 1
        }
        const existingId = entry.streamingId
        const streamingId = existingId ?? uuidv4()
        entry.streamingId = streamingId
        entry.state = {
          ...entry.state,
          agentState: 'executing_tool',
          toolCalls: [...entry.state.toolCalls, toolCall],
          messages: existingId
            ? entry.state.messages.map(message => message.id === streamingId ? {
              ...message,
              toolCalls: [...(message.toolCalls ?? []), toolCall],
              steps: [...(message.steps ?? []), { type: 'tool_call', toolCall }],
            } : message)
            : [...entry.state.messages, {
              id: streamingId,
              role: 'assistant',
              content: '',
              ts: Date.now(),
              streaming: true,
              runId: entry.currentRunId ?? event.run_id ?? undefined,
              toolCalls: [toolCall],
              steps: [{ type: 'tool_call', toolCall }],
            }],
        }

        if (toolCall.name === 'TodoWrite') {
          const items = (toolCall.input.items ?? toolCall.input.todos) as TodoItem[] | undefined
          if (items) entry.state = { ...entry.state, todos: items }
        }
        if (CREATE_TOOLS.has(toolCall.name)) {
          const path = (toolCall.input.path ?? toolCall.input.filename) as string | undefined
          if (path) entry.state = updateFileChange(entry.state, path, 'create')
        } else if (MODIFY_TOOLS.has(toolCall.name)) {
          const path = (toolCall.input.path ?? toolCall.input.filename) as string | undefined
          if (path) entry.state = updateFileChange(entry.state, path, 'modify')
        } else if (toolCall.name === 'bash') {
          const command = toolCall.input.command as string | undefined
          if (command) {
            const path = toolCall.input.path as string | undefined
            if (path && isBashWrite(command)) entry.state = updateFileChange(entry.state, path, 'modify')
            entry.pendingBash.set(toolCall.id, command)
          }
        }
        break
      }

      case 'tool_result':
        entry.state = {
          ...entry.state,
          agentState: 'thinking',
          pendingApprovals: entry.state.pendingApprovals.filter(approval => approval.id !== event.tool_use_id),
          toolCalls: entry.state.toolCalls.map(toolCall => toolCall.id === event.tool_use_id ? {
            ...toolCall,
            output: event.output,
            ts_end: event.ts,
            status: 'done' as const,
          } : toolCall),
          messages: entry.state.messages.map(message => {
            if (!message.toolCalls?.some(toolCall => toolCall.id === event.tool_use_id)) return message
            return {
              ...message,
              toolCalls: message.toolCalls.map(toolCall => toolCall.id === event.tool_use_id ? {
                ...toolCall,
                output: event.output,
                ts_end: event.ts,
                status: 'done' as const,
              } : toolCall),
              steps: (message.steps ?? []).map(step => {
                const stepToolCall = step.toolCall
                if (step.type !== 'tool_call' || !stepToolCall || stepToolCall.id !== event.tool_use_id) {
                  return step
                }
                return {
                  ...step,
                  toolCall: {
                    ...stepToolCall,
                    output: event.output,
                    ts_end: event.ts,
                    status: 'done' as const,
                  },
                }
              }),
            }
          }),
        }
        entry.pendingBash.delete(event.tool_use_id ?? '')
        break

      case 'tool_approval_request':
        entry.state = {
          ...entry.state,
          pendingApprovals: [...entry.state.pendingApprovals, {
            id: event.tool_use_id ?? uuidv4(),
            run_id: event.run_id ?? entry.currentRunId ?? '',
            name: event.name ?? '',
            input: event.input ?? {},
            ts: event.ts,
          }],
        }
        break

      case 'options_presented': {
        const options = (event as AgentEvent & { options?: PlanOption[] }).options
        if (options?.length) entry.state = { ...entry.state, planOptions: options }
        const finishedStreamingId = entry.streamingId
        entry.streamingId = null
        entry.state = finalizeStreamingMessage(entry.state, finishedStreamingId)
        break
      }

      case 'plan_proposed': {
        entry.state = {
          ...entry.state,
          planProposal: {
            run_id: event.run_id ?? entry.currentRunId ?? '',
            content: event.content ?? '',
            ts: event.ts,
            status: 'pending',
          },
        }
        const finishedStreamingId = entry.streamingId
        entry.streamingId = null
        entry.state = finalizeStreamingMessage(entry.state, finishedStreamingId)
        break
      }

      case 'system':
        if (event.message === 'auto-compact' || event.message === 'manual-compact') {
          entry.state = {
            ...entry.state,
            messages: [...entry.state.messages, {
              id: uuidv4(),
              role: 'system',
              content: event.message === 'auto-compact' ? '对话已自动压缩（节省 Token）' : '对话已手动压缩',
              ts: Date.now(),
            }],
          }
        }
        break

      case 'interrupted': {
        const finishedStreamingId = entry.streamingId
        entry.streamingId = null
        entry.currentRunId = null
        entry.state = {
          ...finalizeStreamingMessage(entry.state, finishedStreamingId, true),
          agentState: 'idle',
          runId: null,
          pendingApprovals: [],
        }
        break
      }

      case 'error': {
        const finishedStreamingId = entry.streamingId
        entry.streamingId = null
        entry.currentRunId = null
        entry.state = {
          ...finalizeStreamingMessage(entry.state, finishedStreamingId),
          agentState: 'idle',
          runId: null,
          pendingApprovals: [],
          messages: [...finalizeStreamingMessage(entry.state, finishedStreamingId).messages, {
            id: uuidv4(),
            role: 'assistant',
            content: `⚠️ 错误：${event.message || '未知错误'}`,
            ts: Date.now(),
            runId: event.run_id,
          }],
        }
        break
      }

      default:
        break
    }
    return true
  }
}

export const chatStore = new ChatStore()
