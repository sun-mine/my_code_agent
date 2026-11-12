import type { AssistantStep, ChatMessage, ContentBlock } from './types'
import type { ToolCall } from '../hooks/useChat'

/** Convert persisted model history without turning assistant replies into user messages. */
export function historyToMessages(
  history: Array<{ role: string; content: unknown }>,
  runIds: Array<string | null> = [],
): ChatMessage[] {
  const messages: ChatMessage[] = []
  const restoredAt = Date.now()
  let text = ''
  let steps: AssistantStep[] = []
  let tools: ToolCall[] = []
  let hasAssistant = false
  let assistantRunId: string | undefined
  let turn = 0

  const flush = () => {
    if (!hasAssistant) return
    messages.push({
      id: `history-assistant-${turn++}`, role: 'assistant', content: text,
      ts: restoredAt, steps: steps.length ? steps : undefined,
      toolCalls: tools.length ? tools : undefined,
      runId: assistantRunId,
    })
    text = ''
    steps = []
    tools = []
    hasAssistant = false
    assistantRunId = undefined
  }

  for (const [index, message] of history.entries()) {
    const runId = runIds[index] ?? undefined
    const blocks = Array.isArray(message.content)
      ? message.content.filter((block): block is Record<string, unknown> => !!block && typeof block === 'object')
      : []
    // Tool feedback may also contain a controller reminder, never a new user turn.
    if (message.role === 'user' && blocks.some(block => block.type === 'tool_result')) {
      for (const block of blocks.filter(block => block.type === 'tool_result')) {
        const tool = tools.find(item => item.id === block.tool_use_id)
        if (tool) {
          tool.status = block.is_error ? 'error' : 'done'
          tool.output = typeof block.content === 'string' ? block.content : JSON.stringify(block.content ?? '')
          if (tool.name === 'bash' || tool.name === 'worktree_run') {
            try {
              const result = JSON.parse(tool.output)
              if (typeof result.stdout === 'string' && typeof result.stderr === 'string' && typeof result.duration === 'number') {
                tool.duration = result.duration
                tool.exit_code = typeof result.exit_code === 'number' ? result.exit_code : undefined
                tool.cancelled = result.cancelled === true
                tool.timed_out = result.timed_out === true
                if (tool.cancelled) tool.status = 'cancelled'
                tool.output = (result.stdout + result.stderr).trim() || '(no output)'
              }
            } catch { /* Older transcripts store plain text output. */ }
          }
        }
      }
      continue
    }
    if (message.role === 'assistant') {
      if (hasAssistant && assistantRunId !== runId) flush()
      hasAssistant = true
      assistantRunId = runId
      if (typeof message.content === 'string') text += message.content
      for (const block of blocks) {
        if (block.type === 'text' && typeof block.text === 'string') text += block.text
        if (block.type === 'thinking') {
          const thinking = block.thinking ?? block.text
          if (typeof thinking === 'string') steps.push({ type: 'thinking', text: thinking })
        }
        if (block.type === 'tool_use') {
          const tool: ToolCall = {
            id: String(block.id ?? block.tool_use_id ?? `history-tool-${index}-${tools.length}`),
            run_id: runId ?? '', name: String(block.name ?? 'tool'),
            input: block.input && typeof block.input === 'object' ? block.input as Record<string, unknown> : {},
            status: 'done', ts_start: 0,
          }
          tools.push(tool)
          steps.push({ type: 'tool_call', toolCall: tool })
        }
      }
      continue
    }
    flush()
    if (message.role !== 'user' && message.role !== 'system') continue
    const content = typeof message.content === 'string'
      ? message.content
      : blocks.filter(block => block.type === 'text' || block.type === 'image') as unknown as ContentBlock[]
    if (typeof content !== 'string' && !content.length) continue
    messages.push({ id: `history-${index}`, role: message.role, content, ts: restoredAt, runId })
  }
  flush()
  return messages
}
