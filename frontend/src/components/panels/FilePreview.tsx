import { useState, useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../../lib/api'

interface FileEditorProps {
  path: string | null
  onClose: () => void
  onAddToChat?: (path: string, content: string) => void
}

const LANG_MAP: Record<string, string> = {
  py: 'python', ts: 'typescript', tsx: 'typescript',
  js: 'javascript', jsx: 'javascript', json: 'json',
  md: 'markdown', sh: 'bash', yaml: 'yaml', yml: 'yaml',
  css: 'css', html: 'html', toml: 'toml', rs: 'rust',
  go: 'go', java: 'java', cpp: 'cpp', c: 'c',
}

export function FilePreview({ path, onClose, onAddToChat }: FileEditorProps) {
  const qc = useQueryClient()
  const [editMode, setEditMode] = useState(false)
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [saveMsg, setSaveMsg] = useState('')

  const { data, isLoading, error } = useQuery({
    queryKey: ['fileContent', path],
    queryFn: () => api.fileContent(path!),
    enabled: !!path,
  })

  useEffect(() => {
    if (data) setDraft(data.content)
  }, [data])

  useEffect(() => {
    setEditMode(false)
    setSaveMsg('')
  }, [path])

  if (!path) return null

  const ext = path.split('.').pop()?.toLowerCase() ?? ''
  const isText = !['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'ico', 'svg',
    'zip', 'gz', 'tar', 'exe', 'bin', 'pdf'].includes(ext)

  const handleSave = async () => {
    setSaving(true)
    try {
      await api.saveFile(path, draft)
      setSaveMsg('✓ 已保存')
      qc.invalidateQueries({ queryKey: ['fileContent', path] })
      setTimeout(() => setSaveMsg(''), 2000)
    } catch (e: unknown) {
      setSaveMsg(`✗ 保存失败: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setSaving(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') onClose()
  }

  return (
    <div
      style={{
        position: 'fixed', inset: 0, zIndex: 100,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: 'rgba(0,0,0,0.7)', backdropFilter: 'blur(4px)',
      }}
      onClick={onClose}
      onKeyDown={handleKeyDown}
    >
      <div
        style={{
          background: 'var(--bg-surface)',
          border: '1px solid var(--border-hi)',
          borderRadius: 12,
          width: '82vw', maxWidth: 1100,
          height: '85vh',
          display: 'flex', flexDirection: 'column',
          boxShadow: '0 24px 80px rgba(0,0,0,0.6)',
        }}
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '10px 16px',
          borderBottom: '1px solid var(--border)',
          background: 'var(--bg-raised)',
          borderRadius: '12px 12px 0 0',
        }}>
          <span style={{ fontSize: 11, color: 'var(--text-3)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            {LANG_MAP[ext] || ext || 'text'}
          </span>
          <span style={{ flex: 1, fontSize: 13, fontFamily: 'var(--font-mono)', color: 'var(--text-1)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {path}
          </span>
          <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
            {data && onAddToChat && (
              <button
                onClick={() => onAddToChat(path, data.content)}
                title="将文件内容添加为对话上下文"
                style={{
                  padding: '3px 10px', borderRadius: 5, fontSize: 12,
                  background: 'var(--accent)', border: 'none',
                  color: '#fff', cursor: 'pointer', fontWeight: 500,
                  whiteSpace: 'nowrap',
                }}
                onMouseOver={e => (e.currentTarget.style.background = 'var(--accent-2)')}
                onMouseOut={e => (e.currentTarget.style.background = 'var(--accent)')}
              >
                添加到对话
              </button>
            )}
            {isText && !editMode && (
              <button
                onClick={() => setEditMode(true)}
                style={{
                  padding: '3px 10px', borderRadius: 5, fontSize: 12,
                  background: 'var(--bg-overlay)', border: '1px solid var(--border-hi)',
                  color: 'var(--text-2)', cursor: 'pointer',
                }}
              >
                编辑
              </button>
            )}
            {editMode && (
              <>
                <span style={{ fontSize: 11, color: saveMsg.startsWith('✓') ? 'var(--green)' : 'var(--red)', alignSelf: 'center' }}>
                  {saveMsg}
                </span>
                <button
                  onClick={() => { setEditMode(false); if (data) setDraft(data.content) }}
                  style={{
                    padding: '3px 10px', borderRadius: 5, fontSize: 12,
                    background: 'none', border: '1px solid var(--border)',
                    color: 'var(--text-3)', cursor: 'pointer',
                  }}
                >
                  取消
                </button>
                <button
                  onClick={handleSave}
                  disabled={saving}
                  style={{
                    padding: '3px 10px', borderRadius: 5, fontSize: 12,
                    background: 'var(--accent)', border: 'none',
                    color: '#fff', cursor: saving ? 'not-allowed' : 'pointer',
                    opacity: saving ? 0.7 : 1,
                  }}
                >
                  {saving ? '保存中…' : '保存'}
                </button>
              </>
            )}
            <button
              onClick={onClose}
              style={{
                width: 26, height: 26, borderRadius: 5, fontSize: 16,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                background: 'none', border: 'none',
                color: 'var(--text-3)', cursor: 'pointer',
              }}
              onMouseOver={e => (e.currentTarget.style.color = 'var(--text-1)')}
              onMouseOut={e => (e.currentTarget.style.color = 'var(--text-3)')}
            >
              ×
            </button>
          </div>
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
          {isLoading && (
            <div style={{ padding: 24, color: 'var(--text-3)', fontSize: 13 }}>加载中…</div>
          )}
          {error && (
            <div style={{ padding: 24, color: 'var(--red)', fontSize: 13 }}>
              加载失败：{String(error)}
            </div>
          )}
          {data && isText && (
            editMode ? (
              <textarea
                value={draft}
                onChange={e => setDraft(e.target.value)}
                style={{
                  flex: 1, width: '100%', resize: 'none',
                  background: 'var(--bg-base)',
                  color: 'var(--text-1)',
                  fontFamily: 'var(--font-mono)',
                  fontSize: 13, lineHeight: 1.7,
                  border: 'none', outline: 'none',
                  padding: '16px 20px',
                  tabSize: 2,
                }}
                spellCheck={false}
                autoFocus
              />
            ) : (
              <pre style={{
                flex: 1, overflow: 'auto',
                margin: 0, padding: '16px 20px',
                fontFamily: 'var(--font-mono)',
                fontSize: 13, lineHeight: 1.7,
                color: 'var(--text-1)',
                background: 'var(--bg-base)',
                whiteSpace: 'pre',
                counterReset: 'line',
              }}>
                {data.content.split('\n').map((line, i) => (
                  <div key={i} style={{ display: 'flex', gap: 16 }}>
                    <span style={{ color: 'var(--text-3)', minWidth: 36, textAlign: 'right', userSelect: 'none', flexShrink: 0 }}>
                      {i + 1}
                    </span>
                    <span>{line || '\u00A0'}</span>
                  </div>
                ))}
              </pre>
            )
          )}
          {data && !isText && (
            <div style={{ padding: 32, color: 'var(--text-3)', fontSize: 13, textAlign: 'center' }}>
              二进制文件，无法预览
            </div>
          )}
        </div>

        {/* Footer */}
        {data && (
          <div style={{
            padding: '6px 16px',
            borderTop: '1px solid var(--border)',
            display: 'flex', gap: 16, alignItems: 'center',
            fontSize: 11, color: 'var(--text-3)',
          }}>
            <span>{data.content.split('\n').length} 行</span>
            <span>{data.content.length.toLocaleString()} 字符</span>
            <span>{ext.toUpperCase() || 'TEXT'}</span>
            {editMode && <span style={{ color: 'var(--yellow)' }}>● 编辑中</span>}
          </div>
        )}
      </div>
    </div>
  )
}

