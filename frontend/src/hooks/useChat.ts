import { useState, useRef, useEffect } from 'react'
import type { Message, HistoryItem } from '../types'

function getDisplayContent(raw: string): string {
  const lastOpen = raw.lastIndexOf('```pd-patch')
  if (lastOpen === -1) return raw
  const after = raw.slice(lastOpen + 11)
  if (after.includes('```')) return raw
  return raw.slice(0, lastOpen) + '\n```loading-patch\n```\n'
}

export function useChat() {
  const [messages, setMessages] = useState<Message[]>([])
  const [chatHistory, setChatHistory] = useState<HistoryItem[]>([])
  const [loading, setLoading] = useState(false)
  const [streaming, setStreaming] = useState(false)
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

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-API-Key': import.meta.env.VITE_CHAT_API_KEY ?? '',
        },
        body: JSON.stringify({ message: question, history: chatHistory }),
        signal: controller.signal,
      })

      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      if (!res.body) throw new Error('No response body')

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let rawContent = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        const parts = buffer.split('\n\n')
        buffer = parts.pop() ?? ''

        for (const part of parts) {
          const line = part.trim()
          if (!line.startsWith('data: ')) continue
          const event = JSON.parse(line.slice(6))

          if (event.type === 'meta') {
            hasStreamed = true
            setStreaming(true)
            setMessages(prev => [...prev, {
              role: 'assistant' as const,
              content: '',
              sources: event.sources,
            }])
          } else if (event.type === 'chunk') {
            rawContent += event.text
            const displayContent = getDisplayContent(rawContent)
            setMessages(prev => {
              const last = prev[prev.length - 1]
              return [...prev.slice(0, -1), { ...last, content: displayContent }]
            })
          } else if (event.type === 'done') {
            // Ensure final content is raw (replaces any leftover placeholder)
            setMessages(prev => {
              const last = prev[prev.length - 1]
              return [...prev.slice(0, -1), { ...last, content: rawContent, messageId: event.message_id }]
            })
            setChatHistory(event.history)
          }
        }
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        setMessages(prev => prev.slice(0, hasStreamed ? -2 : -1))
      } else {
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: 'Could not reach the API server. Please try again later.',
        }])
      }
    } finally {
      abortRef.current = null
      setLoading(false)
      setStreaming(false)
    }
  }

  return { messages, loading, streaming, send }
}
