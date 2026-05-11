import { useState } from 'react'
import type { ToolCall } from '../../hooks/useChat'
import { getToolIcon, getToolLabel, StatusIcon, ArrowIcon, ICON_SM } from '../../lib/icons'

function formatDur(ms: number) {
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

interface ToolTimelineProps {
  toolCalls: ToolCall[]
}

export function ToolTimeline({ toolCalls }: ToolTimelineProps) {
  const [open, setOpen] = useState(false)
  if (toolCalls.length === 0) return null

  const running = toolCalls.filter(tc => tc.status === 'running')
  const errors = toolCalls.filter(tc => tc.status === 'error')
  const isActive = running.length > 0

  const headerText = isActive
    ? `${running[0]?.name ? getToolLabel(running[0].name) : '工具调用中'}…`
    : `已使用 ${toolCalls.length} 个工具${errors.length > 0 ? `，${errors.length} 个错误` : ''}`

  return (
    <div style={{
      background: 'var(--bg-surface)',
      border: '1px solid var(--border)',
      borderRadius: 8,
      overflow: 'hidden',
      margin: '4px 0',
    }}>
      {/* Header collapse row */}
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          width: '100%', padding: '7px 12px',
          background: 'none', border: 'none',
          cursor: 'pointer', textAlign: 'left',
        }}
      >
        {/* Status indicator */}
        {isActive ? (
          <span style={{
            display: 'inline-flex', animation: 'spin 0.8s linear infinite',
          }}>
            <StatusIcon.running size={12} strokeWidth={2.5} color="var(--accent)" />
          </span>
        ) : errors.length > 0 ? (
          <StatusIcon.error size={12} strokeWidth={2.5} color="var(--red)" />
        ) : (
          <StatusIcon.done size={12} strokeWidth={2.5} color="var(--green)" />
        )}

        <span style={{
          fontSize: 12, color: isActive ? 'var(--text-1)' : 'var(--text-2)',
          fontWeight: isActive ? 500 : 400, flex: 1,
        }}>
          {headerText}
        </span>

        {/* Running tool preview */}
        {isActive && running[0] && (
          <span style={{
            display: 'flex', alignItems: 'center', gap: 4,
            fontSize: 11, color: 'var(--text-3)',
            fontFamily: 'var(--font-mono)',
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            maxWidth: 250,
          }}>
            {(() => {
              const Icon = getToolIcon(running[0].name)
              return <Icon size={ICON_SM} />
            })()}
            {(() => {
              const inp = running[0].input
              if (inp.path) return String(inp.path)
              if (inp.command) return String(inp.command).slice(0, 50)
              return ''
            })()}
          </span>
        )}

        {open
          ? <ArrowIcon.up size={10} color="var(--text-3)" />
          : <ArrowIcon.down size={10} color="var(--text-3)" />
        }
      </button>

      {/* Expanded list */}
      {open && (
        <div style={{ borderTop: '1px solid var(--border)' }}>
          {toolCalls.map(tc => <ToolRow key={tc.id} tc={tc} />)}
        </div>
      )}
    </div>
  )
}

function ToolRow({ tc }: { tc: ToolCall }) {
  const [open, setOpen] = useState(false)
  const isRunning = tc.status === 'running'
  const isError = tc.output?.startsWith('Error:') || tc.status === 'error'
  const duration = tc.ts_end ? tc.ts_end - tc.ts_start : null

  const inputSummary = (() => {
    const inp = tc.input
    if (inp.path) return String(inp.path)
    if (inp.command) return String(inp.command).slice(0, 60)
    if (inp.query) return String(inp.query).slice(0, 60)
    const keys = Object.keys(inp)
    if (keys.length > 0) return String(inp[keys[0]]).slice(0, 60)
    return ''
  })()

  const ToolIcon = getToolIcon(tc.name)

  return (
    <div>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          width: '100%', padding: '5px 12px 5px 24px',
          background: open ? 'var(--bg-raised)' : 'none',
          border: 'none', cursor: 'pointer', textAlign: 'left',
          borderTop: '1px solid var(--border)',
        }}
      >
        {/* Status mark */}
        {isRunning ? (
          <span style={{ display: 'inline-flex', animation: 'spin 0.8s linear infinite' }}>
            <StatusIcon.running size={10} strokeWidth={2.5} color="var(--accent)" />
          </span>
        ) : isError ? (
          <StatusIcon.error size={10} strokeWidth={2.5} color="var(--red)" />
        ) : (
          <StatusIcon.done size={10} strokeWidth={2.5} color="var(--green)" />
        )}

        <ToolIcon size={ICON_SM} />
        <span style={{
          fontSize: 11, color: 'var(--text-2)',
          fontFamily: 'var(--font-mono)', flexShrink: 0,
        }}>
          {getToolLabel(tc.name)}
        </span>
        {inputSummary && (
          <span style={{
            fontSize: 11, color: 'var(--text-3)',
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            flex: 1,
          }}>
            {inputSummary}
          </span>
        )}
        {!isRunning && duration !== null && (
          <span style={{ fontSize: 10, color: 'var(--text-3)', flexShrink: 0 }}>
            {formatDur(duration * 1000)}
          </span>
        )}
        {open
          ? <ArrowIcon.up size={10} color="var(--text-3)" />
          : <ArrowIcon.down size={10} color="var(--text-3)" />
        }
      </button>

      {open && (
        <div style={{ fontSize: 11, borderTop: '1px solid var(--border)' }}>
          <div style={{ padding: '8px 24px', background: 'var(--bg-base)' }}>
            <p style={{ fontSize: 10, color: 'var(--text-3)', margin: '0 0 4px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              输入
            </p>
            <pre style={{
              margin: 0, fontFamily: 'var(--font-mono)', fontSize: 11,
              color: 'var(--text-2)', whiteSpace: 'pre-wrap', wordBreak: 'break-all',
              maxHeight: 150, overflow: 'auto',
            }}>
              {JSON.stringify(tc.input, null, 2)}
            </pre>
          </div>
          {tc.status === 'done' && tc.output && (
            <div style={{
              padding: '8px 24px',
              borderTop: '1px solid var(--border)',
              background: isError ? 'rgba(248,113,113,0.05)' : 'var(--bg-raised)',
            }}>
              <p style={{
                fontSize: 10, margin: '0 0 4px',
                textTransform: 'uppercase', letterSpacing: '0.05em',
                color: isError ? 'var(--red)' : 'var(--text-3)',
              }}>
                输出
              </p>
              <pre style={{
                margin: 0, fontFamily: 'var(--font-mono)', fontSize: 11,
                color: isError ? 'var(--red)' : 'var(--text-2)',
                whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                maxHeight: 200, overflow: 'auto',
              }}>
                {tc.output.slice(0, 2000)}{tc.output.length > 2000 ? '\n…' : ''}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
