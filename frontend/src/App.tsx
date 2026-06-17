import { useState, useRef, useEffect, FormEvent, KeyboardEvent } from 'react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import { useChat } from './hooks/useChat'
import { useInputHistory } from './hooks/useInputHistory'
import { MessageBubble } from './components/MessageBubble'
import { EmptyState } from './components/EmptyState'

const MAX_TEXTAREA_HEIGHT = 128 // px, matches max-h-[8rem]

export default function App() {
  const { messages, loading, send } = useChat()
  const inputHistory = useInputHistory()
  const [input, setInput] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  function resizeTextarea() {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
    el.style.overflowY = el.scrollHeight > MAX_TEXTAREA_HEIGHT ? 'auto' : 'hidden'
  }

  useEffect(() => { resizeTextarea() }, [input])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

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
          {messages.length === 0 && <EmptyState onSuggest={handleSend} />}

          {messages.map((msg, i) => <MessageBubble key={i} message={msg} />)}

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
            onChange={e => { setInput(e.target.value); inputHistory.reset() }}
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
    </div>
  )
}
