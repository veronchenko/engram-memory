# Product

## Register

product

## Users

Anyone self-hosting Engram (the persistent-memory MCP server) — currently the maintainer, potentially other operators who run their own instance via Docker. They open the dashboard when the MCP tools (`remember`/`recall`/`search`) aren't enough: to visually explore how entries connect, spot orphaned or duplicate knowledge, and hand-edit or delete entries directly. It's a debugging/maintenance surface for a personal knowledge base, not a consumer product — used in short, focused sessions, not continuously.

## Product Purpose

Give the person running Engram a direct window into their knowledge graph: a force-directed canvas view of all entries and their `kb://` relations, full-text/semantic search over the same index the MCP tools use, and a CRUD panel to create, edit, supersede, or delete entries without touching Markdown files by hand. Success is being able to answer "what do I already know about X, and how does it connect to Y" in seconds, and to fix a bad entry without a text editor.

## Brand Personality

Technical and minimal — every pixel earns its place. Closer to Linear's density or a Postgres admin tool than a consumer app: dark by default, monospace-adjacent restraint, no decorative chrome, no onboarding flourishes. Confidence comes from precision (tight type scale, clean graph physics, instant search feedback), not from friendliness or persuasion copy.

## Anti-references

No generic SaaS admin template feel — no bootstrap-y card grids, no dashboard-in-a-box CRUD-table-with-sidebar layout. Nothing that looks like it's trying to look impressive; this is a tool, not a pitch.

## Design Principles

1. **Function over flourish** — every element must justify its screen space; when in doubt, cut it.
2. **The graph is the interface** — the canvas is the primary surface, not a decoration next to a table; panels and controls stay out of its way.
3. **Density with clarity** — pack information tightly (small type scale, compact controls) but never at the cost of legibility or hit-target size.
4. **Fast feedback** — search, hover, and save states respond immediately; no dead air while data is fetched.
5. **No persuasion, just precision** — no marketing tone, no empty states selling a feature; copy is instructional and terse.

## Accessibility & Inclusion

No formal WCAG target. Keep the existing baseline: `prefers-reduced-motion` respected throughout, keyboard navigation on search/list/panel controls, focus-visible outlines on all interactive elements, sufficient contrast against the dark theme's near-white text tokens.
