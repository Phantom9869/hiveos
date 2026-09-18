/* Presentational pieces of the operator console.
 *
 * All of them are pure functions of board state, so what a browser shows is
 * exactly what the last snapshot said — which is the property the demo turns
 * on: three windows, one board, no divergence.
 */

import { useEffect, useRef, useState } from 'react'

import { SEAT_SHADOWS, SHADOWS, SPRITE_HEIGHT, SPRITE_WIDTH, STEP_SHADOWS } from './sprites'

const NUM = new Intl.NumberFormat('en-US')

/** BUILD_PLAN.md: green <50%, amber 50-80%, red >80%. */
export function toneFor(pct) {
  if (pct < 50) return 'safe'
  if (pct <= 80) return 'warn'
  return 'alarm'
}

export function formatEta(seconds) {
  if (seconds == null) return '—'
  if (seconds < 90) return `~${Math.round(seconds)}s`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  return rest ? `~${minutes}m ${rest}s` : `~${minutes}m`
}

function formatClock(date) {
  return date.toLocaleTimeString('en-GB', { hour12: false })
}

export function Mark({ className }) {
  return (
    <svg className={className} viewBox="0 0 32 32" aria-hidden="true">
      <path
        d="M16 5.5l8 4.6v9.2l-8 4.6-8-4.6V10.1z"
        fill="none"
        stroke="#f0a714"
        strokeWidth="2.6"
        strokeLinejoin="round"
      />
      <circle cx="16" cy="14.7" r="2.8" fill="#4fa8c7" />
    </svg>
  )
}

const LAMP_TEXT = {
  idle: 'offline',
  connecting: 'connecting',
  open: 'live',
  reconnecting: 'reconnecting',
  closed: 'offline',
}

export function Lamp({ connection }) {
  return (
    <span className={`lamp lamp--${connection}`}>
      <span className="lamp__dot" />
      {LAMP_TEXT[connection] ?? connection}
    </span>
  )
}

export function StatusRail({ team, members, connection }) {
  const avatars = members
    .map((m) => m.avatar)
    .filter(Boolean)
    .slice(0, 5)
    .join('')

  return (
    <header className="rail">
      <Mark className="rail__mark" />
      <span className="rail__name">HiveOS</span>
      <span className="rail__team">{team}</span>
      <span className="rail__spacer" />
      <span className="rail__members">
        {avatars} {members.length} online
      </span>
      <Lamp connection={connection} />
    </header>
  )
}

export function QuotaPanel({ tokensUsed, tokenBudget, pctUsed, exhausted, estimated }) {
  const pct = Math.max(0, Math.min(100, pctUsed ?? 0))
  const tone = toneFor(pct)
  const remaining = Math.max(0, (tokenBudget ?? 0) - (tokensUsed ?? 0))

  return (
    <section className="panel" aria-labelledby="quota-label">
      <div className="panel__head">
        <span className="panel__label" id="quota-label">
          Team token quota
        </span>
      </div>

      <div className="quota__figures">
        <span className="quota__used">{NUM.format(tokensUsed ?? 0)}</span>
        <span className="quota__budget">/ {NUM.format(tokenBudget ?? 0)}</span>
        <span className={`quota__pct tone--${tone}`}>{pct.toFixed(1)}%</span>
      </div>

      <div
        className="strip"
        role="meter"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Team token quota used"
      >
        <div
          className={`strip__fill tone--${tone}`}
          style={{ clipPath: `inset(0 ${100 - pct}% 0 0)` }}
        />
      </div>

      {exhausted ? (
        <p className="quota__note quota__note--alarm">
          Quota reached. HiveOS stops invoking the agent until the budget is raised.
        </p>
      ) : (
        <p className="quota__note">
          {NUM.format(remaining)} tokens left, shared by the whole team
          {/* Normally absent: counts are the usage the provider reported. This
              appears only when some spend on this board was charged from the
              fallback heuristic because the model was unreachable — said next
              to the number rather than trusting a narrator to remember. */}
          {estimated ? ' · partly estimated — the model was unreachable' : ''}
        </p>
      )}
    </section>
  )
}

/* The quota, as chrome rather than a panel.
 *
 * Same numbers, same semantic tones, same tick geometry as QuotaPanel — this
 * is a re-layout, not a second implementation, and it deliberately keeps the
 * strip because a segment snapping on is what survives video compression.
 * Costs ~70px where the panel cost 148, which is most of what putting the room
 * first had to pay for.
 */
export function QuotaBar({ tokensUsed, tokenBudget, pctUsed, exhausted, estimated }) {
  const pct = Math.max(0, Math.min(100, pctUsed ?? 0))
  const tone = toneFor(pct)
  const remaining = Math.max(0, (tokenBudget ?? 0) - (tokensUsed ?? 0))

  return (
    <section className="quotabar" aria-label="Team token quota">
      <div className="quotabar__row">
        <span className="quotabar__label">Team quota</span>
        <span className="quotabar__used">{NUM.format(tokensUsed ?? 0)}</span>
        <span className="quotabar__budget">/ {NUM.format(tokenBudget ?? 0)}</span>
        <span className={`quotabar__pct tone--${tone}`}>{pct.toFixed(1)}%</span>
      </div>

      <div
        className="strip"
        role="meter"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Team token quota used"
      >
        <div
          className={`strip__fill tone--${tone}`}
          style={{ clipPath: `inset(0 ${100 - pct}% 0 0)` }}
        />
      </div>

      <p className={`quotabar__note ${exhausted ? 'quotabar__note--alarm' : ''}`}>
        {exhausted
          ? 'Quota reached — HiveOS stops invoking the agent.'
          : `${NUM.format(remaining)} tokens left, shared by the whole team`}
        {!exhausted && estimated ? ' · partly estimated' : ''}
      </p>
    </section>
  )
}

/* Who is in the room and what they are doing, along the bottom.
 *
 * The same three states the floor shows, in a form that survives someone
 * standing behind a desk or two people overlapping — the room is the nicer
 * read, this is the reliable one.
 */
export function MemberBar({ members, me, busyUsers, queue }) {
  const queuedBy = new Map(queue.map((entry) => [entry.user_id, entry.queue_position]))

  return (
    <section className="memberbar" aria-label="Who is here">
      {members.map((member, index) => {
        const busy = busyUsers.has(member.user_id)
        const position = queuedBy.get(member.user_id)
        const state = busy ? 'busy' : position ? 'queued' : 'idle'
        return (
          <div key={member.user_id} className={`member member--${state}`}>
            <span
              className={`member__face sprite--${index % 3}`}
              style={{ '--art': SHADOWS[index % 3] }}
              aria-hidden="true"
            />
            <span className="member__name">
              {member.user_id === me ? `${member.user_id} (you)` : member.user_id}
            </span>
            <span className="member__state">
              {busy ? 'working' : position ? `queued #${position}` : 'idle'}
            </span>
          </div>
        )
      })}
    </section>
  )
}

export function SlotsPanel({ agents, me }) {
  const running = agents.filter((a) => a.status === 'BUSY').length

  return (
    <section className="panel" aria-labelledby="slots-label">
      <div className="panel__head">
        <span className="panel__label" id="slots-label">
          Agent slots
        </span>
        <span className="panel__aside">
          {running} of {agents.length || 2} running
        </span>
      </div>

      <div className="slots">
        {agents.map((agent) => {
          const busy = agent.status === 'BUSY'
          const mine = busy && agent.current_user === me
          return (
            <article
              key={agent.slot_id}
              className={`slot ${busy ? 'slot--busy' : 'slot--idle'}`}
            >
              <span className="slot__id">{agent.slot_id}</span>
              <span className="slot__status">
                <span className="slot__dot" />
                {busy ? 'busy' : 'idle'}
              </span>
              {busy ? (
                <p className={`slot__holder ${mine ? 'slot__mine' : ''}`}>
                  {mine ? 'held by you' : `held by ${agent.current_user}`}
                </p>
              ) : (
                <p className="slot__holder slot__holder--empty">Available</p>
              )}
            </article>
          )
        })}
      </div>
    </section>
  )
}

export function QueuePanel({ queue, me }) {
  return (
    <section className="panel" aria-labelledby="queue-label">
      <div className="panel__head">
        <span className="panel__label" id="queue-label">
          Run queue
        </span>
        <span className="panel__aside">
          {queue.length} waiting
        </span>
      </div>

      {queue.length === 0 ? (
        <p className="empty">No tasks waiting.</p>
      ) : (
        <div className="queue">
          {queue.map((entry) => {
            const mine = entry.user_id === me
            return (
              <div
                key={`${entry.user_id}-${entry.queue_position}`}
                className={`queue__row ${mine ? 'queue__row--mine' : ''}`}
              >
                <span className="queue__pos">{entry.queue_position}</span>
                <span className="queue__user">{entry.user_id}</span>
                {mine && <span className="queue__tag">you</span>}
                <span className="queue__eta">
                  {formatEta(entry.estimated_wait_seconds)}
                </span>
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}

/** How far one arrow-key press moves you, in canvas percent. */
const STEP = 4

/* The back wall occupies the top of the floor, so the walkable ground starts
 * below it. Coordinates stay exactly what CONTRACT.md says they are — 0-100
 * over the whole board, unchanged server-side — and only the *rendering* maps
 * that range onto the floor strip. Without this, one spawn in five puts
 * somebody inside the wall.
 *
 * The click handler applies the inverse, so clicking a spot still puts you on
 * that spot. Both directions use this one constant; they cannot drift apart.
 */
const WALK_TOP = 22

/* How long a walk takes, whatever the distance. Must match the `left`/`top`
 * transition in styles.css: the class drives the leg animation and the
 * transition drives the travel, and if they disagree someone arrives and keeps
 * striding, or stops stepping halfway across the room.
 *
 * Fixed rather than proportional to distance on purpose — a real walking speed
 * would make a cross-room move take several seconds, and this is a board, not
 * a game. */
const WALK_MS = 700

/* Tracks who is mid-walk, so the sprite can run its leg cycle only while
 * actually travelling.
 *
 * Diffs the *rendered* position rather than the stored one, because those are
 * no longer the same thing: taking a slot seats you at a desk without changing
 * the coordinate the server holds for you. Diffing stored coordinates would
 * leave people sliding to their desk with no gait, and sliding back with none
 * either.
 *
 * Derived here rather than sent by the server: movement is already broadcast
 * as a new position, and "is walking" is a property of the *rendering* of that
 * change, not a fact about the board. Putting it in the protocol would mean a
 * client that reconnected mid-walk had to be told about an animation.
 */
function useWalking(placed) {
  const [walking, setWalking] = useState(() => new Set())
  const previous = useRef(new Map())
  const timers = useRef(new Map())

  useEffect(() => {
    const moved = []
    placed.forEach(({ id, left, top }) => {
      const was = previous.current.get(id)
      if (was && (was.left !== left || was.top !== top)) moved.push(id)
      previous.current.set(id, { left, top })
    })
    if (!moved.length) return

    setWalking((current) => new Set([...current, ...moved]))

    // Per-user timers. A single shared timer would let one person's move cut
    // short another's walk that started 200ms earlier.
    const handles = timers.current
    moved.forEach((id) => {
      clearTimeout(handles.get(id))
      handles.set(
        id,
        setTimeout(() => {
          handles.delete(id)
          setWalking((current) => {
            const next = new Set(current)
            next.delete(id)
            return next
          })
        }, WALK_MS),
      )
    })
  }, [placed])

  // Unmount only: clearing on every change would cancel walks in flight.
  useEffect(() => {
    const handles = timers.current
    return () => handles.forEach(clearTimeout)
  }, [])

  return walking
}

const toFloor = (y) => WALK_TOP + (y * (100 - WALK_TOP)) / 100
const fromFloor = (v) => ((v - WALK_TOP) * 100) / (100 - WALK_TOP)

const ARROWS = {
  ArrowUp: [0, -STEP],
  ArrowDown: [0, STEP],
  ArrowLeft: [-STEP, 0],
  ArrowRight: [STEP, 0],
}

/* Desk positions, in the same 0-100 percentage space as the avatars.
 *
 * Percentages, not pixels, for exactly the reason CONTRACT.md gives for avatar
 * coordinates: three browsers at different widths have to agree on where
 * things are. A pixel desk would sit under a different person's feet on a
 * narrower window, which is the one thing this board is supposed to be
 * incapable of.
 *
 * `slot_id` ties a desk to a real scheduler slot, so the room is a view of
 * machine state rather than scenery that happens to resemble it.
 */
const DESKS = [
  { slot_id: 'coder', x: 27, y: 34, label: 'coder' },
  { slot_id: 'researcher', x: 73, y: 34, label: 'researcher' },
]

/* How far below a desk's own centre its chair sits, in floor percent. The desk
 * stack is label, monitor, surface, chair from the top, all centred on the
 * desk coordinate, so the seat is roughly a third of that stack below it. */
const SEAT_DROP = 13

/* Fixed decor. Percentages for the same reason. Positions are chosen to stay
 * clear of the desks and to put something in the lower half, which was dead
 * space that made the room read as a field rather than an office. */
const PLANTS = [
  { x: 6, y: 62 },
  { x: 94, y: 62 },
  { x: 16, y: 88 },
]

/* The shared workspace floor.
 *
 * Absolutely positioned elements inside a box, moved with CSS transitions — no
 * canvas element, no game engine, no animation loop (ARCHITECTURE.md rules a
 * game engine out, and nothing here needs one). Everything is positioned in
 * percentages, so the same room renders identically at any window width, which
 * is the property the multi-browser demo depends on.
 *
 * The desks are not decoration: each one is bound to a scheduler slot and
 * lights up while that slot is BUSY, so "both agents are working and a third
 * person is waiting" is legible from the room itself.
 */
export function CanvasPanel({ members, me, busyUsers, agents = [], queue = [], onMove }) {
  const bySlot = new Map(agents.map((agent) => [agent.slot_id, agent]))
  const queuedBy = new Map(queue.map((entry) => [entry.user_id, entry.queue_position]))

  /* Who is sitting where. A slot holder is drawn at that slot's desk rather
   * than at their own coordinate — and crucially the coordinate itself is left
   * alone, so releasing the slot walks them back to wherever they were
   * standing. Writing the seat into their position instead would strand them
   * at the desk afterwards, and would mean the room quietly editing state the
   * server owns. */
  const seatOf = new Map()
  agents.forEach((agent) => {
    if (agent.status !== 'BUSY' || !agent.current_user) return
    const desk = DESKS.find((d) => d.slot_id === agent.slot_id)
    if (desk) seatOf.set(agent.current_user, desk)
  })

  const placed = members.map((member, index) => {
    const desk = seatOf.get(member.user_id)
    return {
      id: member.user_id,
      member,
      index,
      desk,
      left: desk ? desk.x : Math.max(0, Math.min(100, Number(member.x) || 0)),
      top: desk
        ? desk.y + SEAT_DROP
        : toFloor(Math.max(0, Math.min(100, Number(member.y) || 0))),
    }
  })

  const walking = useWalking(placed)

  const move = (event) => {
    const box = event.currentTarget.getBoundingClientRect()
    if (!box.width || !box.height) return
    onMove(
      ((event.clientX - box.left) / box.width) * 100,
      fromFloor(((event.clientY - box.top) / box.height) * 100),
    )
  }

  const nudge = (event) => {
    const delta = ARROWS[event.key]
    if (!delta) return
    event.preventDefault()
    const self = members.find((m) => m.user_id === me)
    onMove((Number(self?.x) || 0) + delta[0], (Number(self?.y) || 0) + delta[1])
  }

  return (
    <section className="panel" aria-labelledby="floor-label">
      <div className="panel__head">
        <span className="panel__label" id="floor-label">
          Workspace floor
        </span>
        <span className="panel__aside">click or use arrow keys to move</span>
      </div>

      <div
        className="floor"
        onClick={move}
        onKeyDown={nudge}
        tabIndex={0}
        role="application"
        aria-label={
          `Shared workspace floor. ${members.length} ` +
          `${members.length === 1 ? 'person' : 'people'} present. ` +
          'Click or use the arrow keys to move your marker.'
        }
      >
        {/* Wall fixtures sit in the back band of the room, above everything
            else, so the floor has an "up" and reads as enclosed rather than as
            a field seen from above. */}
        <div className="fixture fixture--board" aria-hidden="true">
          <span className="board__scribble" />
          <span className="board__scribble board__scribble--short" />
        </div>

        <div className="fixture fixture--window" aria-hidden="true" />
        <div className="fixture fixture--daylight" aria-hidden="true" />

        <div className="fixture fixture--cooler" aria-hidden="true">
          <span className="cooler__bottle" />
          <span className="cooler__body" />
        </div>

        {/* A rug under the lounge end of the room. Purely spatial: it breaks
            the single uniform tile field into zones, which is most of what
            makes a top-down room look designed rather than tiled. */}
        <div className="fixture fixture--rug" aria-hidden="true" />

        {/* Desks after the rug so they sit on it, and before the pawns so
            someone standing at a desk is in front of it, not behind it. */}
        {DESKS.map((desk) => {
          const slot = bySlot.get(desk.slot_id)
          const busy = slot?.status === 'BUSY'
          return (
            <div
              key={desk.slot_id}
              className={`desk ${busy ? 'desk--busy' : ''}`}
              style={{ left: `${desk.x}%`, top: `${desk.y}%` }}
              aria-hidden="true"
            >
              {/* Label above the desk, not below it. People approach a desk
                  from the chair side, so a label under the chair is guaranteed
                  to end up behind somebody's head. */}
              <span className="desk__label">{desk.label}</span>
              <span className="desk__monitor" />
              <span className="desk__surface">
                <span className="desk__keyboard" />
              </span>
              <span className="desk__chair" />
            </div>
          )
        })}

        {PLANTS.map((plant, i) => (
          <div
            key={i}
            className="decor decor--plant"
            style={{ left: `${plant.x}%`, top: `${plant.y}%` }}
            aria-hidden="true"
          >
            <span className="decor__leaves" />
            <span className="decor__pot" />
          </div>
        ))}

        {placed.map(({ id, member, index, desk, left, top }) => {
          const mine = id === me
          const busy = busyUsers.has(id)
          const position = queuedBy.get(id)
          const isWalking = walking.has(id)
          // Seated only once they have actually arrived. Tucking the legs away
          // at the moment the slot is claimed would have them glide to the
          // desk with nothing to walk on.
          const seated = Boolean(desk) && !isWalking
          return (
            <div
              key={id}
              className={
                `pawn ${mine ? 'pawn--mine' : ''} ${busy ? 'pawn--busy' : ''} ` +
                `${isWalking ? 'pawn--walking' : ''} ${seated ? 'pawn--seated' : ''}`
              }
              style={{ left: `${left}%`, top: `${top}%` }}
            >
              {/* Three sprite designs, assigned by position in the deduped
                  member list so everyone looks distinct without the server
                  having to carry an appearance field. */}
              <span
                className={`sprite sprite--${index % 3}`}
                style={{
                  '--art': seated ? SEAT_SHADOWS[index % 3] : SHADOWS[index % 3],
                  '--art-step': STEP_SHADOWS[index % 3],
                  width: SPRITE_WIDTH,
                  height: SPRITE_HEIGHT,
                }}
                aria-hidden="true"
              />
              <span className="pawn__name">
                {mine ? 'you' : id}
              </span>
              <span className="pawn__state">
                {busy ? 'working' : position ? `queued #${position}` : 'idle'}
              </span>
            </div>
          )
        })}
      </div>
    </section>
  )
}

/* Team-level events you would otherwise miss because you were looking at a
 * different panel. Deliberately only two kinds — a toast for everything turns
 * into noise nobody reads, and on a recording it covers the board. */
export function ToastStack({ toasts }) {
  return (
    <div className="toasts" role="status" aria-live="polite">
      {toasts.map((toast) => (
        <article className={`toast toast--${toast.kind}`} key={toast.id}>
          <p className="toast__title">{toast.title}</p>
          <p className="toast__text">{toast.text}</p>
          {toast.who && <p className="toast__who">saved by {toast.who}</p>}
        </article>
      ))}
    </div>
  )
}

export function MemoryPanel({ memory }) {
  return (
    <section className="panel" aria-labelledby="memory-label">
      <div className="panel__head">
        <span className="panel__label" id="memory-label">
          Team memory
        </span>
        <span className="panel__aside">
          {memory.length} {memory.length === 1 ? 'fact' : 'facts'}
        </span>
      </div>
      <div className="memory">
        {memory.map((fact) => (
          <span className="fact" key={fact.key}>
            <span className="fact__key">{fact.key}</span>
            <span className="fact__val"> — {fact.val}</span>
          </span>
        ))}
      </div>
    </section>
  )
}

export function ActivityPanel({ activity, children }) {
  return (
    <section className="panel panel--grow" aria-labelledby="activity-label">
      <div className="panel__head">
        <span className="panel__label" id="activity-label">
          Activity
        </span>
      </div>

      {activity.length === 0 ? (
        <p className="empty">Nothing yet. Request an agent to start.</p>
      ) : (
        <div className="activity">
          {activity.map((entry, index) => (
            <article
              className={`entry entry--${entry.kind}`}
              key={`${entry.ts.getTime()}-${index}`}
            >
              <time className="entry__ts">{formatClock(entry.ts)}</time>
              <div className="entry__body">
                <span className="entry__who">
                  {entry.who}
                  {entry.cost ? (
                    <span className="entry__cost">
                      {entry.estimated ? '~' : ''}
                      {NUM.format(entry.cost)} tokens
                    </span>
                  ) : null}
                </span>
                <p className="entry__text">{entry.text}</p>
              </div>
            </article>
          ))}
        </div>
      )}

      {children}
    </section>
  )
}
