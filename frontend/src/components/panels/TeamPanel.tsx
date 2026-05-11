import { useQuery } from '@tanstack/react-query'
import { api } from '../../lib/api'
import type { TeamMember } from '../../lib/types'
import { Badge } from '../ui/Button'

const STATUS_COLOR = {
  working:  'green' as const,
  idle:     'gray' as const,
  shutdown: 'red' as const,
}
const STATUS_LABEL = { working: '运行中', idle: '空闲', shutdown: '已停止' }

export function TeamPanel() {
  const { data, isLoading } = useQuery({
    queryKey: ['team'],
    queryFn: () => api.team(),
    refetchInterval: 5000,
  })
  const members: TeamMember[] = data?.members ?? []

  if (isLoading) return <p className="text-xs text-[var(--text-3)]">加载中…</p>
  if (members.length === 0) return <p className="text-xs text-[var(--text-3)]">暂无团队成员</p>

  return (
    <div className="flex flex-col gap-2">
      <p className="text-[10px] text-[var(--text-3)] uppercase tracking-wide">
        {data?.team_name}
      </p>
      {members.map(m => (
        <div key={m.name} className="bg-[var(--bg-raised)] border border-[var(--border)] rounded-[var(--radius)] px-3 py-2">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-medium text-[var(--text-1)]">{m.name}</span>
            <Badge color={STATUS_COLOR[m.status] ?? 'gray'}>
              {STATUS_LABEL[m.status] ?? m.status}
            </Badge>
          </div>
          <p className="text-[10px] text-[var(--text-3)] mt-0.5">{m.role}</p>
        </div>
      ))}
    </div>
  )
}
