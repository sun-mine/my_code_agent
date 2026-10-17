import { useState, useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { MessageSquare, FolderTree, Plus, Trash2, FileText } from 'lucide-react'
import { api } from '../../lib/api'
import { FileTree } from './FileTree'
import type { SessionInfo } from '../../lib/types'

interface SidebarProps {
  onSelectFile: (path: string) => void
  onNewSession: () => void
  onLoadSession: (sid: string) => void
  workdir?: string
  onWorkdirChange?: (path: string) => void
}

type Tab = 'chats' | 'project'

export function Sidebar({ onSelectFile, onNewSession, onLoadSession, workdir, onWorkdirChange }: SidebarProps) {
  const [tab, setTab] = useState<Tab>('project')
  const qc = useQueryClient()

  const { data: sessionsData, refetch: refetchSessions } = useQuery({
    queryKey: ['sessions'],
    queryFn: () => api.sessions(),
    refetchInterval: 10000,
  })

  const handleDeleteSession = async (sid: string, e: React.MouseEvent) => {
    e.stopPropagation()
    try {
      await api.deleteSession(sid)
      refetchSessions()
    } catch {
      // ignore
    }
  }

  const formatTime = (ts: number) => {
    const d = new Date(ts * 1000)
    const now = new Date()
    const diff = now.getTime() - d.getTime()
    if (diff < 3600000) return `${Math.round(diff / 60000)}分前`
    if (diff < 86400000) return `${Math.round(diff / 3600000)}小时前`
    return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
  }

  return (
    <aside style={{
      width: 280, flexShrink: 0,
      borderRight: '1px solid var(--border)',
      background: 'var(--bg-surface)',
      display: 'flex', flexDirection: 'column',
      overflow: 'hidden',
    }}>
      {/* ── New Session Button ──────────────────── */}
      <div style={{ padding: '12px 12px 10px' }}>
        <button
          onClick={onNewSession}
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
            width: '100%', padding: '9px 0', borderRadius: 10,
            background: 'linear-gradient(135deg, var(--accent) 0%, var(--accent-2) 100%)',
            border: 'none',
            color: '#fff', fontSize: 13, fontWeight: 600, letterSpacing: '0.02em',
            cursor: 'pointer', transition: 'all 0.18s ease',
            boxShadow: 'var(--shadow-sm), inset 0 1px 0 rgba(255,255,255,.18)',
          }}
          onMouseOver={e => {
            e.currentTarget.style.transform = 'translateY(-1px)'
            e.currentTarget.style.boxShadow = 'var(--shadow-md), inset 0 1px 0 rgba(255,255,255,.18)'
          }}
          onMouseOut={e => {
            e.currentTarget.style.transform = 'translateY(0)'
            e.currentTarget.style.boxShadow = 'var(--shadow-sm), inset 0 1px 0 rgba(255,255,255,.18)'
          }}
        >
          <Plus size={16} strokeWidth={2.4} />
          新建对话
        </button>
      </div>

      {/* ── Tab Switcher (segmented) ───────────── */}
      <div style={{
        display: 'flex', gap: 2,
        margin: '0 12px 8px', padding: 3,
        background: 'var(--bg-raised)',
        border: '1px solid var(--border)',
        borderRadius: 9,
      }}>
        {([
          { key: 'chats', icon: MessageSquare, label: '对话' },
          { key: 'project', icon: FolderTree, label: '项目' },
        ] as const).map(t => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            style={{
              flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 5,
              padding: '6px 0', fontSize: 12, fontWeight: tab === t.key ? 600 : 400,
              background: tab === t.key ? 'var(--bg-overlay)' : 'transparent',
              border: tab === t.key ? '1px solid var(--border-hi)' : '1px solid transparent',
              boxShadow: tab === t.key ? 'var(--shadow-sm)' : 'none',
              color: tab === t.key ? 'var(--text-1)' : 'var(--text-3)',
              cursor: 'pointer', borderRadius: 7,
              transition: 'all 0.15s',
            }}
          >
            <t.icon size={13} />
            {t.label}
          </button>
        ))}
      </div>

      {/* ── Tab Content ────────────────────────── */}
      <div style={{ flex: 1, overflow: 'auto' }}>
        {tab === 'chats' && (
          <div style={{ padding: '4px 0' }}>
            <p style={{
              fontSize: 10, color: 'var(--text-3)', padding: '4px 14px 8px',
              textTransform: 'uppercase', letterSpacing: '0.05em', margin: 0,
            }}>
              最近会话
            </p>
            {(sessionsData?.sessions ?? []).length === 0 && (
              <p style={{
                fontSize: 12, color: 'var(--text-3)', textAlign: 'center',
                padding: '20px 14px', margin: 0,
              }}>
                暂无历史会话
              </p>
            )}
            {(sessionsData?.sessions ?? []).map(s => (
              <button
                key={s.id}
                onClick={() => onLoadSession(s.id)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  width: '100%', padding: '8px 14px',
                  background: 'none', border: 'none',
                  cursor: 'pointer', textAlign: 'left',
                  transition: 'all 0.15s',
                }}
                onMouseOver={e => { e.currentTarget.style.background = 'var(--bg-raised)' }}
                onMouseOut={e => { e.currentTarget.style.background = 'none' }}
              >
                <FileText size={14} color="var(--text-3)" style={{ flexShrink: 0 }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{
                    fontSize: 12, color: 'var(--text-1)',
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    marginBottom: 2,
                  }}>
                    {s.title}
                  </div>
                  <div style={{ fontSize: 10, color: 'var(--text-3)', display: 'flex', gap: 8 }}>
                    <span>{formatTime(s.updated_at)}</span>
                    <span>{s.message_count} 条消息</span>
                  </div>
                </div>
                <button
                  onClick={(e) => handleDeleteSession(s.id, e)}
                  title="删除会话"
                  style={{
                    background: 'none', border: 'none', cursor: 'pointer',
                    color: 'var(--text-3)', padding: 2, display: 'flex',
                    opacity: 0.5,
                  }}
                  onMouseOver={e => { e.currentTarget.style.opacity = '1'; e.currentTarget.style.color = 'var(--red)' }}
                  onMouseOut={e => { e.currentTarget.style.opacity = '0.5'; e.currentTarget.style.color = 'var(--text-3)' }}
                >
                  <Trash2 size={12} />
                </button>
              </button>
            ))}
          </div>
        )}

        {tab === 'project' && (
          <div style={{ padding: '4px 0' }}>
            <FileTree
              onSelectFile={onSelectFile}
              workdir={workdir}
              onWorkdirChange={onWorkdirChange}
            />
          </div>
        )}
      </div>
    </aside>
  )
}
