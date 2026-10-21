import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight, Folder, FolderOpen, LoaderCircle, Plus, X } from 'lucide-react'
import { api } from '../../lib/api'

interface FolderPickerProps {
  initialPath?: string
  onSelect: (path: string) => void | Promise<void>
  onClose: () => void
}

export function FolderPicker({ initialPath, onSelect, onClose }: FolderPickerProps) {
  const [path, setPath] = useState(initialPath || '')
  const [selecting, setSelecting] = useState(false)
  const [selectError, setSelectError] = useState('')
  const [newItemType, setNewItemType] = useState<'folder' | 'file' | null>(null)
  const [newItemName, setNewItemName] = useState('')
  const [createError, setCreateError] = useState('')
  const [createMessage, setCreateMessage] = useState('')
  const queryClient = useQueryClient()
  const { data, isLoading, error: loadError } = useQuery({
    queryKey: ['folderPicker', path],
    queryFn: () => api.fsLs(path || undefined),
  })
  const currentPath = data?.path || path
  const error = loadError instanceof Error ? loadError.message : loadError ? '无法读取此目录' : ''

  const choose = async () => {
    setSelecting(true)
    setSelectError('')
    try {
      await onSelect(currentPath)
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : '切换到所选目录失败'
      setSelectError(message)
    } finally {
      setSelecting(false)
    }
  }

  const createItem = async () => {
    const name = newItemName.trim()
    if (!name || !currentPath) return
    setCreateError('')
    setCreateMessage('')
    try {
      const created = newItemType === 'folder'
        ? await api.fsMkdir(currentPath, name)
        : await api.fsTouch(currentPath, name)
      if (newItemType === 'folder') await queryClient.invalidateQueries({ queryKey: ['folderPicker', path] })
      setNewItemName('')
      setCreateMessage(`${newItemType === 'folder' ? '文件夹' : '文件'}已创建：${created.path}`)
    } catch (cause) {
      setCreateError(cause instanceof Error ? cause.message : '创建失败')
    }
  }

  return (
    <div className="folder-picker-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}>
      <section className="folder-picker" role="dialog" aria-modal="true" aria-labelledby="folder-picker-title">
        <header className="folder-picker-header">
          <div><FolderOpen size={17} /><div><strong id="folder-picker-title">选择项目文件夹</strong><span>之后的对话会在此目录中读取代码</span></div></div>
          <button onClick={onClose} aria-label="关闭"><X size={17} /></button>
        </header>
        <div className="folder-picker-path">
          <button onClick={() => { if (data?.parent) setPath(data.parent) }} disabled={!data?.parent || isLoading} title="上一级"><ChevronLeft size={15} /></button>
          <span title={currentPath}>{currentPath || '正在读取当前目录…'}</span>
        </div>
        <div className="folder-picker-list">
          {newItemType && (
            <form className="folder-picker-create" onSubmit={event => { event.preventDefault(); void createItem() }}>
              <input autoFocus aria-label={newItemType === 'folder' ? '新文件夹名称' : '新文件名称'} placeholder={newItemType === 'folder' ? '新文件夹名称' : '新文件名称，例如 notes.md'} value={newItemName} onChange={event => { setNewItemName(event.target.value); setCreateError(''); setCreateMessage('') }} />
              <button type="submit" disabled={!newItemName.trim()}>创建</button>
              <button type="button" className="folder-picker-create-cancel" onClick={() => { setNewItemType(null); setCreateError(''); setCreateMessage('') }}>关闭</button>
              {(createError || createMessage) && <span className={createError ? 'is-error' : 'is-success'}>{createError || createMessage}</span>}
            </form>
          )}
          {isLoading ? <div className="folder-picker-empty"><LoaderCircle size={15} className="animate-spin-fast" />正在读取文件夹…</div>
            : error ? <div className="folder-picker-empty">{error}</div>
              : (data?.items ?? []).length === 0 ? <div className="folder-picker-empty">此目录下没有子文件夹</div>
                : (data?.items ?? []).map(folder => (
                  <button key={folder.path} className="folder-picker-item" onClick={() => setPath(folder.path)}>
                    <Folder size={15} /><span>{folder.name}</span><ChevronRight size={13} />
                  </button>
                ))}
        </div>
        <footer className="folder-picker-footer">
          {(selectError || error) && <span className="folder-picker-error">{selectError || error}</span>}
          <button className="folder-picker-new-folder" onClick={() => { setNewItemType('folder'); setCreateError(''); setCreateMessage('') }} disabled={isLoading || !!error}><Plus size={14} />新建文件夹</button>
          <button className="folder-picker-new-folder" onClick={() => { setNewItemType('file'); setCreateError(''); setCreateMessage('') }} disabled={isLoading || !!error}><Plus size={14} />新建文件</button>
          <button className="folder-picker-cancel" onClick={onClose}>取消</button>
          <button className="folder-picker-confirm" onClick={() => void choose()} disabled={isLoading || selecting || !!error || !currentPath}>
            {selecting ? '切换中…' : '选择此文件夹'}
          </button>
        </footer>
      </section>
    </div>
  )
}
