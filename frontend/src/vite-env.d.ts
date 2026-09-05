/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<Record<string, unknown>, Record<string, unknown>, unknown>
  export default component
}

// Assigned before AMapLoader.load(); required by JS API 2.0. Missing or wrong
// yields INVALID_USER_SCODE at runtime.
interface Window {
  _AMapSecurityConfig?: { securityJsCode: string; serviceHost?: string }
  // Exposed in dev builds so the collaboration invariants can be asserted from
  // the browser console (e.g. comparing two windows' day-order arrays).
  __trip?: unknown
}
