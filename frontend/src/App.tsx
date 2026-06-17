import { useState, useRef, useEffect, FormEvent, KeyboardEvent } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'

interface Source {
  heading_path: string
  url: string
  source: 'msp_manual' | 'iem_reference'
  content_type: 'conceptual' | 'object_reference'
  object_name: string | null
}

interface Message {
  role: 'user' | 'assistant'
  content: string
  sources?: Source[]
}

interface HistoryItem {
  role: 'user' | 'assistant'
  content: string
}

const SUGGESTIONS = [
  'What does osc~ do?',
  'How does message passing work in Pd?',
  'How do I use tabread4~ for wavetable synthesis?',
  'What are the inlets of pack?',
]

const HISTORY_KEY = 'pd-chat-input-history'
const HISTORY_MAX = 50

export default function App() {
  const [messages, setMessages] = useState<Message[]>([])
  const [history, setHistory] = useState<HistoryItem[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [inputHistory, setInputHistory] = useState<string[]>(() => {
    try { return JSON.parse(localStorage.getItem(HISTORY_KEY) ?? '[]') } catch { return [] }
  })
  const [historyCursor, setHistoryCursor] = useState(-1)
  const draftRef = useRef('')
  const abortRef = useRef<AbortController | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const MAX_TEXTAREA_HEIGHT = 128 // matches max-h-[8rem]

  function resizeTextarea() {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
    el.style.overflowY = el.scrollHeight > MAX_TEXTAREA_HEIGHT ? 'auto' : 'hidden'
  }

  useEffect(() => {
    resizeTextarea()
  }, [input])

  useEffect(() => {
    function onKeyDown(e: globalThis.KeyboardEvent) {
      if (e.key === 'Escape' && abortRef.current) {
        abortRef.current.abort()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  async function send(question: string) {
    if (!question.trim() || loading) return

    const trimmed = question.trim()
    const updated = [trimmed, ...inputHistory.filter(h => h !== trimmed)].slice(0, HISTORY_MAX)
    setInputHistory(updated)
    localStorage.setItem(HISTORY_KEY, JSON.stringify(updated))
    setHistoryCursor(-1)
    draftRef.current = ''

    setMessages(prev => [...prev, { role: 'user', content: question }])
    setInput('')
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
      textareaRef.current.style.overflowY = 'hidden'
    }
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
        body: JSON.stringify({ message: question, history }),
        signal: controller.signal,
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: data.answer,
        sources: data.sources,
      }])
      setHistory(data.history)
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        setMessages(prev => prev.slice(0, -1)) // remove the pending user message
      } else {
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: 'Could not reach the API server. Make sure uvicorn is running on port 8000.',
        }])
      }
    } finally {
      abortRef.current = null
      setLoading(false)
    }
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    send(input.trim())
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send(input.trim())
      return
    }
    if (e.key === 'ArrowUp' && !e.shiftKey && inputHistory.length > 0) {
      e.preventDefault()
      if (historyCursor === -1) draftRef.current = input
      const next = Math.min(historyCursor + 1, inputHistory.length - 1)
      setHistoryCursor(next)
      setInput(inputHistory[next])
      return
    }
    if (e.key === 'ArrowDown' && historyCursor !== -1) {
      e.preventDefault()
      const next = historyCursor - 1
      setHistoryCursor(next)
      setInput(next === -1 ? draftRef.current : inputHistory[next])
    }
  }

  return (
    <div className="flex flex-col h-screen bg-background">
      <header className="border-b px-6 py-4 shrink-0">
        <h1 className="text-base font-semibold tracking-tight">Pd Documentation Assistant</h1>
        <p className="text-xs text-muted-foreground mt-0.5">
          Pure Data objects, patching concepts, and audio signal processing
        </p>
      </header>

      <ScrollArea className="flex-1 min-h-0">
        <div className="max-w-[816px] mx-auto px-4 py-8 space-y-6">

          {messages.length === 0 && (
            <div className="space-y-6 text-center">
              <div className="space-y-2">
                <p className="text-sm font-medium text-foreground">Ask a question to get started</p>
                <p className="text-xs text-muted-foreground">
                  Searches the{' '}
                  <a href="https://msp.ucsd.edu/Pd_documentation/" target="_blank" rel="noreferrer"
                     className="underline underline-offset-2 hover:text-foreground transition-colors">
                    Pure Data Manual
                  </a>
                  {' '}and{' '}
                  <a href="https://pd.iem.sh/objects/" target="_blank" rel="noreferrer"
                     className="underline underline-offset-2 hover:text-foreground transition-colors">
                    IEM Object Reference
                  </a>
                </p>
                <p className="text-xs text-muted-foreground/70 max-w-sm mx-auto">
                  Answers may be incomplete or incorrect. Always verify against the original documentation.
                </p>
              </div>
              <div className="flex flex-wrap justify-center gap-2">
                {SUGGESTIONS.map(s => (
                  <button
                    key={s}
                    onClick={() => send(s)}
                    className="text-xs px-3 py-1.5 rounded-full border border-border bg-muted/50
                               text-muted-foreground hover:bg-muted hover:text-foreground
                               transition-colors cursor-pointer"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((msg, i) => (
            <div
              key={i}
              className={`flex gap-3 ${msg.role === 'user' ? 'flex-row-reverse' : 'flex-row'}`}
            >
              <div className={`flex flex-col gap-2 max-w-[88%] ${msg.role === 'user' ? 'items-end' : 'items-start'}`}>
                <div className={`rounded-xl px-4 py-2.5 text-sm leading-relaxed ${
                  msg.role === 'user'
                    ? 'bg-primary text-primary-foreground whitespace-pre-wrap'
                    : 'bg-muted text-foreground prose-bubble'
                }`}>
                  {msg.role === 'user' ? msg.content : (
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      components={{
                        table: ({ children }) => (
                          <div className="overflow-x-auto my-2">
                            <table className="w-full text-xs border-collapse">{children}</table>
                          </div>
                        ),
                        thead: ({ children }) => <thead className="bg-background/60">{children}</thead>,
                        tbody: ({ children }) => <tbody>{children}</tbody>,
                        tr: ({ children }) => <tr className="border-b border-foreground/10">{children}</tr>,
                        th: ({ children }) => <th className="text-left font-semibold px-3 py-1.5 whitespace-nowrap">{children}</th>,
                        td: ({ children }) => <td className="px-3 py-1.5 align-top">{children}</td>,
                        h1: ({ children }) => <h1 className="text-base font-semibold mt-3 mb-1 first:mt-0">{children}</h1>,
                        h2: ({ children }) => <h2 className="text-sm font-semibold mt-3 mb-1 first:mt-0">{children}</h2>,
                        h3: ({ children }) => <h3 className="text-sm font-medium mt-2 mb-0.5 first:mt-0">{children}</h3>,
                        p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
                        ul: ({ children }) => <ul className="list-disc pl-4 mb-2 space-y-0.5">{children}</ul>,
                        ol: ({ children }) => <ol className="list-decimal pl-4 mb-2 space-y-0.5">{children}</ol>,
                        li: ({ children }) => <li className="leading-relaxed">{children}</li>,
                        code: ({ children, className }) =>
                          className ? (
                            <code className="block bg-background/60 rounded px-3 py-2 my-2 text-xs font-mono overflow-x-auto">{children}</code>
                          ) : (
                            <code className="bg-background/60 rounded px-1 py-0.5 text-xs font-mono">{children}</code>
                          ),
                        pre: ({ children }) => <>{children}</>,
                        strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
                        a: ({ href, children }) => (
                          <a href={href} target="_blank" rel="noreferrer" className="underline underline-offset-2 opacity-80 hover:opacity-100">{children}</a>
                        ),
                        blockquote: ({ children }) => (
                          <blockquote className="border-l-2 border-foreground/30 pl-3 italic opacity-80 my-2">{children}</blockquote>
                        ),
                      }}
                    >
                      {msg.content}
                    </ReactMarkdown>
                  )}
                </div>

                {msg.sources && msg.sources.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 px-0.5">
                    {msg.sources.map((src, j) => (
                      <a key={j} href={src.url} target="_blank" rel="noreferrer">
                        <Badge
                          variant="outline"
                          className={`text-xs font-normal hover:bg-accent transition-colors ${
                            src.source === 'iem_reference'
                              ? 'border-blue-200 text-blue-700 hover:border-blue-300 dark:border-blue-800 dark:text-blue-400'
                              : 'border-border text-muted-foreground'
                          }`}
                        >
                          {src.object_name ?? src.heading_path.split(' > ').pop()}
                        </Badge>
                      </a>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}

          {loading && (
            <div className="flex gap-3">
              <div className="bg-muted rounded-xl px-4 py-2.5 text-sm text-muted-foreground">
                <span className="inline-flex gap-1">
                  <span className="animate-bounce [animation-delay:0ms]">.</span>
                  <span className="animate-bounce [animation-delay:150ms]">.</span>
                  <span className="animate-bounce [animation-delay:300ms]">.</span>
                </span>
              </div>
            </div>
          )}

          <div ref={bottomRef} />
        </div>
      </ScrollArea>

      <Separator />

      <form onSubmit={handleSubmit} className="px-4 py-3 shrink-0">
        <div className="max-w-[816px] mx-auto flex items-end gap-2">
          <Textarea
            ref={textareaRef}
            value={input}
            onChange={e => {
              setInput(e.target.value)
              setHistoryCursor(-1)
              draftRef.current = e.target.value
            }}
            onKeyDown={handleKeyDown}
            placeholder="Ask about Pure Data…"
            disabled={loading}
            autoFocus
            rows={1}
            className="flex-1 resize-none overflow-y-hidden max-h-[8rem] min-h-0 py-2 leading-relaxed"
          />
          <Button type="submit" disabled={loading || !input.trim()}>
            Send
          </Button>
        </div>
        <p className="max-w-[816px] mx-auto mt-1.5 text-[11px] text-muted-foreground/50 text-center">
          May produce incorrect answers — verify with the{' '}
          <a href="https://msp.ucsd.edu/Pd_documentation/" target="_blank" rel="noreferrer"
             className="underline underline-offset-2 hover:text-muted-foreground transition-colors">
            MSP Manual
          </a>
          {' '}or{' '}
          <a href="https://pd.iem.sh/objects/" target="_blank" rel="noreferrer"
             className="underline underline-offset-2 hover:text-muted-foreground transition-colors">
            IEM Object Reference
          </a>
        </p>
      </form>
    </div>
  )
}
