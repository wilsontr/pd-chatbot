/** The session this browser was issued by the server (TASK-5.29).
 *
 * The backend mints a signed session id on the first answer and only that id
 * opens the session's runs and patches, so the browser keeps it to replay its
 * own runs later, including from a new tab or after a reload.
 */

const KEY = 'pd-chatbot.session-id'

export function storedSessionId(): string | null {
  try {
    return window.localStorage.getItem(KEY)
  } catch {
    return null
  }
}

export function storeSessionId(sessionId: string): void {
  try {
    window.localStorage.setItem(KEY, sessionId)
  } catch {
    // Storage can be disabled; the session still works for this page.
  }
}
