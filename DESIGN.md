---
name: Engram Dashboard
description: Dark instrument panel for browsing and editing the Engram knowledge graph
colors:
  bg: "oklch(0.16 0.018 260)"
  surface: "oklch(0.205 0.018 260)"
  surface-hover: "oklch(0.245 0.02 260)"
  surface-active: "oklch(0.28 0.02 260)"
  border: "oklch(0.32 0.02 260)"
  border-strong: "oklch(0.42 0.02 260)"
  text: "oklch(0.93 0.008 260)"
  text-muted: "oklch(0.755 0.018 260)"
  text-faint: "oklch(0.55 0.018 260)"
  accent: "oklch(0.745 0.135 250)"
  accent-strong: "oklch(0.8 0.13 250)"
  accent-ink: "oklch(0.18 0.03 250)"
  danger: "oklch(0.665 0.19 25)"
  danger-ink: "oklch(0.18 0.02 25)"
  success: "oklch(0.72 0.15 150)"
typography:
  title:
    fontFamily: "-apple-system, 'Segoe UI', Roboto, system-ui, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 600
    lineHeight: 1.3
  body:
    fontFamily: "-apple-system, 'Segoe UI', Roboto, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "-apple-system, 'Segoe UI', Roboto, system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 400
    lineHeight: 1.5
  micro:
    fontFamily: "-apple-system, 'Segoe UI', Roboto, system-ui, sans-serif"
    fontSize: "0.6875rem"
    fontWeight: 400
    lineHeight: 1.5
rounded:
  sm: "6px"
  md: "8px"
  lg: "12px"
  pill: "999px"
spacing:
  "1": "0.25rem"
  "2": "0.5rem"
  "3": "0.75rem"
  "4": "1rem"
  "5": "1.5rem"
  "6": "2rem"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-ink}"
    rounded: "{rounded.md}"
    padding: "8px 16px"
  button-primary-hover:
    backgroundColor: "{colors.accent-strong}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    padding: "8px 16px"
  button-secondary-hover:
    backgroundColor: "{colors.surface-hover}"
  button-danger:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.danger}"
    rounded: "{rounded.md}"
    padding: "8px 16px"
  field:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    padding: "8px 12px"
  panel:
    backgroundColor: "{colors.surface}"
    rounded: "0"
    padding: "24px"
  toast:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    padding: "12px 16px"
---

# Design System: Engram Dashboard

## 1. Overview

**Creative North Star: "The Control Room"**

The Engram Dashboard is a dark instrument panel for watching and steering a live knowledge graph, not a marketing surface or a consumer app. Every element on screen is a readout or a control: the canvas is the main viewport, the topbar is a strip of instruments (search, filter, status spinner), the side panel is a form for direct edits. Nothing exists to be admired — it exists to be read at a glance and acted on in seconds, matching PRODUCT.md's "technical and minimal... every pixel earns its place" personality.

This system explicitly rejects the generic-SaaS-admin template feel named in PRODUCT.md's anti-references: no bootstrap-y card grids, no dashboard-in-a-box CRUD-table-with-sidebar layout, no elements that exist to look impressive rather than to work.

**Key Characteristics:**
- Near-black desaturated blue base (`oklch(0.16 0.018 260)`), never pure black or warm neutral.
- One accent color (signal blue) used sparingly, only for primary actions and active/matched states.
- Flat tonal layering for structure; shadows reserved for two floating surfaces only.
- Fixed rem type scale, no fluid/clamp headlines — this is a UI, not editorial content.
- Fast, subtle motion (150–220ms, ease-out-expo) on hover/focus/state changes only.

## 2. Colors

A near-monochrome blue-black scale carries the whole interface; one signal-blue accent is the only saturated color in regular use, with red and green reserved strictly for danger and success states.

### Primary
- **Signal Blue** (`oklch(0.745 0.135 250)`): the accent — primary buttons, active legend/filter chips, selected graph node ring, search score numbers, link color. Nowhere else.

### Neutral
- **Void** (`oklch(0.16 0.018 260)`): page background (`--bg`), the canvas the graph floats on.
- **Panel** (`oklch(0.205 0.018 260)`): resting surface for fields, buttons, the side panel, toasts (`--surface`).
- **Panel Hover** (`oklch(0.245 0.02 260)`): hover state for surfaces (`--surface-hover`).
- **Panel Active** (`oklch(0.28 0.02 260)`): pressed state for surfaces (`--surface-active`).
- **Border** (`oklch(0.32 0.02 260)`): default 1px borders on fields, buttons, panels (`--border`).
- **Border Strong** (`oklch(0.42 0.02 260)`): hover-state border (`--border-strong`).
- **Ink** (`oklch(0.93 0.008 260)`): primary text (`--text`).
- **Ink Muted** (`oklch(0.755 0.018 260)`): secondary text, placeholders, labels (`--text-muted`).
- **Ink Faint** (`oklch(0.55 0.018 260)`): tertiary/meta text, timestamps, icon buttons (`--text-faint`).

### Semantic
- **Danger Red** (`oklch(0.665 0.19 25)`): delete actions, error toasts, superseded-entry pill. Confirm-to-delete state fills the button solid red.
- **Success Green** (`oklch(0.72 0.15 150)`): success toast border only.

### Named Rules
**The One Accent Rule.** Signal Blue is the only saturated hue in active use. It never appears as decoration — only on the element the user is meant to act on or that is currently selected/matched.

**The No-Warm-Neutral Rule.** Every neutral in the scale is a desaturated blue (hue 260), never shifted warm. The system reads as instrument-panel cool, deliberately, never cozy.

## 3. Typography

**Body Font:** -apple-system, "Segoe UI", Roboto, system-ui, sans-serif
**Display Font:** none — this system has no display/hero role; the largest text is a panel title.

**Character:** A single system-font stack at a tight, fixed rem scale (six steps, ~1.125 ratio). No serif, no webfont, no display size — legibility and density over expression.

### Hierarchy
- **Title** (600, 1.125rem, 1.3 line-height): panel headings ("Edit entry", "New entry").
- **Body/UI** (400/500, 0.875rem, 1.5 line-height): the dominant size — buttons, canvas node labels at rest, field labels' sibling text.
- **Base** (400, 0.8125rem, 1.5 line-height): field inputs, search hit titles, textarea content.
- **Label** (400, 0.75rem, 1.5 line-height): form labels, search snippets, legend items, checkbox text.
- **Micro** (400, 0.6875rem, 1.5 line-height): meta line (entry id, last-modified timestamp).

### Named Rules
**The Fixed-Scale Rule.** All type sizes are fixed rem values, never `clamp()` or viewport units. This is a tool rendered at a known density, not editorial content that reflows across devices.

## 4. Elevation

Flat by default, shadow on float. The interface conveys depth mainly through the tonal surface ladder (`bg` → `surface` → `surface-hover` → `surface-active`), not shadows. Real box-shadows are reserved for the two genuinely floating elements: the edit panel (and its mobile slide-over) and toast notifications. Everything else — buttons, fields, the legend, search results dropdown — sits flat against its surface, differentiated by border and background tone alone.

### Shadow Vocabulary
- **panel** (`box-shadow: 0 12px 32px oklch(0 0 0 / 0.4)`): the side panel at all times, and its mobile slide-over variant; also reused by the search-results dropdown.
- **toast** (`box-shadow: 0 8px 20px oklch(0 0 0 / 0.35)`): toast notifications only.

### Named Rules
**The Float-Only Rule.** A shadow appears only on an element that visually floats above the canvas (panel, dropdown, toast). An element that sits in the normal document flow never gets one, no matter how "important" it is.

## 5. Components

Spare and functional throughout: no ornamentation, no bounce. State changes are subtle border/background/opacity shifts on a 150–220ms ease-out-expo transition, never scale-bounce or color-pulse (the one exception is the 0.98 press-scale on buttons, which reads as tactile feedback, not decoration).

### Buttons
- **Shape:** 8px radius (`--radius-md`), 1px border, 8px/16px padding.
- **Secondary (default):** `surface` background, `text` color, `border` outline — this is the default button everywhere.
- **Primary:** `accent` background, `accent-ink` (near-black) text, bold border-color match, 600 weight. Reserved for the single primary action per view ("+ New entry", "Create/Save").
- **Danger:** `surface` background, `danger`-colored text and border on hover; clicking arms a "Confirm delete?" state that fills solid `danger` for 4 seconds before reverting — no modal dialog.
- **Hover / Focus:** hover shifts to `surface-hover` + `border-strong`; active scales to 0.98 and darkens to `surface-active`; focus-visible gets a 2px `accent` outline offset 2px.
- **Disabled:** 0.5 opacity, no transform, `not-allowed` cursor.

### Fields (inputs, textareas, search box)
- **Style:** `surface` background (panel fields use `bg` to sit a level below the panel), 1px `border`, 8px radius, 8px/12px padding.
- **Focus:** border shifts to `accent`, background lifts to `surface-hover` (topbar fields) — no glow, no ring.
- **Placeholder:** `text-muted`, same weight as real content — never lighter-than-muted.

### Panel (side panel / entry form)
- **Corner Style:** square (no radius) — it's a docked instrument, not a floating card.
- **Background:** `surface`, with `panel` shadow.
- **Border:** 1px `border` on the leading edge only (`border-left`), separating it from the canvas.
- **Internal Padding:** `--space-5` (1.5rem).
- **Mobile:** becomes a fixed slide-over from the right (`translateX`), same shadow, adds a close button.

### Chips / Pills
- **Style:** `bg` background, 1px `border`, pill radius, `text-muted` text; the danger variant (superseded marker) uses `danger` text with a translucent danger border.
- **Legend chips:** same pill family but interactive — hover lifts to `surface-hover`, active/selected state fills `surface-active` with a `border-strong` outline and bold text.

### Toasts
- **Style:** `surface` background, 1px `border`, **3px solid left border in `accent`** (or `danger`/`success` per kind) as the only sanctioned use of a colored left-edge accent in the system — reserved exclusively for toast severity, never for cards or list rows.
- **Motion:** slides in from the right (16px, ease-out-expo), slides out the same way on dismiss or 4s auto-timeout.

### Navigation / Canvas Controls
- **Topbar:** floats over the canvas (`position: absolute`), instrument strip of search, tag filter, superseded checkbox, spinner, primary action — never a traditional top nav bar.
- **Legend:** floats bottom-left over the canvas, semi-transparent (`oklch(0.205 0.018 260 / 0.85)`) with `backdrop-filter: blur(6px)` — the one deliberate glass moment in the system, justified because it sits directly on top of graph content that must stay partially visible underneath.

### The Graph Canvas (signature component)
The force-directed canvas is the product's primary surface, not a decoration next to a table. Nodes are colored by entry type from a fixed 7-color palette assigned in first-seen order; edges are thin gray lines labeled with relation type when zoomed in close enough. Search/filter matches stay full-color and full-opacity; non-matches desaturate to gray at 8–35% opacity rather than disappearing, so structure stays visible even while filtered.

## 6. Do's and Don'ts

### Do:
- **Do** keep Signal Blue (`oklch(0.745 0.135 250)`) as the only saturated color in routine use — primary actions, active states, links.
- **Do** build depth with the surface ladder (`bg`/`surface`/`surface-hover`/`surface-active`) before reaching for a shadow.
- **Do** use the 3px solid left-border accent on toasts only — it is the one sanctioned "colored stripe" in the whole system.
- **Do** keep transitions in the 150–220ms range on `ease-out-expo`; no bounce, no elastic.
- **Do** respect `prefers-reduced-motion` (already collapsed to 0.01ms globally) on any new animated element.

### Don't:
- **Don't** build a generic SaaS admin template — no bootstrap-y card grids, no dashboard-in-a-box CRUD-table-with-sidebar layout, per PRODUCT.md's anti-reference.
- **Don't** add a second saturated accent color; new semantic states reuse `danger`/`success`, they don't invent a third hue.
- **Don't** put a colored `border-left`/`border-right` stripe on anything except a toast — not on cards, list rows, or callouts.
- **Don't** add a shadow to anything that isn't the panel, its dropdown, or a toast — flat surfaces stay flat.
- **Don't** introduce a warm-tinted neutral; every gray in this system is desaturated blue (hue 260), never warm.
- **Don't** use `clamp()`/fluid type or a display font — this UI has a fixed rem scale and one system-font stack, no editorial typography.
- **Don't** round the side panel's corners or float it as a card — it's a docked instrument with square corners.
