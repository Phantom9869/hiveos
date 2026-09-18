/* Top-down pixel sprites, drawn with `box-shadow`.
 *
 * Top-down rather than isometric on purpose: the floor is a plan view, and an
 * isometric character standing on a flat grid reads as a mistake rather than a
 * style. From above you mostly see hair and shoulders, which is why the three
 * designs differ by silhouette at the top — that is the only part with enough
 * pixels to carry identity at this size.
 *
 * The art lives here as ASCII because a 90-cell grid hand-written as
 * `box-shadow` offsets is unreviewable and silently wrong the first time a
 * comma moves. The geometry is generated; the *palette* stays in CSS as
 * per-character custom properties, so recolouring a character never touches
 * this file.
 *
 *   H  hair        var(--sp-hair)
 *   F  face        var(--sp-skin)
 *   S  shirt       var(--sp-shirt)
 *   .  transparent
 */

const LAYERS = {
  H: 'var(--sp-hair)',
  F: 'var(--sp-skin)',
  S: 'var(--sp-shirt)',
}

/* 9 wide x 10 tall. Odd width so the sprite has a true centre column, which is
 * what lets `translate(-50%, -50%)` land it exactly on its coordinate. */
export const DESIGNS = [
  // 0 — long hair
  [
    '..HHHHH..',
    '.HHHHHHH.',
    'HHHHHHHHH',
    'HHFFFFFHH',
    '.HFFFFFH.',
    '..FFFFF..',
    '.SSSSSSS.',
    'SSSSSSSSS',
    'FSSSSSSSF',
    '..S...S..',
  ],
  // 1 — cropped
  [
    '...HHH...',
    '.HHHHHHH.',
    'HHHHHHHHH',
    'HHFFFFFHH',
    '.HFFFFFH.',
    '..FFFFF..',
    '.SSSSSSS.',
    'SSSSSSSSS',
    'FSSSSSSSF',
    '..S...S..',
  ],
  // 2 — topknot
  [
    '....H....',
    '..HHHHH..',
    '.HHHHHHH.',
    'HHFFFFFHH',
    '.HFFFFFH.',
    '..FFFFF..',
    '.SSSSSSS.',
    'SSSSSSSSS',
    'FSSSSSSSF',
    '..S...S..',
  ],
]

/* One CSS pixel of the sprite, in real pixels. 4 puts a 9x10 design at 36x40:
 * big enough to have presence in the room at the width a demo browser is
 * recorded at, which is the whole reason not to judge these at desktop zoom.
 * 3 was tried first and the characters read as smudges. */
export const SCALE = 4

/* The walk frame.
 *
 * Only the legs move. At nine pixels wide there is no room for a readable arm
 * swing, and from directly above you would barely see one anyway — feet
 * together alternating with feet apart is what reads as walking, and it reads
 * at 4x on a compressed video, which is the only test that matters here.
 *
 * Derived rather than written out a second time: the walk frame must differ
 * from the rest frame in exactly one row, and three hand-copied 10-row designs
 * would let the other nine drift apart silently.
 */
const LEGS_APART = '.S.....S.'

function stepFrame(design) {
  const rows = [...design]
  rows[rows.length - 1] = LEGS_APART
  return rows
}

/* The seated frame: legs gone, because they are under the desk.
 *
 * From directly above, sitting down is almost entirely about where you are —
 * at the chair rather than beside it. The only part that actually changes
 * shape is the legs disappearing beneath the desk, and that one row is enough
 * to sell it once the character is in the right place.
 */
const LEGS_TUCKED = '.........'

function seatedFrame(design) {
  const rows = [...design]
  rows[rows.length - 1] = LEGS_TUCKED
  return rows
}

/** Build the `box-shadow` value for one design. */
export function shadowFor(design) {
  const cells = []
  design.forEach((row, y) => {
    ;[...row].forEach((cell, x) => {
      const colour = LAYERS[cell]
      if (colour) cells.push(`${x * SCALE}px ${y * SCALE}px 0 0 ${colour}`)
    })
  })
  return cells.join(', ')
}

/* Precomputed once at module load — these never change at runtime, and
 * rebuilding ~60 shadow entries on every render of every pawn would be work
 * done for nothing. */
export const SHADOWS = DESIGNS.map(shadowFor)
export const STEP_SHADOWS = DESIGNS.map((design) => shadowFor(stepFrame(design)))
export const SEAT_SHADOWS = DESIGNS.map((design) => shadowFor(seatedFrame(design)))

export const SPRITE_WIDTH = DESIGNS[0][0].length * SCALE
export const SPRITE_HEIGHT = DESIGNS[0].length * SCALE

/* The markers offered at the entry gate.
 *
 * This list lives here, next to the art, because it is what decides how a
 * person looks on the floor. It used to live in App.jsx as picker options
 * only, and the floor ignored it entirely — see `lookFor`.
 */
export const AVATARS = ['🐝', '🦊', '🐙', '🦉', '🐺', '🦋', '🐢', '🦜']

/* One hair colour per marker, so all eight are telling apart at a glance even
 * though there are only three silhouettes.
 *
 * Identity, not status — which is why these may carry hue at all when the rest
 * of the room may not. Kept desaturated so none of them can be mistaken for
 * the jade/amber/coral of budget health or the instrument blue of a running
 * agent.
 */
const HAIR = [
  '#7a4b6b', // plum
  '#3f5f6b', // steel
  '#8a5a3c', // rust
  '#5b6b45', // moss
  '#4a5570', // slate
  '#6e5a48', // clay
  '#6b3f4f', // wine
  '#52705f', // sage
]

export const HAIR_COLOURS = HAIR

/** Stable small hash, for people who arrived without a marker. */
function hashOf(text) {
  let h = 0
  for (let i = 0; i < (text || '').length; i += 1) {
    h = (h * 31 + text.charCodeAt(i)) | 0
  }
  return Math.abs(h)
}

/* How one person looks. Derived from their *marker*, which is theirs and does
 * not change — never from their position in the member list.
 *
 * The list-position version was a real bug: `index % 3` meant two people in a
 * room of four were identical, and because the member list is deduped and
 * re-synced, an index could shift under someone and change their character
 * while they were standing still. A board whose whole claim is that everyone
 * sees the same thing cannot have people swapping faces.
 */
export function lookFor(avatar, userId) {
  const picked = AVATARS.indexOf(avatar)
  const key = picked >= 0 ? picked : hashOf(userId) % AVATARS.length
  return {
    art: SHADOWS[key % DESIGNS.length],
    step: STEP_SHADOWS[key % DESIGNS.length],
    seat: SEAT_SHADOWS[key % DESIGNS.length],
    hair: HAIR[key % HAIR.length],
  }
}
