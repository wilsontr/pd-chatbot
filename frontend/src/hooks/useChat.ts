import { useState, useRef, useEffect } from 'react'
import type { Message, HistoryItem } from '../types'

export function useChat() {
  const [messages, setMessages] = useState<Message[]>([])
  const [chatHistory, setChatHistory] = useState<HistoryItem[]>([])
  const [loading, setLoading] = useState(false)
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
    const controller = new AbortController()
    abortRef.current = controller

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
      const data = await res.json()
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: data.answer,
        sources: data.sources,
      }])
      setChatHistory(data.history)
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        setMessages(prev => prev.slice(0, -1))
      } else {
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: 'Could not reach the API server. Please try again later.',
        }])
      }
    } finally {
      abortRef.current = null
      setLoading(false)
    }
  }

  return { messages, loading, send }
}
