import assert from 'node:assert/strict'
import test from 'node:test'
import { buildConversation } from '../src/lib/conversation.ts'
import { historyToMessages } from '../src/lib/history.ts'
import { ChatStore } from '../src/lib/chatStore.ts'
import type { ChatMessage, DeliveryReport } from '../src/lib/types.ts'

const message = (id: string, role: ChatMessage['role'], runId?: string): ChatMessage => ({
  id, role, runId, content: id, ts: 1,
})
const report = (run_id: string, started_at = 1): DeliveryReport => ({
  run_id, session_id: 'session', started_at, finished_at: started_at + 1,
  outcome: 'completed', base_sha: null, files: [], diff: '', commands: [],
  usage: { input_tokens: 0, output_tokens: 0 }, warnings: [],
})
const order = (messages: ChatMessage[], reports: DeliveryReport[]) => buildConversation(messages, reports)
  .map(item => item.type === 'message' ? item.message.id : `report:${item.report.run_id}`)

test('reports stay between their answer and the next user question regardless of arrival order', () => {
  const messages = [message('q1', 'user', 'r1'), message('a1', 'assistant', 'r1'),
    message('q2', 'user', 'r2'), message('a2', 'assistant', 'r2')]
  assert.deepEqual(order(messages, [report('r2', 2), report('r1')]),
    ['q1', 'a1', 'report:r1', 'q2', 'a2', 'report:r2'])
})

test('a late report stays before the next streaming run and follows all its own tool rounds', () => {
  const messages = [message('q1', 'user', 'r1'), message('tool-round', 'assistant', 'r1'),
    message('a1', 'assistant', 'r1'), message('q2', 'user', 'r2'),
    { ...message('a2', 'assistant', 'r2'), streaming: true }]
  assert.deepEqual(order(messages, [report('r1')]), ['q1', 'tool-round', 'a1', 'report:r1', 'q2', 'a2'])
})

test('restored history retains run ownership and tool-result messages do not split the turn', () => {
  const messages = historyToMessages([
    { role: 'user', content: 'question one' },
    { role: 'assistant', content: [{ type: 'tool_use', id: 'read-1', name: 'read_file', input: {} }] },
    { role: 'user', content: [{ type: 'tool_result', tool_use_id: 'read-1', content: 'source' }] },
    { role: 'assistant', content: 'answer one' },
    { role: 'user', content: 'question two' },
    { role: 'assistant', content: 'answer two' },
  ], ['r1', 'r1', 'r1', 'r1', 'r2', 'r2'])
  assert.deepEqual(messages.map(item => item.runId), ['r1', 'r1', 'r2', 'r2'])
  assert.equal(messages[1].toolCalls?.[0].run_id, 'r1')
  const timeline = buildConversation(messages, [report('r1'), report('r2', 2)])
  assert.deepEqual(timeline.map(item => item.type === 'message' ? item.message.content : `report:${item.report.run_id}`),
    ['question one', 'answer one', 'report:r1', 'question two', 'answer two', 'report:r2'])
})

test('adjacent restored assistant messages from different runs are never merged', () => {
  const messages = historyToMessages([
    { role: 'assistant', content: 'first' }, { role: 'assistant', content: 'second' },
  ], ['r1', 'r2'])
  assert.equal(messages.length, 2)
  assert.deepEqual(order(messages, [report('r1')]), ['history-assistant-0', 'report:r1', 'history-assistant-1'])
})

test('an interrupted run without assistant output still owns its report', () => {
  const messages = [message('q1', 'user', 'r1'), message('q2', 'user', 'r2')]
  assert.deepEqual(order(messages, [{ ...report('r1'), outcome: 'interrupted' }]), ['q1', 'report:r1', 'q2'])
})

test('legacy command evidence anchors the whole assistant turn instead of the bottom', () => {
  const messages = historyToMessages([
    { role: 'user', content: 'first question' },
    { role: 'assistant', content: [{ type: 'tool_use', id: 'cmd-1', name: 'bash', input: {} }] },
    { role: 'user', content: [{ type: 'tool_result', tool_use_id: 'cmd-1', content: 'ok' }] },
    { role: 'assistant', content: 'first answer' },
    { role: 'user', content: 'next question' },
    { role: 'assistant', content: 'next answer' },
  ])
  const old = { ...report('r1'), commands: [{ tool_use_id: 'cmd-1', command: 'test', exit_code: 0 }] }
  assert.deepEqual(order(messages, [old]), ['history-0', 'history-assistant-0', 'report:r1', 'history-4', 'history-assistant-1'])
})

test('legacy reports align to recent completed turns while excluding the active run', () => {
  const messages = [message('q0', 'user'), message('a0', 'assistant'), message('q1', 'user'),
    message('a1', 'assistant'), message('q2', 'user'), message('a2', 'assistant'), message('active', 'user', 'r3')]
  assert.deepEqual(order(messages, [report('r1'), report('r2', 2)]),
    ['q0', 'a0', 'q1', 'a1', 'report:r1', 'q2', 'a2', 'report:r2', 'active'])
})

test('a legacy interrupted turn without output does not steal the previous answer', () => {
  const messages = [message('q1', 'user'), message('a1', 'assistant'), message('q2', 'user')]
  assert.deepEqual(order(messages, [report('r1'), { ...report('r2', 2), outcome: 'interrupted' }]),
    ['q1', 'a1', 'report:r1', 'q2', 'report:r2'])
})

test('controller reminders stay with tool feedback so the report follows the final answer', () => {
  const messages = historyToMessages([
    { role: 'user', content: 'question' },
    { role: 'assistant', content: [{ type: 'tool_use', id: 'cmd-1', name: 'bash', input: {} }] },
    { role: 'user', content: [{ type: 'tool_result', tool_use_id: 'cmd-1', content: 'passed' },
      { type: 'text', text: '<reminder>Continue the task</reminder>' }] },
    { role: 'assistant', content: 'final answer' },
  ])
  assert.equal(messages.length, 2)
  assert.equal(messages[1].content, 'final answer')
  assert.equal(messages[1].toolCalls?.[0].output, 'passed')
  assert.deepEqual(order(messages, [{ ...report('r1'), commands: [{ tool_use_id: 'cmd-1', command: 'test', exit_code: 0 }] }]),
    ['history-0', 'history-assistant-0', 'report:r1'])
})

test('review updates keep the same position and never duplicate the report', () => {
  const messages = [message('a1', 'assistant', 'r1'), message('q2', 'user', 'r2')]
  const updated = { ...report('r1'), review: { status: 'completed' as const } }
  const items = buildConversation(messages, [updated])
  assert.deepEqual(order(messages, [updated]), ['a1', 'report:r1', 'q2'])
  assert.equal(items.filter(item => item.type === 'report').length, 1)
  assert.equal(items[1].type === 'report' && items[1].report.review?.status, 'completed')
})

test('compacted-away runs and report-only sessions remain visible without attaching to a different run', () => {
  assert.deepEqual(order([], [report('old')]), ['report:old'])
  assert.deepEqual(order([message('new', 'assistant', 'new')], [report('old')]), ['report:old', 'new'])
  const compacted = { ...message('summary', 'user'), content: '[Compacted. Transcript: saved]\nsummary' }
  assert.deepEqual(order([compacted, message('recent', 'assistant')], [report('old')]), ['report:old', 'summary', 'recent'])
})

test('a report arriving in the background only updates its original session timeline', () => {
  const store = new ChatStore()
  const messages = [message('a1', 'assistant', 'r1'), message('q2', 'user', 'r2')]
  store.hydrateSession('session', messages, { event_seq: 0, running: false })
  store.activateSession('other')
  store.handleEvent({ type: 'delivery_report', session_id: 'session', run_id: 'r1', event_seq: 1, ts: 1, report: report('r1') })
  assert.deepEqual(order(store.getSnapshot().messages, store.getSnapshot().deliveryReports), [])
  store.activateSession('session')
  assert.deepEqual(order(store.getSnapshot().messages, store.getSnapshot().deliveryReports), ['a1', 'report:r1', 'q2'])
})
