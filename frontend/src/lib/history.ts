import type { AssistantStep, ChatMessage, ContentBlock } from './types'
import type { ToolCall } from '../hooks/useChat'

/** Convert persisted model history without turning assistant replies into user messages. */
export function historyToMessages(history: Array<{ role: string; content: unknown }>): ChatMessage[] {
  const messages: ChatMessage[] = []
  const restoredAt = Date.now()
  let text = ''
  let steps: AssistantStep[] = []
  let tools: ToolCall[] = []
  let hasAssistant = false
  let turn = 0

  const flush = () => {
    if (!hasAssistant) return
    messages.push({
      id: `history-assistant-${turn++}`, role: 'assistant', content: text,
      ts: restoredAt, steps: steps.length ? steps : undefined,
      toolCalls: tools.length ? tools : undefined,
    })
    text = ''
    steps = []
    tools = []
    hasAssistant = false
  }

  for (const [index, message] of history.entries()) {
    const blocks = Array.isArray(message.content)
      ? message.content.filter((block): block is Record<string, unknown> => !!block && typeof block === 'object')
      : []
    if (message.role === 'user' && blocks.length && blocks.every(block => block.type === 'tool_result')) {
      for (const block of blocks) {
        const tool = tools.find(item => item.id === block.tool_use_id)
        if (tool) {
          tool.status = block.is_error ? 'error' : 'done'
          tool.output = typeof block.content === 'string' ? block.content : JSON.stringify(block.content ?? '')
          tool.ts_end = index
        }
      }
      continue
    }
    if (message.role === 'assistant') {
      hasAssistant = true
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
            run_id: '', name: String(block.name ?? 'tool'),
            input: block.input && typeof block.input === 'object' ? block.input as Record<string, unknown> : {},
            status: 'done', ts_start: index,
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
    messages.push({ id: `history-${index}`, role: message.role, content, ts: restoredAt })
  }
  flush()
  return messages
}
