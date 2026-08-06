# Design

<!-- impeccable:design-schema 1 -->

Recorded from the built system, not from intention. The single source of truth
is the `<style>` block in `templates/base.html`; there is no build step, no
stylesheet file, and `static/` is empty by design.

## Thesis

A chain-of-custody record system, not an admin panel. Status and the physical
identifier — plate, sticker ID, RFID UID — outrank chrome. The interface
refuses the dark navy shell and gold rule it replaced: that pairing was the
highest-contrast object on every screen and read as framework decoration
rather than as this product.

## Platform and constraints

- Django 5.2 server-rendered templates. Bootstrap 5.3 from CDN for grid and
  utilities only; every component is retokenized through Bootstrap's own
  `--bs-*` custom properties so framework defaults never surface.
- **No icon webfont.** Icons are an inline `<symbol>` sprite at the top of
  `<body>` in `base.html`, referenced as
  `<svg class="ic"><use href="#i-name"/></svg>`. 64 symbols on a 24px grid,
  1.75 stroke, round caps, `currentColor`. This deploys to a campus LAN that
  may have no internet route, so a CDN font was a liability.
- Light only. Chosen from the use scene, not from category: applicants on
  phones outdoors, and a guardhouse monitor read at ~2m in daylight.

## Colour

Cool-neutral ramp with one accent. Colour strategy is **Restrained**:
neutrals plus a single interactive hue. Semantic colour appears only on
status, never on chrome.

| Role | Token | Value |
|---|---|---|
| App background | `--bg` | `#FCFCFD` |
| Card surface | `--surface` | `#FFFFFF` |
| Sunk fill / table head | `--surface-sunk` | `#F7F8FA` |
| Hairline | `--border` | `#E4E7EC` |
| Heading text | `--text-strong` | `#101419` |
| Body text | `--text-main` | `#2A3039` |
| Secondary text | `--text-muted` | `#667080` (4.87:1 on `--bg`) |
| "No value" text | `--text-absent` | `#67707F` (4.99:1 on white) |
| Icon-only tone | `--text-faint` | `#98A0AE` — **never for words** |
| Accent (interactive) | `--accent-600` | `#1F4FA3` (7.76:1 with white) |

Every semantic hue carries a **base** (fill/border, 3:1 non-text) and an
**ink** (readable foreground on its own tint, verified ≥4.5:1 at 12px):
success `#1C8A5A`/`#10633F`, danger `#C8372D`/`#A02620`, warning
`#B87503`/`#8A5600`, issued `#3B3A99`. `--success-strong` (`#18774E`) exists
solely because white on `--success` is 4.38:1; a filled success button uses
the deeper green.

## Type

IBM Plex Sans for interface, IBM Plex Mono for identifiers. Mono is not a
costume here — it carries plate numbers, sticker IDs and RFID UIDs, which are
compared character by character and read aloud at a gate.

Scale, with an 11px floor reserved for uppercase tracked micro-labels:
`--fs-micro` 11 · `--fs-2xs` 12 · `--fs-xs` 13 · `--fs-sm` 14 (body) ·
`--fs-md` 15 · `--fs-lg` 17 · `--fs-xl` 20 · `--fs-2xl` 24 · `--fs-3xl` 30.

Hierarchy steps size, weight and tracking together so a heading level is
legible without comparison: h1 24/650/-0.025em, h2 20/620/-0.022em,
h3 17/600. Line height `1.2` tight, `1.35` snug, `1.55` body.

## Space and depth

Strict 4px grid, `--s1` (4) through `--s16` (64). Nothing off-scale.
Radii 4/6/8/12px.

Depth is a 1px hairline first; a shadow only when something genuinely floats.
Shadows always carry an offset plus a soft blur — no zero-offset halos.
`--sh-1` through `--sh-pop`.

## Motion

One easing curve, `cubic-bezier(.32,.72,0,1)`, and three durations
(110/170/240ms). Nothing animates from an invisible default except toasts,
which are genuinely new elements. Hover shifts tone; `:active` depresses
0.5px. `prefers-reduced-motion` collapses everything to 0.01ms.

The only looping animation is a 2s opacity pulse on a *live* status dot —
the gate poller and the RFID scanner chip. Nothing else pulses.

## Named components

Defined once in `base.html`: `.card` · `.stat-card` / `.stat-link` ·
`.status-badge` + the six status and three gate-action variants ·
`.plate` (the anchor object, plate-shaped, not a `<code>` chip) · `.kv`
review table · `.note` (instruction, distinct from `.alert` which is state) ·
`.tag` (a requirement on a field, distinct from a status on a record) ·
`.pick` / `.pick-time` · `.doc-item` / `.doc-row` · `.capacity` ·
`.status-panel` · `.record-head` · `.empty-state` · `.toolbar` ·
`.filter-bar` · `.wizard-*` · `.auth-*` · `.poll-state`.

## Surface exception: the guardhouse monitor

`templates/gate/live.html` is the one screen that gets a saturated field
instead of the light hairline treatment, because PRODUCT.md commits to
legibility on a fixed monitor at distance in daylight. The latest-scan hero
grounds in deep semantic colour — entry `#0F5137` (9.32:1), exit `#6B4A02`
(8.06:1), denied `#8C1F1A` (9.08:1) — with the plate at
`clamp(2.75rem, 7.5vw, 6rem)`, the largest type in the product.

## Accessibility contract (WCAG 2.1 AA)

- **Landmarks.** `<header>` banner, `<nav aria-label="Main navigation">`,
  `<main id="main-content">`, `<nav aria-label>` on every pagination block,
  and `role="search"` on the log / application / queue filter forms. There is
  deliberately **no `<footer>`**: the product has no footer content, and
  inventing some to fill a landmark slot would be decoration.
- **Headings.** One `h1` per page. `.card-title` makes every card header an
  `h2`, which is the layer the `h3`s inside empty states sit under. No level
  is skipped on any page.
- **Forms.** Every control has a programmatic name. Django 5.x already emits
  `aria-invalid="true"` and `aria-describedby="{auto_id}_error"` on any field
  with errors, and gives the matching errorlist that `id` — so
  `{{ form.x }}` + `{{ form.x.errors }}` is correctly bound with no filter and
  no widget change. Hand-written inputs carry `aria-describedby` to their own
  help text. `autocomplete` tokens satisfy 1.3.5 Identify Input Purpose.
- **Invalid state** is driven by the same `aria-invalid` attribute a screen
  reader reads, so the visual and the announced state cannot disagree. Colour
  is never the only signal — the errorlist names the problem in words.
- **Icons** are `aria-hidden="true"` without exception; icon-only controls
  carry an `aria-label` or a `.visually-hidden` span.
- **State** is broadcast: `aria-expanded` on the sidebar toggle and the reject
  disclosure, `aria-current="page"` on the active sidebar link and gate scope,
  `aria-current="step"` on the active wizard step.
- **Focus.** `.focus-ring` and every `:focus-visible` get 2px of `--focus`
  at a 2px offset over a surface-coloured halo, so the indicator survives
  tinted and filled backgrounds.
- **Non-text contrast (1.4.11).** Control boundaries use `--border-control`
  (`#848D9D`, 3.38:1 on white). `--border-strong` is 1.43:1 and is for
  decorative edges only — never for the visible boundary of a control.

Verified across all 22 rendered pages (including form-error states) at
1440 / 390 / 320px: zero contrast failures, zero horizontal overflow, zero
text under 11px, zero duplicate ids, zero dangling ARIA references, zero
heading skips, zero unlabelled controls, zero unnamed icon-only controls.

## Rules that are load-bearing

- `.table-responsive` must keep `position: relative`. The `visually-hidden`
  labels inside wide `<th>` cells are absolutely positioned; without a
  positioned ancestor they escape the scroll container and make the whole
  page scroll sideways.
- Multi-line Django comments must use `{% comment %}`, never `{# … #}`,
  which is single-line only and otherwise renders as visible body text.
- `--text-faint` is for icons. Words that a person reads use
  `--text-absent` or darker.
- No fabricated institutional identity. PRODUCT.md records that no official
  PalSU seal or logo exists, so the mark is a typographic wordmark beside a
  drawn barrier — the actual object at the gate. A shield or crest would read
  as a seal regardless of what it is made of.

## Verified

All 20 rendered surfaces, at 390 / 1024 / 1440px: zero horizontal page
overflow, zero text below 11px, zero contrast failures against WCAG AA
(4.5:1 body, 3:1 large). Design detector clean. All 28 templates compile;
all 64 icon references resolve with no unused symbols.
