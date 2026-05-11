import { cn } from '../../lib/utils'
import type { ReactNode, ButtonHTMLAttributes } from 'react'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'default' | 'ghost' | 'danger' | 'accent'
  size?: 'sm' | 'md' | 'icon'
  children: ReactNode
}

export function Button({ variant = 'default', size = 'md', className, children, ...props }: ButtonProps) {
  return (
    <button
      className={cn(
        'inline-flex items-center justify-center gap-1.5 rounded-[var(--radius)] font-medium transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed select-none',
        size === 'sm' && 'px-2 py-1 text-xs',
        size === 'md' && 'px-3 py-1.5 text-sm',
        size === 'icon' && 'p-1.5 text-sm',
        variant === 'default' && 'bg-[var(--bg-overlay)] text-[var(--text-1)] border border-[var(--border)] hover:border-[var(--border-hi)] hover:bg-[var(--bg-raised)]',
        variant === 'ghost' && 'text-[var(--text-2)] hover:text-[var(--text-1)] hover:bg-[var(--bg-overlay)]',
        variant === 'danger' && 'bg-red-900/30 text-[var(--red)] border border-red-900/40 hover:bg-red-900/50',
        variant === 'accent' && 'bg-[var(--accent)] text-white hover:bg-[var(--accent-2)]',
        className,
      )}
      {...props}
    >
      {children}
    </button>
  )
}

interface BadgeProps {
  color?: 'green' | 'yellow' | 'red' | 'blue' | 'gray' | 'accent'
  children: ReactNode
  className?: string
}
export function Badge({ color = 'gray', children, className }: BadgeProps) {
  return (
    <span className={cn(
      'inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium',
      color === 'green'  && 'bg-green-900/30 text-[var(--green)]',
      color === 'yellow' && 'bg-yellow-900/30 text-[var(--yellow)]',
      color === 'red'    && 'bg-red-900/30 text-[var(--red)]',
      color === 'blue'   && 'bg-blue-900/30 text-[var(--blue)]',
      color === 'gray'   && 'bg-[var(--bg-overlay)] text-[var(--text-3)]',
      color === 'accent' && 'bg-orange-900/30 text-[var(--accent)]',
      className,
    )}>
      {children}
    </span>
  )
}

export function Spinner({ size = 14 }: { size?: number }) {
  return (
    <svg
      width={size} height={size} viewBox="0 0 24 24" fill="none"
      className="animate-spin-fast"
      style={{ stroke: 'currentColor', strokeWidth: 2.5 }}
    >
      <circle cx="12" cy="12" r="10" strokeOpacity=".2" />
      <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
    </svg>
  )
}

export function Divider({ className }: { className?: string }) {
  return <div className={cn('h-px bg-[var(--border)] my-1', className)} />
}
