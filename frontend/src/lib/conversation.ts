import type { ChatMessage, DeliveryReport } from './types'

export type ConversationItem =
  | { type: 'message'; message: ChatMessage }
  | { type: 'report'; report: DeliveryReport }

/** Keep each report after its run, even when it arrives after the next question. */
export function buildConversation(messages: ChatMessage[], reports: DeliveryReport[]): ConversationItem[] {
  const ordered = [...reports].sort((a, b) => a.started_at - b.started_at || a.finished_at - b.finished_at)
  const anchors = new Map<string, number>()
  const legacyTurns: Array<{ start: number; end: number; owned: boolean }> = []
  messages.forEach((message, index) => {
    if (message.role === 'user' || !legacyTurns.length) {
      legacyTurns.push({ start: index, end: index, owned: false })
    }
    const turn = legacyTurns[legacyTurns.length - 1]
    turn.end = index
    turn.owned ||= !!message.runId
  })

  for (const report of ordered) {
    let index = messages.findLastIndex(message => message.runId === report.run_id)
    if (index < 0) {
      // Older transcripts have no run IDs, but command IDs still identify their turn.
      const commandIds = new Set(report.commands.map(command => command.tool_use_id))
      index = messages.findLastIndex(message => !message.runId &&
        message.toolCalls?.some(tool => commandIds.has(tool.id)))
      if (index >= 0) index = legacyTurns.find(turn => turn.start <= index && turn.end >= index)?.end ?? index
    }
    if (index >= 0) anchors.set(report.run_id, index)
  }

  // Legacy reports without command evidence follow the remaining conversation turns.
  const compacted = messages.some(message => typeof message.content === 'string' && message.content.startsWith('[Compacted.'))
  let before = messages.length
  for (let index = ordered.length - 1; index >= 0; index--) {
    const report = ordered[index]
    const known = anchors.get(report.run_id)
    if (known !== undefined) {
      before = known
      continue
    }
    if (report.commands.length || compacted) continue
    const previous = ordered.slice(0, index).map(item => anchors.get(item.run_id))
      .findLast(anchor => anchor !== undefined) ?? -1
    const turn = legacyTurns.findLast(turn => !turn.owned && turn.end < before && turn.end > previous)
    if (turn) {
      anchors.set(report.run_id, turn.end)
      before = turn.start
    }
  }

  const items: ConversationItem[] = []
  const appendReports = (index: number | undefined) => {
    for (const report of ordered) {
      if (anchors.get(report.run_id) === index) items.push({ type: 'report', report })
    }
  }
  // A compacted-away turn has no safe anchor in the remaining conversation.
  appendReports(undefined)
  messages.forEach((message, index) => {
    items.push({ type: 'message', message })
    appendReports(index)
  })
  return items
}
