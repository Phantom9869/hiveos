/* The WebSocket client. Owns all workspace state.
 *
 * Protocol authority is CONTRACT.md. Two things about it shape this file:
 *
 * 1. `state_snapshot` is load-bearing — one frame renders the whole board, so
 *    a cold or reconnecting client never has to wait for the next incremental
 *    event.
 *
 * 2. `queue_update` is broadcast once per *waiting* user and there is no
 *    removal frame. A client applying only increments would keep showing a
 *    user who has already been dispatched. So increments are applied for
 *    instant feel, and a debounced `hello` re-sync follows each burst of
 *    events to pull the authoritative board back. The snapshot is one
 *    DynamoDB query; on a demo with a handful of users the cost is noise, and
 *    a board that cannot drift is worth far more on a recording.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

const WS_URL = import.meta.env.VITE_WS_URL

/** Mirrors scheduler.ESTIMATED_TASK_SECONDS — used only to relabel an ETA
 *  after a local renumber, never as the source of truth. */
const ESTIMATED_TASK_SECONDS = 8

const RESYNC_DELAY_MS = 500
const BACKOFF_MS = [1000, 2000, 4000, 8000]
const MAX_ACTIVITY = 60

/** Events that can move the slot table, the queue or the member list, and
 *  therefore warrant an authoritative re-read. `state_snapshot` is
 *  deliberately absent — including it would make the re-sync feed itself. */
const RESYNC_EVENTS = new Set([
  'agent_state_update',
  'queue_update',
  'agent_response',
  // `user_left` carries a user_id but membership is per-connection, so the
  // optimistic removal below is wrong for anyone holding a second tab. The
  // re-sync is what makes it right again.
  'user_joined',
  'user_left',
])

const EMPTY_BOARD = {
  team: 'alpha',
  agents: [],
  tokens_used: 0,
  token_budget: 0,
  pct_used: 0,
  members: [],
  memory: [],
  queue: [],
}

function renumber(queue) {
  return queue.map((entry, index) => ({
    ...entry,
    queue_position: index + 1,
    estimated_wait_seconds: (index + 1) * ESTIMATED_TASK_SECONDS,
  }))
}

/** `state_snapshot.queue[]` carries no ETA — only `queue_update` does
 *  (CONTRACT.md). Since the re-sync applies a snapshot ~500ms after the
 *  incremental frame, anything that arrived only on `queue_update` gets wiped.
 *  The server's figure is exactly `position * ESTIMATED_TASK_SECONDS`, so
 *  deriving it here is equivalent rather than an approximation — and it gives
 *  a user who reconnects while queued an ETA the snapshot alone could not. */
function withEta(queue) {
  return queue.map((entry) => ({
    ...entry,
    estimated_wait_seconds:
      entry.estimated_wait_seconds ??
      (entry.queue_position ?? 0) * ESTIMATED_TASK_SECONDS,
  }))
}

function bySlot(agents) {
  return [...agents].sort((a, b) =>
    String(a.slot_id ?? '').localeCompare(String(b.slot_id ?? '')),
  )
}

/** The server's `members[]` is one entry per CONN# row, so a person with two
 *  tabs open appears twice. The rail reports how many *people* are in the
 *  workspace, which is the only reading that means anything on a team board. */
function distinctMembers(members) {
  const seen = new Map()
  for (const member of members) {
    if (member?.user_id && !seen.has(member.user_id)) {
      seen.set(member.user_id, member)
    }
  }
  return [...seen.values()]
}

export function applyFrame(board, frame) {
  switch (frame.event) {
    case 'state_snapshot':
      return {
        team: frame.team ?? board.team,
        agents: bySlot(frame.agents ?? []),
        tokens_used: frame.tokens_used ?? 0,
        token_budget: frame.token_budget ?? 0,
        pct_used: frame.pct_used ?? 0,
        members: distinctMembers(frame.members ?? []),
        memory: frame.memory ?? [],
        queue: withEta(frame.queue ?? []),
      }

    case 'agent_state_update': {
      const slotId = frame.slot_id ?? frame.agent_type
      if (!slotId) return board

      const known = board.agents.some((a) => a.slot_id === slotId)
      const patch = {
        slot_id: slotId,
        agent_type: slotId,
        status: frame.status,
        current_user: frame.current_user ?? null,
      }
      const agents = known
        ? board.agents.map((a) => (a.slot_id === slotId ? { ...a, ...patch } : a))
        : bySlot([...board.agents, patch])

      // A user who just went BUSY has been dispatched, so they are no longer
      // waiting. This is the only removal signal the protocol gives us.
      const queue =
        frame.status === 'BUSY' && frame.current_user
          ? renumber(board.queue.filter((q) => q.user_id !== frame.current_user))
          : board.queue

      return { ...board, agents, queue }
    }

    case 'token_update':
      return {
        ...board,
        tokens_used: frame.tokens_used ?? board.tokens_used,
        token_budget: frame.token_budget ?? board.token_budget,
        pct_used: frame.pct_used ?? board.pct_used,
      }

    case 'queue_update': {
      if (!frame.user_id) return board
      const others = board.queue.filter((q) => q.user_id !== frame.user_id)
      const merged = [
        ...others,
        {
          user_id: frame.user_id,
          agent_type: frame.agent_type ?? null,
          queue_position: frame.queue_position,
          estimated_wait_seconds: frame.estimated_wait_seconds,
        },
      ].sort((a, b) => (a.queue_position ?? 0) - (b.queue_position ?? 0))
      return { ...board, queue: withEta(merged) }
    }

    case 'memory_updated': {
      if (!frame.key) return board
      const others = board.memory.filter((m) => m.key !== frame.key)
      return {
        ...board,
        memory: [
          ...others,
          { key: frame.key, val: frame.val, updated_by: frame.updated_by },
        ],
      }
    }

    case 'user_joined': {
      if (!frame.user_id) return board
      const others = board.members.filter((m) => m.user_id !== frame.user_id)
      return {
        ...board,
        members: [
          ...others,
          {
            user_id: frame.user_id,
            avatar: frame.avatar,
            x: frame.x ?? 0,
            y: frame.y ?? 0,
          },
        ],
      }
    }

    case 'user_left':
      return {
        ...board,
        members: board.members.filter((m) => m.user_id !== frame.user_id),
      }

    default:
      return board
  }
}

function activityFor(frame) {
  const ts = new Date()
  switch (frame.event) {
    case 'agent_response':
      return {
        kind: 'response',
        who: `${frame.agent_type ?? 'agent'} → ${frame.user_id ?? 'unknown'}`,
        text: frame.text ?? '',
        cost: frame.tokens_used_this_call,
        // While the agent is stubbed the count is a heuristic over real text,
        // not billed model usage. Carried through so the UI says so.
        estimated: Boolean(frame.estimated),
        ts,
      }
    case 'chat_message':
      return { kind: 'chat', who: frame.user_id ?? 'unknown', text: frame.text ?? '', ts }
    case 'memory_updated':
      return {
        kind: 'memory',
        who: `remembered by ${frame.updated_by ?? 'an agent'}`,
        text: `${frame.key} — ${frame.val}`,
        ts,
      }
    case 'budget_exhausted':
      return {
        kind: 'error',
        who: 'quota',
        text: 'Token quota reached. HiveOS refused the request — no model call was made.',
        ts,
      }
    case 'error':
      return { kind: 'error', who: 'system', text: frame.message ?? 'Something failed.', ts }
    default:
      return null
  }
}

export function useHive(identity) {
  const [connection, setConnection] = useState('idle')
  const [board, setBoard] = useState(EMPTY_BOARD)
  const [activity, setActivity] = useState([])
  const [budgetExhausted, setBudgetExhausted] = useState(false)
  // Sticky: once any usage on this board was estimated, the meter's total is
  // partly estimated for the rest of the session and must keep saying so.
  const [usageEstimated, setUsageEstimated] = useState(false)

  const socketRef = useRef(null)
  const resyncRef = useRef(null)

  const scheduleResync = useCallback(() => {
    clearTimeout(resyncRef.current)
    resyncRef.current = setTimeout(() => {
      const socket = socketRef.current
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ action: 'hello' }))
      }
    }, RESYNC_DELAY_MS)
  }, [])

  const handleFrame = useCallback(
    (raw) => {
      let frame
      try {
        frame = JSON.parse(raw)
      } catch {
        return
      }
      if (!frame || typeof frame !== 'object') return

      setBoard((prev) => applyFrame(prev, frame))

      const entry = activityFor(frame)
      if (entry) setActivity((prev) => [entry, ...prev].slice(0, MAX_ACTIVITY))

      if (frame.event === 'budget_exhausted') setBudgetExhausted(true)

      // `estimated` rides on token_update / agent_response; `usage_estimated`
      // is the same fact on the snapshot, which is the only way a client that
      // loaded cold can learn it. Sticky either way — never cleared, because
      // an estimate already folded into the total does not stop being one.
      if (frame.estimated || frame.usage_estimated) setUsageEstimated(true)
      if (frame.event === 'state_snapshot') {
        const budget = frame.token_budget ?? 0
        setBudgetExhausted(budget > 0 && (frame.tokens_used ?? 0) >= budget)
      }

      if (RESYNC_EVENTS.has(frame.event)) scheduleResync()
    },
    [scheduleResync],
  )

  useEffect(() => {
    if (!identity || !WS_URL) return undefined

    let disposed = false
    let attempt = 0
    let retryTimer = null
    let socket = null

    const open = () => {
      if (disposed) return
      setConnection(attempt === 0 ? 'connecting' : 'reconnecting')

      const url =
        `${WS_URL}?user_id=${encodeURIComponent(identity.userId)}` +
        `&avatar=${encodeURIComponent(identity.avatar)}`
      socket = new WebSocket(url)
      socketRef.current = socket

      socket.onopen = () => {
        attempt = 0
        setConnection('open')
        // The snapshot cannot be pushed from $connect — API Gateway has not
        // finished establishing the connection until that integration
        // returns. The client pulls it instead. CONTRACT.md.
        socket.send(JSON.stringify({ action: 'hello' }))
      }

      socket.onmessage = (event) => handleFrame(event.data)

      socket.onclose = () => {
        if (disposed) return
        socketRef.current = null
        const wait = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)]
        attempt += 1
        setConnection('reconnecting')
        retryTimer = setTimeout(open, wait)
      }
    }

    open()

    return () => {
      disposed = true
      clearTimeout(retryTimer)
      clearTimeout(resyncRef.current)
      if (socket) {
        socket.onclose = null
        socket.close()
      }
      socketRef.current = null
      setConnection('idle')
    }
  }, [identity, handleFrame])

  const send = useCallback((payload) => {
    const socket = socketRef.current
    if (!socket || socket.readyState !== WebSocket.OPEN) return false
    socket.send(JSON.stringify(payload))
    return true
  }, [])

  const requestAgent = useCallback(
    (prompt, agentType) =>
      send({
        action: 'claim_agent',
        prompt,
        agent_type: agentType ?? null,
        user_id: identity?.userId,
      }),
    [send, identity],
  )

  const releaseAgent = useCallback(
    (agentType) =>
      send({
        action: 'release_agent',
        agent_type: agentType,
        user_id: identity?.userId,
      }),
    [send, identity],
  )

  // Derived exactly the way the Router derives it in `_already_working`, so
  // the button disables for precisely the cases the server would refuse.
  const me = identity?.userId
  const holding = useMemo(
    () => board.agents.find((a) => a.status === 'BUSY' && a.current_user === me) ?? null,
    [board.agents, me],
  )
  const queued = useMemo(
    () => board.queue.find((q) => q.user_id === me) ?? null,
    [board.queue, me],
  )

  return {
    connection,
    board,
    activity,
    budgetExhausted,
    usageEstimated,
    holding,
    queued,
    working: Boolean(holding || queued),
    requestAgent,
    releaseAgent,
    configError: WS_URL ? null : 'VITE_WS_URL was not set at build time.',
  }
}
