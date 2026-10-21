import { useEffect, useRef, useCallback } from 'react'
import { io, Socket } from 'socket.io-client'
import type { AgentEvent, ContentBlock } from '../lib/types'

export type SocketStatus = 'connecting' | 'connected' | 'disconnected'

interface UseSocketOptions {
  onEvent: (ev: AgentEvent) => void
  onStatusChange?: (s: SocketStatus) => void
  /** 断线后重连成功时触发（首次连接不触发），用于自动恢复对话数据 */
  onReconnect?: () => void
}

export function useAgentSocket({ onEvent, onStatusChange, onReconnect }: UseSocketOptions) {
  const socketRef = useRef<Socket | null>(null)
  const onEventRef = useRef(onEvent)
  const onStatusRef = useRef(onStatusChange)
  const onReconnectRef = useRef(onReconnect)

  useEffect(() => {
    onEventRef.current = onEvent
    onStatusRef.current = onStatusChange
    onReconnectRef.current = onReconnect
  }, [onEvent, onStatusChange, onReconnect])

  useEffect(() => {
    const socketUrl = import.meta.env.DEV ? 'http://localhost:8000' : '/'
    const socket = io(socketUrl, {
      path: '/socket.io',
      transports: ['polling', 'websocket'],
      reconnection: true,
      reconnectionAttempts: Infinity,
      reconnectionDelay: 1000,
    })
    socketRef.current = socket
    let hasConnectedOnce = false

    onStatusRef.current?.('connecting')
    socket.on('connect', () => {
      onStatusRef.current?.('connected')
      if (hasConnectedOnce) {
        // 这是断线后的重连：自动同步服务器端最新状态，无需用户刷新页面
        onReconnectRef.current?.()
      }
      hasConnectedOnce = true
    })
    socket.on('disconnect', () => onStatusRef.current?.('disconnected'))
    socket.on('connect_error', () => onStatusRef.current?.('disconnected'))
    socket.on('agent_event', (ev: AgentEvent) => onEventRef.current(ev))

    return () => { socket.disconnect() }
  }, [])

  const sendMessage = useCallback((
    message: string | ContentBlock[],
    mode?: string,
    sessionId?: string,
  ): boolean => {
    const socket = socketRef.current
    if (!socket || !socket.connected) {
      // 主动触发立即重连（不等下一次自动重试）
      socket?.connect()
      onEventRef.current({
        type: 'error',
        message: socket ? '连接已断开，正在重连…请稍候重试' : '尚未连接到服务器，请刷新页面',
        session_id: sessionId,
        ts: Date.now() / 1000,
      } as AgentEvent)
      return false
    }
    socket.emit('send_message', { message, mode, session_id: sessionId })
    return true
  }, [])

  const sendInterrupt = useCallback((sessionId?: string) => {
    socketRef.current?.emit('interrupt', { session_id: sessionId })
  }, [])

  return { sendMessage, sendInterrupt }
}
