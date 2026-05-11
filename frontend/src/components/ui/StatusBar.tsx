import { Spinner } from '../ui/Button'
import { RefreshCw } from 'lucide-react'
import type { SocketStatus } from '../../hooks/useAgentSocket'
import { STATUS_LABEL } from '../../lib/utils'

interface StatusBarProps {
  socketStatus: SocketStatus
  agentState: 'idle' | 'thinking' | 'executing_tool'
  workdir?: string
  workdirError?: boolean
  model?: string
  historyLength: number
  onRefreshWorkdir?: () => void
}

export function StatusBar({ socketStatus, agentState, workdir, workdirError, model, historyLength, onRefreshWorkdir }: StatusBarProps) {
  const connColor =
    socketStatus === 'connected' ? 'var(--green)' :
    socketStatus === 'connecting' ? 'var(--yellow)' : 'var(--red)'

  const connLabel =
    socketStatus === 'connected' ? '已连接' :
    socketStatus === 'connecting' ? '连接中' : '断开连接'

  return (
    <div className="flex items-center gap-3 px-4 py-1.5 border-t border-[var(--border)] bg-[var(--bg-surface)] text-[10px] text-[var(--text-3)] select-none overflow-hidden">
      {/* 连接状态 */}
      <span className="flex items-center gap-1.5">
        <span style={{ background: connColor }} className="w-1.5 h-1.5 rounded-full flex-shrink-0" />
        {connLabel}
      </span>

      <span className="text-[var(--border-hi)]">·</span>

      {/* Agent 状态 */}
      <span className="flex items-center gap-1.5">
        {agentState !== 'idle' && <Spinner size={10} />}
        {STATUS_LABEL[agentState] ?? agentState}
      </span>

      {workdir && (
        <>
          <span className="text-[var(--border-hi)]">·</span>
          <span
            className="truncate max-w-[200px] flex items-center gap-1"
            title={workdir}
            style={{ color: workdirError ? 'var(--red)' : undefined }}
          >
            {workdir}
            {workdirError && onRefreshWorkdir && (
              <button
                onClick={onRefreshWorkdir}
                title="刷新工作目录"
                style={{
                  background: 'none', border: 'none', cursor: 'pointer',
                  color: 'var(--text-3)', padding: 0, display: 'flex',
                }}
              >
                <RefreshCw size={10} />
              </button>
            )}
          </span>
        </>
      )}

      {model && (
        <>
          <span className="text-[var(--border-hi)]">·</span>
          <span>{model}</span>
        </>
      )}

      <span className="ml-auto">{historyLength} 条消息</span>
    </div>
  )
}
