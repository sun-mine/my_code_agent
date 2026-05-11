import { useState, useRef, useCallback, useEffect, type KeyboardEvent, type ClipboardEvent } from 'react'
import { Image, Paperclip, ChevronDown, ArrowUp, Square, X, Check, Eye, EyeOff } from 'lucide-react'
import { Spinner } from '../ui/Button'
import { api } from '../../lib/api'
import { ICON_SM } from '../../lib/icons'
import type { AgentMode, FileContext, ImageAttachment, ModelInfo, SlashCommand, ContentBlock } from '../../lib/types'

interface ChatInputProps {
  onSend: (text: string, attachments: { images: ImageAttachment[]; fileContexts: FileContext[] }) => void
  onInterrupt: () => void
  onClearHistory?: () => void
  onCompress?: () => void
  busy: boolean
  disabled?: boolean
  mode: AgentMode
  onModeChange: (mode: AgentMode) => void
  currentModel: string
  onModelChange: (modelId: string) => void
  availableModels: ModelInfo[]
  fileContexts: FileContext[]
  onFileContextToggle: (path: string, included: boolean) => void
  onFileContextAdd?: (ctx: FileContext) => void
  onFileContextRemove?: (path: string) => void
  preFill?: string
  onPreFillConsumed?: () => void
  workdir?: string
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
  onSend, onInterrupt, onClearHistory, onCompress, busy, disabled,
  mode, onModeChange, currentModel, onModelChange, availableModels,
  fileContexts, onFileContextToggle, onFileContextAdd, onFileContextRemove,
  preFill, onPreFillConsumed, workdir,
}: ChatInputProps) {
  const [draft, setDraft] = useState('')
  const [images, setImages] = useState<ImageAttachment[]>([])
  const [plusOpen, setPlusOpen] = useState(false)
  const [modelOpen, setModelOpen] = useState(false)
  const [modeOpen, setModeOpen] = useState(false)
  const [slashState, setSlashState] = useState<{ query: string; selIdx: number } | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (preFill) {
      setDraft(preFill)
      onPreFillConsumed?.()
      textareaRef.current?.focus()
    }
  }, [preFill, onPreFillConsumed])

  const send = useCallback(() => {
    const text = draft.trim()
    if ((!text && images.length === 0) || busy) return
    onSend(text, { images, fileContexts: fileContexts.filter(fc => fc.included) })
    setDraft('')
    setImages([])
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
  }, [draft, images, fileContexts, busy, onSend])

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
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
        setModelOpen(true)
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
    const items = e.clipboardData?.items
    if (!items) return
    for (let i = 0; i < items.length; i++) {
      if (items[i].type.startsWith('image/')) {
        e.preventDefault()
        const file = items[i].getAsFile()
        if (!file) continue
        const reader = new FileReader()
        reader.onload = () => {
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
    setPlusOpen(false)
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = 'image/*'
    input.multiple = true
    input.onchange = () => {
      const files = Array.from(input.files || [])
      files.forEach(file => {
        const reader = new FileReader()
        reader.onload = () => {
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
    setImages(prev => prev.filter(img => img.id !== id))
  }

  const handleModelSelect = async (modelId: string) => {
    setModelOpen(false)
    try {
      await api.setModel(modelId)
      onModelChange(modelId)
    } catch {
      // ignore
    }
  }

  const modelLabel = availableModels.find(m => m.id === currentModel)?.label || currentModel || '选择模型'

  return (
    <div style={{ padding: '8px 12px 12px', background: 'var(--bg-surface)', borderTop: '1px solid var(--border)', position: 'relative' }}>
      {/* ── File context chips ────────────────── */}
      {fileContexts.length > 0 && (
        <div style={{ display: 'flex', gap: 6, marginBottom: 8, flexWrap: 'wrap' }}>
          {fileContexts.map(fc => (
            <span
              key={fc.path}
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 4,
                padding: '2px 8px', borderRadius: 999,
                background: fc.included ? 'var(--badge-accent)' : 'var(--bg-raised)',
                border: `1px solid ${fc.included ? 'var(--accent)' : 'var(--border)'}`,
                color: fc.included ? 'var(--accent)' : 'var(--text-3)',
                fontSize: 11, fontFamily: 'var(--font-mono)',
              }}
            >
              {fc.included
                ? <Eye size={10} />
                : <EyeOff size={10} />
              }
              <button
                onClick={() => onFileContextToggle(fc.path, !fc.included)}
                title={fc.included ? '排除此文件' : '包含此文件'}
                style={{
                  background: 'none', border: 'none', cursor: 'pointer',
                  color: 'inherit', padding: 0, fontSize: 11,
                  fontFamily: 'var(--font-mono)',
                }}
              >
                {fc.path}
              </button>
              {onFileContextRemove && (
                <button
                  onClick={() => onFileContextRemove(fc.path)}
                  style={{
                    background: 'none', border: 'none', cursor: 'pointer',
                    color: 'var(--text-3)', padding: 0, display: 'flex',
                  }}
                >
                  <X size={10} />
                </button>
              )}
            </span>
          ))}
        </div>
      )}

      {/* ── Main input box ────────────────────── */}
      <div style={{
        display: 'flex', flexDirection: 'column',
        background: 'var(--bg-raised)', border: '1px solid var(--border-hi)',
        borderRadius: 10, padding: '8px 10px 6px 6px',
        transition: 'border-color 0.15s',
      }}>
        {/* Image strip */}
        {images.length > 0 && (
          <div style={{ display: 'flex', gap: 6, marginBottom: 8, flexWrap: 'wrap', padding: '0 4px' }}>
            {images.map(img => (
              <div key={img.id} style={{ position: 'relative', width: 56, height: 56, flexShrink: 0 }}>
                <img
                  src={img.dataUrl}
                  alt={img.fileName || 'image'}
                  style={{ width: '100%', height: '100%', objectFit: 'cover', borderRadius: 6, border: '1px solid var(--border)' }}
                />
                <button
                  onClick={() => removeImage(img.id)}
                  style={{
                    position: 'absolute', top: -4, right: -4,
                    width: 18, height: 18, borderRadius: '50%',
                    background: 'var(--bg-overlay)', border: '1px solid var(--border-hi)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    color: 'var(--text-2)', cursor: 'pointer', padding: 0, fontSize: 10,
                  }}
                >
                  <X size={10} />
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Textarea + mode selector + send button row */}
        <div style={{ display: 'flex', alignItems: 'flex-end', gap: 6 }}>
          <textarea
            ref={textareaRef}
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            onInput={onInput}
            onPaste={handleImagePaste}
            rows={1}
            disabled={disabled}
            placeholder={busy ? '智能体运行中…' : '输入消息，Enter 发送，Shift+Enter 换行…'}
            style={{
              flex: 1, resize: 'none', background: 'transparent',
              fontSize: 13, color: 'var(--text-1)', outline: 'none',
              border: 'none', minHeight: 24, maxHeight: 160,
              padding: '2px 0', fontFamily: 'inherit', lineHeight: 1.5,
            }}
          />

          {/* Mode selector + Send */}
          <div style={{ flexShrink: 0, display: 'flex', alignItems: 'center', gap: 4 }}>
            {/* Mode selector - single button with dropdown */}
            <div style={{ position: 'relative' }}>
              <button
                onClick={() => setModeOpen(o => !o)}
                title={MODES.find(m => m.mode === mode)?.desc}
                style={{
                  height: 30, display: 'flex', alignItems: 'center', gap: 4,
                  padding: '0 8px', borderRadius: 7, fontSize: 11, fontWeight: 600,
                  background: modeOpen ? 'var(--bg-overlay)' : 'var(--bg-raised)',
                  border: `1px solid ${modeOpen ? 'var(--border-hi)' : 'var(--border)'}`,
                  color: 'var(--text-2)', cursor: 'pointer',
                  transition: 'all 0.15s',
                }}
              >
                {MODES.find(m => m.mode === mode)?.label || mode}
                <ChevronDown size={10} />
              </button>
              {modeOpen && (
                <>
                  <div style={{ position: 'fixed', inset: 0, zIndex: 100 }} onClick={() => setModeOpen(false)} />
                  <div style={{
                    position: 'absolute', bottom: 'calc(100% + 6px)', right: 0, zIndex: 101,
                    background: 'var(--bg-overlay)', border: '1px solid var(--border-hi)',
                    borderRadius: 8, padding: '4px 0', minWidth: 240,
                    boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
                  }}>
                    <p style={{ fontSize: 10, color: 'var(--text-3)', padding: '4px 12px 6px', margin: 0, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                      选择模式
                    </p>
                    {MODES.map(m => (
                      <button
                        key={m.mode}
                        onClick={() => { onModeChange(m.mode); setModeOpen(false) }}
                        style={{
                          display: 'flex', alignItems: 'center', gap: 8,
                          width: '100%', padding: '6px 12px',
                          background: mode === m.mode ? 'var(--bg-raised)' : 'none',
                          border: 'none', cursor: 'pointer',
                          color: mode === m.mode ? 'var(--text-1)' : 'var(--text-2)',
                          fontSize: 12, textAlign: 'left',
                        }}
                        onMouseOver={e => { if (mode !== m.mode) e.currentTarget.style.background = 'var(--bg-raised)' }}
                        onMouseOut={e => { if (mode !== m.mode) e.currentTarget.style.background = 'none' }}
                      >
                        {mode === m.mode && <Check size={12} color="var(--accent)" />}
                        {mode !== m.mode && <span style={{ width: 12 }} />}
                        <span style={{ fontWeight: 600 }}>{m.label}</span>
                        <span style={{ fontSize: 10, color: 'var(--text-3)' }}>{m.desc}</span>
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>

            {busy ? (
              <button
                onClick={onInterrupt}
                title="停止生成"
                style={{
                  display: 'flex', alignItems: 'center', gap: 5,
                  padding: '4px 10px', borderRadius: 6, fontSize: 11,
                  background: 'var(--bg-overlay)', color: 'var(--text-2)',
                  border: '1px solid var(--border)', cursor: 'pointer',
                }}
              >
                <Square size={9} fill="currentColor" />
                停止
              </button>
            ) : (
              <button
                onClick={send}
                disabled={(!draft.trim() && images.length === 0) || disabled}
                title="发送 (Enter)"
                style={{
                  width: 30, height: 30, borderRadius: 7,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  background: (draft.trim() || images.length > 0) && !disabled ? 'var(--accent)' : 'var(--bg-overlay)',
                  border: 'none',
                  color: (draft.trim() || images.length > 0) && !disabled ? '#fff' : 'var(--text-3)',
                  cursor: (draft.trim() || images.length > 0) && !disabled ? 'pointer' : 'not-allowed',
                  transition: 'all 0.15s',
                }}
              >
                <ArrowUp size={14} strokeWidth={2.5} />
              </button>
            )}
          </div>
        </div>

        {/* Bottom toolbar */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 4,
          marginTop: 2, padding: '0 4px',
        }}>
          {/* + button */}
          <div style={{ position: 'relative' }}>
            <button
              onClick={() => setPlusOpen(o => !o)}
              title="添加内容"
              style={{
                width: 26, height: 26, borderRadius: 5, flexShrink: 0,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                background: plusOpen ? 'var(--bg-overlay)' : 'none',
                border: '1px solid ' + (plusOpen ? 'var(--border-hi)' : 'transparent'),
                color: plusOpen ? 'var(--text-1)' : 'var(--text-3)',
                cursor: 'pointer', fontSize: 14, fontWeight: 700,
                transition: 'all 0.15s',
              }}
            >
              +
            </button>
            {/* + Menu popover */}
            {plusOpen && (
              <>
                <div style={{ position: 'fixed', inset: 0, zIndex: 100 }} onClick={() => setPlusOpen(false)} />
                <div style={{
                  position: 'absolute', bottom: '100%', left: 0, zIndex: 101,
                  background: 'var(--bg-overlay)', border: '1px solid var(--border-hi)',
                  borderRadius: 8, padding: '4px 0', minWidth: 160,
                  boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
                  marginBottom: 6,
                }}>
                  {[
                    { icon: Image, label: '图片', action: 'image' },
                    { icon: Paperclip, label: '添加文件', action: 'file' },
                  ].map(item => (
                    <button
                      key={item.action}
                      onClick={() => item.action === 'image' ? handleImageUpload() : handleFileUpload()}
                      style={{
                        display: 'flex', alignItems: 'center', gap: 8,
                        width: '100%', padding: '6px 12px',
                        background: 'none', border: 'none', cursor: 'pointer',
                        color: 'var(--text-1)', fontSize: 12, textAlign: 'left',
                      }}
                      onMouseOver={e => { e.currentTarget.style.background = 'var(--bg-raised)' }}
                      onMouseOut={e => { e.currentTarget.style.background = 'none' }}
                    >
                      <item.icon size={ICON_SM + 2} />
                      {item.label}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>

          {/* Model selector */}
          <div style={{ position: 'relative' }}>
            <button
              onClick={() => setModelOpen(o => !o)}
              title="切换模型"
              style={{
                display: 'flex', alignItems: 'center', gap: 4,
                padding: '2px 8px', borderRadius: 5, fontSize: 11,
                background: modelOpen ? 'var(--bg-overlay)' : 'none',
                border: '1px solid ' + (modelOpen ? 'var(--border-hi)' : 'transparent'),
                color: 'var(--text-3)', cursor: 'pointer',
                fontFamily: 'var(--font-mono)',
                transition: 'all 0.15s',
              }}
            >
              {modelLabel.replace('claude-', '').replace('deepseek-v4-', 'V4 ').replace('deepseek-', '')}
              <ChevronDown size={10} />
            </button>
            {/* Model picker popover */}
            {modelOpen && (
              <>
                <div style={{ position: 'fixed', inset: 0, zIndex: 100 }} onClick={() => setModelOpen(false)} />
                <div style={{
                  position: 'absolute', bottom: '100%', left: 0, zIndex: 101,
                  background: 'var(--bg-overlay)', border: '1px solid var(--border-hi)',
                  borderRadius: 8, padding: '4px 0', minWidth: 200,
                  boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
                  marginBottom: 6,
                }}>
                  <p style={{ fontSize: 10, color: 'var(--text-3)', padding: '4px 12px 6px', margin: 0, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                    选择模型
                  </p>
                  {availableModels.map(m => (
                    <button
                      key={m.id}
                      onClick={() => handleModelSelect(m.id)}
                      style={{
                        display: 'flex', alignItems: 'center', gap: 8,
                        width: '100%', padding: '6px 12px',
                        background: currentModel === m.id ? 'var(--bg-raised)' : 'none',
                        border: 'none', cursor: 'pointer',
                        color: currentModel === m.id ? 'var(--text-1)' : 'var(--text-2)',
                        fontSize: 12, textAlign: 'left',
                      }}
                      onMouseOver={e => { if (currentModel !== m.id) e.currentTarget.style.background = 'var(--bg-raised)' }}
                      onMouseOut={e => { if (currentModel !== m.id) e.currentTarget.style.background = 'none' }}
                    >
                      {currentModel === m.id && <Check size={12} color="var(--accent)" />}
                      {currentModel !== m.id && <span style={{ width: 12 }} />}
                      <span>{m.label}</span>
                      <span style={{ fontSize: 10, color: 'var(--text-3)', marginLeft: 'auto' }}>{m.provider}</span>
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>

          <div style={{ flex: 1 }} />
        </div>
      </div>

      {/* ── Slash command popover ──────────────── */}
      {slashState && matchedCommands.length > 0 && (
        <>
          <div style={{ position: 'fixed', inset: 0, zIndex: 100 }} onClick={() => setSlashState(null)} />
          <div style={{
            position: 'absolute', bottom: 'calc(100% + 4px)', left: 18, zIndex: 101,
            background: 'var(--bg-overlay)', border: '1px solid var(--border-hi)',
            borderRadius: 8, padding: '4px 0', minWidth: 220,
            boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
          }}>
            {matchedCommands.map((cmd, i) => (
              <button
                key={cmd.trigger}
                onClick={() => handleSlashSelect(cmd)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 10,
                  width: '100%', padding: '6px 12px',
                  background: i === slashState.selIdx ? 'var(--bg-raised)' : 'none',
                  border: 'none', cursor: 'pointer',
                  color: 'var(--text-1)', fontSize: 12, textAlign: 'left',
                }}
              >
                <span style={{
                  fontFamily: 'var(--font-mono)', fontWeight: 600,
                  color: 'var(--accent)', minWidth: 70, fontSize: 11,
                }}>
                  {cmd.trigger}
                </span>
                <span style={{ color: 'var(--text-2)' }}>{cmd.label}</span>
                <span style={{ color: 'var(--text-3)', fontSize: 11, marginLeft: 'auto' }}>{cmd.description}</span>
              </button>
            ))}
          </div>
        </>
      )}

      <p style={{ textAlign: 'center', fontSize: 10, color: 'var(--text-3)', margin: '5px 0 0' }}>
        Shift+Enter 换行 · Enter 发送 · / 调用命令 · Ctrl+V 粘贴图片
      </p>
    </div>
  )
}
