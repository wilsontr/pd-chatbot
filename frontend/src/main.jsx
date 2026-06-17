import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App'
import { BrowserAgent } from '@newrelic/browser-agent/loaders/browser-agent'

if (import.meta.env.VITE_NR_LICENSE_KEY) {
  new BrowserAgent({
    init: { distributed_tracing: { enabled: true }, privacy: { cookies_enabled: true } },
    info: {
      beacon: 'bam.nr-data.net',
      errorBeacon: 'bam.nr-data.net',
      licenseKey: import.meta.env.VITE_NR_LICENSE_KEY,
      applicationID: import.meta.env.VITE_NR_APP_ID,
      sa: 1,
    },
    loader_config: {
      accountID: import.meta.env.VITE_NR_ACCOUNT_ID,
      trustKey: import.meta.env.VITE_NR_ACCOUNT_ID,
      agentID: import.meta.env.VITE_NR_APP_ID,
      licenseKey: import.meta.env.VITE_NR_LICENSE_KEY,
      applicationID: import.meta.env.VITE_NR_APP_ID,
    },
  })
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
