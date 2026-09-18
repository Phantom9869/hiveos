import { createRoot } from 'react-dom/client'

// Self-hosted so a judge's cold load never waits on a font CDN, and the type
// never flashes mid-recording. Latin subsets only — the unscoped entrypoints
// also ship Cyrillic, Greek and Vietnamese, which this UI never renders.
import '@fontsource/ibm-plex-sans/latin-400.css'
import '@fontsource/ibm-plex-sans/latin-500.css'
import '@fontsource/ibm-plex-mono/latin-400.css'
import '@fontsource/ibm-plex-mono/latin-500.css'
import '@fontsource/ibm-plex-mono/latin-600.css'

import './styles.css'
import App from './App'

// No StrictMode: its dev-only double render would open two WebSockets and
// write two CONN# rows, which makes the member count lie while testing.
createRoot(document.getElementById('root')).render(<App />)
