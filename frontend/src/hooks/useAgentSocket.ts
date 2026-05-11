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

  useEffect(() => {
    // In dev, connect directly to Flask to avoid Vite WS proxy EPIPE errors
    const socketUrl = import.meta.env.DEV ? 'http://localhost:5000' : '/'
    const socket = io(socketUrl, {
      path: '/socket.io',
      transports: ['polling', 'websocket'],
    })
    socketRef.current = socket

    onStatusChange?.('connecting')
    socket.on('connect', () => onStatusChange?.('connected'))
    socket.on('disconnect', () => onStatusChange?.('disconnected'))
    socket.on('agent_event', (ev: AgentEvent) => onEvent(ev))

    return () => { socket.disconnect() }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const sendMessage = useCallback((message: string | ContentBlock[], mode?: string) => {
    socketRef.current?.emit('send_message', { message, mode })
  }, [])

  const sendInterrupt = useCallback(() => {
    socketRef.current?.emit('interrupt')
  }, [])

  return { sendMessage, sendInterrupt }
}
