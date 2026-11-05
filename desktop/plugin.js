// Disk plugins are loaded uncompiled, so the UI is built with jsx()/jsxs() calls, not JSX.
//
// Data comes from ctx.rest through React Query. Live updates arrive as native
// 'cron.changed' and as the backend's 'plugin.ariadne-thread.skills.changed' push, but
// push can drop on some remotes, so every query also polls.

import {
  host,
  haptic,
  useQuery,
  useMutation,
  queryClient,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
} from '@hermes/plugin-sdk'
import { jsx, jsxs, Fragment } from 'react/jsx-runtime'
import { useState, useEffect } from 'react'

const ID = 'ariadne-thread'
const SKILLS_EVENT = 'plugin.ariadne-thread.skills.changed'
const POLL_MS = 12000
const DRAFT_ATTEMPTS = 6
const DRAFT_RETRY_MS = 250

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

// Hyperscript over jsx/jsxs: children as trailing args, key pulled out of props.
function h(type, props, ...kids) {
  const { key, ...rest } = props || {}
  if (kids.length === 0) return jsx(type, rest, key)
  if (kids.length === 1) return jsx(type, { ...rest, children: kids[0] }, key)
  return jsxs(type, { ...rest, children: kids }, key)
}

// The default profile is the host's own; any other name is a read-only view.
const withProfile = (path, profile) =>
  profile && profile !== 'default' ? path + '?profile=' + encodeURIComponent(profile) : path

const errorText = (e) => (e && e.message) || String(e)

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
function Pipeline({ sequence, onStepClick }) {
  if (!sequence || !sequence.length) return h(Note, { text: 'No steps' })
  return h(
    'div',
    { style: { display: 'flex', flexDirection: 'column' } },
    sequence.map((step, i) => {
      const st = kindStyle(step.kind || step.type)
      const skillName = onStepClick && step.type === 'skill' && step.name ? step.name : null
      return h(
        'div',
        { key: i, style: { display: 'flex', flexDirection: 'column' } },
        i > 0 ? h(FlowArrow, {}) : null,
        h(
          'div',
          {
            onClick: skillName ? () => onStepClick({ type: 'skill', id: skillName }) : undefined,
            title: skillName ? 'Open skill: ' + skillName : undefined,
            style: {
              width: '100%',
              maxWidth: '440px',
              margin: '0 auto',
              border: '1px solid ' + mix(st.color, 30),
              borderLeft: '3px solid ' + st.color,
              borderRadius: '10px',
              background: 'linear-gradient(180deg, ' + mix(st.color, 9) + ', ' + mix(TXT1, 2) + ')',
              padding: '10px 12px',
              cursor: skillName ? 'pointer' : 'default',
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
            step.kindLabel && step.kindLabel.toLowerCase() !== st.label.toLowerCase()
              ? h('span', { style: { color: TXT4, fontSize: '0.625rem', marginLeft: 'auto' } }, step.kindLabel)
              : null
          ),
          step.label && step.label !== st.label
            ? h('div', { style: { color: TXT2, fontSize: '0.8125rem', fontWeight: 600, overflowWrap: 'break-word' } }, step.label)
            : null,
          step.detail
            ? h('div', { style: { color: TXT3, fontSize: '0.6875rem', overflowWrap: 'break-word', maxHeight: '3.2em', overflow: 'hidden' } }, String(step.detail))
            : null
        )
      )
    })
  )
}

function ListDetail({ top, list, children }) {
  return h(
    'div',
    { style: { display: 'flex', height: '100%', minHeight: 0 } },
    h(
      'div',
      { style: { width: '280px', flexShrink: 0, borderRight: '1px solid ' + STROKE, display: 'flex', flexDirection: 'column', minHeight: 0 } },
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
  if (failed) return failed
  const jobs = (q.data && q.data.jobs) || []
  if (!jobs.length) return h(Note, { text: 'No cron jobs' })
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
    h('div', { style: { marginTop: '16px' } }, h(Pipeline, { sequence: job.sequence, onStepClick: (ref) => onOpenSkill(ref.id) }))
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
  if (failed) return failed
  const routes = (q.data && q.data.routes) || []
  if (!routes.length) return h(Note, { text: 'No webhooks' })
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
    h('div', { style: { marginTop: '16px' } }, h(Pipeline, { sequence: route.sequence, onStepClick: (ref) => onOpenSkill(ref.id) }))
  )
}

function useEscape(ctx, onEscape) {
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') onEscape()
    }
    return ctx.addEventListener(window, 'keydown', onKey)
  }, [ctx, onEscape])
}

function Overlay({ onClose, children }) {
  return h(
    'div',
    {
      onClick: (e) => {
        if (e.target === e.currentTarget) onClose()
      },
      style: { position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.22)', backdropFilter: 'blur(0.125rem)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 'var(--z-modal, 130)', padding: '24px' },
    },
    children
  )
}

function Dialog({ title, onClose, children }) {
  return h(
    'div',
    { style: { width: '100%', maxWidth: '720px', height: '80vh', display: 'flex', flexDirection: 'column', borderRadius: '10px', border: '1px solid ' + STROKE, background: 'var(--ui-bg-card, var(--ui-bg-chrome, #1a1c1f))', color: TXT1, boxShadow: '0 10px 40px rgba(0,0,0,0.4)' } },
    h(
      'div',
      { style: { padding: '12px 16px', borderBottom: '1px solid ' + STROKE, display: 'flex', alignItems: 'center', justifyContent: 'space-between' } },
      h('div', { style: { fontWeight: 600, fontSize: '0.875rem', color: TXT1 } }, title),
      h('button', { onClick: onClose, 'aria-label': 'Close', style: { background: 'transparent', border: 'none', color: TXT3, cursor: 'pointer', fontSize: '1rem' } }, '✕')
    ),
    children
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

function EditDialog({ skill, ctx, profile, onClose }) {
  const [content, setContent] = useState(skill.raw)
  const [err, setErr] = useState(null)
  useEscape(ctx, onClose)

  const mutation = useMutation({
    mutationFn: (next) =>
      ctx.rest(withProfile('/skills/' + encodeURIComponent(skill.name), profile), { method: 'PUT', body: { content: next } }),
    onSuccess: () => {
      haptic('tap')
      queryClient.invalidateQueries({ queryKey: [ID, 'skills'] })
      queryClient.invalidateQueries({ queryKey: [ID, 'skill'] })
      host.notify({ kind: 'success', message: 'Skill saved: ' + skill.name })
      onClose()
    },
    onError: (e) => setErr(errorText(e)),
  })

  return h(
    Overlay,
    { onClose },
    h(
      Dialog,
      { title: 'Edit skill: ' + skill.name, onClose },
      h('div', { style: { padding: '8px 16px', fontSize: '0.6875rem', color: TXT4 } }, 'Full SKILL.md: YAML frontmatter (--- … ---) then the markdown body. Validated on save.'),
      h('textarea', {
        value: content,
        onChange: (e) => {
          setContent(e.target.value)
          setErr(null)
        },
        spellCheck: false,
        style: {
          flex: '1',
          margin: '0 16px',
          padding: '12px',
          resize: 'none',
          fontFamily: MONO,
          fontSize: '0.75rem',
          lineHeight: '1.5',
          borderRadius: '8px',
          border: '1px solid ' + STROKE,
          background: 'var(--ui-bg-input, transparent)',
          color: 'inherit',
          outline: 'none',
          whiteSpace: 'pre',
          overflowX: 'auto',
        },
      }),
      err ? h('div', { style: { margin: '8px 16px', color: RED, fontSize: '0.75rem' } }, err) : null,
      h(
        'div',
        { style: { padding: '12px 16px', display: 'flex', justifyContent: 'flex-end', gap: '8px' } },
        h('button', { onClick: onClose, style: outlineButton }, 'Cancel'),
        h('button', { onClick: () => mutation.mutate(content), disabled: mutation.isPending, style: primaryButton(mutation.isPending) }, mutation.isPending ? 'Saving…' : 'Save')
      )
    )
  )
}

function FlowSection({ flow, onStepClick }) {
  if (!flow) return null
  const note = !flow.fallback
    ? 'Parsed via ' + flow.strategy
    : flow.strategy === 'single'
      ? 'No explicit steps found. Showing the skill as one step.'
      : 'No explicit steps found. Showing its top-level sections.'
  return h(
    'div',
    { style: { marginTop: '16px' } },
    h(SectionLabel, { text: 'Flow' }),
    h('div', { style: { color: TXT4, fontSize: '0.6875rem', marginTop: '2px' } }, note),
    h('div', { style: { marginTop: '8px' } }, h(Pipeline, { sequence: flow.steps, onStepClick }))
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

function SkillDetail({ detail, onEdit, onSwitch, onCron }) {
  return h(
    Fragment,
    {},
    h(
      'div',
      { style: { display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' } },
      h('div', { style: { fontSize: '0.9375rem', fontWeight: 600, color: TXT2 } }, detail.name),
      detail.category ? h(Badge, {}, detail.category) : null,
      !detail.editable ? h(Badge, {}, 'read-only') : null,
      onEdit && detail.editable
        ? h('button', { onClick: onEdit, style: { ...outlineButton, marginLeft: 'auto', padding: '4px 10px', fontSize: '0.75rem' } }, '✎ Edit')
        : null
    ),
    detail.description ? h('div', { style: { marginTop: '8px', color: TXT3, fontSize: '0.8125rem' } }, detail.description) : null,
    h(FlowSection, { flow: detail.flow, onStepClick: onSwitch ? (ref) => onSwitch(ref.id) : undefined }),
    h(ChipList, { label: 'Credentials', items: detail.credentials, tone: 'accent' }),
    h(ChipList, { label: 'Related skills', items: detail.related_skills, onPick: onSwitch }),
    h(ChipList, { label: 'Used by crons', items: detail.used_by_crons, onPick: onCron }),
    h('div', { style: { marginTop: '16px' } }, h(SectionLabel, { text: 'SKILL.md' })),
    h('pre', { style: { marginTop: '8px', padding: '12px', borderRadius: '8px', background: 'var(--ui-bg-editor, rgba(128,128,128,0.10))', color: TXT2, fontSize: '0.75rem', whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontFamily: MONO } }, detail.raw)
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

function SkillsTab({ ctx, profile, nav, onNavigate }) {
  const [selected, setSelected] = useState(null)
  const [filter, setFilter] = useState('')
  const [editing, setEditing] = useState(false)
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
  if (failed) return failed

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

  return h(
    ListDetail,
    { top, list },
    currentName
      ? gate(detail, 'skill') ||
          (detail.data
            ? h(SkillDetail, {
                detail: detail.data,
                onEdit: () => setEditing(true),
                onSwitch: open,
                onCron: (cron) => onNavigate('crons', cron),
              })
            : null)
      : h(Note, { text: 'Select a skill' }),
    editing && detail.data ? h(EditDialog, { skill: detail.data, ctx, profile, onClose: () => setEditing(false) }) : null
  )
}

function buildChangePrompt(detail, request) {
  return [
    'Please update the Hermes skill "' + detail.name + '".',
    '',
    'Skill file: ' + detail.path,
    '',
    'Requested change:',
    request,
    '',
    'Edit that file in place and keep the frontmatter valid: it must start with --- on the first line, ' +
      'include name and description (description at most 1024 characters), and be followed by a non-empty body. ' +
      'When you are done, summarize what you changed.',
  ].join('\n')
}

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

function DraftBox({ ctx, detail, onDone }) {
  const [request, setRequest] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    const text = request.trim()
    if (!text || busy) return
    setBusy(true)
    const prompt = buildChangePrompt(detail, text)
    if (await draftInNewChat(ctx, prompt)) {
      haptic('tap')
      host.notify({ kind: 'info', message: 'Draft ready in a new chat. Review it, then send.' })
      onDone()
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

  return h(
    'div',
    { style: { padding: '12px 16px', borderTop: '1px solid ' + STROKE } },
    h('div', { style: { marginBottom: '6px' } }, h(SectionLabel, { text: 'Ask an agent to change it' })),
    h(
      'div',
      { style: { display: 'flex', gap: '8px' } },
      h('input', {
        value: request,
        onChange: (e) => setRequest(e.target.value),
        onKeyDown: (e) => {
          if (e.key === 'Enter') submit()
        },
        placeholder: 'e.g. reword the description, add a pitfall, restructure…',
        disabled: busy,
        style: { flex: '1', padding: '8px 10px', fontSize: '0.8125rem', borderRadius: '6px', border: '1px solid ' + STROKE, background: 'var(--ui-bg-input, transparent)', color: 'inherit', outline: 'none' },
      }),
      h('button', { onClick: submit, disabled: busy, style: primaryButton(busy) }, busy ? 'Opening…' : 'Draft in new chat')
    ),
    h('div', { style: { marginTop: '6px', color: TXT4, fontSize: '0.6875rem' } }, 'Opens a new chat with the request filled in. Nothing is sent until you send it.')
  )
}

function SkillModal({ name, ctx, profile, onNavigate, onClose }) {
  const [current, setCurrent] = useState(name)
  const [editing, setEditing] = useState(false)
  const q = useSkill(ctx, profile, current)
  useEscape(ctx, () => {
    if (!editing) onClose()
  })

  const detail = q.data
  return h(
    Overlay,
    { onClose },
    h(
      Dialog,
      { title: 'Skill: ' + current, onClose },
      h(
        'div',
        { style: { flex: '1', minHeight: 0, overflowY: 'auto', padding: '16px' } },
        gate(q, 'skill') ||
          (detail
            ? h(SkillDetail, {
                detail,
                onEdit: () => setEditing(true),
                onSwitch: setCurrent,
                onCron: (cron) => {
                  onClose()
                  onNavigate('crons', cron)
                },
              })
            : h(Note, { text: 'No skill data' }))
      ),
      detail && detail.editable ? h(DraftBox, { ctx, detail, onDone: onClose }) : null,
      editing && detail ? h(EditDialog, { skill: detail, ctx, profile, onClose: () => setEditing(false) }) : null
    )
  )
}

const TABS = [
  { id: 'crons', label: 'Crons' },
  { id: 'webhooks', label: 'Webhooks' },
  { id: 'skills', label: 'Skills' },
]

function TabBar({ tab, onTab }) {
  return h(
    'div',
    { style: { display: 'flex', gap: '2px', marginLeft: '8px' } },
    TABS.map((t) =>
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
  const [modalSkill, setModalSkill] = useState(null)

  const navigate = (to, item) => {
    setNav({ to, item })
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

  return h(
    'div',
    { style: { display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 } },
    h(
      'div',
      { style: { display: 'flex', alignItems: 'center', gap: '8px', padding: '8px 12px', borderBottom: '1px solid ' + STROKE } },
      h('div', { style: { fontWeight: 600, fontSize: '0.875rem', color: TXT2 } }, "Ariadne's Thread"),
      h(TabBar, {
        tab,
        onTab: (id) => {
          setNav(null)
          setTab(id)
        },
      }),
      profile !== 'default' ? h(Badge, {}, 'read-only') : null,
      h(ProfileSelect, { ctx, profile, onChange: setProfile })
    ),
    // Keyed by profile so a selection from one profile never leaks into another.
    h('div', { style: { flex: '1', minHeight: 0 } }, h(Active, { key: profile, ctx, profile, nav, onOpenSkill: setModalSkill, onNavigate: navigate })),
    modalSkill ? h(SkillModal, { name: modalSkill, ctx, profile, onNavigate: navigate, onClose: () => setModalSkill(null) }) : null
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
        data: { path: '/ariadne-thread', label: "Ariadne's Thread", codicon: 'type-hierarchy-sub' },
      },
    ])
  },
}
