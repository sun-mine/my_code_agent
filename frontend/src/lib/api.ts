import type { ApiResponse, AgentStatus, Task, TodoItem, TeamMember, Checkpoint, FileNode, FileDiff } from './types'

const BASE = '/api'

async function get<T>(path: string, params?: Record<string, string>): Promise<T> {
  const url = new URL(BASE + path, location.origin)
  if (params) Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v))
  const r = await fetch(url.toString())
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
  const json: ApiResponse<T> = await r.json()
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
  chat: (message: string | import('./types').ContentBlock[]) =>
    post<{ run_id: string; status: string }>('/chat', { message }),

  interrupt: () => post<unknown>('/chat/interrupt'),
  toolApprove: (tool_use_id: string, approved: boolean) =>
    post<unknown>('/chat/tool_approve', { tool_use_id, approved }),

  history: () => get<{ history: Array<{ role: string; content: unknown }>; total: number }>('/history'),
  clearHistory: () => del<unknown>('/history'),

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
    post<{ workdir: string; message: string }>('/workdir', { workdir }),

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
  executePlan: (planContent: string, todos: Array<{ content: string; status: string; activeForm: string }>) =>
    post<{ run_id: string; status: string }>('/plan/execute', { plan_content: planContent, todos }),

  // ── Diff ──────────────────────────────────────
  fileDiff: (path: string) =>
    post<FileDiff>('/files/diff', { path }),

  // ── Sessions ───────────────────────────────────
  sessions: () => get<{ sessions: Array<{ id: string; title: string; created_at: number; updated_at: number; message_count: number; workdir: string }> }>('/sessions'),
  createSession: (id: string, title: string, messageCount: number) =>
    post<{ id: string; title: string }>('/sessions', { id, title, message_count: messageCount }),
  getSession: (sid: string) => get<{ id: string; title: string; history: Array<{ role: string; content: unknown }> }>(`/sessions/${sid}`),
  loadSession: (sid: string) => post<{ history: Array<{ role: string; content: unknown }>; title: string; workdir: string }>(`/sessions/${sid}/load`),
  deleteSession: (sid: string) => del<{ deleted: string }>(`/sessions/${sid}`),
  recentErrors: (limit = 20) =>
    get<unknown[]>('/errors/recent', { limit: String(limit) }),

  // ── Filesystem browser (server-side) ───────────
  fsLs: (path?: string) =>
    get<{ path: string; parent: string | null; items: Array<{ name: string; path: string }> }>('/fs/ls', path ? { path } : {}),
}
