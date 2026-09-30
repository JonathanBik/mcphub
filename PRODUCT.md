# Product

## Register

product

## Users

Solo developer (the author), running this locally on one machine to poke at
MCP servers and the Context Search REST API during development. No other
users, no deployment, no auth boundary beyond localhost.

## Product Purpose

mcphub is a local playground: pick a configured MCP server (or the
context-search REST API), see its tools/endpoints, edit JSON args against
the tool's schema, run it, and read the result. It exists to make manual
API/tool exploration faster than hand-rolling curl each time.

## Brand Personality

Utilitarian developer tool. Terminal-adjacent, dense, fast - not polished
consumer software. Personality is "gets out of the way," not distinctive.

## Anti-references

Not a marketing surface. No landing-page patterns, no onboarding flow, no
illustration/iconography for its own sake.

## Design Principles

- Density over whitespace - this is a tool used by one technical person who
  wants maximum information per screen, not a guided experience.
- Every element earns its place by being used every session (schema, args,
  result, history, log) - no decoration.
- Fast feedback over polish: clock, log, and error states matter more than
  visual refinement.

## Accessibility & Inclusion

Baseline only: sufficient color contrast, keyboard operability (already has
Cmd/Ctrl+Enter to run). No screen-reader/WCAG-AA commitment - single sighted
technical user, localhost only.
