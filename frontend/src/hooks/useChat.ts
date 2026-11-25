import { useState, useRef, useEffect } from 'react'
import { applyStreamEvent, finalizeRunningSteps, type StreamEvent } from '../lib/chatStream'
import type { Message, HistoryItem } from '../types'

export function useChat() {
  const [messages, setMessages] = useState<Message[]>([])
  const [chatHistory, setChatHistory] = useState<HistoryItem[]>([])
  const [loading, setLoading] = useState(false)
  const [streaming, setStreaming] = useState(false)
  const sessionIdRef = useRef<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape' && abortRef.current) {
        abortRef.current.abort()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [])

  async function send(question: string) {
    if (!question.trim() || loading) return

    setMessages(prev => [...prev, { role: 'user', content: question }])
    setLoading(true)
    setStreaming(false)
    const controller = new AbortController()
    abortRef.current = controller

    let hasStreamed = false
    let sawDone = false

    function updateAssistant(updater: (msg: Message) => Message) {
      if (!hasStreamed) {
        hasStreamed = true
        setStreaming(true)
        setMessages(prev => [...prev, updater({ role: 'assistant', content: '' })])
      } else {
        setMessages(prev => {
          const last = prev[prev.length - 1]
          return [...prev.slice(0, -1), updater(last)]
        })
      }
    }

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-API-Key': import.meta.env.VITE_CHAT_API_KEY ?? '',
        },
        body: JSON.stringify({
          message: question,
          history: chatHistory,
          ...(sessionIdRef.current ? { session_id: sessionIdRef.current } : {}),
        }),
        signal: controller.signal,
      })

      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      if (!res.body) throw new Error('No response body')

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        const parts = buffer.split('\n\n')
        buffer = parts.pop() ?? ''

        for (const part of parts) {
          const line = part.trim()
          if (!line.startsWith('data: ')) continue
          const event = JSON.parse(line.slice(6)) as StreamEvent

          if (event.type === 'done') {
            sawDone = true
            if (event.session_id) {
              sessionIdRef.current = event.session_id
            }
            updateAssistant(last => applyStreamEvent(last, event))
            if (Array.isArray(event.history)) {
              setChatHistory(event.history)
            }
          } else if (
            event.type === 'meta' ||
            event.type === 'chunk' ||
            event.type === 'text_reset' ||
            event.type === 'tool_call' ||
            event.type === 'tool_result' ||
            event.type === 'artifact'
          ) {
            updateAssistant(last => applyStreamEvent(last, event))
          }
        }
      }

      if (hasStreamed && !sawDone) {
        setMessages(prev => {
          const last = prev[prev.length - 1]
          return [...prev.slice(0, -1), { ...last, steps: finalizeRunningSteps(last.steps, 'error') }]
        })
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        setMessages(prev => prev.slice(0, hasStreamed ? -2 : -1))
      } else {
        setMessages(prev => {
          const next = hasStreamed
            ? [
                ...prev.slice(0, -1),
                { ...prev[prev.length - 1], steps: finalizeRunningSteps(prev[prev.length - 1].steps, 'error') },
              ]
            : prev
          return [
            ...next,
            {
              role: 'assistant',
              content: 'Could not reach the API server. Please try again later.',
            },
          ]
        })
      }
    } finally {
      abortRef.current = null
      setLoading(false)
      setStreaming(false)
    }
  }

  return { messages, loading, streaming, send }
}
