import { clsx } from 'clsx'
import type { ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export { clsx, twMerge }
export type { ClassValue }

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatTime(ts: number): string {
  return new Date(ts).toLocaleTimeString('zh-CN', {
    hour: '2-digit', minute: '2-digit', second: '2-digit'
  })
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

export const STATUS_COLOR: Record<string, string> = {
  pending:      'text-[var(--text-3)]',
  in_progress:  'text-[var(--yellow)]',
  completed:    'text-[var(--green)]',
  deleted:      'text-[var(--red)]',
  working:      'text-[var(--yellow)]',
  idle:         'text-[var(--text-3)]',
  shutdown:     'text-[var(--red)]',
  thinking:     'text-[var(--blue)]',
  executing_tool: 'text-[var(--accent)]',
}

export const STATUS_LABEL: Record<string, string> = {
  pending:        '待处理',
  in_progress:    '进行中',
  completed:      '已完成',
  deleted:        '已删除',
  working:        '运行中',
  idle:           '空闲',
  shutdown:       '已停止',
  thinking:       '思考中',
  executing_tool: '执行工具',
}

/** Strip ANSI escape codes and other terminal control sequences from a string */
export function stripAnsi(str: string): string {
  return str
    // eslint-disable-next-line no-control-regex
    .replace(/\x1b\[[0-9;]*[a-zA-Z]/g, '')     // CSI sequences (colors, cursor moves)
    .replace(/\x1b\][0-9;]*[^\x07]*\x07/g, '')  // OSC sequences (title, hyperlinks)
    .replace(/\x1b[PX^_].*?\x1b\\/g, '')         // DCS, SOS, PM, APC sequences
    .replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g, '') // other control chars (keep \n, \t)
    .replace(/\r\n/g, '\n')                       // CRLF → LF
    .replace(/\r/g, '\n')                         // CR → LF (progress bars)
    .replace(/\x08+\n/g, '\n')                     // backspace-then-newline (overwrite lines)
    .replace(/[^\S\n]+$/gm, '')                   // trailing whitespace per line
}
