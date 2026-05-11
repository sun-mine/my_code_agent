import { useQuery } from '@tanstack/react-query'
import { api } from '../../lib/api'
import type { TodoItem } from '../../lib/types'
import { Badge } from '../ui/Button'

const ICON = { pending: '○', in_progress: '◉', completed: '●' }
const COLOR = {
  pending:    'text-[var(--text-3)]',
  in_progress:'text-[var(--yellow)]',
  completed:  'text-[var(--green)] line-through opacity-60',
}

export function TodoPanel() {
  const { data, isLoading } = useQuery({
    queryKey: ['todos'],
    queryFn: () => api.todos(),
    refetchInterval: 3000,
  })
  const todos: TodoItem[] = data?.todos ?? []
  const done = todos.filter(t => t.status === 'completed').length

  if (isLoading) return <p className="text-xs text-[var(--text-3)]">加载中…</p>
  if (todos.length === 0) return <p className="text-xs text-[var(--text-3)]">暂无 Todo</p>

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between mb-2">
        <Badge color={done === todos.length ? 'green' : 'yellow'}>
          {done}/{todos.length} 完成
        </Badge>
      </div>
      {todos.map((t, i) => (
        <div key={i} className="flex items-start gap-2 py-1">
          <span className={`text-xs mt-0.5 flex-shrink-0 ${COLOR[t.status]}`}>
            {ICON[t.status]}
          </span>
          <div className="flex-1 min-w-0">
            <p className={`text-xs leading-snug ${COLOR[t.status]}`}>{t.content}</p>
            {t.status === 'in_progress' && t.activeForm && (
              <p className="text-[10px] text-[var(--accent)] mt-0.5">{t.activeForm}</p>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}
