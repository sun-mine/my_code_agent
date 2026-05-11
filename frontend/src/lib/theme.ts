export type Theme = 'dark' | 'beige'

const THEME_KEY = 'code-agent-theme'

export function getStoredTheme(): Theme {
  try {
    const stored = localStorage.getItem(THEME_KEY)
    if (stored === 'dark' || stored === 'beige') return stored
  } catch {}
  return 'beige'
}

export function applyTheme(theme: Theme): void {
  document.documentElement.setAttribute('data-theme', theme)
  try { localStorage.setItem(THEME_KEY, theme) } catch {}
}

export function toggleTheme(): Theme {
  const next = getStoredTheme() === 'beige' ? 'dark' : 'beige'
  applyTheme(next)
  return next
}
