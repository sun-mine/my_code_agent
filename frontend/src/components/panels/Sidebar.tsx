import { useState } from 'react'
import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronRight, Folder, MessageSquare, Plus, SquarePen, Trash2 } from 'lucide-react'
import { api } from '../../lib/api'
import { FolderPicker } from './FolderPicker'

interface ProjectInfo { path: string; name: string }
interface SidebarProps {
  onNewSession: () => void
  onNewProjectSession: (path: string) => void
  onLoadSession: (sid: string) => void
  onProjectChange: (path: string) => Promise<void>
  workdir?: string
}

const PROJECTS_KEY = 'code-agent-projects'
const HIDDEN_PROJECTS_KEY = 'code-agent-hidden-projects'

function readProjects(): ProjectInfo[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(PROJECTS_KEY) || '[]')
    if (!Array.isArray(value)) return []
    return value.filter((item): item is ProjectInfo =>
      !!item && typeof item.path === 'string' && typeof item.name === 'string'
    )
  } catch { return [] }
}

function saveProjects(projects: ProjectInfo[]) {
  try { localStorage.setItem(PROJECTS_KEY, JSON.stringify(projects)) } catch { /* storage may be disabled */ }
}

function projectName(path: string) {
  return path.replace(/[\\/]$/, '').split(/[\\/]/).filter(Boolean).at(-1) || path
}

function ProjectRow({ project, active, expanded, onToggle, onProjectChange, onRemoveProject, onNewSession, onLoadSession }: {
  project: ProjectInfo
  active: boolean
  expanded: boolean
  onToggle: (path: string) => void
  onProjectChange: (path: string) => Promise<void>
  onRemoveProject: (path: string) => void
  onNewSession: (path: string) => void
  onLoadSession: (sid: string) => void
}) {
  const queryClient = useQueryClient()
  const { data } = useQuery({
    queryKey: ['sessions', project.path],
    queryFn: () => api.sessions(project.path),
    enabled: expanded,
    refetchInterval: 10000,
  })

  const selectSession = async (sid: string) => {
    try {
      if (!active) await onProjectChange(project.path)
      onLoadSession(sid)
    } catch { /* leave the current project selected if switching fails */ }
  }

  const deleteSession = async (sid: string, event: React.MouseEvent) => {
    event.stopPropagation()
    try {
      await api.deleteSession(sid, project.path)
      await queryClient.invalidateQueries({ queryKey: ['sessions'] })
    } catch { /* keep the session visible when deletion fails */ }
  }

  return (
    <div className="project-row">
      <div className={`project-entry${active ? ' active' : ''}`}>
        <button className="project-entry-main" onClick={() => { onToggle(project.path); if (!active) void onProjectChange(project.path).catch(() => {}) }} title={project.path}>
          <Folder size={15} />
          <span>{project.name}</span>
          {active && <i className="project-active-dot" />}
        </button>
        <button className="project-add-chat" title="在此项目中新建对话" aria-label={`在 ${project.name} 中新建对话`} onClick={() => onNewSession(project.path)}>
          <Plus size={14} />
        </button>
        <button className="project-remove" title="从项目列表移除" aria-label={`从项目列表移除 ${project.name}`} onClick={() => onRemoveProject(project.path)}>
          <Trash2 size={13} />
        </button>
      </div>
      {expanded && (
        <div className="project-chats">
          {(data?.sessions ?? []).length === 0 ? (
            <div className="project-no-chats">暂无对话</div>
          ) : (data?.sessions ?? []).map(session => (
            <div key={session.id} className="project-chat-row">
              <button className="project-chat-entry" onClick={() => void selectSession(session.id)} title={session.title}>
                <MessageSquare size={12} /><span>{session.title}</span>
              </button>
              <button className="project-chat-delete" onClick={event => void deleteSession(session.id, event)} title="删除对话" aria-label={`删除对话 ${session.title}`}><Trash2 size={12} /></button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export function Sidebar({ onNewSession, onNewProjectSession, onLoadSession, onProjectChange, workdir }: SidebarProps) {
  const [savedProjects, setSavedProjects] = useState<ProjectInfo[]>(readProjects)
  const [hiddenProjects, setHiddenProjects] = useState<string[]>(() => {
    try {
      const value: unknown = JSON.parse(localStorage.getItem(HIDDEN_PROJECTS_KEY) || '[]')
      return Array.isArray(value) ? value.filter((path): path is string => typeof path === 'string') : []
    } catch { return [] }
  })
  const [expandedPath, setExpandedPath] = useState(workdir || '')
  const [recentOpen, setRecentOpen] = useState(false)
  const [pickingFolder, setPickingFolder] = useState(false)

  const projects = savedProjects.filter(project => !hiddenProjects.includes(project.path))
  if (workdir && !hiddenProjects.includes(workdir) && !projects.some(project => project.path === workdir)) {
    projects.unshift({ path: workdir, name: projectName(workdir) })
  }

  const recentQueries = useQueries({
    queries: projects.map(project => ({
      queryKey: ['sessions', project.path],
      queryFn: () => api.sessions(project.path),
      enabled: recentOpen,
      refetchInterval: 10000,
    })),
  })
  const recentSessions = recentQueries.flatMap(result => result.data?.sessions ?? [])
    .sort((a, b) => b.updated_at - a.updated_at)
  const queryClient = useQueryClient()

  const deleteRecentSession = async (sid: string, sessionWorkdir: string | undefined, event: React.MouseEvent) => {
    event.stopPropagation()
    try {
      await api.deleteSession(sid, sessionWorkdir || workdir)
      await queryClient.invalidateQueries({ queryKey: ['sessions'] })
    } catch { /* keep the session visible when deletion fails */ }
  }

  const loadRecentSession = async (sid: string, sessionWorkdir?: string) => {
    try {
      if (sessionWorkdir && sessionWorkdir !== workdir) await onProjectChange(sessionWorkdir)
      onLoadSession(sid)
    } catch { /* keep the current project selected if switching fails */ }
  }

  const rememberProject = (project: ProjectInfo) => {
    setHiddenProjects(previous => {
      const next = previous.filter(path => path !== project.path)
      try { localStorage.setItem(HIDDEN_PROJECTS_KEY, JSON.stringify(next)) } catch { /* storage may be disabled */ }
      return next
    })
    setSavedProjects(previous => {
      const next = [project, ...previous.filter(item => item.path !== project.path)]
      saveProjects(next)
      return next
    })
  }

  const removeProject = (path: string) => {
    setSavedProjects(previous => {
      const next = previous.filter(project => project.path !== path)
      saveProjects(next)
      return next
    })
    setHiddenProjects(previous => {
      const next = previous.includes(path) ? previous : [...previous, path]
      try { localStorage.setItem(HIDDEN_PROJECTS_KEY, JSON.stringify(next)) } catch { /* storage may be disabled */ }
      return next
    })
  }

  const selectProjectFolder = async (path: string) => {
    if (path !== workdir) await onProjectChange(path)
    if (workdir && !hiddenProjects.includes(workdir)) rememberProject({ path: workdir, name: projectName(workdir) })
    rememberProject({ path, name: projectName(path) })
    setExpandedPath(path)
    setPickingFolder(false)
  }

  return (
    <aside className="app-sidebar">
      <div className="sidebar-actions">
        <button className="sidebar-new-chat" onClick={onNewSession}>
          <SquarePen size={16} />
          <span>新聊天</span>
          <kbd>⌘ K</kbd>
        </button>
      </div>

      <div className="sidebar-scroll">
        <div className="sidebar-group-title"><span>项目</span><button title="添加项目文件夹" aria-label="添加项目文件夹" onClick={() => setPickingFolder(true)}><Plus size={15} /></button></div>
        <div className="sidebar-project-list">
          {projects.map(project => (
            <ProjectRow
              key={project.path}
              project={project}
              active={project.path === workdir}
              expanded={expandedPath === project.path}
              onToggle={path => setExpandedPath(current => current === path ? '' : path)}
              onRemoveProject={removeProject}
              onProjectChange={async path => {
                if (workdir && !hiddenProjects.includes(workdir)) rememberProject({ path: workdir, name: projectName(workdir) })
                rememberProject({ path, name: projectName(path) })
                await onProjectChange(path)
              }}
              onNewSession={path => {
                if (workdir && !hiddenProjects.includes(workdir)) rememberProject({ path: workdir, name: projectName(workdir) })
                rememberProject({ path: project.path, name: project.name })
                onNewProjectSession(path)
              }}
              onLoadSession={onLoadSession}
            />
          ))}
          {projects.length === 0 && <div className="sidebar-empty-project">先选择工作目录或新建项目</div>}
        </div>

        <button className="sidebar-recent-toggle" onClick={() => setRecentOpen(value => !value)} aria-expanded={recentOpen}>
          <span>最近</span><ChevronRight size={14} className={recentOpen ? 'rotated' : ''} />
        </button>
        {recentOpen && (
          <div className="sidebar-recent-list">
            {recentSessions.slice(0, 12).map(session => (
              <div key={session.id} className="project-chat-row">
                <button className="project-chat-entry" onClick={() => void loadRecentSession(session.id, session.workdir)} title={session.title}>
                  <MessageSquare size={12} /><span>{session.title}</span>
                </button>
                <button className="project-chat-delete" onClick={event => void deleteRecentSession(session.id, session.workdir, event)} title="删除对话" aria-label={`删除对话 ${session.title}`}><Trash2 size={12} /></button>
              </div>
            ))}
            {recentSessions.length === 0 && <div className="project-no-chats">暂无最近对话</div>}
          </div>
        )}
      </div>
      {pickingFolder && <FolderPicker initialPath={workdir} onSelect={selectProjectFolder} onClose={() => setPickingFolder(false)} />}
    </aside>
  )
}
