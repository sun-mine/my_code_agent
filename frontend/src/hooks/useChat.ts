import { useCallback, useSyncExternalStore } from 'react'
import type { AgentEvent, ChatMessage, ContentBlock } from '../lib/types'
import { chatStore, type HydrateSnapshot } from '../lib/chatStore'

export type {
  ApprovalRequest,
  ChatSessionState,
  FileChange,
  PlanOption,
  PlanProposal,
  ToolCall,
} from '../lib/chatStore'

export function useChat() {
  const state = useSyncExternalStore(
    chatStore.subscribe,
    chatStore.getSnapshot,
    chatStore.getSnapshot,
  )

  const activateSession = useCallback((sessionId: string) => {
    chatStore.activateSession(sessionId)
  }, [])

  const hydrateSession = useCallback((
    sessionId: string,
    messages: ChatMessage[],
    snapshot: HydrateSnapshot,
  ) => {
    chatStore.hydrateSession(sessionId, messages, snapshot)
  }, [])

  const addUserMessage = useCallback((content: string | ContentBlock[]) => {
    chatStore.addUserMessage(content)
  }, [])

  const handleEvent = useCallback((event: AgentEvent) => {
    chatStore.handleEvent(event)
  }, [])

  const clearAll = useCallback(() => chatStore.clearAll(), [])
  const restoreMessages = useCallback((messages: ChatMessage[]) => chatStore.restoreMessages(messages), [])
  const dismissApproval = useCallback((id: string) => chatStore.dismissApproval(id), [])
  const dismissPlan = useCallback(() => chatStore.dismissPlan(), [])
  const setPlanProposal = useCallback(
    (...args: Parameters<typeof chatStore.setPlanProposal>) => chatStore.setPlanProposal(...args),
    [],
  )
  const setPlanOptions = useCallback(
    (...args: Parameters<typeof chatStore.setPlanOptions>) => chatStore.setPlanOptions(...args),
    [],
  )

  return {
    ...state,
    activeSessionId: chatStore.getActiveSessionId(),
    activateSession,
    hydrateSession,
    addUserMessage,
    handleEvent,
    clearAll,
    restoreMessages,
    dismissApproval,
    dismissPlan,
    setPlanProposal,
    setPlanOptions,
  }
}
