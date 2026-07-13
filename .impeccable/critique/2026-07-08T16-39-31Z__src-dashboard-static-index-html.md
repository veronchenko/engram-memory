---
target: src/dashboard/static/index.html
total_score: 30
p0_count: 0
p1_count: 2
timestamp: 2026-07-08T16-39-31Z
slug: src-dashboard-static-index-html
---
Method: dual-agent (A: a3e6e85ae63ccc1f7 · B: a1dccb76b31ed38db)

Note: no browser automation tool was exposed in this session, so both assessments are code-level (static CSS/JS analysis + deterministic scan) rather than rendered-browser verified. The dashboard is confirmed running at `http://localhost:8193/` for a manual look if you want one.

## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 4 | Spinner, search status, toasts, selected-node ring — every action confirms. |
| 2 | Match System / Real World | 4 | Direct language throughout ("Click a node", "Confirm delete?"); no jargon. |
| 3 | User Control and Freedom | 3 | Pan/zoom/drag, search clear, Escape-to-close on mobile all present; Escape doesn't close the desktop panel (minor gap). |
| 4 | Consistency and Standards | 4 | Single `.field`/button vocabulary reused everywhere; 100% match to DESIGN.md tokens, no drift found. |
| 5 | Error Prevention | 3 | Delete requires a second click + 4s revert window (strong); but the entry form has no required-field validation before save. |
| 6 | Recognition Rather Than Recall | 2 | Legend and placeholders help, but tag filter has no autocomplete — users must recall exact tag spelling; icon-only "×" buttons have no text label. |
| 7 | Flexibility and Efficiency | 3 | Keyboard nav in search results (arrows/Enter), visible focus rings; no shortcut for "+ New entry" or Save, no bulk actions. |
| 8 | Aesthetic and Minimalist Design | 4 | Nothing decorative; every element earns its space; matches the "Control Room" north star. |
| 9 | Error Recovery | 2 | Toasts fire but say only "Save failed" / "Search failed" — no root cause or next step. |
| 10 | Help and Documentation | 1 | One empty-state hint ("Click a node...") is the entire onboarding; no explanation of entry types, graph physics, or search scoring. |
| **Total** | | **30/40** | **Good** |

## Anti-Patterns Verdict

**LLM assessment**: Not AI slop, and not generic-SaaS-admin either — this passes the Product register's harder bar. The graph is the primary surface rather than a decoration next to a CRUD table, the topbar reads as an instrument strip rather than a nav bar, and the desaturate-don't-hide filtering behavior is a level of craft templates don't produce. It reads as deliberately designed, matching DESIGN.md's own "Control Room" spec closely.

**Deterministic scan**: 2 findings (exit code 2), both worth a second look rather than a straight fix:
- `side-tab` at `index.html:239` — the toast's `border-left: 3px solid var(--accent)`. This is the generic absolute-ban catching a real pattern, but DESIGN.md explicitly carves out this exact case as "the one sanctioned use of a colored left-edge accent in the system — reserved exclusively for toast severity." Treat this as an accepted, documented exception rather than a bug — but it's worth a gut check: is a left-border toast actually earning its place, or is it "AI grammar" applied to your own spec? Your call, not a forced fix.
- `flat-type-hierarchy` at `index.html:33` — the 6-step 1.125-ratio type scale. The generic rule wants ≥1.25 step contrast; Product register guidance explicitly recommends 1.125–1.2 for product UI ("more type elements here than on brand surfaces; exaggerated contrast creates noise"). This is a false positive against your own register rules — no action needed.

**Visual overlays**: not available — no browser automation tool was exposed in this session, so no live-injected overlay was produced. Detector findings above are text/line-number based only.

## Overall Impression

This is a well-executed, restrained product UI that lives up to its own DESIGN.md almost to the letter — the biggest gap isn't visual craft, it's that a new operator has almost no way to learn how the tool works (type meanings, graph physics, search scoring) beyond trial and error, and the control-dense topbar will crowd on mobile. Both are fixable without touching the visual language at all.

## What's Working

- **The graph is the interface, not a decoration.** Canvas occupies the dominant share of the viewport; clicking a node opens its form in place. This is the single hardest thing to get right in a "graph + form" tool and it's done well here.
- **Desaturate-don't-hide filtering.** Non-matching nodes drop to 8–35% opacity instead of disappearing (`index.html:657-662`), so graph structure stays legible while filtered — most admin tools just hide non-matches and lose spatial context. Rare and deliberate.
- **Delete confirmation without a modal.** Button text/color arms on first click, auto-reverts after 4s — respects the "no persuasion, just precision" principle from PRODUCT.md without resorting to a blocking dialog.

## Priority Issues

**[P1] No onboarding or in-context help**
- **Why it matters:** A first-time operator sees an unlabeled force-directed graph with colored dots and no legend key beyond type names. There's zero explanation of what a "hub" vs. "decision" vs. "pattern" type means, how search scoring works, or that node positions can be dragged. PRODUCT.md's success metric — "answer 'what do I already know about X' in seconds" — assumes fluency the UI never teaches.
- **Fix:** Add a lightweight `?`/info affordance near the legend (tooltip or small popover) explaining entry types and graph interactions; no walkthrough or forced tour needed, just discoverable context.
- **Suggested command:** `/impeccable onboard`

**[P1] Topbar carries 5 controls in one row**
- **Why it matters:** Search, tag filter, superseded checkbox, spinner, and "+ New entry" all sit in one flex row (`index.html:275-289`). Nielsen's cognitive-load guidance caps a group at ≤4; at ≤720px the row wraps onto multiple lines and eats canvas space exactly where mobile users need it most.
- **Fix:** Move the superseded checkbox behind a small filter/settings affordance, or group search+filter visually distinct from the primary action so the row reads as 2 clusters instead of 5 peers.
- **Suggested command:** `/impeccable layout`

**[P2] Graph canvas has no accessible fallback**
- **Why it matters:** The `<canvas>` (`index.html:290`) carries one static aria-label ("Knowledge graph. Click a node to view its entry.") but a screen-reader user cannot perceive node positions, labels, or relations — search is their only path in, and they lose the exploratory browsing that's the tool's main value proposition. PRODUCT.md explicitly opted out of a formal WCAG target, so this is a known, accepted trade-off rather than an oversight — flagging it so it stays a deliberate choice, not a forgotten one.
- **Fix:** If accessibility is ever prioritized, a keyboard-navigable list/tree view of the same graph data (already available via `/api/graph`) would give screen-reader users parity without touching the canvas.
- **Suggested command:** `/impeccable audit`

**[P2] Delete-confirm state change is visual-only**
- **Why it matters:** The button flips to "Confirm delete?" with a red fill (`index.html:830-841`) but nothing announces the change to a screen reader, and on a quick glance a sighted user might not register the color shift either — for the single most destructive action in the tool, the signal is easy to miss.
- **Fix:** Add an `aria-live` announcement on arm, and consider a countdown affordance (e.g., a shrinking underline) so the reversal window is visible, not just timed.
- **Suggested command:** `/impeccable harden`

**[P3] Error toasts are generic**
- **Why it matters:** "Search failed", "Save failed", "Entry not found" (`index.html:404, 468, 779`) name the failure but never the cause — was it a network error, a validation failure, a 500? Users can't self-diagnose.
- **Fix:** Surface the backend's actual error detail in the toast body where the API already returns one (e.g. `result.detail` on save is already available but not always distinguished from a network failure).
- **Suggested command:** `/impeccable clarify`

## Persona Red Flags

**Alex (Power User):**
- No global search hotkey (Cmd/Ctrl+K) — search is reachable only by clicking into the field, unlike the Linear/Raycast-class tools this audience compares against.
- Delete requires a click, a wait-aware second click, and a 4s window — deliberate friction that a power user doing cleanup work will find slow with no faster path.
- No bulk operations — every tag/type edit is a single-entry round trip through the form.

**Sam (Accessibility-dependent):**
- The canvas is the primary way to browse the graph, and it's opaque to a screen reader beyond one static label — Sam is functionally limited to typed search, losing the tool's core "explore connections visually" value.
- Node labels render at full opacity only on hover (`index.html:675-677`); a screen reader user has no hover, so labels are only reachable by clicking a node and reading the opened form.
- The delete-confirm state change (see P2 above) is silent to assistive tech.

## Minor Observations

- The z-index scale is genuinely well-organized (`--z-topbar`/`--z-legend`: 10, `--z-dropdown`: 15, `--z-panel-mobile`: 20, `--z-toast`: 40) — a real semantic scale, not arbitrary 999s.
- Escape closes the search dropdown and the mobile panel, but not the desktop panel — minor inconsistency in an otherwise tight keyboard model.
- The 7-color node palette wraps by index modulo (`index.html:336`) — an 8th entry type reuses the first color, which could read as ambiguous once the graph grows.
- Toasts auto-dismiss after 4s with no pause-on-hover — a longer message can disappear mid-read.
- The entry form (title/type/tags/resource/content + relations) has no sticky save action, so on long content or small viewports the Save button can scroll out of view.
- DESIGN.md compliance is otherwise exact: shadow placement (panel/dropdown/toast only), the one-accent rule, the fixed rem scale, and motion timings all match the spec with no drift.

## Questions to Consider

- Is the graph-first interface actually faster than a Cmd+K-style search-first flow for "what do I know about X", or does it only pay off once the operator already knows the graph's shape?
- Canvas accessibility is a known, accepted gap per PRODUCT.md — does that stay true if the dashboard is ever opened to other operators beyond the maintainer, per the "potentially other operators" language in PRODUCT.md's Users section?
- Should entry-type meanings (hub, feature, decision, pattern, diagnostic, procedure, snippet) live in the dashboard itself, or is documenting the taxonomy the curator's responsibility elsewhere?
