import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Shrink, Brain, Loader2, FileText, Pencil, Terminal, Search, FolderOpen, ListTree, MemoryStick, Wrench } from 'lucide-react'
import { useState, useEffect, useRef } from 'react'
import type { ChatMessage, ContentBlock, AssistantStep } from '../../lib/types'
import type { ToolCall } from '../../hooks/useChat'
import { stripAnsi } from '../../lib/utils'

interface MessageBubbleProps {
  msg: ChatMessage
}

function formatTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`
  return String(n)
}

function renderContent(content: string | ContentBlock[]): React.ReactNode {
  if (typeof content === 'string') {
    return <ReactMarkdown remarkPlugins={[remarkGfm]}>{content || ' '}</ReactMarkdown>
  }
  return (
    <>
      {content.map((block, i) => {
        if (block.type === 'text') {
          return <ReactMarkdown key={i} remarkPlugins={[remarkGfm]}>{block.text || ' '}</ReactMarkdown>
        }
        if (block.type === 'image') {
          return (
            <img
              key={i}
              src={`data:${block.source.media_type};base64,${block.source.data}`}
              alt="attached"
              style={{ maxWidth: '100%', maxHeight: 360, borderRadius: 8, margin: '8px 0' }}
            />
          )
        }
        return null
      })}
    </>
  )
}

// ── Tool description helpers ──────────────────────────────

function getToolDescriptor(name: string, input: Record<string, unknown>): { icon: React.ReactNode; action: string; detail: string } {
  const iconSm = { size: 12, style: { flexShrink: 0, marginTop: 2 } }
  switch (name) {
    case 'read_file': {
      const p = String(input.path ?? input.filename ?? '')
      return { icon: <FileText {...iconSm} color="var(--green)" />, action: '读取', detail: p }
    }
    case 'write_file':
    case 'create_file':
    case 'save_file': {
      const p = String(input.path ?? input.filename ?? '')
      return { icon: <Pencil {...iconSm} color="var(--blue)" />, action: '写入', detail: p }
    }
    case 'edit_file': {
      const p = String(input.path ?? input.filename ?? '')
      return { icon: <Pencil {...iconSm} color="var(--blue)" />, action: '编辑', detail: p }
    }
    case 'list_files': {
      const p = String(input.path ?? input.directory ?? '')
      return { icon: <FolderOpen {...iconSm} color="var(--green)" />, action: '列出', detail: p || '当前目录' }
    }
    case 'glob': {
      const p = String(input.pattern ?? '')
      return { icon: <Search {...iconSm} color="var(--green)" />, action: '搜索文件', detail: p }
    }
    case 'grep': {
      const p = String(input.query ?? input.pattern ?? '')
      return { icon: <Search {...iconSm} color="var(--green)" />, action: '搜索内容', detail: p }
    }
    case 'bash':
    case 'background_run': {
      const cmd = String(input.command ?? '').replace(/\n/g, ' ').slice(0, 80)
      return { icon: <Terminal {...iconSm} color="var(--yellow)" />, action: '执行', detail: cmd }
    }
    case 'task': {
      const p = String(input.prompt ?? '').replace(/\n/g, ' ').slice(0, 60)
      return { icon: <Wrench {...iconSm} color="var(--accent)" />, action: '子任务', detail: p }
    }
    case 'TodoWrite': {
      const items = (input.items ?? input.todos) as Array<{ content: string }> | undefined
      return { icon: <ListTree {...iconSm} color="var(--accent)" />, action: '任务列表', detail: `${items?.length ?? 0} 项` }
    }
    case 'memory_store': {
      const p = String(input.key ?? input.name ?? '')
      return { icon: <MemoryStick {...iconSm} color="var(--accent)" />, action: '保存记忆', detail: p }
    }
    case 'memory_recall': {
      const p = String(input.query ?? '')
      return { icon: <MemoryStick {...iconSm} color="var(--accent)" />, action: '回忆记忆', detail: p }
    }
    default: {
      const keys = Object.keys(input)
      const detail = keys.length > 0 ? String(input[keys[0]]).slice(0, 40) : ''
      return { icon: <Wrench {...iconSm} color="var(--text-3)" />, action: name, detail }
    }
  }
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

// ── Inline tool call line ──────────────────────────────

function ToolLine({ tc }: { tc: ToolCall }) {
  const [expanded, setExpanded] = useState(false)
  const { icon, action, detail } = getToolDescriptor(tc.name, tc.input)
  const dur = (tc.ts_end && tc.ts_start) ? tc.ts_end - tc.ts_start : null
  const isRunning = tc.status === 'running'

  return (
    <div style={{ marginTop: 5 }}>
      <button
        onClick={() => setExpanded(e => !e)}
        style={{
          display: 'flex', alignItems: 'center', gap: 6,
          width: '100%', padding: '4px 8px', borderRadius: 6,
          background: isRunning ? 'rgba(232,133,92,0.08)' : 'var(--bg-base)',
          border: '1px solid var(--border)',
          cursor: 'pointer', textAlign: 'left',
          fontSize: 12, color: 'var(--text-2)',
          transition: 'background 0.15s',
        }}
      >
        {isRunning ? (
          <span style={{ display: 'inline-flex', animation: 'spin 0.8s linear infinite' }}>
            <Loader2 size={12} color="var(--accent)" />
          </span>
        ) : (
          icon
        )}
        <span style={{ fontWeight: 500 }}>{action}</span>
        <span style={{
          color: 'var(--text-3)', flex: 1,
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          fontFamily: 'var(--font-mono)', fontSize: 11,
        }}>
          {detail}
        </span>
        {dur !== null && (
          <span style={{ color: 'var(--text-3)', fontSize: 10, flexShrink: 0 }}>{formatDuration(dur)}</span>
        )}
        {isRunning && (
          <span style={{ color: 'var(--accent)', fontSize: 10, flexShrink: 0, animation: 'pulse 1s ease-in-out infinite' }}>执行中</span>
        )}
      </button>
      {expanded && tc.status === 'done' && tc.output && (
        <pre style={{
          margin: '4px 0 0', padding: '6px 10px', borderRadius: 6,
          background: 'var(--bg-base)', border: '1px solid var(--border)',
          fontSize: 11, fontFamily: 'var(--font-mono)',
          color: tc.output.startsWith('Error:') ? 'var(--red)' : 'var(--text-2)',
          whiteSpace: 'pre-wrap', wordBreak: 'break-all',
          maxHeight: 160, overflow: 'auto',
        }}>
          {stripAnsi(tc.output.slice(0, 2000))}
        </pre>
      )}
    </div>
  )
}

// ── Thinking block with timer ───────────────────────────

function ThinkingBlock({ text, streaming }: { text: string; streaming?: boolean }) {
  const [open, setOpen] = useState(!!streaming)
  const [elapsed, setElapsed] = useState(0)
  const startRef = useRef(Date.now())

  useEffect(() => {
    if (streaming) {
      startRef.current = Date.now()
      setElapsed(0)
      const timer = setInterval(() => {
        setElapsed(Math.floor((Date.now() - startRef.current) / 1000))
      }, 200)
      return () => clearInterval(timer)
    }
  }, [streaming])

  const effectiveOpen = streaming ? true : open

  return (
    <div style={{ marginTop: 6, fontSize: 12 }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 5,
          padding: '3px 9px', borderRadius: 6,
          background: streaming ? 'rgba(139,92,246,0.08)' : 'var(--bg-raised)',
          border: streaming ? '1px solid rgba(139,92,246,0.2)' : '1px solid var(--border)',
          color: 'var(--text-3)', cursor: 'pointer', fontSize: 11,
        }}
      >
        {streaming ? (
          <span style={{ display: 'inline-flex', animation: 'spin 0.8s linear infinite' }}>
            <Loader2 size={11} color="var(--accent)" />
          </span>
        ) : (
          <Brain size={11} />
        )}
        <span>{streaming ? `思考中 ${elapsed}s` : `思考了 ${elapsed || Math.max(1, Math.floor(text.length / 50))}s`}</span>
        {effectiveOpen ? (
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m6 9 6 6 6-6" /></svg>
        ) : (
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="m9 18 6-6-6-6" /></svg>
        )}
      </button>
      {effectiveOpen && (
        <div style={{
          marginTop: 4, padding: '8px 10px', borderRadius: 6,
          background: 'var(--bg-base)', border: '1px solid var(--border)',
          fontSize: 12, color: 'var(--text-2)', whiteSpace: 'pre-wrap',
          lineHeight: 1.5, maxHeight: 300, overflow: 'auto',
          animation: 'slideUp 0.12s ease',
        }}>
          {text.slice(0, 5000)}
        </div>
      )}
    </div>
  )
}

// ── Step renderer ───────────────────────────────────────

function StepView({ step, streaming }: { step: AssistantStep; streaming: boolean }) {
  if (step.type === 'thinking') {
    return <ThinkingBlock text={step.text || ''} streaming={streaming} />
  }
  if (step.type === 'tool_call' && step.toolCall) {
    return <ToolLine tc={step.toolCall} />
  }
  return null
}

// ── Summary ─────────────────────────────────────────────

function SummaryCardView({ data }: { data: { input_tokens: number; output_tokens: number; tool_rounds: number; file_operations: number } }) {
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 8,
      fontSize: 10, color: 'var(--text-3)',
      background: 'var(--bg-raised)', border: '1px solid var(--border)',
      padding: '2px 10px', borderRadius: 999,
      fontFamily: 'var(--font-mono)',
      marginLeft: 8, verticalAlign: 'middle',
    }}>
      <span title="input/output tokens">
        {formatTokens(data.input_tokens ?? 0)}→{formatTokens(data.output_tokens ?? 0)}
      </span>
      <span style={{ color: 'var(--border-hi)' }}>|</span>
      <span title="tool rounds">
        {data.tool_rounds ?? 0} 工具
      </span>
      <span style={{ color: 'var(--border-hi)' }}>|</span>
      <span title="file operations">
        {data.file_operations ?? 0} 文件
      </span>
    </span>
  )
}

// ── Main component ──────────────────────────────────────

export function MessageBubble({ msg }: MessageBubbleProps) {
  const isUser = msg.role === 'user'
  const isSystem = msg.role === 'system'

  if (isSystem) {
    const textContent = typeof msg.content === 'string' ? msg.content : ''
    return (
      <div style={{ display: 'flex', justifyContent: 'center', margin: '8px 0' }}>
        <span style={{
          display: 'flex', alignItems: 'center', gap: 5,
          fontSize: 11, color: 'var(--text-3)',
          background: 'var(--bg-raised)', border: '1px solid var(--border)',
          padding: '3px 12px', borderRadius: 999,
        }}>
          <Shrink size={11} />
          {textContent}
        </span>
      </div>
    )
  }

  if (isUser) {
    return (
      <div style={{
        display: 'flex', justifyContent: 'flex-end',
        gap: 8, margin: '6px 0',
        animation: 'fadeIn 0.2s ease',
      }}>
        <div style={{ maxWidth: '80%', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 3 }}>
          <div style={{
            background: 'var(--bg-overlay)',
            border: '1px solid var(--border-hi)',
            padding: '9px 14px',
            borderRadius: '14px 4px 14px 14px',
            color: 'var(--text-1)', fontSize: 14, lineHeight: 1.6,
            whiteSpace: 'pre-wrap',
          }}>
            {renderContent(msg.content)}
          </div>
          <span style={{ fontSize: 10, color: 'var(--text-3)' }}>
            {new Date(msg.ts).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}
          </span>
        </div>
        <div style={{
          width: 26, height: 26, borderRadius: '50%', flexShrink: 0,
          background: 'var(--bg-overlay)', border: '1px solid var(--border-hi)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 11, fontWeight: 700, color: 'var(--text-2)',
          marginTop: 2,
        }}>
          U
        </div>
      </div>
    )
  }

  // Assistant message — use steps for chronological rendering
  const steps = msg.steps
  const hasContent = typeof msg.content === 'string' ? msg.content.length > 0 : msg.content.length > 0

  return (
    <div style={{
      display: 'flex', gap: 8, margin: '4px 0',
      animation: 'fadeIn 0.2s ease',
    }}>
      <div style={{
        width: 26, height: 26, borderRadius: '50%', flexShrink: 0,
        background: 'var(--accent)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 11, fontWeight: 700, color: '#fff', marginTop: 2,
      }}>
        A
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        {/* Steps timeline: thinking + tool calls in order */}
        {steps && steps.length > 0 && (
          <div style={{
            marginBottom: hasContent ? 8 : 0,
            borderLeft: '2px solid var(--border)',
            paddingLeft: 10,
          }}>
            {steps.map((step, i) => {
              const isLast = i === steps.length - 1
              const contentStarted = hasContent || msg.toolCalls?.some(tc => tc.status === 'done')
              const isStepStreaming = step.type === 'thinking'
                ? !!(msg.streaming && isLast && !contentStarted)
                : step.type === 'tool_call' && step.toolCall?.status === 'running'
              return <StepView key={i} step={step} streaming={isStepStreaming} />
            })}
          </div>
        )}

        {/* Fallback for messages without steps (restored from history) */}
        {!steps && msg.thinking && <ThinkingBlock text={msg.thinking} streaming={msg.streaming} />}
        {!steps && msg.toolCalls && msg.toolCalls.length > 0 && (
          <div style={{ marginBottom: hasContent ? 8 : 0 }}>
            {msg.toolCalls.map(tc => <ToolLine key={tc.id} tc={tc} />)}
          </div>
        )}

        {/* Final text content */}
        {hasContent && (
          <div className="md-content" style={{ fontSize: 14, lineHeight: 1.7 }}>
            {renderContent(msg.content)}
            {msg.streaming && msg.thinking === undefined && (
              <span style={{
                display: 'inline-block', width: 2, height: 14,
                background: 'var(--accent)',
                marginLeft: 2, verticalAlign: 'middle',
                animation: 'pulse 1s ease-in-out infinite',
              }} />
            )}
          </div>
        )}

        <div style={{ fontSize: 10, color: 'var(--text-3)', marginTop: hasContent || (steps && steps.length > 0) ? 4 : 0, display: 'flex', alignItems: 'center', gap: 8 }}>
          <span>{new Date(msg.ts).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}</span>
          {msg.summary && <SummaryCardView data={msg.summary} />}
        </div>
      </div>
    </div>
  )
}
