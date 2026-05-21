# frontend/

### Test Infrastructure
- `vitest.config.ts`: Vitest test runner configuration (jsdom environment, globals, setup files)
- `src/test/setup.ts`: Test setup importing @testing-library/jest-dom matchers
- `src/**/*.test.{ts,tsx}`: Co-located unit tests alongside source files


## Responsibility

- Hosts the Vite-powered React SPA (see `package.json` scripts) that ships the operations workspace: dashboards, orders, delivery schedules, admin tooling, QA flows, and supporting utilities such as the document signing and tagging surfaces.
- Supplies the runtime experience for authenticated users, keeping UI state, navigation, toast notifications, and animation wrappers inside the SPA so backend/Flask only needs to serve `frontend/dist/` in production.

## Design

- Entry and build: `src/main.tsx` boots React StrictMode, pulls in `src/index.css`, and renders `App`. `vite.config.ts` enables hot dev servers with proxies for `/api` and `/socket.io`, while `package.json` defines the usual `dev`, `build`, and linting scripts that run `tsc`, Vite, and ESLint/Tailwind tooling.
- Layout/routing: `src/App.tsx` wires `BrowserRouter`, `AuthProvider`, centralized app/route error boundaries, lazy-loaded page routes, and a layout that combines `Sidebar`, `Breadcrumbs`, `SyncHealthBanner`, and `Toaster`. Framer Motion wrappers animate route transitions while Suspense and error-boundary fallbacks protect lazy loads and unexpected render crashes.
- Auth & guards: `src/contexts/AuthContext.tsx` handles `/auth/me` polling, SAML redirects, and session state; `ProtectedRoute` gates pages and saves the intended destination before redirecting to `/login`.
- Componentization: UI primitives live in `src/components/ui` (buttons, cards, tabs, etc.), while feature sets (delivery controls, dashboards, charts, checkout panels, and shared error-boundary wrappers under `src/components/error-boundaries`) live under `src/components`. `src/pages` holds the route-level screens (dashboard, orders, shipping, admin, QA, delivery dispatch, etc.).
- Data access: `src/api` centralizes axios clients (with interceptors that redirect 401s to `/login`) and resource-specific modules (orders, delivery runs, vetting, analytics, etc.); hooks under `src/hooks` consume those APIs plus WebSocket helpers (`useOrdersWebSocket`) to offer declarative status-aware data for components.

## Flow

- Startup: Vite builds the tree defined in `src/App.tsx` where `AuthProvider` fetches `/auth/me` (without the `/api` prefix) and sets `isAuthenticated` before mounting the main layout.
- Navigation: `BrowserRouter` drives routes defined inside a nested `Routes` tree; unprotected `/login` sits outside the main shell, while internal routes are wrapped in `ProtectedRoute` to block unauthenticated access and to rehydrate state stored in `sessionStorage`.
- Data & control: Hooks such as `useOrders`, `useDeliveryRuns`, `useVehicleStatuses`, and `useDeliveryRun` combine WebSocket pushes (via `useOrdersWebSocket`) with HTTP fallbacks from `ordersApi` to keep order lists and runs fresh. Axios client uses `import.meta.env.VITE_API_URL` or `/api` and attaches credentials, while Socket.IO clients connect via `/socket.io` to the same backend host defined in `vite.config.ts`.
- UI updates: Protected pages consume the hooks to render charts (`src/components/charts`), tables (`OrderTable`), dashboards, and delivery controls, while `StatusTransition`, `SyncHealthBanner`, toast notifications, and the centralized error-boundary fallbacks surface health, status, and crash-recovery affordances triggered by API responses or render failures.

## Integration

- Backend: Dev-time proxies (`vite.config.ts`) and axios baseURL anchor API calls and WebSocket traffic to `http://localhost:8000`, while in production `VITE_API_URL` can point to the Flask app that serves `/api/*` plus `/socket.io`.
- Auth: All requests and route guards depend on `AuthContext`, which uses `axios` directly (with `withCredentials`) for `/auth` endpoints, and the axios client uses interceptors to redirect 401s to `/login`.
- WebSocket: `useOrdersWebSocket` and related hooks emit `join` events for rooms like `orders` and fall back to HTTP when the socket cannot connect; the same Socket.IO endpoint uses the `/socket.io` path configured in both `vite.config.ts` and the backend.
- Shared utils: `src/lib` and `src/utils` provide formatting helpers (timezone helpers, ID parsers, signature cache) so pages and components can reuse domain logic without duplicating API calls.
- Assets/config: `tailwind.config.js`, `postcss.config.js`, and `src/index.css` define the theming and CSS variables that the layout (sidebar width CSS variables, background colors, etc.) relies on across components.
