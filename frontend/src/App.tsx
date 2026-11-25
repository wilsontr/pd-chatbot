import { useState, useRef, useEffect, useCallback, FormEvent, KeyboardEvent } from 'react'
import { History, MessageSquare } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useChat } from './hooks/useChat'
import { useInputHistory } from './hooks/useInputHistory'
import { MessageBubble } from './components/MessageBubble'
import { EmptyState } from './components/EmptyState'
import { RunListPage } from './components/RunListPage'
import { RunTimelinePage } from './components/RunTimelinePage'
import { parseRoute, runPath, runsPath, type AppRoute } from './lib/routes'

const MAX_TEXTAREA_HEIGHT = 128 // px, matches max-h-[8rem]

export default function App() {
  const [route, setRoute] = useState<AppRoute>(() =>
    parseRoute(window.location.pathname, window.location.search),
  )
  const { messages, loading, streaming, send } = useChat()
  const inputHistory = useInputHistory()
  const [input, setInput] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const navigate = useCallback((path: string) => {
    const url = new URL(path, window.location.origin)
    if (url.pathname !== window.location.pathname || url.search !== window.location.search) {
      window.history.pushState(null, '', `${url.pathname}${url.search}`)
    }
    setRoute(parseRoute(url.pathname, url.search))
  }, [])

  useEffect(() => {
    function onPopState() {
      setRoute(parseRoute(window.location.pathname, window.location.search))
    }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  function resizeTextarea() {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
    el.style.overflowY = el.scrollHeight > MAX_TEXTAREA_HEIGHT ? 'auto' : 'hidden'
  }

  useEffect(() => { resizeTextarea() }, [input])

  useEffect(() => {
    if (route.view === 'chat') {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [messages, loading, route.view])

  function handleSend(question: string) {
    if (!question.trim() || loading) return
    inputHistory.push(question.trim())
    inputHistory.reset()
    setInput('')
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
      textareaRef.current.style.overflowY = 'hidden'
    }
    send(question)
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    handleSend(input)
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend(input)
      return
    }
    if (e.key === 'ArrowUp' && !e.shiftKey) {
      const text = inputHistory.navigateUp(input)
      if (text !== null) { e.preventDefault(); setInput(text) }
      return
    }
    if (e.key === 'ArrowDown') {
      const text = inputHistory.navigateDown()
      if (text !== null) { e.preventDefault(); setInput(text) }
    }
  }

  const isRunsView = route.view === 'runs' || route.view === 'run'

  return (
    <div className="flex flex-col h-screen bg-background">
      <header className="border-b-2 border-[#d0d0d0] dark:border-[#3a3a3a] bg-[#f0f0f0] dark:bg-[#2a2a2a] px-6 py-4 shrink-0">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-base font-semibold tracking-tight">
              <a
                href="/"
                onClick={e => {
                  if (!e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey) {
                    e.preventDefault()
                    navigate('/')
                  }
                }}
              >
                Pd Documentation Assistant
              </a>
            </h1>
            <p className="text-xs text-muted-foreground mt-0.5">
              Pure Data objects, patching concepts, and audio signal processing. Built by Trevor Wilson.
              &nbsp;
              <a
                href="https://github.com/wilsontr/pd-chatbot"
                target="_blank"
                rel="noreferrer"
                className="underline underline-offset-2 hover:text-muted-foreground transition-colors"
              >
                GitHub repository
              </a>
            </p>
          </div>
          <nav aria-label="Primary" className="flex items-center gap-1">
            <Button
              variant={route.view === 'chat' ? 'secondary' : 'ghost'}
              size="sm"
              onClick={() => navigate('/')}
              className="gap-1.5 h-8 text-xs"
            >
              <MessageSquare className="size-3.5" />
              Chat
            </Button>
            <Button
              variant={isRunsView ? 'secondary' : 'ghost'}
              size="sm"
              onClick={() => navigate(runsPath())}
              className="gap-1.5 h-8 text-xs"
            >
              <History className="size-3.5" />
              Runs
            </Button>
          </nav>
        </div>
      </header>

      {route.view === 'runs' ? (
        <RunListPage sessionId={route.sessionId} onNavigate={navigate} />
      ) : route.view === 'run' ? (
        <RunTimelinePage runId={route.runId} onNavigate={navigate} />
      ) : (
        <>
          <ScrollArea className="flex-1 min-h-0">
            <div className="max-w-[816px] mx-auto px-4 py-8 space-y-6">
              {messages.length === 0 && <EmptyState onSuggest={handleSend} />}

              {messages.map((msg, i) => (
                <MessageBubble
                  key={i}
                  message={msg}
                  isStreaming={streaming && i === messages.length - 1}
                  onOpenRun={id => navigate(runPath(id))}
                />
              ))}

              {loading && !streaming && (
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

          <form onSubmit={handleSubmit} className="border-t-2 border-[#d0d0d0] dark:border-[#3a3a3a] bg-[#f0f0f0] dark:bg-[#2a2a2a] px-4 py-3 shrink-0">
            <div className="max-w-[816px] mx-auto flex items-end gap-2">
              <Textarea
                ref={textareaRef}
                value={input}
                onChange={e => { setInput(e.target.value); inputHistory.reset() }}
                onKeyDown={handleKeyDown}
                placeholder="Ask about Pure Data…"
                disabled={loading}
                autoFocus
                rows={1}
                className="flex-1 resize-none overflow-y-hidden max-h-[8rem] min-h-0 py-2 leading-relaxed bg-[#fff] dark:bg-[#1a1a1a] border-[#d0d0d0] dark:border-[#3a3a3a]"
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
              {', '}
              <a href="https://pd.iem.sh/objects/" target="_blank" rel="noreferrer"
                 className="underline underline-offset-2 hover:text-muted-foreground transition-colors">
                IEM Object Reference
              </a>
              {', or '}
              <a href="http://msp.ucsd.edu/techniques.htm" target="_blank" rel="noreferrer"
                 className="underline underline-offset-2 hover:text-muted-foreground transition-colors">
                Theory and Techniques of Electronic Music
              </a>
            </p>
          </form>
        </>
      )}
    </div>
  )
}
