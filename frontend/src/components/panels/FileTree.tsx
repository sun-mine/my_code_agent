import { useState, useCallback } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Folder, FolderOpen, ChevronRight, ChevronDown, RefreshCw, Home } from 'lucide-react'
import { api } from '../../lib/api'
import { getFileIcon } from '../../lib/icons'
import { ICON_SM } from '../../lib/icons'
import type { FileNode } from '../../lib/types'

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}K`
  return `${(bytes / 1024 / 1024).toFixed(1)}M`
}

interface FileTreeProps {
  onSelectFile: (path: string) => void
  workdir?: string
  onWorkdirChange?: (path: string) => void
}

export function FileTree({ onSelectFile, workdir, onWorkdirChange }: FileTreeProps) {
  const qc = useQueryClient()
  const [dragOver, setDragOver] = useState(false)

  const { data, isLoading, error } = useQuery({
    queryKey: ['fileTree', workdir],
    queryFn: () => api.fileTree(3),
    refetchInterval: 30000,
    enabled: !!workdir,
  })

  // 生成面包屑路径段
  const breadcrumbs = workdir
    ? workdir.split('/').filter(Boolean).map((seg, i, arr) => ({
        label: seg,
        path: '/' + arr.slice(0, i + 1).join('/'),
      }))
    : []

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragOver(true)
  }, [])

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragOver(false)
  }, [])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragOver(false)
    // 尝试从 dropped items 获取路径
    const items = Array.from(e.dataTransfer.items)
    for (const item of items) {
      if (item.kind === 'file') {
        const file = (item as DataTransferItem & { getAsFile: () => File }).getAsFile?.()
        if (file && 'path' in file) {
          const path = (file as File & { path: string }).path
          // 如果是目录，去掉文件名
          const dirPath = path.lastIndexOf('/') > 0 ? path.substring(0, path.lastIndexOf('/')) : path
          onWorkdirChange?.(dirPath)
          return
        }
      }
    }
  }, [onWorkdirChange])

  if (isLoading) {
    return (
      <div style={{ padding: '12px 8px', color: 'var(--text-3)', fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
        <RefreshCw size={ICON_SM} style={{ animation: 'spin 0.8s linear infinite' }} />
        加载目录中…
      </div>
    )
  }

  if (error || !data?.tree) {
    const errMsg = error instanceof Error ? error.message : '未知错误'
    return (
      <div style={{ padding: '12px 8px' }}>
        <p style={{ color: 'var(--red)', fontSize: 12, margin: 0 }}>目录加载失败{errMsg ? `: ${errMsg}` : ''}</p>
        {!workdir && (
          <p style={{ color: 'var(--text-3)', fontSize: 11, margin: '4px 0 0' }}>工作目录未设置</p>
        )}
        <button
          onClick={() => qc.invalidateQueries({ queryKey: ['fileTree'] })}
          style={{
            marginTop: 8, fontSize: 11, color: 'var(--accent)',
            background: 'none', border: 'none', cursor: 'pointer',
            display: 'flex', alignItems: 'center', gap: 4,
          }}
        >
          <RefreshCw size={10} />
          重试
        </button>
      </div>
    )
  }

  return (
    <div
      style={{ display: 'flex', flexDirection: 'column', height: '100%' }}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {/* Breadcrumb bar */}
      <div style={{
        padding: '6px 8px',
        borderBottom: '1px solid var(--border)',
        background: 'var(--bg-raised)',
      }}>
        <div style={{
          display: 'flex', alignItems: 'center', gap: 2,
          flexWrap: 'wrap',
        }}>
          {/* Home / root */}
          <button
            onClick={() => onWorkdirChange?.('/')}
            title="根目录"
            style={{
              display: 'flex', alignItems: 'center', padding: '2px 3px',
              background: 'none', border: 'none', cursor: 'pointer',
              color: 'var(--text-3)', borderRadius: 3,
              flexShrink: 0,
            }}
          >
            <Home size={12} />
          </button>

          {breadcrumbs.map((crumb, i) => (
            <span key={crumb.path} style={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <ChevronRight size={10} color="var(--text-3)" />
              <button
                onClick={() => onWorkdirChange?.(crumb.path)}
                title={crumb.path}
                style={{
                  padding: '2px 4px', borderRadius: 3,
                  background: i === breadcrumbs.length - 1 ? 'var(--bg-overlay)' : 'none',
                  border: 'none', cursor: 'pointer',
                  color: i === breadcrumbs.length - 1 ? 'var(--text-1)' : 'var(--text-2)',
                  fontSize: 11, fontFamily: 'var(--font-mono)',
                  fontWeight: i === breadcrumbs.length - 1 ? 600 : 400,
                  maxWidth: 120, overflow: 'hidden', textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {crumb.label}
              </button>
            </span>
          ))}
        </div>
      </div>

      {/* Toolbar */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 4,
        padding: '4px 8px',
        borderBottom: '1px solid var(--border)',
      }}>
        <button
          onClick={() => qc.invalidateQueries({ queryKey: ['fileTree'] })}
          title="刷新文件树"
          style={{
            display: 'flex', alignItems: 'center', gap: 4,
            padding: '3px 8px', borderRadius: 5,
            background: 'var(--bg-raised)', border: '1px solid var(--border)',
            color: 'var(--text-2)', fontSize: 11,
            cursor: 'pointer', flex: 1,
          }}
        >
          <RefreshCw size={12} />
          <span>刷新</span>
        </button>
      </div>

      {/* File tree content */}
      <div
        style={{
          flex: 1, overflow: 'auto',
          fontSize: 12, fontFamily: 'var(--font-mono)',
          background: dragOver ? 'rgba(232,133,92,0.08)' : 'transparent',
          transition: 'background 0.15s',
          border: dragOver ? '1px dashed var(--accent)' : '1px solid transparent',
          margin: dragOver ? 4 : 0,
          borderRadius: dragOver ? 6 : 0,
        }}
      >
        <div style={{
          padding: '4px 8px 4px',
          fontSize: 10, color: 'var(--text-3)', letterSpacing: '0.05em',
          textTransform: 'uppercase', fontFamily: 'sans-serif',
        }}>
          文件资源管理器
        </div>
        {data.tree.children?.map(child => (
          <TreeNode
            key={child.path}
            node={child}
            depth={0}
            onSelectFile={onSelectFile}
          />
        ))}
        {dragOver && (
          <div style={{
            padding: '24px 12px', textAlign: 'center',
            color: 'var(--accent)', fontSize: 12, fontWeight: 600,
            pointerEvents: 'none',
          }}>
            释放以切换工作目录
          </div>
        )}
      </div>
    </div>
  )
}

function TreeNode({ node, depth, onSelectFile }: {
  node: FileNode
  depth: number
  onSelectFile: (path: string) => void
}) {
  const [open, setOpen] = useState(depth < 1)
  const [loading, setLoading] = useState(false)
  const [children, setChildren] = useState<FileNode[] | null>(
    node.children ?? null
  )
  // Track if we've tried loading children for empty directories
  const [loaded, setLoaded] = useState(node.children !== null && node.children !== undefined)

  const indent = depth * 14 + 6

  const handleDirClick = async () => {
    if (!open) {
      setOpen(true)
      // Only fetch if we haven't loaded children yet and none exist
      if (!loaded && (!children || children.length === 0)) {
        setLoading(true)
        try {
          const data = await api.listDirectory(node.path)
          if (data.items) {
            const withChildren = data.items.map(item => ({
              ...item,
              children: item.type === 'dir' ? [] : undefined,
            }))
            setChildren(withChildren as FileNode[])
            setLoaded(true)
          }
        } catch {
          // ignore - directory might be empty or inaccessible
          setLoaded(true)
        } finally {
          setLoading(false)
        }
      }
    } else {
      setOpen(false)
    }
  }

  if (node.type === 'file') {
    const FileIcon = getFileIcon(node.name)
    return (
      <button
        onClick={() => onSelectFile(node.path)}
        title={node.path}
        style={{
          display: 'flex', alignItems: 'center', gap: 5,
          width: '100%', padding: `2px 8px 2px ${indent}px`,
          background: 'none', border: 'none', cursor: 'pointer',
          color: 'var(--text-2)', textAlign: 'left',
          borderRadius: 4, transition: 'background 0.1s',
        }}
        onMouseOver={e => (e.currentTarget.style.background = 'var(--bg-overlay)')}
        onMouseOut={e => (e.currentTarget.style.background = 'none')}
      >
        <FileIcon size={ICON_SM} style={{ flexShrink: 0 }} />
        <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {node.name}
        </span>
        {node.size !== undefined && (
          <span style={{ fontSize: 10, color: 'var(--text-3)', flexShrink: 0 }}>
            {formatSize(node.size)}
          </span>
        )}
      </button>
    )
  }

  // Directory
  return (
    <div>
      <button
        onClick={handleDirClick}
        style={{
          display: 'flex', alignItems: 'center', gap: 5,
          width: '100%', padding: `2px 8px 2px ${indent}px`,
          background: 'none', border: 'none', cursor: 'pointer',
          color: 'var(--text-1)', textAlign: 'left',
          borderRadius: 4, transition: 'background 0.1s',
          fontWeight: 500,
        }}
        onMouseOver={e => (e.currentTarget.style.background = 'var(--bg-overlay)')}
        onMouseOut={e => (e.currentTarget.style.background = 'none')}
      >
        {loading ? (
          <RefreshCw size={ICON_SM} style={{ flexShrink: 0, animation: 'spin 0.8s linear infinite' }} color="var(--text-3)" />
        ) : open ? (
          <ChevronDown size={ICON_SM} style={{ flexShrink: 0 }} color="var(--text-3)" />
        ) : (
          <ChevronRight size={ICON_SM} style={{ flexShrink: 0 }} color="var(--text-3)" />
        )}
        {open
          ? <FolderOpen size={ICON_SM} style={{ flexShrink: 0 }} color="var(--yellow)" />
          : <Folder size={ICON_SM} style={{ flexShrink: 0 }} color="var(--yellow)" />
        }
        <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {node.name}
        </span>
      </button>
      {open && (
        <div>
          {(children ?? []).map(child => (
            <TreeNode
              key={child.path}
              node={child}
              depth={depth + 1}
              onSelectFile={onSelectFile}
            />
          ))}
          {(children ?? []).length === 0 && !loading && (
            <div style={{ paddingLeft: indent + 20, fontSize: 11, color: 'var(--text-3)', padding: `2px 0 2px ${indent + 20}px` }}>
              空目录
            </div>
          )}
        </div>
      )}
    </div>
  )
}
