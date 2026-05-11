import { Sun, Moon } from 'lucide-react'
import { useState } from 'react'
import { getStoredTheme, toggleTheme } from '../../lib/theme'
import { ICON_MD } from '../../lib/icons'

export function ThemeToggle() {
  const [theme, setTheme] = useState(getStoredTheme)

  const handleToggle = () => {
    const next = toggleTheme()
    setTheme(next)
  }

  return (
    <button
      onClick={handleToggle}
      title={theme === 'beige' ? '切换深色主题' : '切换浅色主题'}
      style={{
        width: 30, height: 30, borderRadius: 6,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: 'none',
        border: '1px solid transparent',
        color: 'var(--text-3)',
        cursor: 'pointer', transition: 'all 0.15s',
      }}
      onMouseOver={e => {
        e.currentTarget.style.color = 'var(--text-1)'
        e.currentTarget.style.background = 'var(--bg-overlay)'
        e.currentTarget.style.borderColor = 'var(--border-hi)'
      }}
      onMouseOut={e => {
        e.currentTarget.style.color = 'var(--text-3)'
        e.currentTarget.style.background = 'none'
        e.currentTarget.style.borderColor = 'transparent'
      }}
    >
      {theme === 'beige' ? <Moon size={ICON_MD} strokeWidth={1.8} /> : <Sun size={ICON_MD} strokeWidth={1.8} />}
    </button>
  )
}
