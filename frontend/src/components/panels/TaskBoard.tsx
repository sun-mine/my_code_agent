import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../lib/api'
import type { Task, TaskStatus } from '../../lib/types'
import { Badge } from '../ui/Button'

const STATUS_MAP: { key: TaskStatus; label: string; color: 'gray' | 'yellow' | 'green' | 'red' }[] = [
  { key: 'pending',     label: '待处理', color: 'gray'   },
  { key: 'in_progress', label: '进行中', color: 'yellow' },
  { key: 'completed',   label: '已完成', color: 'green'  },
]

export function TaskBoard() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['tasks'],
    queryFn: () => api.tasks(),
    refetchInterval: 5000,
  })
  const tasks = data?.tasks ?? []

  const createMut = useMutation({
    mutationFn: (subject: string) => api.createTask(subject),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tasks'] }),
  })

  if (isLoading) return <SkeletonList />

  return (
    <div className="flex flex-col gap-4">
      {STATUS_MAP.map(({ key, label, color }) => {
        const group = tasks.filter(t => t.status === key)
        return (
          <div key={key}>
            <div className="flex items-center gap-2 mb-2">
              <Badge color={color}>{label}</Badge>
              <span className="text-[11px] text-[var(--text-3)]">{group.length}</span>
            </div>
            <div className="flex flex-col gap-1.5">
              {group.map(t => <TaskCard key={t.id} task={t} />)}
              {group.length === 0 && (
                <p className="text-[11px] text-[var(--text-3)] pl-1">暂无任务</p>
              )}
            </div>
          </div>
        )
      })}
      <button
        onClick={() => {
          const s = prompt('新任务主题：')
          if (s?.trim()) createMut.mutate(s.trim())
        }}
        className="w-full mt-2 text-xs text-[var(--text-3)] border border-dashed border-[var(--border)] rounded-[var(--radius)] py-1.5 hover:border-[var(--border-hi)] hover:text-[var(--text-2)] transition-colors"
      >
        + 新建任务
      </button>
    </div>
  )
}

function TaskCard({ task }: { task: Task }) {
  return (
    <div className="bg-[var(--bg-raised)] border border-[var(--border)] rounded-[var(--radius)] px-3 py-2">
      <div className="flex items-start justify-between gap-2">
        <span className="text-xs text-[var(--text-1)] font-medium leading-snug">
          #{task.id} {task.subject}
        </span>
      </div>
      {task.owner && (
        <p className="text-[10px] text-[var(--text-3)] mt-1">@{task.owner}</p>
      )}
      {task.blockedBy.length > 0 && (
        <p className="text-[10px] text-[var(--yellow)] mt-0.5">阻塞于 #{task.blockedBy.join(', #')}</p>
      )}
    </div>
  )
}

function SkeletonList() {
  return (
    <div className="flex flex-col gap-2">
      {[1,2,3].map(i => (
        <div key={i} className="h-12 bg-[var(--bg-raised)] rounded-[var(--radius)] animate-pulse-slow" />
      ))}
    </div>
  )
}
