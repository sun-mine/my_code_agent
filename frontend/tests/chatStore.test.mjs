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

const deliveryReport = (session_id, run_id, extra = {}) => ({
  run_id,
  session_id,
  outcome: 'completed',
  started_at: 100,
  finished_at: 101,
  base_sha: 'abc123',
  files: [],
  diff: '',
  commands: [],
  usage: { input_tokens: 10, output_tokens: 20 },
  warnings: [],
  ...extra,
})

const a2aReview = (session_id, run_id, extra = {}) => {
  const identity = {
    session_id,
    run_id,
    invocation_id: 'invocation-hash',
    patch_hash: 'patch-hash',
    snapshot_hash: 'snapshot-hash',
  }
  return {
    ...identity,
    status: 'completed',
    task_id: 'task-1',
    context_id: 'context-1',
    agent_url: 'https://review.example.test',
    message_id: 'message-1',
    artifact: {
      schema_version: 1,
      ...identity,
      verdict: 'no_findings',
      findings: [],
      context_limits: ['bounded context'],
      usage: { input_tokens: 3, output_tokens: 2 },
    },
    error: null,
    started_at: 100,
    finished_at: 101,
    duration: 1,
    ...extra,
  }
}

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

test('effective running state survives an older idle HTTP snapshot', () => {
  const store = new ChatStore()
  store.activateSession('A')
  store.handleEvent(event('A', 'run_start', 2, { run_id: 'new-live-run' }))
  store.hydrateSession('A', [], { event_seq: 1, running: false, run_id: null, events: [] })
  assert.equal(store.getSessionState('A')?.runId, 'new-live-run')
  assert.equal(store.getSessionState('A')?.agentState, 'thinking')
  store.activateSession('B')
  assert.equal(store.getSessionState('A')?.runId, 'new-live-run')
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

test('pending messages keep one request id until accepted or cancelled', async () => {
  const store = new ChatStore()
  store.activateSession('A')

  const accepted = store.enqueueMessage('A', 'next task', 'auto')
  const pending = store.getSnapshot().pendingMessage
  assert.equal(pending.content, 'next task')
  assert.equal(store.getSnapshot().draftResetEpoch, 0)

  store.updatePendingMessage('A', pending.requestId, 'failed', 'network unavailable')
  store.updatePendingMessage('A', pending.requestId, 'queued')
  assert.equal(store.getSnapshot().pendingMessage.requestId, pending.requestId)

  store.acceptPendingMessage('A', pending.requestId, 'run-A')
  assert.equal(await accepted, true)
  assert.equal(store.getSnapshot().pendingMessage, null)
  assert.equal(store.getSnapshot().messages[0].content, 'next task')
  assert.equal(store.getSnapshot().runId, 'run-A')
  assert.equal(store.getSnapshot().draftResetEpoch, 1)

  const cancelled = store.enqueueMessage('A', 'cancel me', 'auto')
  const queued = store.getSnapshot().pendingMessage
  store.cancelPendingMessage('A', queued.requestId)
  assert.equal(await cancelled, false)
  assert.equal(store.getSnapshot().pendingMessage, null)
  assert.equal(store.getSnapshot().draftResetEpoch, 1)
})

test('stopping remains non-terminal until interrupted and exposes resume', () => {
  const store = new ChatStore()
  store.activateSession('A')
  store.handleEvent(event('A', 'run_start', 1, { run_id: 'run-A' }))
  store.setStopping('A', true)
  assert.equal(store.getSnapshot().agentState, 'cancelling')

  store.handleEvent(event('A', 'error', 2, { run_id: 'run-A', message: 'still cancelling' }))
  assert.equal(store.getSnapshot().agentState, 'cancelling')
  assert.equal(store.getSnapshot().runId, 'run-A')

  store.handleEvent(event('A', 'interrupted', 3, { run_id: 'run-A' }))
  assert.equal(store.getSnapshot().agentState, 'idle')
  assert.equal(store.getSnapshot().runId, null)
  assert.equal(store.getSnapshot().canResume, true)
  store.handleEvent(event('A', 'run_end', 4, { run_id: 'run-A' }))
  assert.equal(store.getSnapshot().canResume, true)
})

test('reviewing keeps the run live and cancellation can restore that phase', () => {
  const store = new ChatStore()
  store.activateSession('A')
  store.hydrateSession('A', [], {
    event_seq: 1,
    running: true,
    run_id: 'run-A',
    phase: 'reviewing',
    events: [event('A', 'run_start', 1, { run_id: 'run-A' })],
  })
  assert.equal(store.getSnapshot().agentState, 'reviewing')
  assert.equal(store.getSnapshot().runId, 'run-A')

  store.handleEvent(event('A', 'agent_state', 2, { run_id: 'run-A', state: 'reviewing' }))

  assert.equal(store.getSnapshot().agentState, 'reviewing')
  assert.equal(store.getSnapshot().runId, 'run-A')

  store.setStopping('A', true)
  assert.equal(store.getSnapshot().agentState, 'cancelling')
  assert.equal(store.getSnapshot().runId, 'run-A')
  store.setStopping('A', false)
  assert.equal(store.getSnapshot().agentState, 'reviewing')

  store.handleEvent(event('A', 'a2a_review', 3, {
    run_id: 'run-A', review: { status: 'working' },
  }))
  store.handleEvent(event('A', 'delivery_report', 4, {
    run_id: 'run-A',
    report: deliveryReport('A', 'run-A', { review: a2aReview('A', 'run-A') }),
  }))
  assert.equal(store.getSnapshot().agentState, 'reviewing')
  assert.equal(store.getSnapshot().runId, 'run-A')
  assert.equal(store.getSnapshot().deliveryReports[0].review.status, 'completed')

  store.handleEvent(event('A', 'run_end', 5, { run_id: 'run-A' }))
  assert.equal(store.getSnapshot().agentState, 'idle')
  assert.equal(store.getSnapshot().runId, null)
})

test('tool results preserve structured failure and duration fields', () => {
  const store = new ChatStore()
  store.activateSession('A')
  store.handleEvent(event('A', 'run_start', 1, { run_id: 'run-A' }))
  store.handleEvent(event('A', 'tool_start', 2, {
    run_id: 'run-A', tool_use_id: 'tool-A', name: 'bash', input: { command: 'false' },
  }))
  store.handleEvent(event('A', 'tool_result', 3, {
    run_id: 'run-A', tool_use_id: 'tool-A', output: 'failed', is_error: true,
    exit_code: 1, duration: 0.25,
  }))

  const tool = store.getSnapshot().toolCalls[0]
  assert.equal(tool.status, 'error')
  assert.equal(tool.is_error, true)
  assert.equal(tool.exit_code, 1)
  assert.equal(tool.duration, 0.25)
  assert.equal(store.getSnapshot().messages[0].toolCalls[0].status, 'error')
})

test('a fast socket completion before HTTP acknowledgement stays idle and ordered', async () => {
  const store = new ChatStore()
  store.activateSession('A')
  const accepted = store.enqueueMessage('A', 'fast task', 'auto')
  const requestId = store.getSnapshot().pendingMessage.requestId

  store.handleEvent(event('A', 'run_start', 1, { run_id: 'run-fast' }))
  store.handleEvent(event('A', 'chunk', 2, { run_id: 'run-fast', text: 'done' }))
  store.handleEvent(event('A', 'run_end', 3, { run_id: 'run-fast' }))
  store.acceptPendingMessage('A', requestId, 'run-fast')

  assert.equal(await accepted, true)
  assert.equal(store.getSnapshot().agentState, 'idle')
  assert.equal(store.getSnapshot().runId, null)
  assert.deepEqual(store.getSnapshot().messages.map(message => message.role), ['user', 'assistant'])
})

test('hydrated accepted input is not inserted twice on acknowledgement retry', () => {
  const store = new ChatStore()
  store.activateSession('A')
  store.enqueueMessage('A', 'persisted task', 'auto')
  const requestId = store.getSnapshot().pendingMessage.requestId
  store.hydrateSession('A', [{ id: 'history-user', role: 'user', content: 'persisted task', ts: 1 }], {
    event_seq: 0,
    running: false,
    run_id: null,
    events: [],
    accepted_requests: { [requestId]: 'run-persisted' },
  })

  store.acceptPendingMessage('A', requestId, 'run-persisted')
  assert.equal(store.getSnapshot().messages.filter(message => message.role === 'user').length, 1)
  assert.equal(store.getSnapshot().agentState, 'idle')
  assert.equal(store.getSnapshot().runId, null)
})

test('matching old content without an accepted request is a distinct message', () => {
  const store = new ChatStore()
  store.activateSession('A')
  store.enqueueMessage('A', 'same text', 'auto')
  const requestId = store.getSnapshot().pendingMessage.requestId
  store.hydrateSession('A', [{ id: 'older-user', role: 'user', content: 'same text', ts: 1 }], {
    event_seq: 0,
    running: false,
    run_id: null,
    events: [],
  })

  store.acceptPendingMessage('A', requestId, 'run-new')
  assert.equal(store.getSnapshot().messages.filter(message => message.role === 'user').length, 2)
  assert.equal(store.getSnapshot().runId, 'run-new')
})

test('switching sessions does not rebind or strand queued work', () => {
  const store = new ChatStore()
  store.activateSession('A')
  store.enqueueMessage('A', 'task A', 'auto')
  const requestId = store.getSnapshot().pendingMessage.requestId
  store.activateSession('B')

  const next = store.getNextPendingMessage()
  assert.equal(next.sessionId, 'A')
  assert.equal(next.message.requestId, requestId)
  assert.equal(next.message.content, 'task A')
})

test('snapshot hydration retains and deduplicates delivery reports for its session', () => {
  const store = new ChatStore()
  store.activateSession('A')
  store.hydrateSession('A', [], {
    event_seq: 8,
    running: false,
    delivery_reports: [
      deliveryReport('A', 'run-A', { finished_at: 101, warnings: ['old'] }),
      deliveryReport('B', 'run-wrong-session'),
      deliveryReport('A', 'run-A', { finished_at: 102, warnings: ['updated'] }),
    ],
  })

  assert.equal(store.getSnapshot().deliveryReports.length, 1)
  assert.equal(store.getSnapshot().deliveryReports[0].run_id, 'run-A')
  assert.deepEqual(store.getSnapshot().deliveryReports[0].warnings, ['updated'])
})

test('delivery report events stay isolated and do not revive a finished run', () => {
  const store = new ChatStore()
  store.activateSession('A')
  store.handleEvent(event('A', 'run_start', 1, { run_id: 'run-A' }))
  store.handleEvent(event('A', 'run_end', 2, { run_id: 'run-A' }))

  store.activateSession('B')
  store.hydrateSession('B', [], { event_seq: 0, running: false, delivery_reports: [] })
  store.handleEvent(event('A', 'delivery_report', 3, {
    run_id: 'run-A', report: deliveryReport('A', 'run-A'),
  }))

  assert.equal(store.getSnapshot().deliveryReports.length, 0)
  assert.equal(store.getSessionState('A').deliveryReports.length, 1)
  assert.equal(store.getSessionState('A').agentState, 'idle')
  assert.equal(store.getSessionState('A').runId, null)
})

test('a newer report for the same run replaces the old report after snapshot replay', () => {
  const store = new ChatStore()
  store.activateSession('A')
  store.handleEvent(event('A', 'delivery_report', 11, {
    run_id: 'run-A',
    report: deliveryReport('A', 'run-A', { outcome: 'failed', finished_at: 105, warnings: ['tests failed'] }),
  }))

  store.hydrateSession('A', [], {
    event_seq: 10,
    running: false,
    delivery_reports: [deliveryReport('A', 'run-A', { outcome: 'incomplete', finished_at: 104 })],
  })

  assert.equal(store.getSnapshot().deliveryReports.length, 1)
  assert.equal(store.getSnapshot().deliveryReports[0].outcome, 'failed')
  assert.deepEqual(store.getSnapshot().deliveryReports[0].warnings, ['tests failed'])

  store.handleEvent(event('A', 'delivery_report', 12, {
    run_id: 'run-A',
    report: deliveryReport('A', 'run-A', { outcome: 'completed', finished_at: 106 }),
  }))
  assert.equal(store.getSnapshot().deliveryReports.length, 1)
  assert.equal(store.getSnapshot().deliveryReports[0].outcome, 'completed')
})

test('final A2A review refreshes by run and is restored from a snapshot', () => {
  const pendingReview = a2aReview('A', 'run-A', {
    status: 'cancel_unknown', artifact: null, error: 'remote status unavailable',
  })
  const completedReview = a2aReview('A', 'run-A')
  const store = new ChatStore()
  store.activateSession('A')
  store.hydrateSession('A', [], {
    event_seq: 8,
    running: false,
    delivery_reports: [deliveryReport('A', 'run-A', { review: pendingReview })],
  })
  assert.equal(store.getSnapshot().deliveryReports[0].review.status, 'cancel_unknown')

  const finalReport = deliveryReport('A', 'run-A', { finished_at: 103, review: completedReview })
  store.handleEvent(event('A', 'delivery_report', 9, { run_id: 'run-A', report: finalReport }))
  assert.equal(store.getSnapshot().deliveryReports.length, 1)
  assert.equal(store.getSnapshot().deliveryReports[0].review.artifact.verdict, 'no_findings')

  const restored = new ChatStore()
  restored.activateSession('A')
  restored.hydrateSession('A', [], {
    event_seq: 9,
    running: false,
    delivery_reports: [finalReport],
  })
  assert.equal(restored.getSnapshot().deliveryReports[0].review.status, 'completed')
  assert.deepEqual(restored.getSnapshot().deliveryReports[0].review.artifact.context_limits, ['bounded context'])
})

test('resumed A2A review only replaces its original session and run report', () => {
  const store = new ChatStore()
  store.activateSession('A')
  store.hydrateSession('A', [], {
    event_seq: 1,
    running: false,
    delivery_reports: [deliveryReport('A', 'run-A', {
      review: a2aReview('A', 'run-A', { status: 'unknown', artifact: null }),
    })],
  })
  store.activateSession('B')
  store.hydrateSession('B', [], { event_seq: 1, running: false, delivery_reports: [] })

  const completed = deliveryReport('A', 'run-A', { review: a2aReview('A', 'run-A') })
  assert.equal(store.replaceDeliveryReport('B', 'run-A', completed), false)
  assert.equal(store.replaceDeliveryReport('A', 'different-run', completed), false)
  assert.equal(store.replaceDeliveryReport('A', 'run-A', completed), true)
  assert.equal(store.getSessionState('A').deliveryReports[0].review.status, 'completed')
  assert.equal(store.getSessionState('B').deliveryReports.length, 0)
})

test('delivery reports keep only the five newest runs', () => {
  const store = new ChatStore()
  store.activateSession('A')
  for (let index = 1; index <= 6; index += 1) {
    store.handleEvent(event('A', 'delivery_report', index, {
      run_id: `run-${index}`,
      report: deliveryReport('A', `run-${index}`, { finished_at: 100 + index }),
    }))
  }

  assert.deepEqual(
    store.getSnapshot().deliveryReports.map(report => report.run_id),
    ['run-2', 'run-3', 'run-4', 'run-5', 'run-6'],
  )
})
