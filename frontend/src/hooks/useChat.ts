import { useState, useCallback, useRef } from 'react'
import { v4 as uuidv4 } from 'uuid'
import type { ChatMessage, AgentEvent, ContentBlock, TodoItem } from '../lib/types'

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

const CREATE_TOOLS = new Set(['write_file', 'create_file', 'save_file'])
const MODIFY_TOOLS = new Set(['edit_file'])
const WRITE_CMD_PATTERNS = [/\s>\s/, /\s>>\s/, /\| tee\s/, /mkdir\s/, /touch\s/, /mv\s/, /cp\s/, /rm\s/]

function isBashWrite(cmd: string): boolean {
  return WRITE_CMD_PATTERNS.some(p => p.test(cmd))
}

export function useChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [toolCalls, setToolCalls] = useState<ToolCall[]>([])
  const [todos, setTodos] = useState<TodoItem[]>([])
  const [agentState, setAgentState] = useState<'idle' | 'thinking' | 'executing_tool'>('idle')
  const [runId, setRunId] = useState<string | null>(null)
  const [pendingApprovals, setPendingApprovals] = useState<ApprovalRequest[]>([])
  const [changedFiles, setChangedFiles] = useState<FileChange[]>([])
  const [planProposal, setPlanProposal] = useState<PlanProposal | null>(null)
  const [planOptions, setPlanOptions] = useState<Array<{ id: string; label: string; description?: string | null }> | null>(null)
  const streamingIdRef = useRef<string | null>(null)
  const currentRunIdRef = useRef<string | null>(null)  // Tag messages with their run
  const pendingBashRef = useRef<Map<string, string>>(new Map())
  const toolRoundCountRef = useRef(0)
  const fileOpCountRef = useRef(0)
  const runInputTokensRef = useRef(0)
  const runOutputTokensRef = useRef(0)

  const addUserMessage = useCallback((content: string | ContentBlock[]) => {
    setMessages(prev => [...prev, {
      id: uuidv4(), role: 'user', content, ts: Date.now(),
      runId: currentRunIdRef.current ?? undefined,
    }])
  }, [])

  const addFileChange = useCallback((path: string, op: FileChange['op']) => {
    setChangedFiles(prev => {
      const existing = prev.find(f => f.path === path)
      if (existing) {
        const opOrder = { create: 0, modify: 1, delete: 2 } as const
        const newOp = opOrder[op] > opOrder[existing.op] ? op : existing.op
        return prev.map(f => f.path === path ? { ...f, op: newOp } : f)
      }
      return [...prev, { path, op }]
    })
  }, [])

  // Ensure a streaming assistant message exists, creating one if needed
  const ensureStreamingMsg = useCallback((prev: ChatMessage[], overrides: Partial<ChatMessage> = {}) => {
    if (streamingIdRef.current) return prev
    const id = uuidv4()
    streamingIdRef.current = id
    return [...prev, { id, role: 'assistant' as const, content: '', ts: Date.now(), streaming: true, runId: currentRunIdRef.current ?? undefined, ...overrides }]
  }, [])

  const handleEvent = useCallback((ev: AgentEvent) => {
    switch (ev.type) {
      case 'run_start':
        setRunId(ev.run_id ?? null)
        currentRunIdRef.current = ev.run_id ?? null
        setAgentState('thinking')
        setPlanOptions(null)
        streamingIdRef.current = null
        toolRoundCountRef.current = 0
        fileOpCountRef.current = 0
        runInputTokensRef.current = 0
        runOutputTokensRef.current = 0
        break

      case 'run_end':
        setRunId(null)
        setAgentState('idle')
        setPendingApprovals([])
        if (streamingIdRef.current) {
          setMessages(prev => prev.map(m =>
            m.id === streamingIdRef.current ? { ...m, streaming: false } : m
          ))
          streamingIdRef.current = null
        }
        setToolCalls(prev => prev.map(tc =>
          tc.run_id === (ev.run_id ?? '') && tc.status === 'running'
            ? { ...tc, status: 'done', ts_end: ev.ts }
            : tc
        ))
        {
          const inputTokens = runInputTokensRef.current
          const outputTokens = runOutputTokensRef.current
          const tr = toolRoundCountRef.current
          const fo = fileOpCountRef.current
          const summary = { input_tokens: inputTokens, output_tokens: outputTokens, tool_rounds: tr, file_operations: fo }
          setMessages(prev => {
            for (let i = prev.length - 1; i >= 0; i--) {
              if (prev[i].role === 'assistant') {
                const updated = { ...prev[i], summary }
                return [...prev.slice(0, i), updated, ...prev.slice(i + 1)]
              }
            }
            return prev
          })
        }
        break

      case 'agent_state':
        if (ev.state) setAgentState(ev.state as 'idle' | 'thinking' | 'executing_tool')
        break

      case 'chunk': {
        const text = ev.text ?? ''
        setMessages(prev => {
          const msgs = ensureStreamingMsg(prev)
          return msgs.map(m =>
            m.id === streamingIdRef.current
              ? { ...m, content: (typeof m.content === 'string' ? m.content : '') + text }
              : m
          )
        })
        break
      }

      case 'thinking': {
        const text = ev.text ?? ''
        setAgentState('thinking')
        setMessages(prev => {
          const msgs = ensureStreamingMsg(prev, { thinking: text, steps: [{ type: 'thinking', text }] })
          return msgs.map(m => {
            if (m.id !== streamingIdRef.current) return m
            const steps = [...(m.steps || [])]
            const lastStep = steps[steps.length - 1]
            if (lastStep && lastStep.type === 'thinking') {
              steps[steps.length - 1] = { ...lastStep, text: (lastStep.text || '') + text }
            } else {
              steps.push({ type: 'thinking', text })
            }
            return { ...m, thinking: (m.thinking || '') + text, steps }
          })
        })
        break
      }

      case 'usage': {
        runInputTokensRef.current += (ev as AgentEvent & { input_tokens: number }).input_tokens ?? 0
        runOutputTokensRef.current += (ev as AgentEvent & { output_tokens: number }).output_tokens ?? 0
        break
      }

      case 'tool_start': {
        const tc: ToolCall = {
          id: ev.tool_use_id ?? uuidv4(),
          run_id: ev.run_id ?? '',
          name: ev.name ?? '',
          input: ev.input ?? {},
          status: 'running',
          ts_start: ev.ts,
        }
        setToolCalls(prev => [...prev, tc])
        setAgentState('executing_tool')
        toolRoundCountRef.current++
        if (CREATE_TOOLS.has(tc.name) || MODIFY_TOOLS.has(tc.name) || tc.name === 'bash') {
          fileOpCountRef.current++
        }

        // Capture TodoWrite items for real-time task panel
        if (tc.name === 'TodoWrite') {
          const items = (tc.input.items ?? tc.input.todos) as Array<{ content: string; status: string; activeForm: string }> | undefined
          if (items) setTodos(items as TodoItem[])
        }

        // Push tool_call step into the current streaming message
        setMessages(prev => {
          const msgs = ensureStreamingMsg(prev, {
            toolCalls: [tc],
            steps: [{ type: 'tool_call', toolCall: tc }],
          })
          return msgs.map(m => {
            if (m.id !== streamingIdRef.current) return m
            return {
              ...m,
              toolCalls: [...(m.toolCalls || []), tc],
              steps: [...(m.steps || []), { type: 'tool_call', toolCall: tc }],
            }
          })
        })

        if (CREATE_TOOLS.has(tc.name)) {
          const path = (tc.input.path ?? tc.input.filename) as string | undefined
          if (path) addFileChange(path, 'create')
        } else if (MODIFY_TOOLS.has(tc.name)) {
          const path = (tc.input.path ?? tc.input.filename) as string | undefined
          if (path) addFileChange(path, 'modify')
        } else if (tc.name === 'bash') {
          const cmd = tc.input.command as string | undefined
          if (cmd) {
            const filePath = tc.input.path as string | undefined
            if (filePath && isBashWrite(cmd)) {
              addFileChange(filePath, 'modify')
            }
            pendingBashRef.current.set(tc.id, cmd)
          }
        }
        break
      }

      case 'tool_result':
        setToolCalls(prev => prev.map(tc =>
          tc.id === ev.tool_use_id
            ? { ...tc, output: ev.output, ts_end: ev.ts, status: 'done' }
            : tc
        ))
        setMessages(prev => {
          for (let i = prev.length - 1; i >= 0; i--) {
            const msg = prev[i]
            if (msg.role === 'assistant' && msg.toolCalls?.some((tc: ToolCall) => tc.id === ev.tool_use_id)) {
              return prev.map((m, idx) => {
                if (idx !== i) return m
                const updatedTC = m.toolCalls!.map(tc =>
                  tc.id === ev.tool_use_id
                    ? { ...tc, output: ev.output, ts_end: ev.ts, status: 'done' as const }
                    : tc
                )
                const updatedSteps = (m.steps || []).map(s =>
                  s.type === 'tool_call' && s.toolCall?.id === ev.tool_use_id
                    ? { ...s, toolCall: { ...s.toolCall!, output: ev.output, ts_end: ev.ts, status: 'done' as const } }
                    : s
                )
                return { ...m, toolCalls: updatedTC, steps: updatedSteps }
              })
            }
          }
          return prev
        })
        setAgentState('thinking')
        pendingBashRef.current.delete(ev.tool_use_id ?? '')
        break

      case 'tool_approval_request': {
        const req: ApprovalRequest = {
          id: ev.tool_use_id!,
          run_id: ev.run_id ?? '',
          name: ev.name ?? '',
          input: ev.input ?? {},
          ts: ev.ts,
        }
        setPendingApprovals(prev => [...prev, req])
        break
      }

      case 'options_presented': {
        const opts = (ev as AgentEvent & { options: Array<{ id: string; label: string; description?: string | null }> }).options
        if (opts && opts.length > 0) {
          setPlanOptions(opts)
        }
        if (streamingIdRef.current) {
          setMessages(prev => prev.map(m =>
            m.id === streamingIdRef.current ? { ...m, streaming: false } : m
          ))
          streamingIdRef.current = null
        }
        break
      }

      case 'plan_proposed': {
        setPlanProposal({
          run_id: ev.run_id ?? '',
          content: (ev as AgentEvent & { content: string }).content ?? '',
          ts: ev.ts,
          status: 'pending',
        })
        // Mark the streaming message as finalized
        if (streamingIdRef.current) {
          setMessages(prev => prev.map(m =>
            m.id === streamingIdRef.current ? { ...m, streaming: false } : m
          ))
          streamingIdRef.current = null
        }
        break
      }

      case 'system':
        if (ev.message === 'auto-compact' || ev.message === 'manual-compact') {
          setMessages(prev => [...prev, {
            id: uuidv4(), role: 'system',
            content: ev.message === 'auto-compact' ? '对话已自动压缩（节省 Token）' : '对话已手动压缩',
            ts: Date.now(),
          }])
        }
        break

      case 'interrupted':
        setAgentState('idle')
        setRunId(null)
        setPendingApprovals([])
        if (streamingIdRef.current) {
          setMessages(prev => prev.map(m => {
            if (m.id !== streamingIdRef.current) return m
            const content = typeof m.content === 'string' ? m.content : ''
            return { ...m, content: content + '\n\n_[已中断]_', streaming: false }
          }))
          streamingIdRef.current = null
        }
        break

      case 'error':
        setAgentState('idle')
        setRunId(null)
        setPendingApprovals([])
        if (streamingIdRef.current) {
          setMessages(prev => prev.map(m =>
            m.id === streamingIdRef.current ? { ...m, streaming: false } : m
          ))
          streamingIdRef.current = null
        }
        setMessages(prev => [...prev, {
          id: uuidv4(), role: 'assistant',
          content: `⚠️ 错误：${ev.message || '未知错误'}`, ts: Date.now(),
        }])
        break

      default:
        break
    }
  }, [addFileChange, ensureStreamingMsg])

  const clearAll = useCallback(() => {
    setMessages([])
    setToolCalls([])
    setTodos([])
    setAgentState('idle')
    setRunId(null)
    setPendingApprovals([])
    setChangedFiles([])
    setPlanProposal(null)
    streamingIdRef.current = null
    pendingBashRef.current.clear()
  }, [])

  const restoreMessages = useCallback((msgs: ChatMessage[]) => {
    setMessages(msgs)
    setToolCalls([])
    setAgentState('idle')
    setRunId(null)
    setPendingApprovals([])
    setChangedFiles([])
    setPlanProposal(null)
    streamingIdRef.current = null
    pendingBashRef.current.clear()
  }, [])

  const dismissApproval = useCallback((id: string) => {
    setPendingApprovals(prev => prev.filter(a => a.id !== id))
  }, [])

  const dismissPlan = useCallback(() => {
    setPlanProposal(null)
  }, [])

  return {
    messages, todos, toolCalls, agentState, runId,
    pendingApprovals, changedFiles, planProposal, setPlanProposal,
    planOptions, setPlanOptions,
    addUserMessage, handleEvent, clearAll, restoreMessages, dismissApproval, dismissPlan,
  }
}
