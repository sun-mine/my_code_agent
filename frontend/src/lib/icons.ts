import {
  FileText, FileCode, FileEdit, Terminal, Search, Folder, FolderOpen,
  ListTodo, Brain, Camera, Shield, RotateCcw, Wrench, Globe, Paperclip,
  Shrink, Plus, Check, X, AlertTriangle, Loader, ChevronDown, ChevronRight,
  ChevronUp, FolderTree, LayoutPanelLeft, Trash2, CheckCircle, XCircle,
  AlertCircle, Loader2, Circle, Send, Square, ClipboardList, CheckSquare,
  Users, FileImage, FileJson, FileArchive, File, type LucideIcon,
} from 'lucide-react'

// ── Icon size constants ───────────────────────────
export const ICON_SM = 13
export const ICON_MD = 15
export const ICON_LG = 18

// ── Tool name -> Lucide component ──────────────────
export const TOOL_ICON_MAP: Record<string, LucideIcon> = {
  read_file: FileText,
  write_file: FileEdit,
  create_file: FileEdit,
  save_file: FileEdit,
  edit_file: FileEdit,
  list_dir: Folder,
  run_command: Terminal,
  bash: Terminal,
  grep: Search,
  search: Search,
  TodoRead: ClipboardList,
  TodoWrite: CheckSquare,
  memory_store: Brain,
  memory_search: Search,
  task_create: ListTodo,
  load_skill: Wrench,
  safety_checkpoint: Shield,
  safety_restore: RotateCcw,
  create_checkpoint: Camera,
  restore_checkpoint: RotateCcw,
  background_run: Loader,
  send_message: Send,
}

export const TOOL_LABEL_MAP: Record<string, string> = {
  read_file: '读取文件',
  write_file: '写入文件',
  create_file: '创建文件',
  save_file: '保存文件',
  edit_file: '编辑文件',
  list_dir: '列出目录',
  run_command: '运行命令',
  bash: '执行脚本',
  grep: '文本搜索',
  search: '搜索',
  TodoRead: '查看任务',
  TodoWrite: '更新任务',
  memory_store: '存储记忆',
  memory_search: '搜索记忆',
  task_create: '创建任务',
  load_skill: '加载技能',
  safety_checkpoint: '创建快照',
  safety_restore: '恢复快照',
  create_checkpoint: '创建快照',
  restore_checkpoint: '恢复快照',
  background_run: '后台运行',
  send_message: '发送消息',
}

// ── File extension -> Lucide component ─────────────
export const FILE_ICON_MAP: Record<string, LucideIcon> = {
  py: FileCode,
  ts: FileCode,
  tsx: FileCode,
  js: FileCode,
  jsx: FileCode,
  rs: FileCode,
  go: FileCode,
  java: FileCode,
  cpp: FileCode,
  c: FileCode,
  h: FileCode,
  json: FileJson,
  yaml: FileText,
  yml: FileText,
  toml: FileText,
  md: FileText,
  txt: FileText,
  sh: Terminal,
  css: FileCode,
  html: FileCode,
  svg: FileImage,
  png: FileImage,
  jpg: FileImage,
  gif: FileImage,
  csv: FileText,
  zip: FileArchive,
  gz: FileArchive,
  tar: FileArchive,
}

// ── Navigation / panel tab icons ───────────────────
export const NAV_ICONS: Record<string, LucideIcon> = {
  tasks: ClipboardList,
  todos: CheckSquare,
  team: Users,
  safety: Shield,
  files: FolderTree,
}

// ── Status icons ───────────────────────────────────
export const StatusIcon = {
  running: Loader2,
  done: CheckCircle,
  error: XCircle,
  pending: Circle,
  warning: AlertTriangle,
}

// ── Arrow / expand icons ───────────────────────────
export const ArrowIcon = {
  down: ChevronDown,
  right: ChevronRight,
  up: ChevronUp,
}

export function getToolIcon(name: string): LucideIcon {
  return TOOL_ICON_MAP[name] ?? Wrench
}

export function getToolLabel(name: string): string {
  return TOOL_LABEL_MAP[name] ?? name
}

export function getFileIcon(name: string): LucideIcon {
  const ext = name.split('.').pop()?.toLowerCase() ?? ''
  return FILE_ICON_MAP[ext] ?? File
}
