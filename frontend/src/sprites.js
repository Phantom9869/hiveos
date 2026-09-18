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

export const SPRITE_WIDTH = DESIGNS[0][0].length * SCALE
export const SPRITE_HEIGHT = DESIGNS[0].length * SCALE
