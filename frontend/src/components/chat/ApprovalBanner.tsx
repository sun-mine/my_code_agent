import { useState } from 'react'
import { ShieldAlert, ChevronDown, ChevronUp, AlertTriangle, Terminal, FileText, Bot } from 'lucide-react'
import type { ApprovalRequest } from '../../hooks/useChat'
import { ICON_SM } from '../../lib/icons'

interface ApprovalBannerProps {
  approval: ApprovalRequest
  onApprove: (id: string, approved: boolean) => void
}

function getToolLabel(name: string): string {
  const labels: Record<string, string> = {
    bash: '终端命令', background_run: '后台命令',
    write_file: '写入文件', edit_file: '编辑文件',
    task: '子任务调度', safety_checkpoint: '安全快照',
  }
  return labels[name] || name
}

function getToolIcon(name: string) {
  switch (name) {
    case 'bash': return Terminal
    case 'background_run': return Terminal
    case 'write_file': return FileText
    case 'edit_file': return FileText
    case 'task': return Bot
    default: return AlertTriangle
  }
}

function getRiskLevel(name: string, input: Record<string, unknown>): { level: string; color: string; bg: string } {
  const cmd = String(input.command || input.prompt || '')
  if (/\brm\s+-rf?\b|\bsudo\b|\bchmod\s+.*777\b|\bgit\s+push\s+.*-f\b|\bgit\s+reset\s+--hard\b|\bdd\s+if=\b|\bmkfs\b|\bshutdown\b|\breboot\b/.test(cmd)) {
    return { level: '高风险', color: 'var(--red)', bg: 'rgba(255,100,100,0.08)' }
  }
  if (name === 'bash' || name === 'background_run') {
    return { level: '中风险', color: 'var(--yellow)', bg: 'rgba(250,200,80,0.07)' }
  }
  return { level: '低风险', color: 'var(--green)', bg: 'rgba(134,239,172,0.06)' }
}

export function ApprovalBanner({ approval, onApprove }: ApprovalBannerProps) {
  const [expanded, setExpanded] = useState(true)
  const ToolIcon = getToolIcon(approval.name)
  const risk = getRiskLevel(approval.name, approval.input)

  const summary = (() => {
    const inp = approval.input
    if (inp.command) return String(inp.command)
    if (inp.path) return `${String(inp.path)}`
    if (inp.prompt) return String(inp.prompt).slice(0, 80)
    const keys = Object.keys(inp)
    if (keys.length > 0) return `${keys[0]}: ${JSON.stringify(inp[keys[0]]).slice(0, 80)}`
    return JSON.stringify(inp).slice(0, 80)
  })()

  return (
    <div style={{
      margin: '6px 0', borderRadius: 10, overflow: 'hidden',
      background: 'var(--bg-surface)',
      border: `1px solid ${risk.color}33`,
      boxShadow: `0 2px 12px rgba(0,0,0,0.2), 0 0 0 1px ${risk.color}15`,
      animation: 'slideUp 0.2s ease',
    }}>
      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10,
        padding: '10px 14px',
        background: risk.bg,
        borderBottom: expanded ? `1px solid ${risk.color}22` : 'none',
      }}>
        <div style={{
          width: 32, height: 32, borderRadius: 8, flexShrink: 0,
          background: `${risk.color}20`, border: `1px solid ${risk.color}40`,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <ToolIcon size={16} color={risk.color} />
        </div>

        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-1)' }}>
              {getToolLabel(approval.name)}
            </span>
            <span style={{
              fontSize: 10, fontWeight: 600, padding: '1px 6px', borderRadius: 4,
              background: `${risk.color}20`, color: risk.color,
            }}>
              {risk.level}
            </span>
          </div>
          <div style={{
            fontSize: 11, color: 'var(--text-3)',
            fontFamily: 'var(--font-mono)',
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          }}>
            {summary.slice(0, 100)}{summary.length > 100 ? '…' : ''}
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
          {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </button>

        <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
          <button
            onClick={() => onApprove(approval.id, false)}
            style={{
              padding: '6px 14px', borderRadius: 7, fontSize: 12, fontWeight: 500,
              background: 'var(--bg-raised)', border: '1px solid var(--border)',
              color: 'var(--text-2)', cursor: 'pointer',
              transition: 'all 0.15s',
            }}
          >
            拒绝
          </button>
          <button
            onClick={() => onApprove(approval.id, true)}
            style={{
              padding: '6px 18px', borderRadius: 7, fontSize: 12, fontWeight: 600,
              background: risk.level === '高风险' ? 'var(--red)' : 'var(--accent)',
              border: 'none', color: '#fff', cursor: 'pointer',
              transition: 'all 0.15s',
              display: 'flex', alignItems: 'center', gap: 4,
            }}
          >
            允许执行
          </button>
        </div>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div style={{ padding: '12px 14px', background: 'var(--bg-raised)' }}>
          <div style={{ marginBottom: 8 }}>
            <span style={{ fontSize: 10, color: 'var(--text-3)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              工具参数
            </span>
          </div>
          <pre style={{
            margin: 0, padding: '10px 12px', borderRadius: 8,
            background: 'var(--bg-base)', border: '1px solid var(--border)',
            fontFamily: 'var(--font-mono)', fontSize: 12,
            color: 'var(--text-1)', whiteSpace: 'pre-wrap', wordBreak: 'break-all',
            maxHeight: 200, overflow: 'auto', lineHeight: 1.6,
          }}>
            {JSON.stringify(approval.input, null, 2)}
          </pre>
        </div>
      )}
    </div>
  )
}
