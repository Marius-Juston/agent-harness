import { expect, test } from 'claude-code/testing'

import { line } from './register'

test('band line names agents, memory and quota', async () => {
  const text = line({
    gate: 'idle',
    mem_gb: 12.5,
    quota: '',
    quota_pct: 40,
    quota_cap: 95,
    worktrees: [
      { path: '/r', branch: 'main', ahead: 0, dirty: 0, last: '' },
      { path: '/w', branch: 'worktree-12-notes', ahead: 2, dirty: 1, last: '' },
    ],
  })
  expect(text).toBe('gate idle · agents 12-notes+2* · 12.5 GB free · quota 40%/95%')
})
