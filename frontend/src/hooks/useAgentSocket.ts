import { useEffect, useRef } from 'react'
import { io } from 'socket.io-client'
import type { AgentEvent } from '../lib/types'

export type SocketStatus = 'connecting' | 'connected' | 'disconnected'

interface UseSocketOptions {
  onEvent: (ev: AgentEvent) => void
  onStatusChange?: (s: SocketStatus) => void
  /** 断线后重连成功时触发（首次连接不触发），用于自动恢复对话数据 */
  onReconnect?: () => void
}

export function useAgentSocket({ onEvent, onStatusChange, onReconnect }: UseSocketOptions) {
  const onEventRef = useRef(onEvent)
  const onStatusRef = useRef(onStatusChange)
  const onReconnectRef = useRef(onReconnect)

  useEffect(() => {
    onEventRef.current = onEvent
    onStatusRef.current = onStatusChange
    onReconnectRef.current = onReconnect
  }, [onEvent, onStatusChange, onReconnect])

  useEffect(() => {
    const socket = io({
      path: '/socket.io',
      transports: ['polling', 'websocket'],
      reconnection: true,
      reconnectionAttempts: Infinity,
      reconnectionDelay: 1000,
    })
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

}
