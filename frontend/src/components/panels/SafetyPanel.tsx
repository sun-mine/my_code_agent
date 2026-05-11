import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../lib/api'
import type { Checkpoint } from '../../lib/types'
import { Button, Badge } from '../ui/Button'
import { formatBytes } from '../../lib/utils'

export function SafetyPanel() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['checkpoints'],
    queryFn: () => api.checkpoints(),
    refetchInterval: 10000,
  })
  const checkpoints: Checkpoint[] = data?.checkpoints ?? []

  const createMut = useMutation({
    mutationFn: (name: string) => api.createCheckpoint(name, '手动创建'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['checkpoints'] }),
  })

  const restoreMut = useMutation({
    mutationFn: (id: string) => api.restoreCheckpoint(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['checkpoints'] }),
  })

  const [confirmId, setConfirmId] = useState<string | null>(null)

  if (isLoading) return <p className="text-xs text-[var(--text-3)]">加载中…</p>

  return (
    <div className="flex flex-col gap-3">
      <Button
        size="sm"
        variant="default"
        onClick={() => {
          const n = prompt('快照名称：', `manual_${Date.now()}`)
          if (n?.trim()) createMut.mutate(n.trim())
        }}
        disabled={createMut.isPending}
      >
        {createMut.isPending ? '创建中…' : '+ 创建快照'}
      </Button>

      {checkpoints.length === 0 && (
        <p className="text-xs text-[var(--text-3)]">暂无快照</p>
      )}

      <div className="flex flex-col gap-2">
        {checkpoints.map(cp => (
          <div key={cp.id} className="bg-[var(--bg-raised)] border border-[var(--border)] rounded-[var(--radius)] px-3 py-2">
            <div className="flex items-start justify-between gap-1">
              <div className="min-w-0">
                <p className="text-xs font-medium text-[var(--text-1)] truncate">{cp.name}</p>
                <p className="text-[10px] text-[var(--text-3)]">
                  {cp.datetime?.slice(0, 16).replace('T', ' ')} · {formatBytes(cp.size)}
                </p>
                {cp.description && (
                  <p className="text-[10px] text-[var(--text-3)] truncate">{cp.description}</p>
                )}
              </div>
              <Badge color="gray">
                {cp.paths.length} 文件
              </Badge>
            </div>

            {confirmId === cp.id ? (
              <div className="mt-2 flex gap-1.5">
                <button
                  onClick={() => { restoreMut.mutate(cp.id); setConfirmId(null) }}
                  className="text-[10px] px-2 py-0.5 rounded bg-[var(--red)]/20 text-[var(--red)] border border-[var(--red)]/30 hover:bg-[var(--red)]/30"
                >
                  {restoreMut.isPending ? '恢复中…' : '确认恢复'}
                </button>
                <button
                  onClick={() => setConfirmId(null)}
                  className="text-[10px] px-2 py-0.5 rounded bg-[var(--bg-overlay)] text-[var(--text-3)] border border-[var(--border)] hover:text-[var(--text-2)]"
                >
                  取消
                </button>
              </div>
            ) : (
              <button
                onClick={() => setConfirmId(cp.id)}
                className="mt-2 text-[10px] text-[var(--text-3)] hover:text-[var(--accent)] transition-colors"
              >
                ↩ 恢复到此快照
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
