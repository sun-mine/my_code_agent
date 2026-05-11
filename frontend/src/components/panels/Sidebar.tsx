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
      <div style={{ padding: '10px 12px' }}>
        <button
          onClick={onNewSession}
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
            width: '100%', padding: '8px 0', borderRadius: 8,
            background: 'var(--accent)', border: 'none',
            color: '#fff', fontSize: 13, fontWeight: 600,
            cursor: 'pointer', transition: 'all 0.15s',
          }}
        >
          <Plus size={16} />
          新建对话
        </button>
      </div>

      {/* ── Tab Switcher ───────────────────────── */}
      <div style={{
        display: 'flex', gap: 0,
        padding: '0 12px', marginBottom: 4,
      }}>
        <button
          onClick={() => setTab('chats')}
          style={{
            flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 5,
            padding: '7px 0', fontSize: 12, fontWeight: tab === 'chats' ? 600 : 400,
            background: tab === 'chats' ? 'var(--bg-raised)' : 'none',
            border: 'none', borderBottom: tab === 'chats' ? '2px solid var(--accent)' : '2px solid transparent',
            color: tab === 'chats' ? 'var(--text-1)' : 'var(--text-3)',
            cursor: 'pointer', borderRadius: '6px 6px 0 0',
            transition: 'all 0.15s',
          }}
        >
          <MessageSquare size={14} />
          对话
        </button>
        <button
          onClick={() => setTab('project')}
          style={{
            flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 5,
            padding: '7px 0', fontSize: 12, fontWeight: tab === 'project' ? 600 : 400,
            background: tab === 'project' ? 'var(--bg-raised)' : 'none',
            border: 'none', borderBottom: tab === 'project' ? '2px solid var(--accent)' : '2px solid transparent',
            color: tab === 'project' ? 'var(--text-1)' : 'var(--text-3)',
            cursor: 'pointer', borderRadius: '6px 6px 0 0',
            transition: 'all 0.15s',
          }}
        >
          <FolderTree size={14} />
          项目
        </button>
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
