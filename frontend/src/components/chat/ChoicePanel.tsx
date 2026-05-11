import { useState } from 'react'
import { Check, ListChecks } from 'lucide-react'

interface PlanOption {
  id: string
  label: string
  description?: string | null
}

interface ChoicePanelProps {
  options: PlanOption[]
  onSelect: (optionId: string) => void
}

export function ChoicePanel({ options, onSelect }: ChoicePanelProps) {
  const [selected, setSelected] = useState<string | null>(null)
  const [submitted, setSubmitted] = useState(false)

  const handleConfirm = () => {
    if (!selected) return
    setSubmitted(true)
    onSelect(selected)
  }

  const handleOptionClick = (id: string) => {
    if (submitted) return
    setSelected(prev => prev === id ? null : id)
  }

  return (
    <div style={{
      margin: '12px 0', borderRadius: 12, overflow: 'hidden',
      background: 'var(--bg-surface)',
      border: '1px solid var(--accent-blue, #5a9cf6)',
      boxShadow: '0 0 0 1px rgba(90,156,246,0.2), 0 4px 24px rgba(0,0,0,0.3)',
      animation: 'slideUp 0.25s ease',
    }}>
      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10,
        padding: '12px 16px',
        background: 'rgba(90,156,246,0.08)',
        borderBottom: '1px solid rgba(90,156,246,0.2)',
      }}>
        <div style={{
          width: 34, height: 34, borderRadius: 9, flexShrink: 0,
          background: 'rgba(90,156,246,0.15)', border: '1px solid rgba(90,156,246,0.3)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <ListChecks size={17} color="var(--accent-blue, #5a9cf6)" />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-1)', marginBottom: 1 }}>
            请选择
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-3)' }}>
            点击选择一个方案，然后确认
          </div>
        </div>
      </div>

      {/* Options */}
      <div style={{
        padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: 8,
        background: 'var(--bg-raised)',
      }}>
        {options.map(opt => {
          const isSel = selected === opt.id
          return (
            <button
              key={opt.id}
              onClick={() => handleOptionClick(opt.id)}
              disabled={submitted}
              style={{
                display: 'flex', alignItems: 'flex-start', gap: 10,
                width: '100%', padding: '10px 12px',
                borderRadius: 8, border: isSel
                  ? '2px solid var(--accent-blue, #5a9cf6)'
                  : '1px solid var(--border)',
                background: isSel
                  ? 'rgba(90,156,246,0.08)'
                  : 'var(--bg-surface)',
                cursor: submitted ? 'default' : 'pointer',
                textAlign: 'left',
                transition: 'all 0.15s',
                opacity: submitted && !isSel ? 0.4 : 1,
              }}
            >
              {/* Radio indicator */}
              <div style={{
                width: 18, height: 18, borderRadius: '50%', flexShrink: 0,
                border: isSel ? '2px solid var(--accent-blue, #5a9cf6)' : '2px solid var(--text-3)',
                background: isSel ? 'var(--accent-blue, #5a9cf6)' : 'transparent',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                marginTop: 1,
              }}>
                {isSel && <Check size={11} color="#fff" strokeWidth={3} />}
              </div>
              <div>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-1)' }}>
                  {opt.label}
                </div>
                {opt.description && (
                  <div style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 3, lineHeight: 1.4 }}>
                    {opt.description}
                  </div>
                )}
              </div>
            </button>
          )
        })}
      </div>

      {/* Action */}
      <div style={{
        padding: '10px 16px', display: 'flex', gap: 8, justifyContent: 'flex-end',
        background: 'var(--bg-surface)',
      }}>
        <button
          onClick={handleConfirm}
          disabled={!selected || submitted}
          style={{
            display: 'flex', alignItems: 'center', gap: 5,
            padding: '8px 20px', borderRadius: 8, fontSize: 12, fontWeight: 600,
            background: selected ? 'var(--accent-blue, #5a9cf6)' : 'var(--bg-overlay)',
            border: 'none',
            color: selected ? '#fff' : 'var(--text-3)',
            cursor: selected && !submitted ? 'pointer' : 'not-allowed',
            transition: 'all 0.15s',
          }}
        >
          <Check size={14} />
          {submitted ? '已确认' : '确认选择'}
        </button>
      </div>
    </div>
  )
}
