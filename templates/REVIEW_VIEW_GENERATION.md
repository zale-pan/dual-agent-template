# Deterministic human-review views

Machine facts are edited once. The Task Steward owns `PROJECT_GOALS.json`,
`DEVELOPMENT_STATUS.json`, and `COMPONENT_REGISTRY.json`; a renderer produces the Markdown
views mapped in `review-views.json`. Views are not independent fact sources. The project
brief, consultations, architecture decisions, task contracts, instructions, and verification
narratives remain authored documents, not renderer output.

## Running the renderer
From the target project root run python -B .dual-agent/tools.py review_views --write to create the three mapped files. Run with --check for read-only comparison. Output bytes are deterministic and include hashes of the source JSON. The Steward owns writes. Generate views only for projects consuming them.
