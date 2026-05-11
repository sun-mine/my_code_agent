import { useEffect, useRef, useState, useCallback, useMemo } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Trash2, Circle, AlertCircle, Plus, Minus, Pencil, Bot, FolderOpen, ChevronRight, ChevronUp } from 'lucide-react'

import { useAgentSocket, type SocketStatus } from './hooks/useAgentSocket'
import { useChat, type FileChange, type ToolCall } from './hooks/useChat'
import { api } from './lib/api'
import { applyTheme } from './lib/theme'
import type { AgentMode, FileContext, ModelInfo, ContentBlock, ImageAttachment, ChatMessage, AssistantStep } from './lib/types'

import { MessageBubble } from './components/chat/MessageBubble'
import { ChatInput } from './components/chat/ChatInput'
import { ApprovalBanner } from './components/chat/ApprovalBanner'
import { PlanReview } from './components/chat/PlanReview'
import { ChoicePanel } from './components/chat/ChoicePanel'
import { FilePreview } from './components/panels/FilePreview'
import { ProgressPanel } from './components/panels/ProgressPanel'
import { Sidebar } from './components/panels/Sidebar'
import { StatusBar } from './components/ui/StatusBar'
import { ThemeToggle } from './components/ui/ThemeToggle'
import { ContextPanel } from './components/ui/ContextPanel'
import { ICON_SM, ICON_MD } from './lib/icons'

// ── FolderBrowser ────────────────────────────────
function FolderBrowser({ onSelect, onClose }: { onSelect: (path: string) => void; onClose: () => void }) {
  const [browsePath, setBrowsePath] = useState<string>('')
  const [items, setItems] = useState<Array<{ name: string; path: string }>>([])
  const [parent, setParent] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async (path?: string) => {
    setLoading(true)
    try {
      const data = await api.fsLs(path)
      setBrowsePath(data.path)
      setParent(data.parent)
      setItems(data.items)
    } catch { /* ignore */ }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <>
      <div style={{ position: 'fixed', inset: 0, zIndex: 200 }} onClick={onClose} />
      <div style={{
        position: 'absolute', top: 44, left: 0, zIndex: 201, width: 340,
        background: 'var(--bg-overlay)', border: '1px solid var(--border-hi)',
        borderRadius: 8, boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
        display: 'flex', flexDirection: 'column', maxHeight: 360,
      }}>
        <div style={{ padding: '8px 10px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 6 }}>
          {parent && (
            <button onClick={() => load(parent)} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-3)', display: 'flex', padding: 2 }}>
              <ChevronUp size={14} />
            </button>
          )}
          <span style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--text-2)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {browsePath}
          </span>
          <button
            onClick={() => onSelect(browsePath)}
            style={{ padding: '2px 8px', borderRadius: 4, fontSize: 11, background: 'var(--accent)', border: 'none', color: '#fff', cursor: 'pointer', flexShrink: 0 }}
          >
            选择
          </button>
        </div>
        <div style={{ overflowY: 'auto', flex: 1 }}>
          {loading ? (
            <div style={{ padding: 16, textAlign: 'center', color: 'var(--text-3)', fontSize: 12 }}>加载中…</div>
          ) : items.length === 0 ? (
            <div style={{ padding: 16, textAlign: 'center', color: 'var(--text-3)', fontSize: 12 }}>（空目录）</div>
          ) : items.map(item => (
            <button
              key={item.path}
              onClick={() => load(item.path)}
              style={{
                display: 'flex', alignItems: 'center', gap: 6,
                width: '100%', padding: '5px 10px',
                background: 'none', border: 'none', cursor: 'pointer',
                color: 'var(--text-1)', fontSize: 12, textAlign: 'left',
              }}
              onMouseOver={e => { e.currentTarget.style.background = 'var(--bg-raised)' }}
              onMouseOut={e => { e.currentTarget.style.background = 'none' }}
            >
              <FolderOpen size={12} color="var(--yellow)" />
              <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontFamily: 'var(--font-mono)' }}>
                {item.name}
              </span>
              <ChevronRight size={10} color="var(--text-3)" />
            </button>
          ))}
        </div>
      </div>
    </>
  )
}

// ── WorkdirSelector ──────────────────────────────
function WorkdirSelector({ current, onChanged, error }: {
  current?: string
  onChanged: () => void
  error?: boolean
}) {
  const [editing, setEditing] = useState(false)
  const [browsing, setBrowsing] = useState(false)
  const [draft, setDraft] = useState(current ?? '')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')

  const handleSave = async (path?: string) => {
    const target = (path ?? draft).trim()
    if (!target) return
    setSaving(true)
    setErr('')
    try {
      await api.setWorkdir(target)
      setEditing(false)
      setBrowsing(false)
      onChanged()
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  // 显示最后2级路径
  const displayPath = current
    ? (() => {
        const parts = current.replace(/\/$/, '').split('/')
        if (parts.length <= 2) return current
        return '…/' + parts.slice(-2).join('/')
      })()
    : '选择目录'

  return (
    <div style={{ position: 'relative' }}>
      <button
        onClick={() => { setDraft(current ?? ''); setEditing(o => !o); setBrowsing(false) }}
        title={`工作目录: ${current || '未设置'}`}
        style={{
          display: 'flex', alignItems: 'center', gap: 5,
          padding: '3px 8px', borderRadius: 5,
          background: 'var(--bg-raised)', border: '1px solid var(--border)',
          color: 'var(--text-2)', fontSize: 11,
          fontFamily: 'var(--font-mono)',
          cursor: 'pointer', maxWidth: 260,
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}
      >
        {error
          ? <AlertCircle size={ICON_SM} color="var(--red)" />
          : <Circle size={8} fill="var(--green)" color="var(--green)" style={{ flexShrink: 0 }} />
        }
        <FolderOpen size={ICON_SM} style={{ flexShrink: 0 }} color="var(--text-3)" />
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {displayPath}
        </span>
        <span style={{ color: 'var(--text-3)', fontSize: 9, flexShrink: 0 }}>▾</span>
      </button>

      {editing && (
        <>
          <div style={{ position: 'fixed', inset: 0, zIndex: 49 }} onClick={() => { setEditing(false); setBrowsing(false) }} />
          <div style={{
            position: 'absolute', top: 'calc(100% + 6px)', left: 0, zIndex: 50, width: 360,
            background: 'var(--bg-surface)', border: '1px solid var(--border-hi)',
            borderRadius: 8, padding: '12px', boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
          }}>
            <p style={{ fontSize: 11, color: 'var(--text-3)', marginBottom: 6, marginTop: 0 }}>
              输入工作目录路径，或点击浏览选择
            </p>
            <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
              <input
                autoFocus
                value={draft}
                onChange={e => setDraft(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter') handleSave()
                  if (e.key === 'Escape') { setEditing(false); setBrowsing(false) }
                }}
                placeholder="/home/user/my-project"
                style={{
                  flex: 1, padding: '6px 10px', borderRadius: 6,
                  background: 'var(--bg-base)', border: '1px solid var(--border-hi)',
                  color: 'var(--text-1)', fontSize: 12, fontFamily: 'var(--font-mono)',
                  outline: 'none',
                }}
              />
              <button
                onClick={() => setBrowsing(o => !o)}
                title="浏览文件夹"
                style={{
                  padding: '6px 10px', borderRadius: 6, fontSize: 11,
                  background: browsing ? 'var(--bg-overlay)' : 'var(--bg-raised)',
                  border: '1px solid var(--border-hi)',
                  color: 'var(--text-2)', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 4,
                }}
              >
                <FolderOpen size={13} />
                浏览
              </button>
            </div>
            <div style={{ display: 'flex', gap: 6 }}>
              <button
                onClick={() => handleSave()}
                disabled={saving}
                style={{
                  flex: 1, padding: '6px 14px', borderRadius: 6, fontSize: 12,
                  background: 'var(--accent)', border: 'none',
                  color: '#fff', cursor: saving ? 'not-allowed' : 'pointer',
                }}
              >
                {saving ? '切换中…' : '切换'}
              </button>
              <button
                onClick={() => { setEditing(false); setBrowsing(false) }}
                style={{
                  padding: '6px 10px', borderRadius: 6, fontSize: 12,
                  background: 'none', border: '1px solid var(--border)',
                  color: 'var(--text-3)', cursor: 'pointer',
                }}
              >
                取消
              </button>
            </div>
            {err && <p style={{ color: 'var(--red)', fontSize: 11, marginTop: 6, marginBottom: 0 }}>{err}</p>}
          </div>
          {browsing && (
            <FolderBrowser
              onSelect={(path) => { setDraft(path); handleSave(path) }}
              onClose={() => setBrowsing(false)}
            />
          )}
        </>
      )}
    </div>
  )
}

// ── File change operation icons ──────────────────
function ChangeOpIcon({ op }: { op: FileChange['op'] }) {
  switch (op) {
    case 'create':
      return <Plus size={10} color="var(--green)" />
    case 'modify':
      return <Pencil size={10} color="var(--yellow)" />
    case 'delete':
      return <Minus size={10} color="var(--red)" />
  }
}

function changeOpLabel(op: FileChange['op']): string {
  switch (op) {
    case 'create': return '+'
    case 'modify': return '~'
    case 'delete': return '-'
  }
}

// ── Main App ─────────────────────────────────────
export default function App() {
  const qc = useQueryClient()
  const [socketStatus, setSocketStatus] = useState<SocketStatus>('connecting')
  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [changedFilesOpen, setChangedFilesOpen] = useState(false)
  const [workdirError, setWorkdirError] = useState(false)
  const [preFillInput, setPreFillInput] = useState('')
  const [agentMode, setAgentMode] = useState<AgentMode>('auto')
  const [currentModel, setCurrentModel] = useState<string>('')
  const [availableModels, setAvailableModels] = useState<ModelInfo[]>([])
  const [fileContexts, setFileContexts] = useState<FileContext[]>([])
  const bottomRef = useRef<HTMLDivElement>(null)

  // Apply theme on mount
  useEffect(() => {
    applyTheme((() => { try { const v = localStorage.getItem('code-agent-theme'); if (v === 'dark' || v === 'beige') return v } catch {} return 'beige' })())
  }, [])

  // Restore conversation history on page load
  useEffect(() => {
    api.history().then(data => {
      if (data.history && data.history.length > 0) {
        const restored: ChatMessage[] = []
        type PendingStep = { type: 'thinking'; text: string } | { type: 'tool_call'; toolCall: ToolCall }

        const buildToolCall = (name: string, input: Record<string, unknown>, id: string, ts_start: number): ToolCall => ({
          id, run_id: '', name, input, status: 'running' as const, ts_start,
        })

        let currentUser: ChatMessage | null = null
        let pendingSteps: PendingStep[] = []
        let assistantText = ''
        let assistantTs = 0
        // Map of tool_use_id → index into pendingSteps, for pairing with results
        let toolStepIndex: Map<string, number> = new Map()

        const flushTurn = () => {
          if (currentUser) {
            restored.push(currentUser)
            currentUser = null
          }
          if (pendingSteps.length > 0 || assistantText.trim()) {
            // Build completed toolCalls list for backwards compat
            const toolCalls: ToolCall[] = pendingSteps
              .filter((s): s is { type: 'tool_call'; toolCall: ToolCall } => s.type === 'tool_call')
              .map(s => s.toolCall)
            restored.push({
              id: crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${restored.length}`,
              role: 'assistant' as const,
              content: assistantText.trim(),
              ts: assistantTs || Date.now(),
              steps: pendingSteps.length > 0 ? pendingSteps as AssistantStep[] : undefined,
              toolCalls: toolCalls.length > 0 ? toolCalls : undefined,
            })
            pendingSteps = []
            toolStepIndex = new Map()
            assistantText = ''
            assistantTs = 0
          }
        }

        for (let i = 0; i < data.history.length; i++) {
          const m = data.history[i] as { role: string; content: unknown }
          const ts = Date.now() - (data.history.length - i) * 1000

          if (typeof m.content === 'string') {
            // Real user message — flush previous turn
            flushTurn()
            restored.push({
              id: crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${restored.length}`,
              role: 'user',
              content: m.content,
              ts,
            })
          } else if (Array.isArray(m.content)) {
            const blocks = m.content as Array<Record<string, unknown>>
            const hasOnlyToolResults = blocks.length > 0 && blocks.every(b => b.type === 'tool_result')
            if (hasOnlyToolResults && m.role === 'user') {
              // Pair tool_results with pending tool_use steps
              for (const blk of blocks) {
                if (blk.type === 'tool_result') {
                  const tid = blk.tool_use_id as string | undefined
                  if (tid && toolStepIndex.has(tid)) {
                    const idx = toolStepIndex.get(tid)!
                    const step = pendingSteps[idx] as { type: 'tool_call'; toolCall: ToolCall }
                    step.toolCall.status = 'done'
                    step.toolCall.output = (blk.content as string) || ''
                    step.toolCall.ts_end = ts
                  }
                }
              }
              continue
            }
            if (m.role === 'assistant') {
              if (!assistantTs) assistantTs = ts
              for (const blk of blocks) {
                if (blk.type === 'thinking' && typeof blk.text === 'string') {
                  pendingSteps.push({ type: 'thinking', text: blk.text })
                } else if (blk.type === 'text' && typeof blk.text === 'string') {
                  assistantText += blk.text
                } else if (blk.type === 'tool_use') {
                  const name = (blk.name as string) || ''
                  const input = (blk.input as Record<string, unknown>) || {}
                  const tid = (blk.tool_use_id as string) || `${name}_${pendingSteps.length}`
                  const tc = buildToolCall(name, input, tid, ts)
                  const idx = pendingSteps.length
                  toolStepIndex.set(tid, idx)
                  pendingSteps.push({ type: 'tool_call', toolCall: tc })
                }
              }
            } else if (m.role === 'user') {
              // User message with mixed content (text + images, not tool_results)
              flushTurn()
              const textParts: string[] = []
              for (const blk of blocks) {
                if (blk.type === 'text' && typeof blk.text === 'string') {
                  textParts.push(blk.text)
                }
              }
              const userContent = textParts.join('')
              if (userContent) {
                restored.push({
                  id: crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${restored.length}`,
                  role: 'user',
                  content: userContent,
                  ts,
                })
              }
            }
          }
        }
        flushTurn()

        if (restored.length > 0) restoreMessages(restored)
      }
    }).catch(() => {})
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  const {
    messages, todos, toolCalls, agentState,
    pendingApprovals, changedFiles, planProposal, setPlanProposal,
    planOptions, setPlanOptions,
    addUserMessage, handleEvent, clearAll, restoreMessages, dismissApproval, dismissPlan,
  } = useChat()

  // Merge consecutive messages with the same runId to avoid showing internal tool rounds
  const displayMessages = useMemo(() => {
    const merged: ChatMessage[] = []
    for (const msg of messages) {
      const last = merged[merged.length - 1]
      if (last && last.role === 'assistant' && msg.role === 'assistant' &&
          last.runId && msg.runId && last.runId === msg.runId) {
        merged[merged.length - 1] = {
          ...last,
          content: (typeof last.content === 'string' && typeof msg.content === 'string')
            ? last.content + msg.content
            : last.content,
          steps: [...(last.steps || []), ...(msg.steps || [])],
          toolCalls: [...(last.toolCalls || []), ...(msg.toolCalls || [])],
          summary: msg.summary || last.summary,
        }
      } else {
        merged.push(msg)
      }
    }
    return merged
  }, [messages])

  const { sendMessage, sendInterrupt } = useAgentSocket({
    onEvent: handleEvent,
    onStatusChange: setSocketStatus,
  })

  const { data: statusData, refetch: refetchStatus } = useQuery({
    queryKey: ['agentStatus'],
    queryFn: () => api.status(),
    refetchInterval: 5000,
    retry: false,
  })

  // 检测工作目录是否有效
  useEffect(() => {
    if (statusData?.workdir) {
      setWorkdirError(false)
    }
  }, [statusData?.workdir])

  // 初始化当前模型和可用模型列表
  useEffect(() => {
    if (statusData?.model && !currentModel) {
      setCurrentModel(statusData.model)
    }
  }, [statusData?.model])

  // 获取可用模型列表
  useEffect(() => {
    api.listModels().then(data => {
      setAvailableModels(data.models)
      if (!currentModel && data.current) {
        setCurrentModel(data.current)
      }
    }).catch(() => {})
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  const busy = agentState !== 'idle'

  const handleSend = (text: string, attachments?: { images: ImageAttachment[]; fileContexts: FileContext[] }) => {
    const images = attachments?.images ?? []
    const fctxs = attachments?.fileContexts ?? fileContexts.filter(fc => fc.included)
    // Build message content blocks
    const blocks: ContentBlock[] = []
    // Prepend file contexts to the message
    const ctxText = fctxs.length > 0
      ? fctxs.map(fc => `[文件: ${fc.path}]\n\`\`\`\n${fc.content.slice(0, 4000)}\n\`\`\``).join('\n\n')
      : ''
    const fullText = ctxText ? `${ctxText}\n\n${text}` : text
    if (fullText) blocks.push({ type: 'text', text: fullText })
    for (const img of images) {
      blocks.push({ type: 'image', source: { type: 'base64', media_type: img.mediaType, data: img.base64Data } })
    }
    // If only text and no images, send as plain string for simplicity
    const content: string | ContentBlock[] = blocks.length === 1 && blocks[0].type === 'text'
      ? blocks[0].text
      : blocks
    addUserMessage(content)
    sendMessage(content, agentMode)
    setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: 'smooth' }), 50)
  }

  const handleCompress = () => {
    addUserMessage('__compress__')
    sendMessage('__compress__', agentMode)
  }

  const handleAddToChat = useCallback((path: string, content: string) => {
    const snippet = content.length > 8000
      ? content.slice(0, 8000) + '\n... (truncated)'
      : content
    setFileContexts(prev => {
      const existing = prev.find(fc => fc.path === path)
      if (existing) return prev.map(fc => fc.path === path ? { ...fc, content: snippet, included: true } : fc)
      return [...prev, { path, content: snippet, included: true }]
    })
    setSelectedFile(null)
  }, [])

  const handleInterrupt = () => {
    sendInterrupt()
    api.interrupt().catch(() => {})
  }

  const handleClearHistory = async () => {
    await api.clearHistory()
    clearAll()
    qc.invalidateQueries({ queryKey: ['agentStatus'] })
  }

  const handleNewSession = async () => {
    // Save current session before starting new one
    const userMessages = messages.filter(m => m.role === 'user')
    const title = userMessages.length > 0
      ? (typeof userMessages[0].content === 'string' ? userMessages[0].content.slice(0, 50) : '对话')
      : `会话 ${new Date().toLocaleString('zh-CN')}`
    try {
      await api.createSession(
        `session_${Date.now()}`,
        title,
        messages.filter(m => m.role !== 'system').length
      )
    } catch {
      // ignore save errors
    }
    await handleClearHistory()
    setFileContexts([])
    setChangedFilesOpen(false)
  }

  const handleLoadSession = async (sid: string) => {
    try {
      const data = await api.loadSession(sid)
      // Restore frontend messages
      const restored: ChatMessage[] = (data.history || []).map((m: { role: string; content: unknown }) => ({
        id: crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`,
        role: m.role as ChatMessage['role'],
        content: typeof m.content === 'string' ? m.content : (m.content as ContentBlock[]),
        ts: Date.now(),
      }))
      restoreMessages(restored)
      setFileContexts([])
    } catch {
      // ignore
    }
  }

  const handleWorkdirChanged = () => {
    refetchStatus()
    qc.invalidateQueries({ queryKey: ['fileTree'] })
    clearAll()
  }

  const handleApprove = async (toolUseId: string, approved: boolean) => {
    dismissApproval(toolUseId)
    try {
      await api.toolApprove(toolUseId, approved)
    } catch (e) {
      console.warn('approve error:', e)
    }
  }

  const handlePlanApprove = async (_planRunId: string) => {
    const planContent = planProposal?.content || ''
    setPlanProposal(prev => prev ? { ...prev, status: 'approved' } : null)
    if (planContent) {
      api.savePlan(planContent, 'plan').catch(() => {})
    }
    dismissPlan()
    setAgentMode('auto')
    api.executePlan(planContent, todos).catch((e) => {
      console.error('executePlan error:', e)
    })
    setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: 'smooth' }), 50)
  }

  const handlePlanReject = async (_planRunId: string) => {
    setPlanProposal(prev => prev ? { ...prev, status: 'rejected' } : null)
    dismissPlan()
    const rejectMsg = '请重新规划，考虑不同的方案'
    addUserMessage(rejectMsg)
    sendMessage(rejectMsg, agentMode)
    setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: 'smooth' }), 50)
  }

  const handlePlanModify = async (_planRunId: string, feedback: string) => {
    dismissPlan()
    const modifyMsg = `请根据以下反馈修改计划：\n${feedback}`
    addUserMessage(modifyMsg)
    sendMessage(modifyMsg, agentMode)
    setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: 'smooth' }), 50)
  }

  // Drag-and-drop on main area to switch workdir
  const handleMainDrop = useCallback(async (e: React.DragEvent) => {
    e.preventDefault()
    const items = Array.from(e.dataTransfer.items)
    for (const item of items) {
      if (item.kind === 'file') {
        const file = (item as DataTransferItem & { getAsFile: () => File }).getAsFile?.()
        if (file && 'path' in file && (file as File & { path: string }).path) {
          const fullPath = (file as File & { path: string }).path
          const dirPath = fullPath.lastIndexOf('/') > 0 ? fullPath.substring(0, fullPath.lastIndexOf('/')) : fullPath
          try {
            await api.setWorkdir(dirPath)
            handleWorkdirChanged()
          } catch {
            setWorkdirError(true)
          }
          return
        }
      }
    }
  }, [])

  useEffect(() => {
    const el = bottomRef.current?.parentElement
    if (el) el.scrollTop = el.scrollHeight
  }, [messages, toolCalls, pendingApprovals])

  // 计算相对路径
  const relativePath = (fullPath: string) => {
    const wd = statusData?.workdir
    if (wd && fullPath.startsWith(wd)) {
      return fullPath.slice(wd.length).replace(/^\//, '')
    }
    return fullPath
  }

  return (
    <div
      style={{ display: 'flex', flexDirection: 'column', height: '100%', background: 'var(--bg-base)', position: 'relative' }}
      onDragOver={e => { e.preventDefault() }}
      onDrop={handleMainDrop}
    >
      {/* ─── Title bar ──────────────────────────────── */}
      <header style={{
        display: 'flex', alignItems: 'center', gap: 10,
        padding: '0 12px', height: 44, flexShrink: 0,
        borderBottom: '1px solid var(--border)',
        background: 'var(--bg-surface)',
        position: 'relative', zIndex: 40,
      }}>
        {/* Left: Logo + WorkdirSelector */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Bot size={18} strokeWidth={2} color="var(--accent)" />
          <span style={{ fontWeight: 700, fontSize: 13, color: 'var(--text-1)', marginRight: 4 }}>Code Agent</span>
          <WorkdirSelector
            current={statusData?.workdir}
            onChanged={handleWorkdirChanged}
            error={workdirError}
          />
        </div>

        <div style={{ flex: 1 }} />

        {/* Changed files indicator */}
        {changedFiles.length > 0 && (
          <button
            onClick={() => setChangedFilesOpen(o => !o)}
            title="已更改的文件"
            style={{
              display: 'flex', alignItems: 'center', gap: 5,
              padding: '3px 8px', borderRadius: 5,
              background: 'rgba(134,239,172,0.1)', border: '1px solid rgba(134,239,172,0.3)',
              color: 'var(--green)', fontSize: 11, fontFamily: 'var(--font-mono)',
              cursor: 'pointer',
            }}
          >
            <Pencil size={ICON_SM} />
            <span>已更改 {changedFiles.length} 个文件</span>
          </button>
        )}

        {/* Context panel (replaces old compression button) */}
        <ContextPanel
          tokenBreakdown={statusData?.token_breakdown}
          onCompress={handleCompress}
          busy={busy}
        />

        {/* model name */}
        <span style={{ fontSize: 11, color: 'var(--text-3)', fontFamily: 'var(--font-mono)' }}>
          {statusData?.model?.replace('claude-', '').replace('deepseek-v4-', 'V4 ').replace('deepseek-', '')}
        </span>

        <ThemeToggle />

        <button
          onClick={handleClearHistory}
          title="清空对话"
          style={iconBtn(false)}
        >
          <Trash2 size={ICON_MD - 1} strokeWidth={1.8} />
        </button>
      </header>

      {/* ─── Changed files dropdown ───────────────────── */}
      {changedFilesOpen && changedFiles.length > 0 && (
        <>
          <div style={{ position: 'fixed', inset: 0, zIndex: 39 }} onClick={() => setChangedFilesOpen(false)} />
          <div style={{
            position: 'absolute', top: 44, right: 12, zIndex: 50,
            background: 'var(--bg-overlay)', border: '1px solid var(--border-hi)',
            borderRadius: 8, padding: '8px 0', minWidth: 300,
            boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
            maxHeight: 400, overflow: 'auto',
          }}>
            <p style={{ fontSize: 10, color: 'var(--text-3)', padding: '2px 14px 6px', margin: 0, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              已更改 {changedFiles.length} 个文件
            </p>
            {changedFiles.map(fc => (
              <button
                key={fc.path}
                onClick={() => { setSelectedFile(fc.path); setChangedFilesOpen(false) }}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  width: '100%', padding: '5px 14px',
                  background: 'none', border: 'none', cursor: 'pointer',
                  textAlign: 'left',
                }}
                onMouseOver={e => { e.currentTarget.style.background = 'var(--bg-raised)' }}
                onMouseOut={e => { e.currentTarget.style.background = 'none' }}
              >
                <ChangeOpIcon op={fc.op} />
                <span style={{
                  color: fc.op === 'create' ? 'var(--green)' : fc.op === 'delete' ? 'var(--red)' : 'var(--yellow)',
                  fontSize: 10, fontFamily: 'var(--font-mono)', width: 14, flexShrink: 0,
                }}>
                  {changeOpLabel(fc.op)}
                </span>
                <span style={{
                  flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  color: 'var(--text-2)', fontSize: 12, fontFamily: 'var(--font-mono)',
                }}>
                  {relativePath(fc.path)}
                </span>
              </button>
            ))}
          </div>
        </>
      )}

      {/* ─── Main layout ─────────────────────────────── */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>

        {/* Left sidebar: sessions + file tree */}
        <Sidebar
          onSelectFile={(path) => setSelectedFile(path)}
          onNewSession={handleNewSession}
          onLoadSession={handleLoadSession}
          workdir={statusData?.workdir}
          onWorkdirChange={async (dirPath) => {
            try {
              await api.setWorkdir(dirPath)
              handleWorkdirChanged()
            } catch {
              setWorkdirError(true)
            }
          }}
        />

        {/* ─── Chat area ─────────────────────────────── */}
        <main style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0 }}>
          {/* Messages scroll area */}
          <div style={{ flex: 1, overflowY: 'auto', padding: '20px 16px' }}>
            {messages.length === 0 && (
              <div style={{ textAlign: 'center', marginTop: '12vh' }}>
                <Bot size={44} strokeWidth={1.5} color="var(--text-2)" style={{ marginBottom: 10 }} />
                <p style={{ color: 'var(--text-1)', fontSize: 16, fontWeight: 600, margin: '0 0 6px' }}>
                  Code Agent 已就绪
                </p>
                <p style={{ color: 'var(--text-3)', fontSize: 13, margin: '0 0 4px' }}>
                  工作目录：
                  <code style={{
                    fontFamily: 'var(--font-mono)',
                    color: workdirError ? 'var(--red)' : 'var(--text-2)',
                  }}>
                    {statusData?.workdir || '未初始化'}
                  </code>
                  {workdirError && (
                    <span style={{ marginLeft: 6, color: 'var(--red)', fontSize: 11 }}>
                      目录不可访问
                    </span>
                  )}
                </p>
                <p style={{ color: 'var(--text-3)', fontSize: 12, margin: '0 0 20px' }}>
                  点击顶部目录名可切换到其他项目 · 使用 + 号添加文件或选择模型
                </p>
                <div style={{ display: 'flex', gap: 8, justifyContent: 'center', flexWrap: 'wrap' }}>
                  {['分析项目结构', '列出所有 Python 文件', '查找 TODO 注释', '解释 main.py 的功能'].map(hint => (
                    <button
                      key={hint}
                      onClick={() => handleSend(hint)}
                      style={{
                        padding: '7px 14px', borderRadius: 999,
                        border: '1px solid var(--border-hi)',
                        background: 'var(--bg-raised)',
                        color: 'var(--text-2)', fontSize: 12,
                        cursor: 'pointer', transition: 'all 0.15s',
                      }}
                      onMouseOver={e => {
                        e.currentTarget.style.background = 'var(--bg-overlay)'
                        e.currentTarget.style.color = 'var(--text-1)'
                        e.currentTarget.style.borderColor = 'var(--accent)'
                      }}
                      onMouseOut={e => {
                        e.currentTarget.style.background = 'var(--bg-raised)'
                        e.currentTarget.style.color = 'var(--text-2)'
                        e.currentTarget.style.borderColor = 'var(--border-hi)'
                      }}
                    >
                      {hint}
                    </button>
                  ))}
                </div>
              </div>
            )}

            <div style={{ maxWidth: 800, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: 2 }}>
              {displayMessages.map(msg => (
                <MessageBubble key={msg.id} msg={msg} />
              ))}
            </div>

            {/* Tool calls are now shown inline in MessageBubble */}
            <div ref={bottomRef} style={{ height: 1 }} />
          </div>

          {/* ─── Approval banners ────────────────────── */}
          {pendingApprovals.length > 0 && (
            <div style={{ padding: '0 12px', maxWidth: 800, margin: '0 auto' }}>
              {pendingApprovals.map(req => (
                <ApprovalBanner
                  key={req.id}
                  approval={req}
                  onApprove={handleApprove}
                />
              ))}
            </div>
          )}

          {/* ─── Choice panel (Phase 1 options) ───── */}
          {planOptions && planOptions.length > 0 && (
            <div style={{ padding: '0 12px', maxWidth: 800, margin: '0 auto' }}>
              <ChoicePanel
                options={planOptions}
                onSelect={(optionId) => {
                  setPlanOptions(null)
                  addUserMessage(`选择: ${optionId}`)
                  sendMessage(`选择: ${optionId}`, 'plan')
                }}
              />
            </div>
          )}

          {/* ─── Plan review ────────────────────────── */}
          {planProposal && planProposal.status === 'pending' && (
            <div style={{ padding: '0 12px', maxWidth: 800, margin: '0 auto' }}>
              <PlanReview
                content={planProposal.content}
                runId={planProposal.run_id}
                onApprove={handlePlanApprove}
                onReject={handlePlanReject}
                onModify={handlePlanModify}
              />
            </div>
          )}

          {/* Input */}
          <ChatInput
            onSend={handleSend}
            onInterrupt={handleInterrupt}
            onClearHistory={handleClearHistory}
            onCompress={handleCompress}
            busy={busy}
            disabled={socketStatus !== 'connected'}
            mode={agentMode}
            onModeChange={setAgentMode}
            currentModel={currentModel}
            onModelChange={setCurrentModel}
            availableModels={availableModels}
            fileContexts={fileContexts}
            onFileContextToggle={(path, included) => {
              setFileContexts(prev => prev.map(fc =>
                fc.path === path ? { ...fc, included } : fc
              ))
            }}
            onFileContextAdd={(ctx) => {
              setFileContexts(prev => {
                const existing = prev.find(fc => fc.path === ctx.path)
                if (existing) return prev.map(fc => fc.path === ctx.path ? ctx : fc)
                return [...prev, ctx]
              })
            }}
            onFileContextRemove={(path) => {
              setFileContexts(prev => prev.filter(fc => fc.path !== path))
            }}
            preFill={preFillInput}
            onPreFillConsumed={() => setPreFillInput('')}
            workdir={statusData?.workdir}
          />
        </main>
      </div>

      {/* ─── Status bar ──────────────────────────────── */}
      <StatusBar
        socketStatus={socketStatus}
        agentState={agentState}
        workdir={statusData?.workdir}
        workdirError={workdirError}
        model={statusData?.model}
        historyLength={messages.filter(m => m.role !== 'system').length}
        onRefreshWorkdir={() => {
          refetchStatus()
          qc.invalidateQueries({ queryKey: ['fileTree'] })
          setWorkdirError(false)
        }}
      />

      {/* ─── Progress panel (floating) ───────────────── */}
      <ProgressPanel
        todos={todos}
        visible={todos.length > 0}
      />

      {/* ─── File editor modal ──────────────────────── */}
      <FilePreview path={selectedFile} onClose={() => setSelectedFile(null)} onAddToChat={handleAddToChat} />
    </div>
  )
}

function iconBtn(active: boolean): React.CSSProperties {
  return {
    width: 30, height: 30, borderRadius: 6,
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    background: active ? 'var(--bg-overlay)' : 'none',
    border: active ? '1px solid var(--border-hi)' : '1px solid transparent',
    color: active ? 'var(--text-1)' : 'var(--text-3)',
    cursor: 'pointer', transition: 'all 0.15s',
  }
}
