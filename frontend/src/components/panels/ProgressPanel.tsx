import { useState } from 'react'
import { Check, ChevronDown, ChevronUp, Loader2 } from 'lucide-react'
import type { TodoItem } from '../../lib/types'
import { ICON_SM } from '../../lib/icons'

interface ProgressPanelProps {
  todos: TodoItem[]
  visible: boolean
}

const STATUS_ICON: Record<string, string> = {
  pending: '○',
  in_progress: '◉',
  completed: '●',
}

export function ProgressPanel({ todos, visible }: ProgressPanelProps) {
  const [collapsed, setCollapsed] = useState(false)

  if (!visible || todos.length === 0) return null

  const done = todos.filter(t => t.status === 'completed').length
  const total = todos.length
  const pct = total > 0 ? Math.round((done / total) * 100) : 0

  return (
    <div style={{
      position: 'fixed', right: 24, bottom: 120, zIndex: 110,
      width: collapsed ? 48 : 260,
      background: 'var(--bg-surface)',
      border: '1px solid var(--accent, #e8855c)',
      borderRadius: 10,
      boxShadow: '0 4px 24px rgba(0,0,0,0.5)',
      transition: 'width 0.2s ease',
      overflow: 'hidden',
    }}>
      {/* Header */}
      <button
        onClick={() => setCollapsed(c => !c)}
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          width: '100%', padding: collapsed ? '8px' : '10px 14px',
          background: 'rgba(134,239,172,0.06)',
          border: 'none', borderBottom: collapsed ? 'none' : '1px solid var(--border)',
          cursor: 'pointer',
          color: 'var(--text-1)',
        }}
      >
        {collapsed ? (
          <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--green)', margin: '0 auto' }}>
            {done}
          </span>
        ) : (
          <>
            <div style={{
              width: 28, height: 28, borderRadius: 7, flexShrink: 0,
              background: 'rgba(134,239,172,0.12)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
              <Check size={14} color="var(--green)" />
            </div>
            <div style={{ flex: 1, textAlign: 'left' }}>
              <div style={{ fontSize: 12, fontWeight: 600 }}>
                任务进度
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-3)' }}>
                {done}/{total} 完成 ({pct}%)
              </div>
            </div>
            <ChevronUp size={ICON_SM} color="var(--text-3)" />
          </>
        )}
      </button>

      {!collapsed && (
        <>
          {/* Progress bar */}
          <div style={{ padding: '8px 14px', borderBottom: '1px solid var(--border)' }}>
            <div style={{
              width: '100%', height: 4, borderRadius: 2,
              background: 'var(--bg-overlay)',
            }}>
              <div style={{
                width: `${pct}%`, height: '100%', borderRadius: 2,
                background: pct === 100 ? 'var(--green)' : 'var(--accent)',
                transition: 'width 0.4s ease',
              }} />
            </div>
          </div>

          {/* Task list */}
          <div style={{
            maxHeight: 280, overflowY: 'auto',
            padding: '6px 14px',
          }}>
            {todos.map((t, i) => (
              <div
                key={i}
                style={{
                  display: 'flex', alignItems: 'flex-start', gap: 8,
                  padding: '6px 0',
                  opacity: t.status === 'completed' ? 0.5 : 1,
                  transition: 'opacity 0.3s ease',
                }}
              >
                <span style={{
                  flexShrink: 0, marginTop: 1, fontSize: 11,
                  color: t.status === 'completed' ? 'var(--green)'
                    : t.status === 'in_progress' ? 'var(--yellow)'
                    : 'var(--text-3)',
                }}>
                  {STATUS_ICON[t.status]}
                </span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <span style={{
                    fontSize: 11, color: 'var(--text-2)',
                    textDecoration: t.status === 'completed' ? 'line-through' : 'none',
                    lineHeight: 1.3, wordBreak: 'break-word',
                  }}>
                    {t.content}
                  </span>
                  {t.status === 'in_progress' && (
                    <div style={{
                      display: 'flex', alignItems: 'center', gap: 4,
                      marginTop: 3,
                    }}>
                      <Loader2 size={10} color="var(--accent)" style={{ animation: 'spin 1s linear infinite' }} />
                      {t.activeForm && (
                        <span style={{
                          fontSize: 10, color: 'var(--accent)',
                        }}>
                          {t.activeForm}
                        </span>
                      )}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
