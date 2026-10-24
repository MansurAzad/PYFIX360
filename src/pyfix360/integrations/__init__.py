"""Knowledge catalogue of external tools, CLIs and services.

PYFIX 360 is an offline-first auditor. This package holds *knowledge* about
integrations (GitHub, Supabase, Vercel, Docker, ...): what they are, which
CLI commands exist, how they are wired into a project, and what to check.
Nothing here performs network I/O or installs anything. The data is used by:

* the "Integrations & CLIs" screen (browsing + copy-paste commands),
* the feature-completeness analyzer (detecting which integrations a project
  already uses),
* the AI assistant (as grounded context when explaining a project).

Honesty rules (same as the rest of the app):
* Commands are documented *examples*, not guarantees. CLIs change flags
  between versions; the user must verify against the installed tool.
* Nothing is run automatically. Detection is static (manifest files, env
  vars, import statements, config files) — no subprocess is spawned.
* Detection results are "detected / not detected / unknown", never a claim
  that an integration is correctly configured.
"""
