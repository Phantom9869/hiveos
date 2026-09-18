import { useCallback, useState } from 'react'

import { useHive } from './useHive'
import {
  ActivityPanel,
  Mark,
  QueuePanel,
  QuotaPanel,
  MemoryPanel,
  SlotsPanel,
  StatusRail,
  formatEta,
} from './components'

const AVATARS = ['🐝', '🦊', '🐙', '🦉', '🐺', '🦋', '🐢', '🦜']
const STORAGE_KEY = 'hiveos.identity'

// The Router truncates both of these server-side; matching the limits here
// keeps what you typed and what arrives the same thing.
const MAX_USER_ID = 40
const MAX_PROMPT = 2000

function loadIdentity() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    return parsed?.userId ? parsed : null
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
  const [avatar, setAvatar] = useState(AVATARS[0])

  const submit = (event) => {
    event.preventDefault()
    const userId = name.trim().slice(0, MAX_USER_ID)
    if (!userId) return
    onEnter({ userId, avatar })
  }

  return (
    <main className="gate">
      <div className="gate__card">
        <div className="gate__head">
          <Mark className="gate__mark" />
          <h1 className="gate__title">HiveOS</h1>
          <p className="gate__blurb">
            Team Alpha shares two agent slots and one token budget. Pick a name
            to join the workspace — everything you do is visible to everyone
            else on the board, live.
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
    <section className="panel" aria-labelledby="request-label">
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

/* --- Workspace ------------------------------------------------------------ */

function Workspace({ identity }) {
  const hive = useHive(identity)
  const { board } = hive

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

      <QuotaPanel
        tokensUsed={board.tokens_used}
        tokenBudget={board.token_budget}
        pctUsed={board.pct_used}
        exhausted={hive.budgetExhausted}
      />

      <SlotsPanel agents={board.agents} me={identity.userId} />
      <QueuePanel queue={board.queue} me={identity.userId} />

      {/* Only once there is something to show — an empty panel is dead space
          on the recording, and it appears on its own when memory lands. */}
      {board.memory.length > 0 && <MemoryPanel memory={board.memory} />}

      <RequestPanel hive={hive} />
      <ActivityPanel activity={hive.activity} />
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
