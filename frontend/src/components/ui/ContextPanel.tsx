import { useState, useEffect, useRef } from 'react'
import { Shrink, ChevronDown, ChevronRight, Gauge } from 'lucide-react'
import type { TokenBreakdown } from '../../lib/types'
import { ICON_SM } from '../../lib/icons'

interface ContextPanelProps {
  tokenBreakdown?: TokenBreakdown
  onCompress: () => void
  busy: boolean
}

function formatTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`
  return String(n)
}

export function ContextPanel({ tokenBreakdown, onCompress, busy }: ContextPanelProps) {
  const [open, setOpen] = useState(false)
  const [messagesExpanded, setMessagesExpanded] = useState(false)
  const panelRef = useRef<HTMLDivElement>(null)

  const bd = tokenBreakdown
  const total = bd?.estimated_tokens ?? 0
  const threshold = bd?.threshold ?? 100000
  const pct = bd?.usage_percent ?? 0
  const remaining = Math.max(0, threshold - total)

  const categories = [
    { key: 'system', label: 'System Instructions', tokens: bd?.system_tokens ?? 0, pct: total > 0 ? ((bd?.system_tokens ?? 0) / total * 100).toFixed(1) : '0.0' },
    { key: 'tools', label: 'Tool Definitions', tokens: bd?.tool_tokens ?? 0, pct: total > 0 ? ((bd?.tool_tokens ?? 0) / total * 100).toFixed(1) : '0.0' },
    { key: 'context', label: 'User Context', tokens: bd?.user_context_tokens ?? 0, pct: total > 0 ? ((bd?.user_context_tokens ?? 0) / total * 100).toFixed(1) : '0.0' },
    { key: 'messages', label: 'Messages', tokens: bd?.messages_tokens ?? 0, pct: total > 0 ? ((bd?.messages_tokens ?? 0) / total * 100).toFixed(1) : '0.0' },
  ]

  // Close on Escape
  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('keydown', handler as EventListener)
    return () => document.removeEventListener('keydown', handler as EventListener)
  }, [open])

  const handleCompress = () => {
    onCompress()
    setOpen(false)
  }

  return (
    <>
      {/* Trigger button */}
      <button
        onClick={() => setOpen(o => !o)}
        title="上下文窗口"
        style={{
          display: 'flex', alignItems: 'center', gap: 5,
          padding: '3px 10px', borderRadius: 5,
          background: open ? 'var(--bg-overlay)' : 'var(--bg-base)',
          border: `1px solid ${open ? 'var(--border-hi)' : 'var(--border)'}`,
          color: 'var(--text-2)', fontSize: 11,
          cursor: 'pointer',
          transition: 'all 0.15s',
          fontFamily: 'var(--font-mono)',
        }}
      >
        <Gauge size={ICON_SM} />
        <span>{formatTokens(total)}/{formatTokens(threshold)}</span>
        <span style={{ color: 'var(--text-3)' }}>·</span>
        <span style={{
          color: pct > 70 ? 'var(--red)' : 'var(--text-2)',
          fontWeight: pct > 70 ? 600 : 400,
        }}>
          {pct}%
        </span>
      </button>

      {/* Dropdown panel */}
      {open && (
        <>
          <div
            style={{ position: 'fixed', inset: 0, zIndex: 39 }}
            onClick={() => setOpen(false)}
          />
          <div
            ref={panelRef}
            style={{
              position: 'absolute', top: 44, right: 12, zIndex: 50,
              background: 'var(--bg-overlay)',
              border: '1px solid var(--border-hi)',
              borderRadius: 'var(--radius-lg)',
              padding: '0',
              width: 380,
              boxShadow: '0 8px 32px rgba(0,0,0,0.25)',
              display: 'flex', flexDirection: 'column',
            }}
          >
            {/* Title */}
            <div style={{
              padding: '14px 18px 10px',
              borderBottom: '1px solid var(--border)',
            }}>
              <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-1)' }}>
                上下文窗口
              </span>
            </div>

            {/* Overview: Progress bar */}
            <div style={{ padding: '14px 18px 10px', borderBottom: '1px solid var(--border)' }}>
              <div style={{
                height: 6, borderRadius: 99,
                background: 'var(--bg-raised)',
                overflow: 'hidden',
                marginBottom: 8,
              }}>
                <div style={{
                  height: '100%', borderRadius: 99,
                  width: `${Math.min(pct, 100)}%`,
                  background: pct > 70 ? 'var(--red)' : 'var(--accent)',
                  transition: 'width 0.5s ease',
                }} />
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12 }}>
                <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-1)', fontWeight: 600 }}>
                  {formatTokens(total)}/{formatTokens(threshold)} 个令牌 · {pct}%
                </span>
                <span style={{ color: 'var(--text-3)' }}>
                  保留用于响应 · {formatTokens(remaining)} 个令牌
                </span>
              </div>
            </div>

            {/* Category breakdown */}
            <div style={{ padding: '8px 0', borderBottom: '1px solid var(--border)' }}>
              {categories.map((cat, i) => (
                <div key={cat.key}>
                  {cat.key === 'messages' ? (
                    <>
                      <button
                        onClick={() => setMessagesExpanded(e => !e)}
                        style={{
                          display: 'flex', alignItems: 'center', gap: 6,
                          width: '100%', padding: '6px 18px',
                          background: 'none', border: 'none', cursor: 'pointer',
                          color: 'var(--text-1)', fontSize: 12, textAlign: 'left',
                        }}
                        onMouseOver={e => (e.currentTarget.style.background = 'var(--bg-raised)')}
                        onMouseOut={e => (e.currentTarget.style.background = 'none')}
                      >
                        {messagesExpanded
                          ? <ChevronDown size={12} color="var(--text-3)" />
                          : <ChevronRight size={12} color="var(--text-3)" />
                        }
                        <span style={{ flex: 1 }}>{cat.label}</span>
                        <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-2)', fontSize: 11, marginRight: 8 }}>
                          {formatTokens(cat.tokens)}
                        </span>
                        <span style={{ color: 'var(--text-3)', fontSize: 11, minWidth: 36, textAlign: 'right' }}>
                          {cat.pct}%
                        </span>
                      </button>
                      {messagesExpanded && (
                        <div style={{
                          marginLeft: 32,
                          borderLeft: '1px solid var(--border)',
                          paddingLeft: 8,
                        }}>
                          {[
                            { label: '用户消息', share: 0.2 },
                            { label: '助手消息', share: 0.5 },
                            { label: '工具结果', share: 0.3 },
                          ].map(sub => (
                            <div
                              key={sub.label}
                              style={{
                                display: 'flex', alignItems: 'center',
                                padding: '4px 14px',
                                fontSize: 11, color: 'var(--text-3)',
                              }}
                            >
                              <span style={{ flex: 1 }}>{sub.label}</span>
                              <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-3)' }}>
                                ~{formatTokens(Math.round(cat.tokens * sub.share))}
                              </span>
                            </div>
                          ))}
                        </div>
                      )}
                    </>
                  ) : (
                    <div style={{
                      display: 'flex', alignItems: 'center', gap: 6,
                      padding: '6px 18px',
                      color: 'var(--text-1)', fontSize: 12,
                    }}>
                      {cat.key === 'context' && (
                        <span style={{ width: 12, flexShrink: 0 }} />
                      )}
                      {cat.key !== 'context' && (
                        <span style={{
                          width: 4, height: 4, borderRadius: '50%',
                          background: cat.key === 'system' ? 'var(--blue)'
                            : cat.key === 'tools' ? 'var(--yellow)'
                            : 'var(--green)',
                          flexShrink: 0, marginLeft: cat.key === 'system' ? 0 : 4,
                        }} />
                      )}
                      <span style={{
                        flex: 1,
                        paddingLeft: cat.key !== 'system' ? 4 : 0,
                      }}>
                        {cat.label}
                      </span>
                      <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-2)', fontSize: 11, marginRight: 8 }}>
                        {formatTokens(cat.tokens)}
                      </span>
                      <span style={{ color: 'var(--text-3)', fontSize: 11, minWidth: 36, textAlign: 'right' }}>
                        {cat.pct}%
                      </span>
                    </div>
                  )}
                </div>
              ))}
            </div>

            {/* Compress button */}
            <div style={{ padding: '12px 18px' }}>
              <button
                onClick={handleCompress}
                disabled={busy}
                style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
                  width: '100%', padding: '8px 0', borderRadius: 8,
                  background: busy ? 'var(--bg-raised)' : 'var(--accent)',
                  border: 'none',
                  color: busy ? 'var(--text-3)' : '#fff',
                  fontSize: 13, fontWeight: 600,
                  cursor: busy ? 'not-allowed' : 'pointer',
                  opacity: busy ? 0.7 : 1,
                  transition: 'all 0.15s',
                }}
                onMouseOver={e => {
                  if (!busy) e.currentTarget.style.background = 'var(--accent-2)'
                }}
                onMouseOut={e => {
                  if (!busy) e.currentTarget.style.background = 'var(--accent)'
                }}
              >
                <Shrink size={14} />
                压缩对话
              </button>
            </div>
          </div>
        </>
      )}
    </>
  )
}
