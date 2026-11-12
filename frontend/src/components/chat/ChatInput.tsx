import { useState, useRef, useCallback, useEffect, type KeyboardEvent, type ClipboardEvent } from 'react'
import { Image, Paperclip, Plus, ChevronDown, ArrowUp, Square, X, Check, Eye, EyeOff } from 'lucide-react'
import { api } from '../../lib/api'
import type { AgentMode, FileContext, ImageAttachment, ModelInfo, SlashCommand } from '../../lib/types'
import { chatDrafts, type PendingMessage } from '../../lib/chatStore'
import './ChatInput.css'

interface ChatInputProps {
  sessionId?: string
  onSend: (text: string, attachments: { images: ImageAttachment[]; fileContexts: FileContext[] }) => Promise<boolean>
  onInterrupt: () => void
  onCancelPending: () => void
  onRetryPending: () => void
  onClearHistory?: () => void
  onCompress?: () => void
  busy: boolean
  stopping?: boolean
  pendingMessage?: PendingMessage
  disabled?: boolean
  sendDisabled?: boolean
  mode: AgentMode
  onModeChange: (mode: AgentMode) => void
  currentModel: string
  currentProvider: string
  onModelChange: (modelId: string, provider: string) => void
  availableModels: ModelInfo[]
  fileContexts: FileContext[]
  onFileContextToggle: (path: string, included: boolean) => void
  onFileContextAdd?: (ctx: FileContext) => void
  onFileContextRemove?: (path: string) => void
  preFill?: string
  onPreFillConsumed?: () => void
}

const MODES: { mode: AgentMode; label: string; desc: string }[] = [
  { mode: 'plan', label: 'Plan', desc: '先规划展示方案，征求意见确认后再行动执行' },
  { mode: 'auto', label: 'Auto', desc: '自动执行所有操作，仅极高危命令需要审批' },
  { mode: 'manual', label: 'Manual', desc: '写文件和命令需要审批，只读操作自动放行' },
]

const SLASH_COMMANDS: SlashCommand[] = [
  { trigger: '/file', label: '添加文件', description: '选择文件添加到对话上下文' },
  { trigger: '/fix', label: '修复代码', description: '修复当前文件的错误' },
  { trigger: '/explain', label: '解释代码', description: '解释当前文件的功能' },
  { trigger: '/search', label: '搜索项目', description: '在项目中搜索代码' },
  { trigger: '/model', label: '切换模型', description: '更换 AI 模型' },
  { trigger: '/clear', label: '清空对话', description: '清空当前对话历史' },
  { trigger: '/compress', label: '压缩对话', description: '压缩对话以节省 Token' },
]

export function ChatInput({
  sessionId, onSend, onInterrupt, onCancelPending, onRetryPending,
  onClearHistory, onCompress, busy, stopping, pendingMessage, disabled, sendDisabled,
  mode, onModeChange, currentModel, currentProvider, onModelChange, availableModels,
  fileContexts, onFileContextToggle, onFileContextAdd, onFileContextRemove,
  preFill, onPreFillConsumed,
}: ChatInputProps) {
  const [draft, setDraft] = useState(() => sessionId ? chatDrafts.get(sessionId)?.draft ?? '' : '')
  const [images, setImages] = useState<ImageAttachment[]>(() => sessionId ? chatDrafts.get(sessionId)?.images ?? [] : [])
  const [plusOpen, setPlusOpen] = useState(false)
  const [modelOpen, setModelOpen] = useState(false)
  const [modelSwitching, setModelSwitching] = useState(false)
  const [modelError, setModelError] = useState('')
  const [modeOpen, setModeOpen] = useState(false)
  const [focused, setFocused] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [slashState, setSlashState] = useState<{ query: string; selIdx: number } | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const composingRef = useRef(false)
  const submittingRef = useRef(false)
  const pendingRef = useRef(!!pendingMessage)
  const imageReadEpochRef = useRef(0)

  useEffect(() => {
    if (sessionId) chatDrafts.set(sessionId, draft, images)
  }, [sessionId, draft, images])

  useEffect(() => {
    pendingRef.current = !!pendingMessage
    if (pendingMessage) imageReadEpochRef.current += 1
  }, [pendingMessage])

  useEffect(() => {
    if (!preFill) return
    let cancelled = false
    queueMicrotask(() => {
      if (cancelled) return
      setDraft(preFill)
      onPreFillConsumed?.()
      textareaRef.current?.focus()
    })
    return () => { cancelled = true }
  }, [preFill, onPreFillConsumed])

  const send = useCallback(async () => {
    const text = draft.trim()
    if ((!text && images.length === 0) || disabled || sendDisabled || pendingMessage || submittingRef.current || composingRef.current) return
    imageReadEpochRef.current += 1
    setPlusOpen(false)
    submittingRef.current = true
    setSubmitting(true)
    try {
      const accepted = await onSend(text, { images, fileContexts: fileContexts.filter(fc => fc.included) })
      if (!accepted) return
      setDraft('')
      setImages([])
      if (textareaRef.current) textareaRef.current.style.height = 'auto'
    } finally {
      submittingRef.current = false
      setSubmitting(false)
    }
  }, [draft, images, fileContexts, disabled, sendDisabled, pendingMessage, onSend])

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.nativeEvent.isComposing || composingRef.current) return

    if (slashState) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSlashState(s => s ? { ...s, selIdx: Math.min(s.selIdx + 1, matchedCommands.length - 1) } : null)
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSlashState(s => s ? { ...s, selIdx: Math.max(s.selIdx - 1, 0) } : null)
        return
      }
      if (e.key === 'Enter') {
        e.preventDefault()
        handleSlashSelect(matchedCommands[slashState.selIdx])
        return
      }
      if (e.key === 'Escape') {
        setSlashState(null)
        return
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  const onInput = () => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 160) + 'px'

    // Slash command detection
    const value = el.value
    const cursorPos = el.selectionStart
    const textBefore = value.slice(0, cursorPos)
    const lines = textBefore.split('\n')
    const currentLine = lines[lines.length - 1]
    if (currentLine.startsWith('/') && currentLine.length >= 1) {
      setSlashState({ query: currentLine.slice(1), selIdx: 0 })
    } else {
      setSlashState(null)
    }
  }

  const matchedCommands = slashState
    ? SLASH_COMMANDS.filter(c => c.trigger.includes(slashState.query) || c.label.includes(slashState.query))
    : []

  const handleSlashSelect = (cmd: SlashCommand) => {
    setSlashState(null)
    switch (cmd.trigger) {
      case '/file':
        handleFileUpload()
        return
      case '/model':
        if (!busy && !disabled) setModelOpen(true)
        return
      case '/clear':
        onClearHistory?.()
        return
      case '/compress':
        onCompress?.()
        return
      default: {
        // Replace the /command text in draft with the expanded prompt
        const el = textareaRef.current
        if (el) {
          const cursorPos = el.selectionStart
          const textBefore = el.value.slice(0, cursorPos)
          const textAfter = el.value.slice(cursorPos)
          const lines = textBefore.split('\n')
          const lastLine = lines[lines.length - 1]
          const beforeSlash = textBefore.slice(0, textBefore.length - lastLine.length)
          const prompts: Record<string, string> = {
            '/fix': '请修复以下代码中的错误：',
            '/explain': '请解释以下代码的功能：',
            '/search': '请在项目中搜索：',
          }
          const replacement = prompts[cmd.trigger] || ''
          const newValue = beforeSlash + replacement + textAfter
          setDraft(newValue)
        }
      }
    }
  }

  const handleImagePaste = useCallback((e: ClipboardEvent<HTMLTextAreaElement>) => {
    if (submittingRef.current || pendingRef.current) return
    const items = e.clipboardData?.items
    if (!items) return
    const epoch = imageReadEpochRef.current
    for (let i = 0; i < items.length; i++) {
      if (items[i].type.startsWith('image/')) {
        e.preventDefault()
        const file = items[i].getAsFile()
        if (!file) continue
        const reader = new FileReader()
        reader.onload = () => {
          if (epoch !== imageReadEpochRef.current || submittingRef.current || pendingRef.current) return
          const dataUrl = reader.result as string
          setImages(prev => [...prev, {
            id: crypto.randomUUID(),
            dataUrl,
            base64Data: dataUrl.split(',')[1],
            mediaType: file.type,
            fileName: `paste.${file.type.split('/')[1]}`,
          }])
        }
        reader.readAsDataURL(file)
      }
    }
  }, [])

  const handleImageUpload = () => {
    if (submittingRef.current || pendingRef.current) return
    setPlusOpen(false)
    const epoch = imageReadEpochRef.current
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = 'image/*'
    input.multiple = true
    input.onchange = () => {
      if (epoch !== imageReadEpochRef.current || submittingRef.current || pendingRef.current) return
      const files = Array.from(input.files || [])
      files.forEach(file => {
        const reader = new FileReader()
        reader.onload = () => {
          if (epoch !== imageReadEpochRef.current || submittingRef.current || pendingRef.current) return
          const dataUrl = reader.result as string
          setImages(prev => [...prev, {
            id: crypto.randomUUID(),
            dataUrl,
            base64Data: dataUrl.split(',')[1],
            mediaType: file.type,
            fileName: file.name,
          }])
        }
        reader.readAsDataURL(file)
      })
    }
    input.click()
  }

  const handleFileUpload = () => {
    setPlusOpen(false)
    const input = document.createElement('input')
    input.type = 'file'
    input.multiple = true
    input.accept = '.txt,.md,.json,.py,.ts,.tsx,.js,.jsx,.yaml,.yml,.toml,.csv,.html,.css,.rs,.go,.java,.sh,.c,.cpp'
    input.onchange = async () => {
      const files = Array.from(input.files || [])
      for (const file of files) {
        const text = await file.text()
        const snippet = text.length > 4000 ? text.slice(0, 4000) + '\n... (truncated)' : text
        onFileContextAdd?.({ path: file.name, content: snippet, included: true })
      }
      textareaRef.current?.focus()
    }
    input.click()
  }

  const removeImage = (id: string) => {
    if (submittingRef.current || pendingRef.current) return
    setImages(prev => prev.filter(img => img.id !== id))
  }

  const handleModelSelect = async (model: ModelInfo) => {
    if (!model.configured || busy || disabled || modelSwitching) return
    setModelError('')
    setModelSwitching(true)
    try {
      const selected = await api.setModel(model.id, model.provider)
      onModelChange(selected.model, selected.provider)
      setModelOpen(false)
    } catch (error) {
      setModelError(error instanceof Error ? error.message : '切换模型失败，请重试')
    } finally {
      setModelSwitching(false)
    }
  }

  const modelLabel = availableModels.find(m => m.id === currentModel && m.provider === currentProvider)?.label || currentModel || '选择模型'

  return (
    <div className="chat-composer">
      {fileContexts.length > 0 && (
        <div style={{ display: 'flex', gap: 6, marginBottom: 10, flexWrap: 'wrap' }}>
          {fileContexts.map(fc => (
            <span key={fc.path} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, maxWidth: '100%', padding: '4px 9px', borderRadius: 8, background: fc.included ? 'var(--badge-accent)' : 'var(--bg-raised)', border: '1px solid var(--border)', color: fc.included ? 'var(--accent)' : 'var(--text-3)', fontSize: 11 }}>
              {fc.included ? <Eye size={12} /> : <EyeOff size={12} />}
              <button onClick={() => onFileContextToggle(fc.path, !fc.included)} title={fc.included ? '排除此文件' : '包含此文件'} style={{ border: 0, background: 'transparent', color: 'inherit', cursor: 'pointer', font: 'inherit', padding: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{fc.path}</button>
              {onFileContextRemove && <button className="icon-button" style={{ width: 18, height: 18 }} aria-label={`移除文件 ${fc.path}`} onClick={() => onFileContextRemove(fc.path)}><X size={12} /></button>}
            </span>
          ))}
        </div>
      )}
      <div className={`composer-card${focused ? ' is-focused' : ''}`} onFocusCapture={() => setFocused(true)} onBlurCapture={() => setFocused(false)}>
        {images.length > 0 && (
          <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
            {images.map(img => (
              <div key={img.id} style={{ position: 'relative', width: 56, height: 56, flexShrink: 0 }}>
                <img src={img.dataUrl} alt={img.fileName || '附加图片'} style={{ width: '100%', height: '100%', objectFit: 'cover', borderRadius: 8, border: '1px solid var(--border)' }} />
                <button className="icon-button" aria-label={`移除图片 ${img.fileName || ''}`} onClick={() => removeImage(img.id)} disabled={submitting || !!pendingMessage} style={{ position: 'absolute', top: -5, right: -5, width: 20, height: 20, borderRadius: '50%', background: 'var(--bg-overlay)', border: '1px solid var(--border-hi)' }}><X size={11} /></button>
              </div>
            ))}
          </div>
        )}
        <textarea
          className="composer-textarea"
          aria-label="输入消息"
          ref={textareaRef}
          value={draft}
          onChange={e => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          onInput={onInput}
          onPaste={handleImagePaste}
          onCompositionStart={() => { composingRef.current = true }}
          onCompositionEnd={() => { composingRef.current = false }}
          rows={2}
          disabled={disabled || submitting || !!pendingMessage}
          placeholder={busy ? '可输入下一条消息并加入队列…' : disabled ? '连接本地服务后，即可开始对话…' : '描述你的想法，或输入 / 查看指令…'}
        />
        <div className="composer-toolbar">
          <div className="composer-control">
            <button className="composer-tool-button" onClick={() => setPlusOpen(open => !open)} disabled={submitting || !!pendingMessage} title="添加内容" aria-label="添加内容" aria-expanded={plusOpen}><Plus size={18} /></button>
            {plusOpen && <>
              <div style={{ position: 'fixed', inset: 0, zIndex: 100 }} onClick={() => setPlusOpen(false)} />
              <div className="composer-menu composer-plus-menu">
                <button className="composer-menu-item" onClick={handleImageUpload} disabled={submitting || !!pendingMessage}><Image size={16} />添加图片</button>
                <button className="composer-menu-item" onClick={handleFileUpload}><Paperclip size={16} />添加文件</button>
              </div>
            </>}
          </div>
          <div className="composer-control">
            <button className="composer-model-button" onClick={() => setModelOpen(open => !open)} disabled={busy || disabled || modelSwitching} title={busy ? '任务进行中不可切换模型' : '切换模型'} aria-label="切换模型" aria-expanded={modelOpen}>
              <span>{modelSwitching ? '切换中…' : modelLabel.replace('claude-', '').replace('deepseek-v4-', 'V4 ').replace('deepseek-', '')}</span><ChevronDown size={11} />
            </button>
            {modelOpen && <>
              <div style={{ position: 'fixed', inset: 0, zIndex: 100 }} onClick={() => setModelOpen(false)} />
              <div className="composer-menu composer-model-menu">
                <p className="composer-menu-title">选择模型</p>
                {availableModels.length === 0 && <p className="composer-menu-title">连接本地服务后查看可用模型</p>}
                {availableModels.map(m => {
                  const selected = currentModel === m.id && currentProvider === m.provider
                  return (
                    <button
                      className={`composer-menu-item${selected ? ' is-selected' : ''}${!m.configured ? ' is-unconfigured' : ''}`}
                      key={`${m.provider}:${m.id}`}
                      onClick={() => { void handleModelSelect(m) }}
                      disabled={!m.configured || busy || disabled || modelSwitching}
                      title={m.configured ? `${m.provider} · ${m.id}` : `${m.provider} 缺少配置`}
                    >
                      {selected ? <Check size={12} color="var(--accent)" /> : <span className="composer-option-check" />}
                      <span>{m.label}</span>
                      <span className="composer-provider">{m.provider}{!m.configured && <small>缺少配置</small>}</span>
                    </button>
                  )
                })}
              </div>
            </>}
          </div>
          <div className="composer-actions">
            <div className="composer-control">
              <button className="composer-mode-button" onClick={() => setModeOpen(open => !open)} title={MODES.find(m => m.mode === mode)?.desc} aria-label="选择执行模式" aria-expanded={modeOpen}>
                {MODES.find(m => m.mode === mode)?.label || mode}<ChevronDown size={11} />
              </button>
              {modeOpen && <>
                <div style={{ position: 'fixed', inset: 0, zIndex: 100 }} onClick={() => setModeOpen(false)} />
                <div className="composer-menu composer-mode-menu">
                  <p className="composer-menu-title">执行模式</p>
                  {MODES.map(m => (
                    <button className={`composer-menu-item${mode === m.mode ? ' is-selected' : ''}`} key={m.mode} onClick={() => { onModeChange(m.mode); setModeOpen(false) }}>
                      {mode === m.mode ? <Check size={12} color="var(--accent)" /> : <span className="composer-option-check" />}
                      <span className="composer-option-copy"><strong>{m.label}</strong><small>{m.desc}</small></span>
                    </button>
                  ))}
                </div>
              </>}
            </div>
            {busy && (
              <button className="composer-stop" onClick={onInterrupt} disabled={stopping} title={stopping ? '正在停止' : '停止生成'}>
                <Square size={10} fill="currentColor" />{stopping ? '停止中…' : '停止'}
              </button>
            )}
            <button
              className="composer-send"
              onClick={() => { void send() }}
              disabled={(!draft.trim() && images.length === 0) || disabled || sendDisabled || submitting || !!pendingMessage}
              title={busy ? '加入发送队列' : '发送 (Enter)'}
              aria-label={busy ? '将消息加入发送队列' : '发送消息'}
            ><ArrowUp size={18} strokeWidth={2} /></button>
          </div>
        </div>
      </div>
      {modelError && (
        <div className="composer-model-error" role="alert">
          <span>模型切换失败：{modelError}</span>
          <button onClick={() => setModelError('')} aria-label="关闭模型切换错误"><X size={12} /></button>
        </div>
      )}
      {pendingMessage && (
        <div className={`composer-queue${pendingMessage.status === 'failed' ? ' is-error' : ''}`} role={pendingMessage.status === 'failed' ? 'alert' : 'status'}>
          <span>{pendingMessage.status === 'failed' ? `发送失败：${pendingMessage.error}` : pendingMessage.status === 'sending' ? '正在确认发送…' : '消息已排队，等待当前任务结束'}</span>
          {pendingMessage.status === 'failed' && <button onClick={onRetryPending}>重试</button>}
          {pendingMessage.status !== 'sending' && <button onClick={onCancelPending}>取消待发</button>}
        </div>
      )}
      {slashState && matchedCommands.length > 0 && <>
        <div style={{ position: 'fixed', inset: 0, zIndex: 100 }} onClick={() => setSlashState(null)} />
        <div className="composer-menu composer-slash-menu">
          {matchedCommands.map((cmd, i) => (
            <button className={`composer-menu-item${i === slashState.selIdx ? ' is-selected' : ''}`} key={cmd.trigger} onClick={() => handleSlashSelect(cmd)}>
              <span className="composer-slash-command">{cmd.trigger}</span><span>{cmd.label}</span><span className="composer-slash-description">{cmd.description}</span>
            </button>
          ))}
        </div>
      </>}
      <div className="composer-footer">
        <span className={`composer-connection${disabled ? ' is-offline' : ''}`}><i />{disabled ? '等待本地服务连接' : stopping ? '正在安全停止任务' : busy ? '正在处理你的任务' : '准备就绪，随时开始'}</span>
        <span className="composer-keyboard-hint">Enter 发送 <span aria-hidden="true">·</span> Shift + Enter 换行</span>
      </div>
    </div>
  )
}
