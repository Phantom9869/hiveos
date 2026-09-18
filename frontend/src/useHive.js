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

/** Floor between outbound `move_avatar` frames. A held arrow key repeats at
 *  the OS rate (~30/s), and every frame is a WebSocket send plus a DynamoDB
 *  write plus a fan-out to every member. The trailing send below guarantees
 *  the final resting position still goes out, so throttling costs nothing but
 *  intermediate frames nobody would have seen at 150 ms of CSS transition. */
const MOVE_THROTTLE_MS = 100

/** How long a memory toast stays up. Long enough to read a short fact on a
 *  recording, short enough that it is gone before the next beat. */
const TOAST_MS = 4500

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
  // Any rejected action means the client just applied something optimistically
  // that the server did not accept — a failed `move_avatar` leaves your marker
  // somewhere nobody else can see it, which is precisely the divergence the
  // board is supposed to be incapable of. Re-reading is the cheap way to make
  // every optimistic update self-correcting.
  'error',
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
  // Derived server-side from TASK# rows and carried on the snapshot, so a cold
  // client gets the whole ledger rather than only what happens after it joins.
  history: [],
  spend: [],
  protected: false,
  owned: false,
  // Whether *this* connection holds admin rights. Server-decided at the
  // handshake; the UI has no other way to know, and must not guess.
  is_admin: false,
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
        history: frame.history ?? [],
        spend: frame.spend ?? [],
        protected: Boolean(frame.protected),
        owned: Boolean(frame.owned),
        is_admin: Boolean(frame.is_admin),
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

    /* Keyed by user_id, not connection: `members[]` carries no connection_id
     * (CONTRACT.md), so one person is one marker and a second tab moves the
     * same one. Ignored for someone not on the board — a move from a user we
     * have not seen join would otherwise add a member with no avatar. */
    case 'avatar_moved': {
      if (!frame.user_id) return board
      return {
        ...board,
        members: board.members.map((m) =>
          m.user_id === frame.user_id
            ? { ...m, x: Number(frame.x) || 0, y: Number(frame.y) || 0 }
            : m,
        ),
      }
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
        // Normally false — the count is the usage the provider reported. True
        // only when the model was unreachable and the answer was composed
        // locally. Carried through so the UI can prefix the cost with `~`.
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
  // Terminal: the workspace's rows are gone, so there is nothing to reconnect to.
  const [deleted, setDeleted] = useState(false)
  // Sticky: once any usage on this board was estimated, the meter's total is
  // partly estimated for the rest of the session and must keep saying so.
  const [usageEstimated, setUsageEstimated] = useState(false)

  const [toasts, setToasts] = useState([])

  const socketRef = useRef(null)
  const resyncRef = useRef(null)
  const moveRef = useRef({ last: 0, timer: null, pending: null })
  const toastTimers = useRef(new Set())

  const pushToast = useCallback((toast) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
    setToasts((prev) => [...prev, { ...toast, id }])
    const timer = setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id))
      toastTimers.current.delete(timer)
    }, TOAST_MS)
    toastTimers.current.add(timer)
  }, [])

  useEffect(
    () => () => {
      for (const timer of toastTimers.current) clearTimeout(timer)
      toastTimers.current.clear()
    },
    [],
  )

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

      // Toasts are for the two things that happen to the *team* rather than to
      // you, and that you would otherwise only notice by watching a panel you
      // were not looking at.
      if (frame.event === 'memory_updated' && frame.key) {
        pushToast({
          kind: 'memory',
          title: 'Team memory updated',
          text: `${frame.key} — ${frame.val}`,
          who: frame.updated_by,
        })
      }
      if (frame.event === 'budget_exhausted') {
        pushToast({
          kind: 'alarm',
          title: 'Token quota reached',
          text: 'HiveOS refused the request — no model call was made.',
        })
      }

      // `estimated` rides on token_update / agent_response; `usage_estimated`
      // is the same fact on the snapshot, which is the only way a client that
      // loaded cold can learn it. Sticky either way — never cleared, because
      // an estimate already folded into the total does not stop being one.
      if (frame.estimated || frame.usage_estimated) setUsageEstimated(true)
      if (frame.event === 'workspace_deleted') {
        setDeleted(true)
        return
      }

      if (frame.event === 'state_snapshot') {
        const budget = frame.token_budget ?? 0
        setBudgetExhausted(budget > 0 && (frame.tokens_used ?? 0) >= budget)
      }

      // `avatar_moved` is deliberately NOT in RESYNC_EVENTS. It is the only
      // high-frequency event in the protocol, and re-reading the whole board
      // after each one would turn a walk across the canvas into a burst of
      // snapshot queries. Nothing else depends on a position, so drift here
      // costs nothing and is corrected by the next real re-sync anyway.
      if (RESYNC_EVENTS.has(frame.event)) scheduleResync()
    },
    [scheduleResync, pushToast],
  )

  useEffect(() => {
    if (!identity || !WS_URL) return undefined

    let disposed = false
    let attempt = 0
    // Distinguishes "refused" from "dropped": a socket that has opened at
    // least once was authorised, so a later close is a network event.
    let everOpened = false
    let retryTimer = null
    let socket = null

    const open = () => {
      if (disposed) return
      setConnection(attempt === 0 ? 'connecting' : 'reconnecting')

      const url =
        `${WS_URL}?user_id=${encodeURIComponent(identity.userId)}` +
        `&avatar=${encodeURIComponent(identity.avatar)}` +
        // Only readable by the server on $connect — every frame after this
        // carries a connection id and nothing else, so the team is recorded
        // server-side at connect time and looked up from there.
        `&team=${encodeURIComponent(identity.team || 'alpha')}` +
        // A browser cannot set headers on a WebSocket handshake, so a
        // protected workspace's passphrase has to travel here. TLS covers it
        // in transit; it would appear in API Gateway access logs if those were
        // ever switched on, which they are not. Noted rather than hidden.
        (identity.passphrase
          ? `&passphrase=${encodeURIComponent(identity.passphrase)}`
          : '') +
        // Minted by this browser, never returned by the server. Whoever
        // creates a workspace already holds it, so there is nothing to hand
        // back and no window in which it could be intercepted. On an existing
        // workspace it is simply checked, and a token that does not match just
        // means no admin rights.
        (identity.adminToken
          ? `&admin_token=${encodeURIComponent(identity.adminToken)}`
          : '')
      socket = new WebSocket(url)
      socketRef.current = socket

      socket.onopen = () => {
        attempt = 0
        everOpened = true
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

        // A handshake that never opened, twice running, is a refusal rather
        // than a blip: API Gateway answers a failed $connect with 403 and the
        // browser surfaces it as an ordinary close, with no status a script
        // can read. Retrying it forever would spin silently against a
        // workspace this person simply cannot join, so say so and stop.
        if (!everOpened && attempt >= 1) {
          setConnection('refused')
          return
        }

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

  const me = identity?.userId

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

  const sendMessage = useCallback((text) => send({ action: 'send_message', text }), [send])

  /* Administration. Every one of these is refused server-side unless this
   * connection was admitted with the workspace's admin token, so the UI
   * hiding them is convenience rather than the control. */
  const setBudget = useCallback(
    (tokenBudget) => send({ action: 'admin_set_budget', token_budget: tokenBudget }),
    [send],
  )
  const rotatePassphrase = useCallback(
    (passphrase) => send({ action: 'admin_rotate_passphrase', passphrase }),
    [send],
  )
  const deleteWorkspace = useCallback(
    () => send({ action: 'admin_delete_workspace' }),
    [send],
  )

  /** Move this client's avatar, throttled, with a guaranteed trailing send.
   *
   *  The optimistic local apply is what makes the canvas feel immediate: the
   *  server echo is ~150-300 ms away, and waiting for it makes your own marker
   *  lag your cursor. Everyone else's marker moves only on the echo, which is
   *  the authoritative position.
   */
  const moveAvatar = useCallback(
    (x, y) => {
      const clamped = {
        x: Math.round(Math.max(0, Math.min(100, x)) * 100) / 100,
        y: Math.round(Math.max(0, Math.min(100, y)) * 100) / 100,
      }

      if (me) {
        setBoard((prev) => ({
          ...prev,
          members: prev.members.map((m) =>
            m.user_id === me ? { ...m, ...clamped } : m,
          ),
        }))
      }

      const state = moveRef.current
      const now = Date.now()
      const flush = () => {
        state.last = Date.now()
        state.timer = null
        const next = state.pending
        state.pending = null
        if (next) send({ action: 'move_avatar', ...next })
      }

      if (now - state.last >= MOVE_THROTTLE_MS && !state.timer) {
        state.last = now
        send({ action: 'move_avatar', ...clamped })
        return true
      }

      // Inside the window: remember the newest position and make sure exactly
      // one trailing send is scheduled. Without this the last move of a drag
      // or a key-repeat is dropped and your avatar ends up somewhere nobody
      // else sees it.
      state.pending = clamped
      if (!state.timer) {
        state.timer = setTimeout(flush, MOVE_THROTTLE_MS - (now - state.last))
      }
      return true
    },
    [send, me],
  )

  useEffect(
    () => () => {
      clearTimeout(moveRef.current.timer)
      moveRef.current.timer = null
    },
    [],
  )

  // Derived exactly the way the Router derives it in `_already_working`, so
  // the button disables for precisely the cases the server would refuse.
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
    toasts,
    budgetExhausted,
    usageEstimated,
    holding,
    queued,
    me,
    working: Boolean(holding || queued),
    requestAgent,
    releaseAgent,
    sendMessage,
    moveAvatar,
    deleted,
    setBudget,
    rotatePassphrase,
    deleteWorkspace,
    configError: WS_URL ? null : 'VITE_WS_URL was not set at build time.',
  }
}
