/* Presentational pieces of the operator console.
 *
 * All of them are pure functions of board state, so what a browser shows is
 * exactly what the last snapshot said — which is the property the demo turns
 * on: three windows, one board, no divergence.
 */

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

const ARROWS = {
  ArrowUp: [0, -STEP],
  ArrowDown: [0, STEP],
  ArrowLeft: [-STEP, 0],
  ArrowRight: [STEP, 0],
}

/* The shared workspace floor.
 *
 * Absolutely positioned markers inside a fixed-ratio box, moved with a CSS
 * transition — no canvas element, no game engine, no animation loop
 * (ARCHITECTURE.md rules a game engine out, and nothing here needs one).
 * Positions are percentages, so the same board renders identically at any
 * window width, which is the property the three-browser demo depends on.
 */
export function CanvasPanel({ members, me, busyUsers, onMove }) {
  const move = (event) => {
    const box = event.currentTarget.getBoundingClientRect()
    if (!box.width || !box.height) return
    onMove(
      ((event.clientX - box.left) / box.width) * 100,
      ((event.clientY - box.top) / box.height) * 100,
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
        {members.map((member) => {
          const mine = member.user_id === me
          const busy = busyUsers.has(member.user_id)
          return (
            <div
              key={member.user_id}
              className={`pawn ${mine ? 'pawn--mine' : ''} ${busy ? 'pawn--busy' : ''}`}
              style={{
                left: `${Math.max(0, Math.min(100, Number(member.x) || 0))}%`,
                top: `${Math.max(0, Math.min(100, Number(member.y) || 0))}%`,
              }}
            >
              <span className="pawn__body">{member.avatar || '🐝'}</span>
              <span className="pawn__name">
                {mine ? 'you' : member.user_id}
                {busy && <span className="pawn__work" aria-label="running a task" />}
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
