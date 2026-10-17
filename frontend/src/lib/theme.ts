export type Theme = 'dark' | 'light'

const THEME_KEY = 'code-agent-theme'

export function getStoredTheme(): Theme {
  try {
    const stored = localStorage.getItem(THEME_KEY)
    if (stored === 'dark' || stored === 'light') return stored
    if (stored === 'beige') return 'light' // 旧值迁移
  } catch {}
  return 'dark'
}

export function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute('data-theme', theme)
  try { localStorage.setItem(THEME_KEY, theme) } catch {}
}

export function toggleTheme(): Theme {
  const next = getStoredTheme() === 'light' ? 'dark' : 'light'
  applyTheme(next)
  return next
}
