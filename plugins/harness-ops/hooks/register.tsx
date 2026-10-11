import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { OpsStatus } from '../types'

// One source: the repository's own `dev status --json` (devkit/workflow.py). The band, /ops and agents read it.
const status = atom({ plugin: 'harness-ops', key: 'status' } as const, null)
const isHidden = atom({ plugin: 'harness-ops', key: 'isHidden' } as const, false)

const POLL_MS = 20_000
const LOW_MEM_GB = 4

export function line(s: OpsStatus): string {
  const agents = s.worktrees
    .filter(w => w.branch !== 'main')
    .map(w => `${w.branch.replace(/^worktree-/, '')}+${w.ahead}${w.dirty ? '*' : ''}`)
  const quota = s.quota_pct == null ? 'quota ?' : `quota ${s.quota_pct}%/${s.quota_cap}%`
  const gate = s.gate.startsWith('running') ? 'gate running' : 'gate idle'
  return `${gate} · agents ${agents.join(' ') || 'none'} · ${s.mem_gb} GB free · ${quota}`
}

let root = ''
let warned = ''

async function refresh($: EngineInterface) {
  if (!root) return
  try {
    const ran = await $.process.run([`${root}/.venv/bin/dev`, 'status', '--json'], { cwd: root, timeoutMs: 20_000 })
    if (ran.exitCode !== 0) return
    const next: OpsStatus = JSON.parse(ran.stdout)
    const alarm =
      next.mem_gb < LOW_MEM_GB
        ? `Low memory: ${next.mem_gb} GB free (hold heavy agents)`
        : next.quota_pct != null && next.quota_pct >= next.quota_cap
          ? `Quota at ${next.quota_pct}% (cap ${next.quota_cap}%): stop spawning, commit and hand off`
          : ''
    if (alarm && alarm !== warned) $.ui.toast(alarm)
    warned = alarm
    await update($, status, () => next)
  } catch {
    // Not a devkit project or no venv yet (`uv run dev setup`): show nothing.
  }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    const cwd = await $.session.cwd()
    const probe = await $.process.run(['test', '-x', `${cwd}/.venv/bin/dev`]).catch(() => null)
    if (probe?.exitCode === 0) {
      root = cwd
      await $.command.register({ name: 'ops', description: 'Gate, agents, memory and quota (no Claude turn); /ops hide|show' })
      await refresh($)
      $.clock.every(POLL_MS, () => refresh($))
    }
    return started
  })

  on('command.run', { command: 'ops' }, async ($, e) => {
    const arg = e.args.trim()
    if (arg === 'hide' || arg === 'show') {
      await update($, isHidden, () => arg === 'hide')
      return { text: `Ops band ${arg === 'hide' ? 'hidden' : 'shown'}.` }
    }
    await refresh($)
    const s = await read($, status)
    return { text: s ? `${line(s)}\n${s.quota}` : 'No status: run `uv run dev setup` in the repository root.' }
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const s = await read($, status)
    if (!root || e.props.hasSurvey || s === null || (await read($, isHidden))) return next(e)
    const { Box, Text } = $.ui.resolve(e)
    return (
      <Box>
        <Text dimColor>{line(s)}</Text>
      </Box>
    )
  })
}
