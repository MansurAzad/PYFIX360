# PYFIX 360 Documentation

| Document | Purpose |
|---|---|
| [`../README.md`](../README.md) | Overview, installation, quick start, the Settings Center, quality gates, themes/accessibility, packaging, architecture, supported-language matrix, built-in rules, safety model. |
| [`OPERATING.md`](OPERATING.md) | Day-to-day usage: import, scan, findings, repair, rollback, testing, features, gates, reports, AI providers and models, platform playbooks and operating an app builder, autopilot and the Enter gestures, the background host and headless service modes, settings, troubleshooting. |
| [`DATA_MODEL.md`](DATA_MODEL.md) | Persisted schema (20 tables), entity map, finding/repair lifecycle, stable keys, incremental scanning, quality gates, audit trail and AI request metadata. |
| [`AI.md`](AI.md) | Providers, the free/low-cost model catalogue, live discovery, and the exact privacy guarantees. |
| [`PACKAGING.md`](PACKAGING.md) | Building the `.exe`, the icon, and the installer; what the bundle contains and what it deliberately omits. |

## Reading order

- **New user** → README (installation + quick start) → OPERATING.md.
- **Evaluating safety/coverage** → README sections *Core principles*,
  *Supported languages — an honest matrix*, *Safety model and known limitations*.
- **Integrating or extending** → README *Architecture* → DATA_MODEL.md.

## Non-negotiables encoded across these documents

1. Offline-first: local analysis never requires the network.
2. No silent success: unavailable tools are reported; scans become `PARTIAL`.
3. Evidence over assertion: every finding has file/line/snippet; every repair has a diff.
4. Never label an untested patch as verified.
5. Never overwrite the original project without a recoverable snapshot.
6. AI is optional, privacy-gated, redacted before send, and never the verification authority.
