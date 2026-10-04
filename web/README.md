# OpenManus Web

The browser client for OpenManus: a React 19 + TypeScript PWA that talks to the FastAPI backend in
`app/web` (see `docs/` for deployment). It works on desktop and phones and can be installed as an app.

## Scripts

| Command                              | What it does                                                                                                                             |
| ------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `npm run dev`                        | Vite dev server on :5173; `/api` is proxied (SSE-safe) to `OPENMANUS_DEV_BACKEND` (default `http://127.0.0.1:8000`)                      |
| `npm run build`                      | Type-checks and builds to `dist/` (served by the backend from `OPENMANUS_STATIC_DIR`)                                                    |
| `npm run preview`                    | Serves `dist/` locally with the same `/api` proxy                                                                                        |
| `npm run typecheck` / `npm run lint` | `tsc -b` (strict) / ESLint flat config                                                                                                   |
| `npm test`                           | Vitest + Testing Library (jsdom)                                                                                                         |
| `npm run format`                     | Prettier (with Tailwind class sorting)                                                                                                   |
| `npm run icons`                      | Regenerates the PWA icons in `public/` from the vector mark (`scripts/generate-icons.mjs`, uses sharp)                                   |
| `npm run test:e2e`                   | Playwright end-to-end tests (`e2e/`, `playwright.config.ts`); run them via `../scripts/e2e.sh`, which starts the fake LLM and the server |

## Layout

```
src/
  api/          REST client (client.ts), wire types (types.ts), SSE parser + resilient run subscription (sse.ts), React Query hooks
  i18n/         typed dictionaries (en.ts is the source of truth, ru.ts mirrors it), plural rules, formatting
  theme/        light / dark / system theme (class strategy; public/theme-init.js applies it before first paint)
  components/   UI primitives: buttons, forms, modal, popover/menu, toasts, markdown, code view, image viewer
  hooks/        media queries, focus trap, visual viewport, speech recognition, clipboard…
  features/
    runs/       run-event reducer (pure, unit-tested), live/lazy run views, activity timeline, ask-human card
    chat/       composer (drafts + uploads), mode picker, message list, timeline ordering, chat actions
    conversations/  sidebar, conversation menu and actions
    files/      files panel, tree, previews (HTML/SVG in a sandboxed iframe)
    settings/   settings tabs (admin: model, search & browser, agents & team, MCP, users)
    shell/      app shell (sidebar / drawer), top bar, banners
  pages/        login, chat, settings
  sw.ts         service worker: precached shell, network-first navigation with offline fallback, never /api
```

Run events flow from `GET /api/runs/{id}/events` (fetch-based SSE with reconnect, resume after the last
`seq`, and a polling fallback) through `features/runs/reducer.ts` into the React Query cache, so the
timeline, the streaming answer, the composer's Stop button and the sidebar all update from one place.

End-to-end selectors use `data-testid` attributes (`composer-input`, `activity`, `tool-call`, …).
