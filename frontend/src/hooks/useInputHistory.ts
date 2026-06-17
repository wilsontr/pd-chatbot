import { useState, useRef } from 'react'

const HISTORY_KEY = 'pd-chat-input-history'
const HISTORY_MAX = 50

export function useInputHistory() {
  const [items, setItems] = useState<string[]>(() => {
    try { return JSON.parse(localStorage.getItem(HISTORY_KEY) ?? '[]') } catch { return [] }
  })
  const [cursor, setCursor] = useState(-1)
  const draftRef = useRef('')

  function push(text: string) {
    const updated = [text, ...items.filter(h => h !== text)].slice(0, HISTORY_MAX)
    setItems(updated)
    localStorage.setItem(HISTORY_KEY, JSON.stringify(updated))
  }

  function reset() {
    setCursor(-1)
    draftRef.current = ''
  }

  // Returns the text to display, or null if there is no history to navigate.
  // Saves the current input as a draft on the first upward press.
  function navigateUp(currentInput: string): string | null {
    if (items.length === 0) return null
    if (cursor === -1) draftRef.current = currentInput
    const next = Math.min(cursor + 1, items.length - 1)
    setCursor(next)
    return items[next]
  }

  // Returns the text to display, or null if already at the live input (cursor = -1).
  function navigateDown(): string | null {
    if (cursor === -1) return null
    const next = cursor - 1
    setCursor(next)
    return next === -1 ? draftRef.current : items[next]
  }

  return { items, push, reset, navigateUp, navigateDown }
}
