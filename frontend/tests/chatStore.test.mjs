import test from 'node:test'
import assert from 'node:assert/strict'
import { ChatStore } from '../src/lib/chatStore.ts'

const event = (session_id, type, event_seq, extra = {}) => ({
  session_id,
  type,
  event_seq,
  ts: event_seq,
  ...extra,
})

test('background events remain in their owning session across switches', () => {
  const store = new ChatStore()
  store.activateSession('A')
  store.hydrateSession('A', [], { event_seq: 0, running: false })
  store.handleEvent(event('A', 'run_start', 1, { run_id: 'run-A' }))
  store.handleEvent(event('A', 'chunk', 2, { run_id: 'run-A', text: 'hel' }))

  store.activateSession('B')
  store.hydrateSession('B', [], { event_seq: 0, running: false })
  store.handleEvent(event('A', 'chunk', 3, { run_id: 'run-A', text: 'lo' }))
  store.handleEvent(event('A', 'tool_start', 4, {
    run_id: 'run-A',
    tool_use_id: 'tool-A',
    name: 'read_file',
    input: { path: 'a.txt' },
  }))
  store.handleEvent(event('A', 'tool_approval_request', 5, {
    run_id: 'run-A',
    tool_use_id: 'approval-A',
    name: 'bash',
    input: { command: 'true' },
  }))

  assert.deepEqual(store.getSnapshot().messages, [])
  assert.deepEqual(store.getSnapshot().toolCalls, [])
  assert.deepEqual(store.getSnapshot().pendingApprovals, [])

  store.activateSession('A')
  assert.equal(store.getActiveSessionId(), 'A')
  assert.equal(store.getSnapshot().runId, 'run-A')
  assert.equal(store.getSnapshot().agentState, 'executing_tool')
  assert.equal(store.getSnapshot().messages[0].content, 'hello')
  assert.equal(store.getSnapshot().toolCalls[0].id, 'tool-A')
  assert.equal(store.getSnapshot().pendingApprovals[0].id, 'approval-A')

  store.handleEvent(event('A', 'error', 6, { run_id: 'run-A', message: 'failed' }))
  store.handleEvent(event('A', 'run_end', 7, { run_id: 'run-A' }))
  assert.equal(store.getSnapshot().runId, null)
  assert.equal(store.getSnapshot().agentState, 'idle')
  assert.ok(store.getSnapshot().messages.every(message => message.streaming !== true))
  assert.equal(store.getSessionState('B').messages.length, 0)
})

test('a late run_start cannot erase already streamed output', () => {
  const store = new ChatStore()
  store.activateSession('A')
  store.handleEvent(event('A', 'chunk', 2, { run_id: 'run-A', text: 'hello' }))
  store.handleEvent(event('A', 'run_start', 1, { run_id: 'run-A' }))

  assert.equal(store.getSnapshot().runId, 'run-A')
  assert.equal(store.getSnapshot().agentState, 'thinking')
  assert.equal(store.getSnapshot().messages[0].content, 'hello')

  // Even if an acknowledgement carries a later sequence, the same run is not reset.
  store.handleEvent(event('A', 'run_start', 3, { run_id: 'run-A' }))
  store.handleEvent(event('A', 'chunk', 4, { run_id: 'run-A', text: ' world' }))
  assert.equal(store.getSnapshot().messages.length, 1)
  assert.equal(store.getSnapshot().messages[0].content, 'hello world')
})

test('hydration replays only socket events beyond the snapshot boundary', () => {
  const store = new ChatStore()
  store.activateSession('A')
  store.handleEvent(event('A', 'run_start', 9, { run_id: 'run-A' }))
  store.handleEvent(event('A', 'chunk', 10, { run_id: 'run-A', text: 'snap' }))
  store.handleEvent(event('A', 'chunk', 11, { run_id: 'run-A', text: 'live' }))
  store.handleEvent(event('A', 'tool_start', 12, {
    run_id: 'run-A',
    tool_use_id: 'tool-A',
    name: 'read_file',
    input: {},
  }))

  const baseline = [{ id: 'user-A', role: 'user', content: 'go', ts: 1 }]
  store.hydrateSession('A', baseline, {
    event_seq: 10,
    running: true,
    run_id: 'run-A',
    events: [
      event('A', 'run_start', 9, { run_id: 'run-A' }),
      event('A', 'chunk', 10, { run_id: 'run-A', text: 'snap' }),
    ],
  })

  const assistant = store.getSnapshot().messages.find(message => message.role === 'assistant')
  assert.equal(assistant.content, 'snaplive')
  assert.equal(store.getSnapshot().toolCalls.length, 1)
  assert.equal(store.getSnapshot().runId, 'run-A')

  store.handleEvent(event('A', 'tool_start', 12, {
    run_id: 'run-A', tool_use_id: 'tool-A', name: 'read_file', input: {},
  }))
  assert.equal(store.getSnapshot().toolCalls.length, 1)

  // A request started earlier must not replace the newer boundary or activate A.
  store.activateSession('B')
  store.hydrateSession('B', [], { event_seq: 0, running: false })
  store.hydrateSession('A', [], { event_seq: 9, running: true, run_id: 'run-A' })
  assert.equal(store.getActiveSessionId(), 'B')
  assert.equal(store.getSessionState('A').messages.length, 2)
})

test('running snapshots restore stop state and stale run_end cannot stop a newer run', () => {
  const store = new ChatStore()
  store.activateSession('A')
  store.hydrateSession('A', [], {
    event_seq: 20,
    running: true,
    run_id: 'old-run',
    events: [],
  })
  assert.equal(store.getSnapshot().runId, 'old-run')
  assert.notEqual(store.getSnapshot().agentState, 'idle')

  store.handleEvent(event('A', 'run_start', 22, { run_id: 'new-run' }))
  store.handleEvent(event('A', 'run_end', 21, { run_id: 'old-run' }))
  assert.equal(store.getSnapshot().runId, 'new-run')
  assert.equal(store.getSnapshot().agentState, 'thinking')

  store.handleEvent(event('A', 'chunk', 23, { run_id: 'new-run', text: 'done' }))
  store.handleEvent(event('A', 'run_end', 24, { run_id: 'new-run' }))
  assert.equal(store.getSnapshot().runId, null)
  assert.equal(store.getSnapshot().messages.at(-1).streaming, false)
})
