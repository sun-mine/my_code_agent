import type { ApiResponse, AgentStatus, Task, TodoItem, TeamMember, Checkpoint, FileNode, FileDiff, SessionInfo, SessionSnapshot } from './types'

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
  getModel: () => get<{ model: string }>('/model'),
  setModel: (model: string) =>
    post<{ model: string; message: string }>('/model', { model }),

  status: () => get<AgentStatus>('/status'),

  // ── Chat ────────────────────────────────────────
  chat: (message: string | import('./types').ContentBlock[], sessionId?: string) =>
    post<{ run_id: string; status: string; session_id: string }>('/chat', { message, session_id: sessionId }),

  interrupt: (sessionId?: string, runId?: string) => post<unknown>('/chat/interrupt', { session_id: sessionId, run_id: runId }),
  toolApprove: (tool_use_id: string, approved: boolean, sessionId?: string, runId?: string) =>
    post<unknown>('/chat/tool_approve', { tool_use_id, approved, session_id: sessionId, run_id: runId }),

  history: (sessionId?: string) => get<SessionSnapshot>('/history', sessionId ? { session_id: sessionId } : undefined),
  clearHistory: (sessionId?: string) => del<unknown>(`/history${sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''}`),

  // ── Tasks ────────────────────────────────────────
  tasks: () => get<{ tasks: Task[] }>('/tasks'),
  createTask: (subject: string, description?: string) =>
    post<Task>('/tasks', { subject, description }),

  // ── Todos ────────────────────────────────────────
  todos: () => get<{ todos: TodoItem[] }>('/todos'),

  // ── Team ────────────────────────────────────────
  team: () => get<{ team_name: string; members: TeamMember[] }>('/team'),

  // ── Memory ─────────────────────────────────────
  memoryStats: () => get<Record<string, unknown>>('/memory/stats'),
  memorySearch: (category?: string, keyword?: string, limit = 20) =>
    post<unknown[]>('/memory/search', { category, keyword, limit }),
  memoryStore: (category: string, key: string, value: string, confidence = 1) =>
    post<unknown>('/memory/store', { category, key, value, confidence }),

  // ── Safety ─────────────────────────────────────
  checkpoints: () => get<{ checkpoints: Checkpoint[] }>('/safety/checkpoints'),
  createCheckpoint: (name: string, description?: string) =>
    post<{ checkpoint_id: string }>('/safety/checkpoint', { name, description }),
  restoreCheckpoint: (checkpoint_id: string) =>
    post<{ message: string }>('/safety/restore', { checkpoint_id }),
  safetyStats: () => get<Record<string, unknown>>('/safety/stats'),

  // ── Workdir ────────────────────────────────────
  getWorkdir: () => get<{ workdir: string }>('/workdir'),
  setWorkdir: (workdir: string) =>
    post<{ workdir: string; message: string; session_id?: string }>('/workdir', { workdir }),

  // ── Files ─────────────────────────────────────
  fileTree: (depth = 3, path?: string) =>
    get<{ tree: FileNode }>('/files/tree', { depth: String(depth), ...(path ? { path } : {}) }),
  listDirectory: (path: string) =>
    get<{ items: Array<{ name: string; type: string; path: string; size?: number }>; path: string }>('/files/list', { path }),
  fileContent: (path: string) =>
    get<{ path: string; content: string; ext: string }>('/files/content', { path }),
  saveFile: (path: string, content: string) =>
    put<{ path: string; bytes_written: number }>('/files/content', { path, content }),
  uploadFile: async (file: File) => {
    const formData = new FormData()
    formData.append('file', file)
    const r = await fetch(BASE + '/files/upload', { method: 'POST', body: formData })
    const json: ApiResponse<{ path: string; size: number }> = await r.json()
    if (!json.success) throw new Error(json.error || 'Upload failed')
    return json.data!
  },

  // ── Plan ───────────────────────────────────
  savePlan: (content: string, title?: string) =>
    post<{ filename: string; path: string }>('/plan/save', { content, title }),
  executePlan: (planContent: string, todos: Array<{ content: string; status: string; activeForm: string }>, sessionId?: string) =>
    post<{ run_id: string; status: string; session_id: string }>('/plan/execute', { plan_content: planContent, todos, session_id: sessionId }),

  // ── Diff ──────────────────────────────────────
  fileDiff: (path: string) =>
    post<FileDiff>('/files/diff', { path }),

  // ── Sessions ───────────────────────────────────
  sessions: (workdir?: string) => get<{ sessions: SessionInfo[] }>('/sessions', workdir ? { workdir } : undefined),
  newSession: (workdir?: string) => post<SessionSnapshot>('/sessions/new', { workdir }),
  createSession: (id: string, title: string, messageCount: number) =>
    post<{ id: string; title: string }>('/sessions', { id, title, message_count: messageCount }),
  getSession: (sid: string) => get<{ id: string; title: string; history: Array<{ role: string; content: unknown }> }>(`/sessions/${sid}`),
  loadSession: (sid: string) => post<SessionSnapshot>(`/sessions/${encodeURIComponent(sid)}/load`),
  deleteSession: (sid: string, workdir?: string) => del<{ deleted: string }>(`/sessions/${sid}${workdir ? `?workdir=${encodeURIComponent(workdir)}` : ''}`),
  recentErrors: (limit = 20) =>
    get<unknown[]>('/errors/recent', { limit: String(limit) }),

  // ── Filesystem browser (server-side) ───────────
  fsLs: (path?: string) =>
    get<{ path: string; parent: string | null; items: Array<{ name: string; path: string }> }>('/fs/ls', path ? { path } : {}),
  fsMkdir: (path: string, name: string) =>
    post<{ path: string; name: string }>('/fs/mkdir', { path, name }),
  fsTouch: (path: string, name: string) =>
    post<{ path: string; name: string }>('/fs/touch', { path, name }),
}
