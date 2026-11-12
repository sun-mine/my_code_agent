import { useEffect, useRef, useState, useCallback, useMemo, type SetStateAction } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Trash2, Circle, AlertCircle, Plus, Minus, Pencil, Terminal, FolderOpen, ChevronRight, ChevronUp, Layers3, Code2, Search, ArrowUpRight, PanelLeft, X } from 'lucide-react'
import './App.css'

import { useAgentSocket, type SocketStatus } from './hooks/useAgentSocket'
import { useChat, type FileChange } from './hooks/useChat'
import { chatDrafts, chatStore } from './lib/chatStore'
import { api, ApiError } from './lib/api'
import { historyToMessages } from './lib/history'
import { buildConversation } from './lib/conversation'
import { applyTheme } from './lib/theme'
import type { AgentMode, FileContext, ModelInfo, ContentBlock, ImageAttachment, ChatMessage, AgentEvent, SessionSnapshot } from './lib/types'

import { MessageBubble } from './components/chat/MessageBubble'
import { DeliveryReport } from './components/chat/DeliveryReport'
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
  const loadEpochRef = useRef(0)

  const load = useCallback(async (path?: string) => {
    const epoch = ++loadEpochRef.current
    setLoading(true)
    try {
      const data = await api.fsLs(path)
      if (epoch !== loadEpochRef.current) return
      setBrowsePath(data.path)
      setParent(data.parent)
      setItems(data.items)
    } catch { /* ignore */ }
    finally { if (epoch === loadEpochRef.current) setLoading(false) }
  }, [])

  useEffect(() => {
    let cancelled = false
    const epoch = ++loadEpochRef.current
    void api.fsLs().then(data => {
      if (cancelled || epoch !== loadEpochRef.current) return
      setBrowsePath(data.path)
      setParent(data.parent)
      setItems(data.items)
    }).catch(() => { /* The picker keeps its empty state on load failure. */ }).finally(() => {
      if (!cancelled && epoch === loadEpochRef.current) setLoading(false)
    })
    return () => { cancelled = true }
  }, [])

  return (
    <>
      <div style={{ position: 'fixed', inset: 0, zIndex: 200 }} onClick={onClose} />
      <div style={{
        position: 'absolute', top: 44, left: 0, zIndex: 201, width: 340,
        background: 'var(--bg-overlay)', border: '1px solid var(--border-hi)',
        borderRadius: 8, boxShadow: 'var(--shadow-lg)',
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
    <div className="workdir-selector" style={{ position: 'relative' }}>
      <button
        className="workdir-trigger"
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
          : <Circle size={6} fill={current ? 'var(--green)' : 'var(--text-3)'} color={current ? 'var(--green)' : 'var(--text-3)'} style={{ flexShrink: 0 }} />
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
            borderRadius: 8, padding: '12px', boxShadow: 'var(--shadow-lg)',
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
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [agentMode, setAgentMode] = useState<AgentMode>('auto')
  const [currentModel, setCurrentModel] = useState<string>('')
  const [currentProvider, setCurrentProvider] = useState<string>('')
  const [availableModels, setAvailableModels] = useState<ModelInfo[]>([])
  const modelLoadEpochRef = useRef(0)
  const [fileContexts, setFileContexts] = useState<FileContext[]>([])
  const bottomRef = useRef<HTMLDivElement>(null)
  const sidebarToggleRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!sidebarOpen) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setSidebarOpen(false)
        sidebarToggleRef.current?.focus()
      }
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [sidebarOpen])

  // Apply theme on mount
  useEffect(() => {
    applyTheme((() => { try { const v = localStorage.getItem('code-agent-theme'); if (v === 'dark' || v === 'light') return v; if (v === 'beige') return 'light' } catch { /* Storage can be unavailable in privacy mode. */ } return 'dark' })())
  }, [])

  const {
    activeSessionId, activateSession, hydrateSession,
    messages, todos, toolCalls, agentState, runId, pendingMessage, canResume, draftResetEpoch, deliveryReports,
    pendingApprovals, changedFiles, planProposal, setPlanProposal,
    planOptions, setPlanOptions,
    handleEvent, clearAll, dismissApproval, dismissPlan,
  } = useChat()
  const activeSessionRef = useRef<string | null>(null)
  const selectionEpochRef = useRef(0)
  const [sessionSwitching, setSessionSwitching] = useState(true)
  const [sessionError, setSessionError] = useState('')
  const [runningSessionId, setRunningSessionId] = useState<string | null>(null)
  const runningStateEpochRef = useRef(0)
  const updateRunningSession = useCallback((update: SetStateAction<string | null>) => {
    runningStateEpochRef.current += 1
    setRunningSessionId(update)
  }, [])
  const [queueTick, setQueueTick] = useState(0)
  const sendingRequestRef = useRef<string | null>(null)

  const applySessionSnapshot = useCallback((snapshot: SessionSnapshot, epoch: number) => {
    if (epoch !== selectionEpochRef.current) return false
    if (!snapshot.session_id || !Array.isArray(snapshot.events) || typeof snapshot.event_seq !== 'number') {
      throw new Error('后端仍是旧版本，请在主机重启 Python 后端服务后刷新页面。')
    }
    hydrateSession(snapshot.session_id, historyToMessages(snapshot.history, snapshot.history_run_ids), snapshot)
    activeSessionRef.current = snapshot.session_id
    activateSession(snapshot.session_id)
    if (snapshot.mode) setAgentMode(snapshot.mode)
    const effective = chatStore.getSessionState(snapshot.session_id)
    if (effective?.runId) updateRunningSession(snapshot.session_id)
    else updateRunningSession(previous => previous === snapshot.session_id ? null : previous)
    return true
  }, [activateSession, hydrateSession, updateRunningSession])

  const restoreHistory = useCallback(async () => {
    const epoch = ++selectionEpochRef.current
    const selected = activeSessionRef.current
    setSessionSwitching(true)
    try {
      const snapshot = await api.history(selected ?? undefined)
      if (applySessionSnapshot(snapshot, epoch)) {
        setSessionError('')
        return snapshot
      }
    } catch (error) {
      if (epoch === selectionEpochRef.current) setSessionError(error instanceof Error ? error.message : '无法恢复聊天')
    } finally {
      if (epoch === selectionEpochRef.current) setSessionSwitching(false)
    }
    return null
  }, [applySessionSnapshot])

  useEffect(() => {
    let cancelled = false
    const epoch = ++selectionEpochRef.current
    const selected = activeSessionRef.current
    void api.history(selected ?? undefined).then(snapshot => {
      if (!cancelled && applySessionSnapshot(snapshot, epoch)) setSessionError('')
    }).catch(error => {
      if (!cancelled && epoch === selectionEpochRef.current) {
        setSessionError(error instanceof Error ? error.message : '无法恢复聊天')
      }
    }).finally(() => {
      if (!cancelled && epoch === selectionEpochRef.current) setSessionSwitching(false)
    })
    return () => { cancelled = true }
  }, [applySessionSnapshot])

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

  const conversation = useMemo(() => buildConversation(displayMessages, deliveryReports), [displayMessages, deliveryReports])

  const onAgentEvent = useCallback((event: AgentEvent) => {
    handleEvent(event)
    if (!event.session_id) return
    if (event.type === 'run_start') updateRunningSession(event.session_id)
    if (event.type === 'run_end' || event.type === 'interrupted' || (event.type === 'error' && !event.run_id)) {
      updateRunningSession(previous => previous === event.session_id ? null : previous)
    }
    if (event.type === 'run_start' || event.type === 'run_end' || event.type === 'interrupted' || event.type === 'error') {
      void qc.invalidateQueries({ queryKey: ['sessions'] })
      void qc.invalidateQueries({ queryKey: ['agentStatus'] })
    }
  }, [handleEvent, qc, updateRunningSession])

  useAgentSocket({
    onEvent: onAgentEvent,
    onStatusChange: setSocketStatus,
    onReconnect: restoreHistory,
  })

  const {
    data: statusData,
    refetch: refetchStatus,
    isLoading: statusLoading,
    error: statusError,
  } = useQuery({
    queryKey: ['agentStatus'],
    queryFn: () => api.status(),
    refetchInterval: 5000,
    retry: false,
  })

  useEffect(() => {
    if (!statusData || !('running_session_id' in statusData)) return
    let cancelled = false
    const epoch = runningStateEpochRef.current
    const sessionId = statusData.running_session_id ?? null
    queueMicrotask(() => {
      if (!cancelled && epoch === runningStateEpochRef.current) updateRunningSession(sessionId)
    })
    return () => { cancelled = true }
  }, [statusData, updateRunningSession])

  // 获取当前会话/工作区对应的模型列表与选择。
  useEffect(() => {
    let cancelled = false
    const epoch = ++modelLoadEpochRef.current
    api.listModels().then(data => {
      if (cancelled || epoch !== modelLoadEpochRef.current) return
      setAvailableModels(data.models)
      setCurrentModel(data.current)
      setCurrentProvider(data.current_provider)
    }).catch(() => { /* Model selection remains available from status data. */ })
    return () => { cancelled = true }
  }, [activeSessionId, statusData?.workdir])

  const handleModelChange = useCallback((model: string, provider: string) => {
    // Ignore any older list request that may still be resolving while the switch completes.
    modelLoadEpochRef.current += 1
    setCurrentModel(model)
    setCurrentProvider(provider)
    void qc.invalidateQueries({ queryKey: ['agentStatus'] })
  }, [qc])

  const busy = agentState !== 'idle' || (!!activeSessionId && runningSessionId === activeSessionId)
  const backgroundRunning = !!runningSessionId && runningSessionId !== activeSessionId

  const handleSend = (text: string, attachments?: { images: ImageAttachment[]; fileContexts: FileContext[] }): Promise<boolean> => {
    if (!activeSessionId || sessionSwitching) return Promise.resolve(false)
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
    return chatStore.enqueueMessage(activeSessionId, content, agentMode)
  }

  useEffect(() => {
    const next = chatStore.getNextPendingMessage()
    if (!next || runningSessionId || agentState !== 'idle' || socketStatus !== 'connected' ||
        sendingRequestRef.current) return

    const { sessionId, message } = next
    const requestId = message.requestId
    sendingRequestRef.current = requestId
    chatStore.updatePendingMessage(sessionId, requestId, 'sending')
    void api.chat(message.content, message.mode, sessionId, requestId)
      .then(accepted => {
        if (accepted.session_id !== sessionId) {
          throw new Error('服务器返回了不匹配的会话，消息仍保留在待发队列')
        }
        sendingRequestRef.current = null
        chatDrafts.clear(sessionId)
        const running = chatStore.acceptPendingMessage(sessionId, requestId, accepted.run_id)
        updateRunningSession(previous => running ? accepted.session_id : previous === sessionId ? null : previous)
        setQueueTick(tick => tick + 1)
        setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: 'smooth' }), 50)
      })
      .catch(error => {
        if (error instanceof ApiError && error.status === 409) {
          chatStore.updatePendingMessage(sessionId, requestId, 'queued')
          sendingRequestRef.current = null
          void refetchStatus()
          window.setTimeout(() => setQueueTick(tick => tick + 1), 1000)
          return
        }
        sendingRequestRef.current = null
        chatStore.updatePendingMessage(
          sessionId,
          requestId,
          'failed',
          error instanceof Error ? error.message : '无法确认服务器已接收，请重试',
        )
        setQueueTick(tick => tick + 1)
      })
  }, [
    activeSessionId, pendingMessage, runningSessionId, agentState, socketStatus, queueTick,
    refetchStatus, updateRunningSession,
  ])

  const handleCompress = () => {
    handleSend('__compress__')
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

  const handleResumeReview = useCallback(async (sessionId: string, reportRunId: string) => {
    const report = await api.resumeReview(sessionId, reportRunId)
    if (!chatStore.replaceDeliveryReport(sessionId, reportRunId, report)) {
      throw new Error('返回的审查报告不属于原会话或运行')
    }
  }, [])

  const handleInterrupt = async () => {
    const sessionId = activeSessionRef.current
    if (!sessionId || agentState === 'cancelling') return
    let targetRunId = runId
    if (!targetRunId) {
      const snapshot = await restoreHistory()
      targetRunId = snapshot?.session_id === sessionId && snapshot.running ? snapshot.run_id : null
    }
    if (!targetRunId) {
      setSessionError('当前没有可停止的任务')
      return
    }
    chatStore.setStopping(sessionId, true)
    try {
      await api.interrupt(sessionId, targetRunId)
    } catch (error) {
      chatStore.setStopping(sessionId, false)
      setSessionError(error instanceof Error ? error.message : '停止失败，请重试')
    }
  }

  const handleClearHistory = async () => {
    const sessionId = activeSessionRef.current
    if (!sessionId) return
    try {
      await api.clearHistory(sessionId)
      if (activeSessionRef.current === sessionId) clearAll()
      void qc.invalidateQueries({ queryKey: ['agentStatus'] })
      void qc.invalidateQueries({ queryKey: ['sessions'] })
    } catch (error) {
      setSessionError(error instanceof Error ? error.message : '无法清空聊天')
    }
  }

  const handleNewSession = async (workdir?: string) => {
    const epoch = ++selectionEpochRef.current
    setSessionSwitching(true)
    setSessionError('')
    try {
      const snapshot = await api.newSession(workdir)
      if (applySessionSnapshot(snapshot, epoch)) {
        setFileContexts([])
        setChangedFilesOpen(false)
        void qc.invalidateQueries({ queryKey: ['sessions'] })
        void qc.invalidateQueries({ queryKey: ['agentStatus'] })
        void qc.invalidateQueries({ queryKey: ['fileTree'] })
      }
    } catch (error) {
      if (epoch === selectionEpochRef.current) setSessionError(error instanceof Error ? error.message : '新建聊天失败')
    } finally {
      if (epoch === selectionEpochRef.current) setSessionSwitching(false)
    }
  }

  const handleLoadSession = async (sid: string) => {
    const epoch = ++selectionEpochRef.current
    setSessionSwitching(true)
    setSessionError('')
    try {
      const snapshot = await api.loadSession(sid)
      if (applySessionSnapshot(snapshot, epoch)) {
        setFileContexts([])
        setChangedFilesOpen(false)
        void qc.invalidateQueries({ queryKey: ['agentStatus'] })
      }
    } catch (error) {
      if (epoch === selectionEpochRef.current) setSessionError(error instanceof Error ? error.message : '无法打开聊天')
    } finally {
      if (epoch === selectionEpochRef.current) setSessionSwitching(false)
    }
  }

  const handleWorkdirChanged = useCallback(() => {
    setWorkdirError(false)
    void refetchStatus()
    void qc.invalidateQueries({ queryKey: ['fileTree'] })
    const epoch = ++selectionEpochRef.current
    setSessionSwitching(true)
    void api.history().then(snapshot => {
      if (applySessionSnapshot(snapshot, epoch)) {
        setFileContexts([])
        setSessionError('')
      }
    }).catch(error => {
      if (epoch === selectionEpochRef.current) setSessionError(error instanceof Error ? error.message : '切换项目失败')
    }).finally(() => {
      if (epoch === selectionEpochRef.current) setSessionSwitching(false)
    })
  }, [applySessionSnapshot, qc, refetchStatus])

  const handleApprove = async (toolUseId: string, approved: boolean) => {
    const approval = pendingApprovals.find(item => item.id === toolUseId)
    try {
      await api.toolApprove(toolUseId, approved, activeSessionId ?? undefined, approval?.run_id)
      dismissApproval(toolUseId)
    } catch (error) {
      setSessionError(error instanceof Error ? error.message : '审批提交失败')
    }
  }

  const handlePlanApprove = async (_planRunId: string) => {
    void _planRunId
    const planContent = planProposal?.content || ''
    setPlanProposal(prev => prev ? { ...prev, status: 'approved' } : null)
    if (planContent) {
      api.savePlan(planContent, 'plan').catch(() => { /* Plan execution can continue without a saved copy. */ })
    }
    dismissPlan()
    setAgentMode('auto')
    api.executePlan(planContent, todos, activeSessionId ?? undefined).catch((e) => {
      console.error('executePlan error:', e)
    })
    setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: 'smooth' }), 50)
  }

  const handlePlanReject = async (_planRunId: string) => {
    void _planRunId
    setPlanProposal(prev => prev ? { ...prev, status: 'rejected' } : null)
    dismissPlan()
    const rejectMsg = '请重新规划，考虑不同的方案'
    handleSend(rejectMsg)
    setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: 'smooth' }), 50)
  }

  const handlePlanModify = async (_planRunId: string, feedback: string) => {
    void _planRunId
    dismissPlan()
    const modifyMsg = `请根据以下反馈修改计划：\n${feedback}`
    handleSend(modifyMsg)
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
  }, [handleWorkdirChanged])

  useEffect(() => {
    const el = bottomRef.current?.parentElement
    if (el) el.scrollTo({ top: el.scrollHeight })
  }, [messages, toolCalls, pendingApprovals, deliveryReports])

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
      className="workbench"
      onDragOver={e => { e.preventDefault() }}
      onDrop={handleMainDrop}
    >
      {/* ─── Title bar ──────────────────────────────── */}
      <header className="workbench-header">
        <div className="app-brand">
          <button
            ref={sidebarToggleRef}
            className="sidebar-toggle icon-button"
            onClick={() => setSidebarOpen(open => !open)}
            aria-label={sidebarOpen ? '关闭项目侧栏' : '打开项目侧栏'}
            aria-expanded={sidebarOpen}
            aria-controls="project-sidebar"
          >
            {sidebarOpen ? <X size={18} /> : <PanelLeft size={18} />}
          </button>
          <span className="brand-mark"><Terminal size={19} strokeWidth={2} /></span>
          <span className="brand-wordmark">Code Agent<span className="brand-caption">你的编程伙伴</span></span>
        </div>
        <div className="header-workdir">
          <span className="header-breadcrumb">工作区 <ChevronRight size={12} /></span>
          <WorkdirSelector
            current={statusData?.workdir}
            onChanged={handleWorkdirChanged}
            error={workdirError}
          />
        </div>
        <div className="header-actions">
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
        <div className="header-context">
          <ContextPanel
            tokenBreakdown={statusData?.token_breakdown}
            onCompress={handleCompress}
            busy={busy}
          />
        </div>

        {/* model name */}
        <span className="header-model">
          {statusData?.model?.replace('claude-', '').replace('deepseek-v4-', 'V4 ').replace('deepseek-', '')}
        </span>

        <ThemeToggle />

        <button
          onClick={handleClearHistory}
          title="清空对话"
          aria-label="清空对话"
          className="icon-button clear-chat-button"
        >
          <Trash2 size={ICON_MD - 1} strokeWidth={1.8} />
        </button>
        </div>
      </header>

      {/* ─── Changed files dropdown ───────────────────── */}
      {changedFilesOpen && changedFiles.length > 0 && (
        <>
          <div style={{ position: 'fixed', inset: 0, zIndex: 39 }} onClick={() => setChangedFilesOpen(false)} />
          <div style={{
            position: 'absolute', top: 64, right: 12, zIndex: 50,
            background: 'var(--bg-overlay)', border: '1px solid var(--border-hi)',
            borderRadius: 12, padding: '8px 0', width: 'min(340px, calc(100vw - 24px))',
            boxShadow: 'var(--shadow-lg)',
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
      <div className="workbench-body">
        {sidebarOpen && <button className="sidebar-backdrop" aria-label="关闭项目侧栏遮罩" onClick={() => setSidebarOpen(false)} />}
        {/* Left sidebar: sessions + file tree */}
        <div id="project-sidebar" className={`sidebar-shell${sidebarOpen ? ' is-open' : ''}`}>
        <Sidebar
          onNewSession={() => { setSidebarOpen(false); void handleNewSession() }}
          onNewProjectSession={path => {
            setSidebarOpen(false)
            void handleNewSession(path)
          }}
          onLoadSession={sid => { setSidebarOpen(false); void handleLoadSession(sid) }}
          workdir={statusData?.workdir}
          activeSessionId={activeSessionId ?? undefined}
          onProjectChange={async dirPath => {
            try {
              await api.setWorkdir(dirPath)
              handleWorkdirChanged()
              setSidebarOpen(false)
            } catch (error) {
              setWorkdirError(true)
              throw error
            }
          }}
        />
        </div>

        {/* ─── Chat area ─────────────────────────────── */}
        <main className="chat-workspace">
          {/* Messages scroll area */}
          <div className={`conversation-scroll${messages.length === 0 && deliveryReports.length === 0 ? ' is-empty' : ''}`}>
            {messages.length === 0 && deliveryReports.length === 0 && (
              <section className="welcome-screen" aria-labelledby="welcome-title">
                <div className="welcome-eyebrow"><span /> 你的本地 AI 工作台</div>
                <h1 id="welcome-title">让想法，<span>成为代码。</span></h1>
                <p className="welcome-description">理解项目、探索方案、完成修改。<br className="mobile-break" />从这里开始你的下一次构建。</p>
                <div className={`welcome-project${workdirError ? ' has-error' : ''}`}>
                  <FolderOpen size={14} />
                  <span title={statusData?.workdir}>
                    {statusData?.workdir
                      ? statusData.workdir.replace(/[\\/]$/, '').split(/[\\/]/).filter(Boolean).at(-1)
                      : statusLoading ? '正在连接工作区…' : '连接本地服务后，选择一个项目'}
                  </span>
                  {workdirError && <span className="welcome-project-error">目录不可访问</span>}
                </div>
                <div className="welcome-suggestions" aria-label="开始一个任务">
                  {[
                    { icon: Layers3, title: '梳理项目', description: '快速了解目录结构与技术栈', prompt: '请分析当前项目的目录结构、技术栈和主要入口。' },
                    { icon: Code2, title: '读懂代码', description: '理清核心逻辑与模块关系', prompt: '请找到项目的主要入口，并解释核心模块之间的调用关系。' },
                    { icon: Search, title: '寻找线索', description: '搜索 TODO 与待完善的细节', prompt: '请查找项目中的 TODO 注释，并按模块整理待办事项。' },
                    { icon: Pencil, title: '一起改进', description: '发现可以优化的实现', prompt: '请审查当前项目，找出值得优先改进的问题，并先给出建议。' },
                  ].map(({ icon: Icon, title, description, prompt }) => (
                    <button className="suggestion-card" key={title} onClick={() => setPreFillInput(prompt)}>
                      <span className="suggestion-icon"><Icon size={18} strokeWidth={1.7} /></span>
                      <span className="suggestion-copy"><strong>{title}</strong><span>{description}</span></span>
                      <ArrowUpRight className="suggestion-arrow" size={16} />
                    </button>
                  ))}
                </div>
                {!statusData?.workdir && !statusLoading && (
                  <div className="connection-notice" role="status">
                    <AlertCircle size={14} />
                    <span>{statusError instanceof Error && statusError.message.includes('Agent not initialized')
                      ? '工作区正在准备中，请检查本地服务的初始化状态。'
                      : '本地服务尚未连接，启动后即可开始对话。'}</span>
                    <button onClick={() => { void refetchStatus() }}>重新连接</button>
                  </div>
                )}
              </section>
            )}

            <div className="conversation-messages">
              {conversation.map(item => item.type === 'message'
                ? <MessageBubble key={`message-${item.message.id}`} msg={item.message} />
                : <DeliveryReport key={`report-${item.report.run_id}`} report={item.report} onResumeReview={handleResumeReview} />)}
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
                  void handleSend(`选择: ${optionId}`)
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

          {sessionError && <div className="session-notice is-error" role="alert"><AlertCircle size={14} /><span>{sessionError}</span><button aria-label="关闭聊天提示" onClick={() => setSessionError('')}><X size={14} /></button></div>}
          {backgroundRunning && <div className="session-notice" role="status"><Circle size={6} fill="currentColor" /><span>上一段对话仍在生成，你可以先写下新的想法。</span><button onClick={() => { if (runningSessionId) void handleLoadSession(runningSessionId) }}>返回并查看</button></div>}
          {canResume && !busy && <div className="session-notice" role="status"><Circle size={6} fill="currentColor" /><span>任务已中断，上下文仍保留。</span><button onClick={() => { void handleSend('请继续执行刚才被中断的任务。') }}>继续执行</button></div>}
          {/* Input */}
          <ChatInput
            key={`${activeSessionId ?? 'connecting'}:${draftResetEpoch}`}
            sessionId={activeSessionId ?? undefined}
            onSend={handleSend}
            onInterrupt={handleInterrupt}
            onCancelPending={() => {
              if (activeSessionId && pendingMessage) chatStore.cancelPendingMessage(activeSessionId, pendingMessage.requestId)
            }}
            onRetryPending={() => {
              if (!activeSessionId || !pendingMessage) return
              chatStore.updatePendingMessage(activeSessionId, pendingMessage.requestId, 'queued')
              setQueueTick(tick => tick + 1)
            }}
            onClearHistory={handleClearHistory}
            onCompress={handleCompress}
            busy={busy}
            stopping={agentState === 'cancelling'}
            pendingMessage={pendingMessage ?? undefined}
            disabled={socketStatus !== 'connected'}
            sendDisabled={sessionSwitching || !activeSessionId}
            mode={agentMode}
            onModeChange={setAgentMode}
            currentModel={currentModel}
            currentProvider={currentProvider}
            onModelChange={handleModelChange}
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
      <FilePreview key={selectedFile ?? 'closed'} path={selectedFile} onClose={() => setSelectedFile(null)} onAddToChat={handleAddToChat} />
    </div>
  )
}
