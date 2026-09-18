import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Relative base so the built bundle works from any Amplify path or a local
// `vite preview` without a rebuild.
export default defineConfig({
  base: './',
  plugins: [react()],
  build: {
    outDir: 'dist',
    // The demo is judged from a recording; a source map costs nothing to ship
    // and makes a console error on the day actually readable.
    sourcemap: true,
  },
})
