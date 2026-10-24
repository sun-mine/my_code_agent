import type { ApiResponse, AgentStatus, SessionInfo, SessionSnapshot } from './types'

const BASE = '/api'

async function get<T>(path: string, params?: Record<string, string>): Promise<T> {
  const query = params ? new URLSearchParams(params).toString() : ''
  const url = `${BASE}${path}${query ? `?${query}` : ''}`
  const r = await fetch(url)
  const json: ApiResponse<T> = await r.json()
  if (!json.success) throw new Error(json.error || 'API error')
  return json.data as T
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
  let json: ApiResponse<T>
  try {
    json = await r.json()
  } catch {
    if ((path === '/fs/mkdir' || path === '/fs/touch') && r.status === 404) {
      throw new Error('文件创建接口尚未加载，请重启 Python 后端服务后重试')
    }
    throw new Error(`服务器返回了无法解析的响应（HTTP ${r.status}）`)
  }
  if (!json.success) throw new Error(json.error || 'API error')
  return json.data as T
}

async function del<T>(path: string): Promise<T> {
  const r = await fetch(BASE + path, { method: 'DELETE' })
  const json: ApiResponse<T> = await r.json()
  if (!json.success) throw new Error(json.error || 'API error')
  return json.data as T
}

async function put<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(BASE + path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
  const json: ApiResponse<T> = await r.json()
  if (!json.success) throw new Error(json.error || 'API error')
  return json.data as T
}

// ── Model ────────────────────────────────────────

// ── Status ────────────────────────────────────────
export const api = {
  listModels: () => get<{ models: Array<{ id: string; label: string; provider: string }>; current: string }>('/models'),
  setModel: (model: string) =>
    post<{ model: string; message: string }>('/model', { model }),

  status: () => get<AgentStatus>('/status'),

  // ── Chat ────────────────────────────────────────
  interrupt: (sessionId?: string, runId?: string) => post<unknown>('/chat/interrupt', { session_id: sessionId, run_id: runId }),
  toolApprove: (tool_use_id: string, approved: boolean, sessionId?: string, runId?: string) =>
    post<unknown>('/chat/tool_approve', { tool_use_id, approved, session_id: sessionId, run_id: runId }),

  history: (sessionId?: string) => get<SessionSnapshot>('/history', sessionId ? { session_id: sessionId } : undefined),
  clearHistory: (sessionId?: string) => del<unknown>(`/history${sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''}`),

  // ── Workdir ────────────────────────────────────
  setWorkdir: (workdir: string) =>
    post<{ workdir: string; message: string; session_id?: string }>('/workdir', { workdir }),

  // ── Files ─────────────────────────────────────
  fileContent: (path: string) =>
    get<{ path: string; content: string; ext: string }>('/files/content', { path }),
  saveFile: (path: string, content: string) =>
    put<{ path: string; bytes_written: number }>('/files/content', { path, content }),

  // ── Plan ───────────────────────────────────
  savePlan: (content: string, title?: string) =>
    post<{ filename: string; path: string }>('/plan/save', { content, title }),
  executePlan: (planContent: string, todos: Array<{ content: string; status: string; activeForm: string }>, sessionId?: string) =>
    post<{ run_id: string; status: string; session_id: string }>('/plan/execute', { plan_content: planContent, todos, session_id: sessionId }),

  // ── Sessions ───────────────────────────────────
  sessions: (workdir?: string) => get<{ sessions: SessionInfo[] }>('/sessions', workdir ? { workdir } : undefined),
  newSession: (workdir?: string) => post<SessionSnapshot>('/sessions/new', { workdir }),
  loadSession: (sid: string) => post<SessionSnapshot>(`/sessions/${encodeURIComponent(sid)}/load`),
  deleteSession: (sid: string, workdir?: string) => del<{ deleted: string }>(`/sessions/${sid}${workdir ? `?workdir=${encodeURIComponent(workdir)}` : ''}`),

  // ── Filesystem browser (server-side) ───────────
  fsLs: (path?: string) =>
    get<{ path: string; parent: string | null; items: Array<{ name: string; path: string }> }>('/fs/ls', path ? { path } : {}),
  fsMkdir: (path: string, name: string) =>
    post<{ path: string; name: string }>('/fs/mkdir', { path, name }),
  fsTouch: (path: string, name: string) =>
    post<{ path: string; name: string }>('/fs/touch', { path, name }),
}
