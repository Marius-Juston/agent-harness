export type Worktree = { path: string; branch: string; ahead: number; dirty: number; last: string }
export type OpsStatus = {
  gate: string
  mem_gb: number
  worktrees: Worktree[]
  quota: string
  quota_pct: number | null
  quota_cap: number
}

declare module 'claude-code' {
  interface PluginState {
    'harness-ops': { status: OpsStatus | null; isHidden: boolean }
  }
}
