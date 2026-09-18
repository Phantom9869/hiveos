import { useCallback, useMemo, useState } from 'react'

import { useHive } from './useHive'
import { AVATARS } from './sprites'
import {
  ActivityPanel,
  CanvasPanel,
  Mark,
  QueuePanel,
  QuotaBar,
  MemberBar,
  MemoryPanel,
  SpendPanel,
  SlotsPanel,
  StatusRail,
  ToastStack,
  formatEta,
} from './components'

const STORAGE_KEY = 'hiveos.identity'

// The Router truncates both of these server-side; matching the limits here
// keeps what you typed and what arrives the same thing.
const MAX_USER_ID = 40
// Mirrors state.TEAM_PATTERN server-side; a name outside it falls back to the
// default team rather than being rejected, so this is a hint, not a gate.
const MAX_TEAM = 31
const DEFAULT_TEAM = 'alpha'
const MAX_PROMPT = 2000
const MAX_TEXT = 500

function loadIdentity() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    // `team` was added after the first release; anyone with a stored
    // identity from before it lands in the default workspace.
    return parsed?.userId ? { team: DEFAULT_TEAM, ...parsed } : null
  } catch {
    return null
  }
}

function saveIdentity(identity) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(identity))
  } catch {
    // A blocked localStorage is not worth failing entry over.
  }
}

/* --- Entry gate ----------------------------------------------------------- */

function Gate({ onEnter }) {
  const [name, setName] = useState('')
  const [team, setTeam] = useState(DEFAULT_TEAM)
  const [avatar, setAvatar] = useState(AVATARS[0])

  const submit = (event) => {
    event.preventDefault()
    const userId = name.trim().slice(0, MAX_USER_ID)
    if (!userId) return
    // Lowercased to match the server: `Alpha` and `alpha` must be one room,
    // not two that look identical and cannot see each other.
    const teamId = team.trim().toLowerCase().slice(0, MAX_TEAM) || DEFAULT_TEAM
    onEnter({ userId, avatar, team: teamId })
  }

  return (
    <main className="gate">
      <div className="gate__card">
        <div className="gate__head">
          <Mark className="gate__mark" />
          <h1 className="gate__title">HiveOS</h1>
          <p className="gate__blurb">
            A workspace shares two agent slots and one token budget. Pick a
            name and a workspace — everything you do is visible to everyone
            else on that board, live.
          </p>
        </div>

        <form className="gate__form" onSubmit={submit}>
          <div>
            <label className="gate__legend" htmlFor="name">
              Your name
            </label>
            <input
              id="name"
              className="field"
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={MAX_USER_ID}
              placeholder="alice"
              autoComplete="off"
              autoFocus
            />
          </div>

          <div>
            <label className="gate__legend" htmlFor="team">
              Workspace
            </label>
            <input
              id="team"
              className="field"
              value={team}
              onChange={(event) => setTeam(event.target.value)}
              maxLength={MAX_TEAM}
              placeholder={DEFAULT_TEAM}
              autoComplete="off"
              aria-describedby="team-hint"
            />
            <p className="gate__hint" id="team-hint">
              Separate workspaces have their own budget, slots, queue and
              memory — they cannot see each other.
            </p>
          </div>

          <fieldset style={{ border: 0, margin: 0, padding: 0 }}>
            <legend className="gate__legend">Your marker</legend>
            <div className="chips">
              {AVATARS.map((option) => (
                <button
                  type="button"
                  key={option}
                  className={`chip ${option === avatar ? 'chip--on' : ''}`}
                  aria-pressed={option === avatar}
                  aria-label={`Marker ${option}`}
                  onClick={() => setAvatar(option)}
                >
                  {option}
                </button>
              ))}
            </div>
          </fieldset>

          <button className="btn" type="submit" disabled={!name.trim()}>
            Join workspace
          </button>
        </form>
      </div>
    </main>
  )
}

/* --- Request panel -------------------------------------------------------- */

function hintFor({ connection, budgetExhausted, holding, queued }) {
  if (connection !== 'open') {
    return { text: 'Reconnecting. The board catches up on its own.', alarm: false }
  }
  if (budgetExhausted) {
    return {
      text: 'Team quota reached. HiveOS will not invoke the model.',
      alarm: true,
    }
  }
  if (holding) {
    return { text: `Your task is running on ${holding.slot_id}.`, alarm: false }
  }
  if (queued) {
    return {
      text: `Position ${queued.queue_position} in the run queue · ${formatEta(
        queued.estimated_wait_seconds,
      )} away.`,
      alarm: false,
    }
  }
  return { text: 'If both slots are busy, your task joins the queue.', alarm: false }
}

function RequestPanel({ hive }) {
  const [prompt, setPrompt] = useState('')

  const { connection, budgetExhausted, holding, queued, working } = hive
  const hint = hintFor({ connection, budgetExhausted, holding, queued })
  const blocked = working || budgetExhausted || connection !== 'open'

  const submit = (event) => {
    event.preventDefault()
    const text = prompt.trim().slice(0, MAX_PROMPT)
    if (!text || blocked) return
    // Only clear the box if the frame actually went out — otherwise the user
    // loses what they typed to a socket that was not open.
    if (hive.requestAgent(text)) setPrompt('')
  }

  return (
    <section className="panel panel--request" aria-labelledby="request-label">
      <div className="panel__head">
        <span className="panel__label" id="request-label">
          Request an agent
        </span>
      </div>

      <form className="request" onSubmit={submit}>
        <textarea
          className="field"
          rows={2}
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          maxLength={MAX_PROMPT}
          placeholder="Summarise the incident report and list the follow-ups"
          aria-label="Task for the agent"
        />

        <div className="request__actions">
          <button className="btn" type="submit" disabled={blocked || !prompt.trim()}>
            Request agent
          </button>

          {holding && (
            <button
              className="btn btn--ghost"
              type="button"
              onClick={() => hive.releaseAgent(holding.slot_id)}
            >
              Release slot
            </button>
          )}

          <p className={`request__hint ${hint.alarm ? 'request__hint--alarm' : ''}`}>
            {hint.text}
          </p>
        </div>
      </form>
    </section>
  )
}

/* --- Team chat ------------------------------------------------------------ */

/* Plain human chat, not the agent. `send_message` has existed since Phase 1
 * and was broadcast to every client, but no UI ever sent one — the activity
 * log could render a `chat_message` that nothing could produce. */
function ChatComposer({ hive }) {
  const [text, setText] = useState('')
  const disabled = hive.connection !== 'open'

  const submit = (event) => {
    event.preventDefault()
    const message = text.trim().slice(0, MAX_TEXT)
    if (!message || disabled) return
    if (hive.sendMessage(message)) setText('')
  }

  return (
    <form className="chat" onSubmit={submit}>
      <input
        className="field"
        value={text}
        onChange={(event) => setText(event.target.value)}
        maxLength={MAX_TEXT}
        placeholder="Say something to the team"
        aria-label="Message the team"
        autoComplete="off"
      />
      <button className="btn btn--ghost" type="submit" disabled={disabled || !text.trim()}>
        Send
      </button>
    </form>
  )
}

/* --- Workspace ------------------------------------------------------------ */

function Workspace({ identity }) {
  const hive = useHive(identity)
  const { board } = hive

  // Who is mid-task, so the floor can mark them working. Derived from the slot
  // table rather than tracked separately — the slots are the authority on who
  // holds an agent, and a second source would be one more thing to drift.
  const busyUsers = useMemo(
    () =>
      new Set(
        board.agents
          .filter((a) => a.status === 'BUSY' && a.current_user)
          .map((a) => a.current_user),
      ),
    [board.agents],
  )

  if (hive.configError) {
    return (
      <main className="gate">
        <div className="gate__card">
          <div className="gate__head">
            <h1 className="gate__title">Not configured</h1>
            <p className="gate__blurb">
              {hive.configError} Rebuild the frontend with the WebSocket URL
              from the stack output — see DEPLOYMENT.md.
            </p>
          </div>
        </div>
      </main>
    )
  }

  return (
    <div className="board">
      <StatusRail
        team={board.team}
        members={board.members}
        connection={hive.connection}
      />

      <QuotaBar
        tokensUsed={board.tokens_used}
        tokenBudget={board.token_budget}
        pctUsed={board.pct_used}
        exhausted={hive.budgetExhausted}
        estimated={hive.usageEstimated}
      />

      <CanvasPanel
        members={board.members}
        me={identity.userId}
        busyUsers={busyUsers}
        agents={board.agents}
        queue={board.queue}
        onMove={hive.moveAvatar}
      />

      {/* No SlotsPanel and no QueuePanel here any more. Both said exactly what
          the room now says — a lit monitor *is* the slot being BUSY, and a
          "queued #1" label under a person *is* their queue position. Keeping
          the cards would have been the same state rendered twice, and they
          cost 266px of a viewport the floor needs. `SlotsPanel` and
          `QueuePanel` are still exported; nothing else changed about them. */}
      {board.memory.length > 0 && <MemoryPanel memory={board.memory} />}

      <SpendPanel
        spend={board.spend}
        members={board.members}
        tokenBudget={board.token_budget}
      />

      <RequestPanel hive={hive} />

      <ActivityPanel activity={hive.activity}>
        <ChatComposer hive={hive} />
      </ActivityPanel>

      <MemberBar
        members={board.members}
        me={identity.userId}
        busyUsers={busyUsers}
        queue={board.queue}
      />

      <ToastStack toasts={hive.toasts} />
    </div>
  )
}

export default function App() {
  const [identity, setIdentity] = useState(loadIdentity)

  const enter = useCallback((next) => {
    saveIdentity(next)
    setIdentity(next)
  }, [])

  if (!identity) return <Gate onEnter={enter} />
  return <Workspace identity={identity} />
}
