import { useEffect, useRef, useCallback } from 'react'
import { io, Socket } from 'socket.io-client'
import type { AgentEvent, ContentBlock } from '../lib/types'

export type SocketStatus = 'connecting' | 'connected' | 'disconnected'

interface UseSocketOptions {
  onEvent: (ev: AgentEvent) => void
  onStatusChange?: (s: SocketStatus) => void
}

export function useAgentSocket({ onEvent, onStatusChange }: UseSocketOptions) {
  const socketRef = useRef<Socket | null>(null)
  const onEventRef = useRef(onEvent)
  const onStatusRef = useRef(onStatusChange)
  onEventRef.current = onEvent
  onStatusRef.current = onStatusChange

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

    onStatusRef.current?.('connecting')
    socket.on('connect', () => onStatusRef.current?.('connected'))
    socket.on('disconnect', () => onStatusRef.current?.('disconnected'))
    socket.on('connect_error', () => onStatusRef.current?.('disconnected'))
    socket.on('agent_event', (ev: AgentEvent) => onEventRef.current(ev))

    return () => { socket.disconnect() }
  }, [])

  const sendMessage = useCallback((message: string | ContentBlock[], mode?: string) => {
    const socket = socketRef.current
    if (!socket || !socket.connected) {
      // 连接未就绪：直接在前端抛出错误事件，避免"发了没反应"
      onEventRef.current({
        type: 'error',
        message: socket ? '连接已断开，正在重连…请稍候重试' : '尚未连接到服务器，请刷新页面',
        ts: Date.now() / 1000,
      } as AgentEvent)
      return
    }
    socket.emit('send_message', { message, mode })
  }, [])

  const sendInterrupt = useCallback(() => {
    socketRef.current?.emit('interrupt')
  }, [])

  return { sendMessage, sendInterrupt }
}
