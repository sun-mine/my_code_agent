import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { FileText, Check, X, MessageSquare, ChevronDown, ChevronUp } from 'lucide-react'

interface PlanReviewProps {
  content: string
  runId: string
  onApprove: (runId: string) => void
  onReject: (runId: string) => void
  onModify: (runId: string, feedback: string) => void
  onCancel?: () => void
}

export function PlanReview({ content, runId, onApprove, onReject, onModify, onCancel }: PlanReviewProps) {
  const [expanded, setExpanded] = useState(true)
  const [modifyMode, setModifyMode] = useState(false)
  const [feedback, setFeedback] = useState('')

  const handleModify = () => {
    if (!feedback.trim()) return
    onModify(runId, feedback.trim())
    setModifyMode(false)
    setFeedback('')
  }

  return (
    <div style={{
      margin: '12px 0', borderRadius: 12, overflow: 'hidden',
      background: 'var(--bg-surface)',
      border: '1px solid var(--accent)',
      boxShadow: '0 0 0 1px rgba(232,133,92,0.2), 0 4px 24px rgba(0,0,0,0.3)',
      animation: 'slideUp 0.25s ease',
    }}>
      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10,
        padding: '12px 16px',
        background: 'rgba(232,133,92,0.08)',
        borderBottom: expanded ? '1px solid rgba(232,133,92,0.2)' : 'none',
      }}>
        <div style={{
          width: 34, height: 34, borderRadius: 9, flexShrink: 0,
          background: 'rgba(232,133,92,0.15)', border: '1px solid rgba(232,133,92,0.3)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <FileText size={17} color="var(--accent)" />
        </div>

        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-1)', marginBottom: 1 }}>
            实施计划
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-3)' }}>
            智能体已生成详细计划，请审核后决定是否执行
          </div>
        </div>

        <button
          onClick={() => setExpanded(o => !o)}
          style={{
            background: 'none', border: 'none', cursor: 'pointer',
            color: 'var(--text-3)', padding: '4px',
            borderRadius: 4, display: 'flex',
          }}
        >
          {expanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </button>
      </div>

      {/* Plan content (rendered markdown) */}
      {expanded && (
        <div style={{
          padding: '16px 20px', maxHeight: 400, overflow: 'auto',
          background: 'var(--bg-raised)',
          borderBottom: modifyMode ? 'none' : '1px solid var(--border)',
        }}>
          <div className="md-content" style={{ fontSize: 13 }}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
          </div>
        </div>
      )}

      {/* Modify feedback input */}
      {modifyMode && (
        <div style={{ padding: '12px 16px', background: 'var(--bg-raised)', borderBottom: '1px solid var(--border)' }}>
          <div style={{ display: 'flex', gap: 8 }}>
            <textarea
              autoFocus
              value={feedback}
              onChange={e => setFeedback(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && e.ctrlKey) handleModify()
                if (e.key === 'Escape') { setModifyMode(false); setFeedback('') }
              }}
              placeholder="输入修改建议..."
              rows={2}
              style={{
                flex: 1, resize: 'none', background: 'var(--bg-base)',
                border: '1px solid var(--border-hi)', borderRadius: 8,
                padding: '8px 12px', fontSize: 13, color: 'var(--text-1)',
                fontFamily: 'inherit', outline: 'none',
              }}
            />
          </div>
          <div style={{ display: 'flex', gap: 6, marginTop: 8, justifyContent: 'flex-end' }}>
            <button
              onClick={() => { setModifyMode(false); setFeedback('') }}
              style={{
                padding: '5px 12px', borderRadius: 6, fontSize: 11,
                background: 'none', border: '1px solid var(--border)',
                color: 'var(--text-3)', cursor: 'pointer',
              }}
            >
              取消
            </button>
            <button
              onClick={handleModify}
              disabled={!feedback.trim()}
              style={{
                padding: '5px 14px', borderRadius: 6, fontSize: 11, fontWeight: 600,
                background: feedback.trim() ? 'var(--accent)' : 'var(--bg-overlay)',
                border: 'none', color: feedback.trim() ? '#fff' : 'var(--text-3)',
                cursor: feedback.trim() ? 'pointer' : 'not-allowed',
                display: 'flex', alignItems: 'center', gap: 4,
              }}
            >
              <MessageSquare size={12} />
              提交反馈
            </button>
          </div>
        </div>
      )}

      {/* Action buttons */}
      {!modifyMode && (
        <div style={{
          padding: '10px 16px', display: 'flex', gap: 8,
          background: 'var(--bg-surface)',
        }}>
          <button
            onClick={() => onReject(runId)}
            style={{
              display: 'flex', alignItems: 'center', gap: 5,
              padding: '8px 16px', borderRadius: 8, fontSize: 12, fontWeight: 500,
              background: 'var(--bg-raised)', border: '1px solid var(--border)',
              color: 'var(--red)', cursor: 'pointer',
              transition: 'all 0.15s',
            }}
          >
            <X size={14} />
            拒绝，重新规划
          </button>
          <button
            onClick={() => setModifyMode(true)}
            style={{
              display: 'flex', alignItems: 'center', gap: 5,
              padding: '8px 16px', borderRadius: 8, fontSize: 12, fontWeight: 500,
              background: 'var(--bg-raised)', border: '1px solid var(--border)',
              color: 'var(--text-2)', cursor: 'pointer',
              transition: 'all 0.15s',
            }}
          >
            <MessageSquare size={14} />
            修改建议
          </button>
          <div style={{ flex: 1 }} />
          {onCancel && (
            <button
              onClick={onCancel}
              style={{
                padding: '8px 12px', borderRadius: 8, fontSize: 12,
                background: 'none', border: 'none',
                color: 'var(--text-3)', cursor: 'pointer',
              }}
            >
              取消
            </button>
          )}
          <button
            onClick={() => onApprove(runId)}
            style={{
              display: 'flex', alignItems: 'center', gap: 5,
              padding: '8px 20px', borderRadius: 8, fontSize: 12, fontWeight: 600,
              background: 'var(--accent)', border: 'none',
              color: '#fff', cursor: 'pointer',
              transition: 'all 0.15s',
            }}
          >
            <Check size={14} />
            同意，开始执行
          </button>
        </div>
      )}
    </div>
  )
}
