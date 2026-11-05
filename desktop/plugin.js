// Disk plugins are loaded uncompiled, so the UI is built with jsx()/jsxs() calls, not JSX.
//
// Data comes from ctx.rest through React Query. Live updates arrive as native
// 'cron.changed' and as the backend's 'plugin.ariadne-thread.skills.changed' push, but
// push can drop on some remotes, so every query also polls.

import {
  host,
  haptic,
  useQuery,
  queryClient,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
} from '@hermes/plugin-sdk'
import { jsx, jsxs, Fragment } from 'react/jsx-runtime'
import { createContext, useContext, useState, useEffect, useRef, useLayoutEffect } from 'react'

const ID = 'ariadne-thread'
const SKILLS_EVENT = 'plugin.ariadne-thread.skills.changed'
const POLL_MS = 12000
const DRAFT_ATTEMPTS = 6
const DRAFT_RETRY_MS = 250
const AGENT_BOX_MAX_PX = 240

const TXT1 = 'var(--ui-text-primary, #e5e7eb)'
const TXT2 = 'var(--ui-text-secondary, #e5e7eb)'
const TXT3 = 'var(--ui-text-tertiary, #9ca3af)'
const TXT4 = 'var(--ui-text-quaternary, #6b7280)'
const STROKE = 'var(--ui-stroke-secondary, rgba(128,128,128,0.25))'
const ACCENT = 'var(--ui-accent, #22c55e)'
const RED = 'var(--ui-red, #ef4444)'
const AMBER = 'var(--ui-orange, #f59e0b)'
const GREEN = 'var(--ui-green, #22c55e)'
const BLUE = 'var(--ui-blue, #3b82f6)'
const MONO = 'ui-monospace, SFMono-Regular, Menlo, monospace'

const mix = (color, pct) => 'color-mix(in srgb, ' + color + ' ' + pct + '%, transparent)'

const KIND_STYLE = {
  trigger: { color: GREEN, icon: '⚡', label: 'Trigger' },
  agent: { color: GREEN, icon: '◈', label: 'Agent' },
  prompt: { color: TXT3, icon: '📄', label: 'Prompt' },
  skill: { color: GREEN, icon: '🛡', label: 'Skill' },
  script: { color: AMBER, icon: '🖥', label: 'Script' },
  deterministic: { color: AMBER, icon: '⚙', label: 'Deterministic' },
  deliver: { color: BLUE, icon: '📤', label: 'Deliver' },
}

const kindStyle = (kind) => KIND_STYLE[kind] || KIND_STYLE.prompt

// What clicking a step expands to show: its script file or its full text. Skill steps get a "Go to skill" button instead.
const stepAction = (step) => (step.script ? 'script' : step.body ? 'text' : null)

const STEP_TITLE = { script: 'Show script', text: 'Show full text' }

const skillTarget = (step) => (step.type === 'skill' && step.name ? step.name : null)

// Hyperscript over jsx/jsxs: children as trailing args, key pulled out of props.
function h(type, props, ...kids) {
  const { key, ...rest } = props || {}
  if (kids.length === 0) return jsx(type, rest, key)
  if (kids.length === 1) return jsx(type, { ...rest, children: kids[0] }, key)
  return jsxs(type, { ...rest, children: kids }, key)
}

// The default profile is the host's own; any other name is a read-only view.
const withProfile = (path, profile) =>
  profile && profile !== 'default' ? path + (path.includes('?') ? '&' : '?') + 'profile=' + encodeURIComponent(profile) : path

const errorText = (e) => (e && e.message) || String(e)

// Edits are only offered on the host's own profile; the backend refuses the others too.
const canEdit = (profile) => !profile || profile === 'default'

// Every save carries the text the edit started from, so the backend answers 409 instead of
// overwriting a change made meanwhile by Hermes, an agent, or another window.
async function save(ctx, path, body, what) {
  const res = await ctx.rest(path, { method: 'PUT', body })
  haptic('tap')
  queryClient.invalidateQueries({ queryKey: [ID] })
  host.notify({ kind: 'success', message: 'Saved ' + what })
  return res
}

const scriptEdit = (ctx, script, owner) => ({
  save: (content, expected) => save(ctx, '/scripts', { path: script, expected, content }, script),
  ask: (request) => scriptPrompt(script, owner, request),
})

const cronStepEdit = (ctx, job) => (step) => {
  if (step.script) return scriptEdit(ctx, step.script, 'cron job "' + job.name + '"')
  if (step.type !== 'prompt' || !job.id) return null
  return {
    save: (content, expected) => save(ctx, '/crons/' + encodeURIComponent(job.id) + '/prompt', { expected, content }, 'the prompt of ' + job.name),
    ask: (request) => cronPromptPrompt(job, step.body, request),
  }
}

const webhookStepEdit = (ctx, route) => (step) => {
  if (step.script) return scriptEdit(ctx, step.script, 'webhook route "' + route.name + '"')
  if (step.type !== 'prompt') return null
  return {
    save: (content, expected) => save(ctx, '/webhooks/' + encodeURIComponent(route.name) + '/prompt', { expected, content }, 'the prompt of ' + route.name),
    ask: (request) => webhookPromptPrompt(route, step.body, request),
  }
}

const skillStepEdit = (ctx, detail) => (step) =>
  step.span
    ? {
        save: (content, expected) =>
          save(ctx, '/skills/' + encodeURIComponent(detail.name) + '/span', { start: step.span[0], end: step.span[1], expected, content }, 'a step of ' + detail.name),
        ask: (request) => skillStepPrompt(detail, step, request),
      }
    : null

const pick = (items, selected) =>
  items.find((i) => i.name === selected) || items[0] || null

function StatusDot({ status }) {
  const color = { ok: GREEN, error: RED, paused: TXT3 }[status] || TXT3
  return h('span', {
    title: status === 'ok' || status === 'error' ? status : 'paused',
    style: { display: 'inline-block', width: '8px', height: '8px', borderRadius: '9999px', background: color, flexShrink: 0 },
  })
}

function Badge({ children, tone }) {
  return h(
    'span',
    {
      style: {
        fontSize: '0.6875rem',
        lineHeight: '1',
        padding: '2px 6px',
        borderRadius: '9999px',
        border: '1px solid ' + STROKE,
        color: tone === 'accent' ? ACCENT : TXT3,
        whiteSpace: 'nowrap',
      },
    },
    children
  )
}

function Note({ text, tone }) {
  const color = tone === 'error' ? RED : TXT3
  return h('div', { style: { padding: '24px', textAlign: 'center', color, fontSize: '0.8125rem' } }, text)
}

function SectionLabel({ text }) {
  return h(
    'div',
    { style: { color: TXT4, fontSize: '0.6875rem', textTransform: 'uppercase', letterSpacing: '0.05em' } },
    text
  )
}

function gate(q, what) {
  if (q.isLoading) return h(Note, { text: 'Loading ' + what + '…' })
  if (q.isError) return h(Note, { tone: 'error', text: 'Failed to load ' + what + ': ' + errorText(q.error) })
  return null
}

function FlowArrow() {
  const line = { stroke: 'var(--ui-stroke-secondary, rgba(128,128,128,0.5))' }
  return h(
    'svg',
    { width: 14, height: 20, style: { margin: '0 auto', display: 'block', flexShrink: 0 } },
    jsx('line', { x1: 7, y1: 0, x2: 7, y2: 14, strokeWidth: 1, style: line }),
    jsx('path', { d: 'M2.5 13.5 L7 18.5 L11.5 13.5', fill: 'none', strokeWidth: 1.5, strokeLinecap: 'round', strokeLinejoin: 'round', style: line })
  )
}

// The SDK cannot import a graph library, so a chain of node cards stands in for one.
// Clicking a step expands it in place to show its content; a widened card keeps scripts readable.
// Skill steps never expand: their "Go to skill" button opens the whole skill instead.
// A lone step has nothing to scan past, so it opens expanded and does not collapse.
// edit(step) returns { save(content, expected), ask(request) } for a step the user may change, else null.
function Pipeline({ sequence, ctx, profile, onOpenSkill, edit }) {
  const [open, setOpen] = useState(() => new Set())
  if (!sequence || !sequence.length) return h(Note, { text: 'No steps' })
  const single = sequence.length === 1
  const toggle = (i) =>
    setOpen((prev) => {
      const next = new Set(prev)
      if (!next.delete(i)) next.add(i)
      return next
    })
  return h(
    'div',
    { style: { display: 'flex', flexDirection: 'column' } },
    sequence.map((step, i) => {
      const st = kindStyle(step.kind || step.type)
      const skill = onOpenSkill ? skillTarget(step) : null
      const action = ctx && !skill ? stepAction(step) : null
      const expanded = action && (single || open.has(i))
      const toggleable = action && !single
      const editor = expanded && edit ? edit(step) : null
      const ask = editor && editor.ask ? (close) => h(AgentBox, { ctx, buildPrompt: editor.ask, onClose: close, placeholder: 'e.g. make this step also check…' }) : null
      const sub = step.kindLabel && step.kindLabel.toLowerCase() !== st.label.toLowerCase() ? step.kindLabel : null
      return h(
        'div',
        { key: i, style: { display: 'flex', flexDirection: 'column' } },
        i > 0 ? h(FlowArrow, {}) : null,
        h(
          'div',
          {
            onClick: toggleable ? () => toggle(i) : undefined,
            title: toggleable ? (expanded ? 'Collapse' : STEP_TITLE[action]) : undefined,
            style: {
              width: '100%',
              maxWidth: expanded ? '880px' : '440px',
              transition: 'max-width 160ms ease',
              margin: '0 auto',
              border: '1px solid ' + mix(st.color, 30),
              borderLeft: '3px solid ' + st.color,
              borderRadius: '10px',
              background: 'linear-gradient(180deg, ' + mix(st.color, 9) + ', ' + mix(TXT1, 2) + ')',
              padding: '10px 12px',
              cursor: toggleable ? 'pointer' : 'default',
              display: 'flex',
              flexDirection: 'column',
              gap: '2px',
            },
          },
          h(
            'div',
            { style: { display: 'flex', alignItems: 'center', gap: '6px' } },
            h('span', { style: { color: st.color, fontSize: '0.8125rem', lineHeight: '1' } }, st.icon),
            h('span', { style: { color: st.color, fontSize: '0.625rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em' } }, st.label),
            sub ? h('span', { style: { color: TXT4, fontSize: '0.625rem', marginLeft: 'auto' } }, sub) : null,
            toggleable ? h('span', { style: { color: TXT4, fontSize: '0.625rem', marginLeft: sub ? '0' : 'auto' } }, expanded ? '▾' : '▸') : null,
            skill
              ? h(
                  'button',
                  {
                    onClick: (e) => {
                      e.stopPropagation()
                      onOpenSkill(skill)
                    },
                    title: 'Open ' + skill + ' in the Skills tab',
                    style: { ...outlineButton, marginLeft: 'auto', padding: '2px 8px', fontSize: '0.6875rem' },
                  },
                  'Go to skill →'
                )
              : null
          ),
          step.label && step.label !== st.label
            ? h('div', { style: { color: TXT2, fontSize: '0.8125rem', fontWeight: 600, overflowWrap: 'break-word' } }, step.label)
            : null,
          step.detail && !expanded
            ? h('div', { style: { color: TXT3, fontSize: '0.6875rem', overflowWrap: 'break-word', maxHeight: '3.2em', overflow: 'hidden' } }, String(step.detail))
            : null,
          expanded
            ? h(
                'div',
                // Clicks inside the content (selecting, scrolling) must not collapse the card.
                { onClick: (e) => e.stopPropagation(), style: { marginTop: '8px', cursor: 'auto' } },
                step.script
                  ? h(ScriptBody, { ctx, profile, script: step.script, onSave: editor && editor.save, ask, maxHeight: '60vh' })
                  : h(TextEditor, { value: step.body, onSave: editor && editor.save, ask, maxHeight: '60vh' })
              )
            : null
        )
      )
    })
  )
}

// The page's tab bar, rendered by ListDetail at the top of the list column.
const TabsSlot = createContext(null)

function ListDetail({ top, list, children }) {
  const tabs = useContext(TabsSlot)
  return h(
    'div',
    { style: { display: 'flex', height: '100%', minHeight: 0 } },
    h(
      'div',
      { style: { width: '280px', flexShrink: 0, borderRight: '1px solid ' + STROKE, display: 'flex', flexDirection: 'column', minHeight: 0 } },
      tabs,
      top || null,
      h('div', { style: { flex: '1', overflowY: 'auto', minHeight: 0 } }, list)
    ),
    h('div', { style: { flex: '1', minWidth: 0, overflowY: 'auto', padding: '16px' } }, ...[].concat(children))
  )
}

function ListRow({ active, onClick, children }) {
  return h(
    'button',
    {
      onClick,
      style: {
        display: 'block',
        width: '100%',
        textAlign: 'left',
        padding: '8px',
        border: 'none',
        background: active ? mix(ACCENT, 10) : 'transparent',
        color: TXT2,
        cursor: 'pointer',
        borderLeft: '2px solid ' + (active ? ACCENT : 'transparent'),
      },
    },
    children
  )
}

function CronsTab({ ctx, profile, nav, onOpenSkill }) {
  const [selected, setSelected] = useState(null)
  const q = useQuery({
    queryKey: [ID, 'crons', profile],
    queryFn: () => ctx.rest(withProfile('/crons', profile)),
    refetchInterval: POLL_MS,
  })

  useEffect(() => {
    if (nav && nav.to === 'crons' && nav.item) setSelected(nav.item)
  }, [nav])

  const failed = gate(q, 'crons')
  if (failed) return h(ListDetail, { list: failed })
  const jobs = (q.data && q.data.jobs) || []
  if (!jobs.length) return h(ListDetail, { list: h(Note, { text: 'No cron jobs' }) })
  const job = pick(jobs, selected)

  return h(
    ListDetail,
    {
      list: jobs.map((j) =>
        h(
          ListRow,
          { key: j.name, active: j.name === job.name, onClick: () => setSelected(j.name) },
          h(
            'div',
            { style: { display: 'flex', alignItems: 'center', gap: '6px' } },
            h(StatusDot, { status: j.status }),
            h('span', { style: { fontSize: '0.8125rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, j.name)
          ),
          h('div', { style: { fontSize: '0.6875rem', color: TXT3, paddingLeft: '14px' } }, j.schedule_human || j.schedule)
        )
      ),
    },
    h(
      'div',
      { style: { display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' } },
      h('div', { style: { fontSize: '0.9375rem', fontWeight: 600, color: TXT2 } }, job.name),
      h(StatusDot, { status: job.status }),
      job.category ? h(Badge, {}, job.category) : null,
      job.deliver && job.deliver !== 'local' ? h(Badge, { tone: 'accent' }, '→ ' + job.deliver) : null
    ),
    h('div', { style: { fontSize: '0.75rem', color: TXT3, marginTop: '4px' } }, job.schedule_human || job.schedule),
    job.issues && job.issues.length
      ? h('div', { style: { marginTop: '8px' } }, job.issues.map((iss, i) => h('div', { key: i, style: { color: AMBER, fontSize: '0.75rem' } }, '⚠ ' + iss)))
      : null,
    h('div', { style: { marginTop: '16px' } }, h(Pipeline, { key: job.name, sequence: job.sequence, ctx, profile, edit: canEdit(profile) ? cronStepEdit(ctx, job) : null, onOpenSkill: (name) => onOpenSkill(name, { to: 'crons', item: job.name, kind: 'cron' }) }))
  )
}

function WebhooksTab({ ctx, profile, nav, onOpenSkill }) {
  const [selected, setSelected] = useState(null)
  const q = useQuery({
    queryKey: [ID, 'webhooks', profile],
    queryFn: () => ctx.rest(withProfile('/webhooks', profile)),
    refetchInterval: POLL_MS,
  })

  useEffect(() => {
    if (nav && nav.to === 'webhooks' && nav.item) setSelected(nav.item)
  }, [nav])

  const failed = gate(q, 'webhooks')
  if (failed) return h(ListDetail, { list: failed })
  const routes = (q.data && q.data.routes) || []
  if (!routes.length) return h(ListDetail, { list: h(Note, { text: 'No webhooks' }) })
  const route = pick(routes, selected)

  return h(
    ListDetail,
    {
      list: routes.map((r) =>
        h(
          ListRow,
          { key: r.name, active: r.name === route.name, onClick: () => setSelected(r.name) },
          h('span', { style: { fontSize: '0.8125rem' } }, r.name),
          r.description ? h('div', { style: { fontSize: '0.6875rem', color: TXT3 } }, r.description) : null
        )
      ),
    },
    h('div', { style: { fontSize: '0.9375rem', fontWeight: 600, color: TXT2 } }, route.name),
    h('div', { style: { fontSize: '0.75rem', color: TXT3, marginTop: '4px', fontFamily: MONO } }, route.path),
    route.events && route.events.length
      ? h('div', { style: { marginTop: '8px', display: 'flex', gap: '4px', flexWrap: 'wrap' } }, route.events.map((e) => h(Badge, { key: e }, e)))
      : h('div', { style: { marginTop: '8px' } }, h(Badge, {}, 'all events')),
    h('div', { style: { marginTop: '16px' } }, h(Pipeline, { key: route.name, sequence: route.sequence, ctx, profile, edit: canEdit(profile) ? webhookStepEdit(ctx, route) : null, onOpenSkill: (name) => onOpenSkill(name, { to: 'webhooks', item: route.name, kind: 'webhook' }) }))
  )
}

const outlineButton = {
  padding: '6px 12px',
  borderRadius: '6px',
  border: '1px solid ' + STROKE,
  background: 'transparent',
  color: TXT2,
  cursor: 'pointer',
  fontSize: '0.8125rem',
}

const smallButton = { ...outlineButton, padding: '3px 10px', fontSize: '0.75rem' }

const primaryButton = (disabled) => ({
  padding: '6px 14px',
  borderRadius: '6px',
  border: '1px solid ' + ACCENT,
  background: mix(ACCENT, 18),
  color: TXT1,
  cursor: disabled ? 'not-allowed' : 'pointer',
  fontSize: '0.8125rem',
  fontWeight: 600,
  whiteSpace: 'nowrap',
})

const codeBlock = { margin: 0, padding: '12px', borderRadius: '8px', background: 'var(--ui-bg-editor, rgba(128,128,128,0.10))', color: TXT2, fontSize: '0.75rem', lineHeight: '1.5', whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontFamily: MONO }

// A small code editor: no editor library can be loaded, since a disk plugin imports only the SDK and React.
// Each language is one regex whose groups name the token; words are then sorted into keywords and builtins.
const words = (s) => new Set(s.split(' '))
const LANGS = {
  python: {
    re: /(#[^\n]*)|((?:\b[rRbBuUfF]{1,2})?(?:"""[\s\S]*?(?:"""|$)|'''[\s\S]*?(?:'''|$)|"(?:\\.|[^"\\\n])*"?|'(?:\\.|[^'\\\n])*'?))|(@[A-Za-z_][\w.]*)|(\b0[xXoObB][\da-fA-F_]+\b|\b\d[\d_]*(?:\.\d*)?(?:[eE][+-]?\d+)?j?\b)|([A-Za-z_]\w*)/g,
    keywords: words('and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield match case'),
    builtins: words('True False None self cls print len range open str int float bool list dict set tuple type isinstance super Exception'),
    defs: words('def class'),
    opener: /:\s*(#.*)?$/,
  },
  shell: {
    re: /((?<![^\s;])#[^\n]*)|("(?:\\.|[^"\\])*"?|'[^']*'?)|(\$\{[^}\n]*\}?|\$[\w@#?$!*-])|(\b\d+\b)|([A-Za-z_][\w-]*)/g,
    keywords: words('if then else elif fi for while until do done case esac in function return export local readonly set unset shift exit break continue source'),
    builtins: words('echo printf cd test true false read eval exec trap wait'),
    defs: words('function'),
    opener: /(\bthen|\bdo|\{|\()\s*$/,
  },
  js: {
    re: /(\/\/[^\n]*|\/\*[\s\S]*?(?:\*\/|$))|("(?:\\.|[^"\\\n])*"?|'(?:\\.|[^'\\\n])*'?|`(?:\\.|[^`\\])*`?)|(@[A-Za-z_][\w.]*)|(\b0[xXoObB][\da-fA-F_]+n?\b|\b\d[\d_]*(?:\.\d*)?(?:[eE][+-]?\d+)?n?\b)|([A-Za-z_$][\w$]*)/g,
    keywords: words('async await break case catch class const continue default delete do else export extends finally for from function if import in instanceof let new of return static switch throw try typeof var void while yield'),
    builtins: words('true false null undefined this super console process require module'),
    defs: words('function class'),
    opener: /[{[(]\s*$/,
  },
}
const TOKEN_GROUPS = ['comment', 'string', 'decorator', 'number']

const scriptLang = (path, content) => {
  const ext = (/\.(\w+)$/.exec(path) || [])[1]
  if (ext === 'py') return 'python'
  if (ext === 'sh' || ext === 'bash' || ext === 'zsh') return 'shell'
  if (['js', 'mjs', 'cjs', 'ts'].includes(ext)) return 'js'
  const bang = /^#!.*\b(python3?|node|bash|zsh|sh)\b/.exec(content || '')
  if (!bang) return 'plain'
  return bang[1].startsWith('python') ? 'python' : bang[1] === 'node' ? 'js' : 'shell'
}

const TOKEN_STYLE = {
  comment: { color: TXT4, fontStyle: 'italic' },
  string: { color: GREEN },
  decorator: { color: AMBER },
  number: { color: AMBER },
  keyword: { color: 'var(--ui-purple, #a855f7)' },
  builtin: { color: 'var(--ui-cyan, #06b6d4)' },
  def: { color: BLUE },
}

function highlight(text, lang) {
  const spec = LANGS[lang]
  if (!spec) return text
  const out = []
  let at = 0
  let prevWord = null
  for (const m of text.matchAll(spec.re)) {
    if (m.index > at) out.push(text.slice(at, m.index))
    at = m.index + m[0].length
    let kind = TOKEN_GROUPS.find((_, i) => m[i + 1] !== undefined)
    if (!kind) {
      kind = prevWord && spec.defs.has(prevWord) ? 'def' : spec.keywords.has(m[0]) ? 'keyword' : spec.builtins.has(m[0]) ? 'builtin' : null
      prevWord = m[0]
    }
    out.push(kind ? h('span', { key: out.length, style: TOKEN_STYLE[kind] }, m[0]) : m[0])
  }
  out.push(text.slice(at))
  return out
}

// The indent the file already uses: tabs, else its smallest space indent, else four spaces.
const indentUnit = (text) => {
  if (/^\t/m.test(text)) return '\t'
  const sizes = (text.match(/^ +(?=\S)/gm) || []).map((s) => s.length)
  const n = sizes.length ? Math.min(...sizes) : 4
  return ' '.repeat(Math.min(8, Math.max(2, n)))
}

// Replaces [start, end) through the browser's own editing, so ⌘Z still undoes it, then selects [selStart, selEnd).
function replaceRange(ta, start, end, text, selStart, selEnd) {
  ta.setSelectionRange(start, end)
  const done = text ? document.execCommand('insertText', false, text) : start === end || document.execCommand('delete')
  if (!done) {
    ta.setRangeText(text, start, end, 'end')
    ta.dispatchEvent(new Event('input', { bubbles: true }))
  }
  if (selStart !== undefined) ta.setSelectionRange(selStart, selEnd)
}

// Tab and Shift+Tab indent or dedent the selected lines, Enter keeps the line's indent (one more after
// an opener such as a Python colon), and Backspace in leading spaces removes one indent step.
function codeKey(e, ta, lang, unit) {
  if (e.metaKey || e.ctrlKey || e.altKey) return
  const v = ta.value
  const s = ta.selectionStart
  const end = ta.selectionEnd
  const lineStart = v.lastIndexOf('\n', s - 1) + 1
  if (e.key === 'Tab') {
    e.preventDefault()
    if (!e.shiftKey && s === end) return replaceRange(ta, s, end, unit)
    const blockEnd = end > s && v[end - 1] === '\n' ? end - 1 : end
    const lines = v.slice(lineStart, blockEnd).split('\n')
    const next = lines.map((l) => (e.shiftKey ? l.replace(new RegExp('^(\\t| {1,' + unit.length + '})'), '') : l ? unit + l : l)).join('\n')
    const first = e.shiftKey ? lines[0].length - next.split('\n')[0].length : lines[0] ? unit.length : 0
    return replaceRange(ta, lineStart, blockEnd, next, Math.max(lineStart, s + (e.shiftKey ? -first : first)), blockEnd + next.length - (blockEnd - lineStart) + (end - blockEnd))
  }
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    const before = v.slice(lineStart, s)
    const lead = /^[ \t]*/.exec(before)[0]
    return replaceRange(ta, s, end, '\n' + lead + (LANGS[lang] && LANGS[lang].opener.test(before) ? unit : ''))
  }
  if (e.key === 'Backspace' && s === end && unit !== '\t' && s > lineStart) {
    const before = v.slice(lineStart, s)
    if (/^ +$/.test(before)) {
      e.preventDefault()
      replaceRange(ta, s - ((before.length - 1) % unit.length) - 1, s, '')
    }
  }
}

// Line numbers beside highlighted text. With onChange, a transparent textarea lies exactly over the
// highlighted copy, so the browser still does the caret, selection, undo and IME.
function CodeArea({ value, lang, maxHeight, onChange, onKeyDown }) {
  const ref = useRef(null)
  const unit = useRef(null)
  if (unit.current === null) unit.current = indentUnit(value)
  useEffect(() => {
    if (onChange && ref.current) ref.current.focus({ preventScroll: true })
  }, [])
  const lines = value.split('\n').length
  const text = { margin: 0, padding: '12px', fontFamily: MONO, fontSize: '0.75rem', lineHeight: '1.5', tabSize: 4, whiteSpace: 'pre', fontVariantLigatures: 'none', letterSpacing: 0 }
  return h(
    'div',
    {
      style: {
        maxHeight,
        overflow: 'auto',
        borderRadius: '8px',
        background: 'var(--ui-bg-editor, rgba(128,128,128,0.10))',
        border: '1px solid ' + (onChange ? ACCENT : 'transparent'),
      },
    },
    h(
      'div',
      { style: { display: 'flex', width: 'max-content', minWidth: '100%' } },
      h(
        'pre',
        { 'aria-hidden': true, style: { ...text, paddingRight: '8px', color: TXT4, textAlign: 'right', userSelect: 'none', borderRight: '1px solid ' + STROKE } },
        Array.from({ length: lines }, (_, i) => i + 1).join('\n')
      ),
      h(
        'div',
        { style: { position: 'relative', flex: 1 } },
        // The trailing space gives a final empty line its height, as the textarea gives it.
        h('pre', { 'aria-hidden': !!onChange, style: { ...text, color: TXT2 } }, highlight(value, lang), ' '),
        onChange
          ? h('textarea', {
              ref,
              value,
              wrap: 'off',
              spellCheck: false,
              autoCapitalize: 'off',
              autoCorrect: 'off',
              onChange: (e) => onChange(e.target.value),
              onKeyDown: (e) => {
                onKeyDown && onKeyDown(e)
                codeKey(e, e.currentTarget, lang, unit.current)
              },
              style: {
                ...text,
                position: 'absolute',
                inset: 0,
                width: '100%',
                height: '100%',
                boxSizing: 'border-box',
                border: 'none',
                outline: 'none',
                resize: 'none',
                overflow: 'hidden',
                background: 'transparent',
                color: 'transparent',
                caretColor: TXT1,
              },
            })
          : null
      )
    )
  )
}

// Shows text, and with onSave an Edit button that swaps it for a textarea in place.
// The text at the moment Edit is clicked is what the save claims to replace, so polling can refresh
// `value` underneath an open editor without losing the draft or hiding a concurrent change.
// ask(close), when given, adds an "Ask agent" button that opens the returned box docked under the text.
// With maxHeight only the text scrolls, so the buttons above it and the box below it stay in view.
// Without it the box sticks to the bottom of a scrolling parent, so on a long text it stays in view.
// With lang, the text shows as highlighted code with line numbers and edits in CodeArea.
function TextEditor({ value, onSave, hint, ask, maxHeight, lang }) {
  const [draft, setDraft] = useState(null)
  const [base, setBase] = useState('')
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const [asking, setAsking] = useState(false)

  if (draft === null) {
    return h(
      Fragment,
      {},
      onSave || ask
        ? h(
            'div',
            { style: { display: 'flex', justifyContent: 'flex-end', gap: '6px', marginBottom: '6px' } },
            ask
              ? h('button', { onClick: () => setAsking(!asking), style: asking ? { ...smallButton, borderColor: ACCENT, color: TXT1 } : smallButton }, '✦ Ask agent')
              : null,
            onSave ? h('button', { onClick: () => { setBase(value); setDraft(value); setErr(null); setAsking(false) }, style: smallButton }, '✎ Edit') : null
          )
        : null,
      lang
        ? h(CodeArea, { value, lang, maxHeight })
        : maxHeight
          ? h('div', { style: { maxHeight, overflowY: 'auto', borderRadius: '8px' } }, h('pre', { style: codeBlock }, value))
          : h('pre', { style: codeBlock }, value),
      ask && asking
        ? h(
            'div',
            {
              style: {
                ...(maxHeight ? {} : { position: 'sticky', bottom: 0, zIndex: 1 }),
                marginTop: '8px',
                padding: '8px 10px 10px',
                borderRadius: '8px',
                border: '1px solid ' + STROKE,
                background: 'var(--ui-bg-elevated, #1f2937)',
                boxShadow: '0 -6px 16px rgba(0,0,0,0.12)',
              },
            },
            ask(() => setAsking(false))
          )
        : null
    )
  }

  const submit = async () => {
    setBusy(true)
    setErr(null)
    try {
      await onSave(draft, base)
      setDraft(null)
    } catch (e) {
      setErr(errorText(e))
    }
    setBusy(false)
  }
  const onKeyDown = (e) => {
    if (e.key === 'Escape') setDraft(null)
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) submit()
  }
  return h(
    Fragment,
    {},
    hint ? h('div', { style: { marginBottom: '6px', fontSize: '0.6875rem', color: TXT4 } }, hint) : null,
    lang
      ? h(CodeArea, { value: draft, lang, maxHeight: maxHeight || '70vh', onChange: setDraft, onKeyDown })
      : h('textarea', {
          value: draft,
          autoFocus: true,
          onChange: (e) => setDraft(e.target.value),
          onKeyDown,
          spellCheck: false,
          rows: Math.min(30, Math.max(6, draft.split('\n').length + 1)),
          style: { ...codeBlock, width: '100%', boxSizing: 'border-box', resize: 'vertical', border: '1px solid ' + ACCENT, outline: 'none', whiteSpace: 'pre', overflowX: 'auto' },
        }),
    err ? h('div', { style: { marginTop: '6px', color: RED, fontSize: '0.75rem' } }, err) : null,
    h(
      'div',
      { style: { marginTop: '6px', display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: '8px' } },
      h('span', { style: { color: TXT4, fontSize: '0.6875rem', marginRight: 'auto' } }, '⌘/Ctrl+Enter to save · Esc to cancel'),
      h('button', { onClick: () => setDraft(null), style: smallButton }, 'Cancel'),
      h('button', { onClick: submit, disabled: busy || draft === base, style: { ...primaryButton(busy || draft === base), padding: '3px 12px', fontSize: '0.75rem' } }, busy ? 'Saving…' : 'Save')
    )
  )
}

function FlowSection({ flow, ctx, profile, onOpenSkill, edit }) {
  if (!flow) return null
  const note = !flow.fallback
    ? 'Parsed via ' + flow.strategy
    : flow.strategy === 'single'
      ? 'No explicit steps found. Showing the skill as one step.'
      : 'No explicit steps found. Showing its top-level sections.'
  return h(
    'div',
    { style: { marginTop: '16px' } },
    h('div', { style: { color: TXT4, fontSize: '0.6875rem' } }, note),
    h('div', { style: { marginTop: '8px' } }, h(Pipeline, { sequence: flow.steps, ctx, profile, onOpenSkill, edit }))
  )
}

function ChipList({ label, items, tone, onPick }) {
  if (!items || !items.length) return null
  return h(
    'div',
    { style: { marginTop: '8px' } },
    h(SectionLabel, { text: label }),
    h(
      'div',
      { style: { display: 'flex', gap: '4px', flexWrap: 'wrap', marginTop: '4px' } },
      items.map((item) =>
        h(
          'button',
          {
            key: String(item),
            onClick: () => onPick && onPick(String(item)),
            style: { background: 'transparent', border: 'none', padding: 0, cursor: onPick ? 'pointer' : 'default' },
          },
          h(Badge, { tone }, String(item))
        )
      )
    )
  )
}


const SKILL_VIEWS = [
  { id: 'workflow', label: 'Workflow' },
  { id: 'md', label: 'SKILL.md' },
]

function SkillDetail({ detail, ctx, profile, view, onView, onSwitch, onCron }) {
  const editable = detail.editable && canEdit(profile)
  return h(
    Fragment,
    {},
    h(
      'div',
      { style: { display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' } },
      h('div', { style: { fontSize: '0.9375rem', fontWeight: 600, color: TXT2 } }, detail.name),
      detail.category ? h(Badge, {}, detail.category) : null,
      detail.source === 'external' ? h(Badge, {}, 'external') : !detail.editable ? h(Badge, {}, 'read-only') : null,
      h('div', { style: { marginLeft: 'auto' } }, h(TabBar, { tabs: SKILL_VIEWS, tab: view, onTab: onView }))
    ),
    detail.description ? h('div', { style: { marginTop: '8px', color: TXT3, fontSize: '0.8125rem' } }, detail.description) : null,
    view === 'md'
      ? h(
          'div',
          { key: 'md', style: { marginTop: '16px' } },
          h(TextEditor, {
            value: detail.raw,
            hint: 'Full SKILL.md: YAML frontmatter (--- … ---) then the markdown body. Validated on save.',
            onSave: editable
              ? (content, expected) => save(ctx, '/skills/' + encodeURIComponent(detail.name), { content, expected }, detail.name)
              : null,
          })
        )
      : h(
          Fragment,
          {},
          h(FlowSection, {
            key: detail.name,
            flow: detail.flow,
            ctx,
            profile,
            onOpenSkill: onSwitch,
            edit: editable ? skillStepEdit(ctx, detail) : null,
          }),
          h(ChipList, { label: 'Credentials', items: detail.credentials, tone: 'accent' }),
          h(ChipList, { label: 'Related skills', items: detail.related_skills, onPick: onSwitch }),
          h(ChipList, { label: 'Used by crons', items: detail.used_by_crons, onPick: onCron })
        )
  )
}

function useSkill(ctx, profile, name) {
  return useQuery({
    queryKey: [ID, 'skill', profile, name],
    queryFn: () => ctx.rest(withProfile('/skills/' + encodeURIComponent(name), profile)),
    enabled: !!name,
    refetchInterval: POLL_MS,
  })
}

function groupByCategory(skills, filter) {
  const needle = filter.trim().toLowerCase()
  const groups = new Map()
  for (const s of skills) {
    if (needle && !s.name.toLowerCase().includes(needle) && !(s.description || '').toLowerCase().includes(needle)) continue
    if (!groups.has(s.category)) groups.set(s.category, [])
    groups.get(s.category).push(s)
  }
  return [...groups.entries()].sort(([a], [b]) => (a === b ? 0 : a === '' ? 1 : b === '' ? -1 : a.localeCompare(b)))
}

// Shown on a skill reached through "Go to skill", so the cron or webhook it came from is one click away.
function BackBar({ from, onBack }) {
  return h(
    'div',
    {
      style: {
        margin: '-16px -16px 16px',
        padding: '8px 16px',
        borderBottom: '1px solid ' + STROKE,
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
      },
    },
    h('button', { onClick: onBack, style: { ...outlineButton, padding: '3px 10px', fontSize: '0.75rem' } }, '← Back to ' + from.kind),
    h('span', { style: { color: TXT3, fontSize: '0.75rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, from.item)
  )
}


function SkillsTab({ ctx, profile, nav, onNavigate }) {
  const [selected, setSelected] = useState(null)
  const [filter, setFilter] = useState('')
  // Workflow or SKILL.md; kept while moving between skills.
  const [view, setView] = useState('workflow')
  const q = useQuery({
    queryKey: [ID, 'skills', profile],
    queryFn: () => ctx.rest(withProfile('/skills', profile)),
    refetchInterval: POLL_MS,
  })
  const skills = (q.data && q.data.skills) || []
  const groups = groupByCategory(skills, filter)
  const first = groups.length ? groups[0][1][0] : skills[0]
  const currentName = selected || (first ? first.name : null)
  const detail = useSkill(ctx, profile, currentName)

  useEffect(() => {
    if (nav && nav.to === 'skills' && nav.item) setSelected(nav.item)
  }, [nav])

  const failed = gate(q, 'skills')
  if (failed) return h(ListDetail, { list: failed })

  const open = (name) => {
    setFilter('')
    setSelected(name)
  }
  const top = h('input', {
    value: filter,
    onChange: (e) => setFilter(e.target.value),
    placeholder: 'Filter skills',
    style: { margin: '8px', padding: '5px 8px', fontSize: '0.75rem', borderRadius: '6px', border: '1px solid ' + STROKE, background: 'var(--ui-bg-input, transparent)', color: 'inherit', outline: 'none' },
  })
  const list = groups.length
    ? groups.map(([category, items]) =>
        h(
          'div',
          { key: category || 'uncategorized' },
          h('div', { style: { padding: '8px 8px 2px' } }, h(SectionLabel, { text: category || 'Uncategorized' })),
          items.map((s) =>
            h(
              ListRow,
              { key: s.path, active: s.name === currentName, onClick: () => setSelected(s.name) },
              h(
                'div',
                { style: { display: 'flex', alignItems: 'center', gap: '6px' } },
                h('span', { style: { flex: '1', fontSize: '0.8125rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, s.name),
                h('span', { title: s.fallback ? 'No explicit steps found' : undefined, style: { fontSize: '0.6875rem', color: s.fallback ? TXT4 : TXT3 } }, s.steps + (s.steps === 1 ? ' step' : ' steps'))
              )
            )
          )
        )
      )
    : h(Note, { text: filter ? 'No skill matches' : 'No skills' })

  const from = nav && nav.to === 'skills' && nav.from
  return h(
    ListDetail,
    { top, list },
    from ? h(BackBar, { from, onBack: () => onNavigate(from.to, from.item) }) : null,
    currentName
      ? gate(detail, 'skill') ||
          (detail.data
            ? h(SkillDetail, {
                detail: detail.data,
                view,
                onView: setView,
                onSwitch: open,
                onCron: (cron) => onNavigate('crons', cron),
                ctx,
                profile,
              })
            : null)
      : h(Note, { text: 'Select a skill' }),
    detail.data && detail.data.editable && canEdit(profile)
      ? h(
          'div',
          { style: { margin: '16px -16px -16px', padding: '12px 16px', borderTop: '1px solid ' + STROKE } },
          h(AgentBox, {
            key: detail.data.name,
            ctx,
            buildPrompt: (request) => skillPrompt(detail.data, request),
            placeholder: 'e.g. reword the description, add a pitfall, restructure…',
          })
        )
      : null
  )
}


const fenced = (text) => ['~~~~', text, '~~~~'].join('\n')

function changePrompt(lines, request) {
  return [...lines, '', 'Requested change:', request, '', 'When you are done, summarize what you changed.'].join('\n')
}

const FRONTMATTER_RULE =
  'Keep the frontmatter valid: it must start with --- on the first line, include name and description ' +
  '(description at most 1024 characters), and be followed by a non-empty body.'

const skillPrompt = (detail, request) =>
  changePrompt(['Please update the Hermes skill "' + detail.name + '".', '', 'Skill file: ' + detail.path, '', 'Edit that file in place. ' + FRONTMATTER_RULE], request)

const skillStepPrompt = (detail, step, request) =>
  changePrompt(
    [
      'Please update one step of the Hermes skill "' + detail.name + '".',
      '',
      'Skill file: ' + detail.path,
      'Step: ' + (step.label || '(unnamed)'),
      'Its current text in that file:',
      fenced(step.body),
      '',
      'Change only that part of the file, in place. ' + FRONTMATTER_RULE,
    ],
    request
  )

const cronPromptPrompt = (job, current, request) =>
  changePrompt(
    [
      'Please update the prompt of the Hermes cron job "' + job.name + '" (id ' + job.id + ').',
      'Use the cronjob tool to update it; do not edit cron/jobs.json by hand.',
      '',
      'Current prompt:',
      fenced(current),
    ],
    request
  )

const webhookPromptPrompt = (route, current, request) =>
  changePrompt(
    [
      'Please update the prompt template of the Hermes webhook route "' + route.name + '".',
      'Leave its secret and every other field unchanged.',
      '',
      'Current prompt template:',
      fenced(current),
    ],
    request
  )

const scriptPrompt = (script, owner, request) =>
  changePrompt(
    ['Please update the script "' + script + '" in the Hermes scripts folder. The ' + owner + ' runs it.', 'Edit the file in place and keep it runnable.'],
    request
  )


// The new-chat composer mounts asynchronously after the navigation, so setDraft is retried.
async function draftInNewChat(ctx, text) {
  host.navigate('/')
  for (let attempt = 0; attempt < DRAFT_ATTEMPTS; attempt++) {
    try {
      if (await host.composer.setDraft('new', text)) return true
    } catch (e) {
      return false
    }
    await new Promise((resolve) => ctx.setTimeout(resolve, DRAFT_RETRY_MS))
  }
  return false
}


// One request box for an agent: the skill as a whole at the bottom of the Skills tab, or one step
// inside its expanded card. buildPrompt(request) gives the full prompt for the new chat.
// onClose, when given, makes the box dismissable: it takes focus on open, and Esc or the × closes it.
function AgentBox({ ctx, buildPrompt, label, placeholder, onClose }) {
  const [request, setRequest] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    const text = request.trim()
    if (!text || busy) return
    setBusy(true)
    const prompt = buildPrompt(text)
    if (await draftInNewChat(ctx, prompt)) {
      haptic('tap')
      host.notify({ kind: 'info', message: 'Draft ready in a new chat. Review it, then send.' })
      return
    }
    const copied = await ctx.os.writeClipboard(prompt)
    host.notify(
      copied
        ? { kind: 'info', message: 'Could not open a draft. The prompt was copied to your clipboard.' }
        : { kind: 'error', message: 'Could not open a draft or copy the prompt.' }
    )
    setBusy(false)
  }

  // The box grows with its text up to AGENT_BOX_MAX_PX, then scrolls.
  const ref = useRef(null)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, AGENT_BOX_MAX_PX) + 'px'
  }, [request])

  const empty = !request.trim()
  return h(
    'div',
    {},
    h(
      'div',
      { style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '6px', marginBottom: '6px' } },
      h(SectionLabel, { text: label || 'Ask an agent to change it' }),
      onClose
        ? h(
            'button',
            {
              onClick: onClose,
              title: 'Close',
              'aria-label': 'Close',
              style: { flexShrink: 0, width: '20px', height: '20px', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 0, borderRadius: '4px', border: 'none', background: 'transparent', color: TXT4, cursor: 'pointer' },
            },
            h(
              'svg',
              { width: 12, height: 12, viewBox: '0 0 16 16', fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round' },
              jsx('path', { d: 'M4 4l8 8' }),
              jsx('path', { d: 'M12 4l-8 8' })
            )
          )
        : null
    ),
    h(
      'div',
      { style: { display: 'flex', alignItems: 'flex-end', gap: '6px', padding: '6px 6px 6px 10px', borderRadius: '8px', border: '1px solid ' + STROKE, background: 'var(--ui-bg-input, transparent)' } },
      h('textarea', {
        ref,
        rows: 2,
        value: request,
        onChange: (e) => setRequest(e.target.value),
        onKeyDown: (e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault()
            submit()
          }
          if (e.key === 'Escape' && onClose) onClose()
        },
        autoFocus: !!onClose,
        placeholder,
        disabled: busy,
        style: { flex: '1', minWidth: 0, padding: '4px 0', fontSize: '0.8125rem', lineHeight: '1.45', fontFamily: 'inherit', border: 'none', background: 'transparent', color: 'inherit', outline: 'none', resize: 'none', overflowY: 'auto', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' },
      }),
      h(
        'button',
        {
          onClick: submit,
          disabled: busy || empty,
          title: 'Draft in new chat',
          'aria-label': 'Draft in new chat',
          style: { flexShrink: 0, width: '28px', height: '28px', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 0, borderRadius: '6px', border: 'none', background: empty ? mix(ACCENT, 10) : ACCENT, color: empty ? TXT4 : 'var(--ui-text-on-accent, #fff)', cursor: busy || empty ? 'not-allowed' : 'pointer', opacity: busy ? 0.6 : 1 },
        },
        h(
          'svg',
          { width: 14, height: 14, viewBox: '0 0 16 16', fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round', strokeLinejoin: 'round' },
          jsx('path', { d: 'M8 13V3' }),
          jsx('path', { d: 'M3.5 7.5 8 3l4.5 4.5' })
        )
      )
    )
  )
}

function ScriptBody({ ctx, profile, script, onSave, ask, maxHeight }) {
  const q = useQuery({
    queryKey: [ID, 'script', profile, script],
    queryFn: () => ctx.rest(withProfile('/scripts?path=' + encodeURIComponent(script), profile)),
    refetchInterval: POLL_MS,
  })
  const failed = gate(q, 'script')
  if (failed) return failed
  const s = q.data
  return h(
    Fragment,
    {},
    h('div', { style: { color: TXT4, fontSize: '0.6875rem', fontFamily: MONO, marginBottom: '8px', overflowWrap: 'anywhere' } }, s.path),
    s.truncated ? h('div', { style: { color: AMBER, fontSize: '0.75rem', marginBottom: '8px' } }, 'Showing the first ' + s.content.length + ' of ' + s.size + ' bytes. Too large to edit here.') : null,
    h(TextEditor, { value: s.content, onSave: s.truncated ? null : onSave, ask, maxHeight, lang: scriptLang(s.path, s.content) })
  )
}

const TABS = [
  { id: 'crons', label: 'Crons' },
  { id: 'webhooks', label: 'Webhooks' },
  { id: 'skills', label: 'Skills' },
]

function TabBar({ tabs, tab, onTab }) {
  return h(
    'div',
    { style: { display: 'flex', gap: '2px' } },
    tabs.map((t) =>
      h(
        'button',
        {
          key: t.id,
          onClick: () => onTab(t.id),
          style: {
            padding: '4px 10px',
            borderRadius: '6px',
            border: 'none',
            background: tab === t.id ? mix(ACCENT, 14) : 'transparent',
            color: tab === t.id ? TXT1 : TXT3,
            cursor: 'pointer',
            fontSize: '0.8125rem',
          },
        },
        t.label
      )
    )
  )
}

function ProfileSelect({ ctx, profile, onChange }) {
  const q = useQuery({
    queryKey: [ID, 'profiles'],
    queryFn: () => ctx.rest('/profiles'),
    refetchInterval: POLL_MS * 5,
  })
  const profiles = (q.data && q.data.profiles) || ['default']
  if (profiles.length < 2) return null
  return h(
    'select',
    {
      value: profile,
      onChange: (e) => onChange(e.target.value),
      title: 'Profile',
      style: { marginLeft: 'auto', padding: '3px 6px', fontSize: '0.75rem', borderRadius: '6px', border: '1px solid ' + STROKE, background: 'var(--ui-bg-input, transparent)', color: 'inherit' },
    },
    profiles.map((p) => h('option', { key: p, value: p }, p))
  )
}

function AriadnePage({ ctx }) {
  const [tab, setTab] = useState('crons')
  const [profile, setProfile] = useState('default')
  // A badge click can jump to another tab and select an item there: { to, item }.
  const [nav, setNav] = useState(null)

  // from: the cron or webhook a "Go to skill" click left, so the skill page can lead back to it.
  const navigate = (to, item, from) => {
    setNav({ to, item, from })
    setTab(to)
  }

  useEffect(() => {
    const offCron = ctx.onEvent('cron.changed', () => {
      queryClient.invalidateQueries({ queryKey: [ID, 'crons'] })
    })
    const offSkills = ctx.onEvent(SKILLS_EVENT, () => {
      queryClient.invalidateQueries({ queryKey: [ID, 'skills'] })
      queryClient.invalidateQueries({ queryKey: [ID, 'skill'] })
    })
    return () => {
      offCron()
      offSkills()
    }
  }, [ctx])

  const Active = { crons: CronsTab, webhooks: WebhooksTab, skills: SkillsTab }[tab]
  const tabBar = h(
    'div',
    { style: { padding: '8px', borderBottom: '1px solid ' + STROKE } },
    h(TabBar, {
      tabs: TABS,
      tab,
      onTab: (id) => {
        setNav(null)
        setTab(id)
      },
    })
  )

  return h(
    'div',
    { style: { display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 } },
    h(
      'div',
      { style: { display: 'flex', alignItems: 'center', gap: '8px', padding: '8px 12px', borderBottom: '1px solid ' + STROKE } },
      h('div', { style: { fontWeight: 600, fontSize: '0.875rem', color: TXT2 } }, "Ariadne's Thread"),
      profile !== 'default' ? h(Badge, {}, 'read-only') : null,
      h(ProfileSelect, { ctx, profile, onChange: setProfile })
    ),
    // Keyed by profile so a selection from one profile never leaks into another.
    h(
      TabsSlot.Provider,
      { value: tabBar },
      h('div', { style: { flex: '1', minHeight: 0 } }, h(Active, { key: profile, ctx, profile, nav, onOpenSkill: (name, from) => navigate('skills', name, from), onNavigate: navigate }))
    )
  )
}

export default {
  id: ID,
  name: "Ariadne's Thread",
  register(ctx) {
    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/ariadne-thread' },
        render: () => jsx(AriadnePage, { ctx }),
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        data: { path: '/ariadne-thread', label: "Ariadne's thread", codicon: 'type-hierarchy-sub' },
      },
    ])
  },
}
