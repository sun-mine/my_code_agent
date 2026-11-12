import assert from 'node:assert/strict'
import test from 'node:test'
import { historyToMessages } from '../src/lib/history.ts'

test('restoring a saved chat preserves user and assistant ownership', () => {
  const messages = historyToMessages([
    { role: 'user', content: 'question' },
    { role: 'assistant', content: 'answer from the old chat' },
  ])
  assert.deepEqual(messages.map(message => [message.role, message.content]), [
    ['user', 'question'], ['assistant', 'answer from the old chat'],
  ])
})

test('tool results stay attached to the original assistant turn', () => {
  const messages = historyToMessages([
    { role: 'user', content: 'read a file' },
    { role: 'assistant', content: [{ type: 'tool_use', id: 'tool-a', name: 'read_file', input: { path: 'a.txt' } }] },
    { role: 'user', content: [{ type: 'tool_result', tool_use_id: 'tool-a', content: 'file contents' }] },
    { role: 'assistant', content: [{ type: 'text', text: 'finished' }] },
  ])
  assert.equal(messages.length, 2)
  assert.equal(messages[1].content, 'finished')
  assert.equal(messages[1].toolCalls?.[0].output, 'file contents')
  assert.equal(messages[1].steps?.[0].toolCall?.status, 'done')
  assert.equal(messages[1].toolCalls?.[0].ts_end, undefined)
  assert.equal(messages[1].toolCalls?.[0].duration, undefined)
})

test('saved command results retain cancellation and actual duration', () => {
  const messages = historyToMessages([
    { role: 'assistant', content: [{ type: 'tool_use', id: 'bash-1', name: 'bash', input: { command: 'sleep 30' } }] },
    { role: 'user', content: [{ type: 'tool_result', tool_use_id: 'bash-1', is_error: true, content: JSON.stringify({
      stdout: '', stderr: 'stopped', exit_code: -15, cancelled: true, timed_out: false, duration: 0.125,
    }) }] },
  ])
  const tool = messages[0].toolCalls?.[0]
  assert.equal(tool?.status, 'cancelled')
  assert.equal(tool?.duration, 0.125)
  assert.equal(tool?.exit_code, -15)
  assert.equal(tool?.output, 'stopped')
})

test('image attachments and thinking survive history restoration', () => {
  const image = { type: 'image', source: { type: 'base64', media_type: 'image/png', data: 'test-image' } }
  const messages = historyToMessages([
    { role: 'user', content: [{ type: 'text', text: 'look here' }, image] },
    { role: 'assistant', content: [{ type: 'thinking', thinking: 'reasoning' }, { type: 'text', text: 'response' }] },
  ])
  assert.deepEqual(messages[0].content, [{ type: 'text', text: 'look here' }, image])
  assert.equal(messages[1].steps?.[0].text, 'reasoning')
})
