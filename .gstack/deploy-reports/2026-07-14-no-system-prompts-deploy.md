# LAND & DEPLOY REPORT

```text
LAND & DEPLOY REPORT
═════════════════════
PR:           N/A — production hotfix requested directly
Branch:       codex/V2
Merged:       N/A
Merge SHA:    N/A
Merge path:   direct rsync of verified src/adapters.py
First run:    no (existing deployment path)

Timing:
  Dry-run:    payload and focused regression tests passed
  CI wait:    N/A
  Queue:      0 jobs, all workers idle before deploy
  Deploy:     completed
  Staging:    skipped (direct production environment)
  Canary:     single-pass health and payload verification
  Total:      798s

Reviews:
  Eng review: focused diff and runtime path audit
  Inline fix: yes (5 payload injection sites)

CI:           SKIPPED (no PR; local focused and full test suites executed)
Deploy:       PASSED
Staging:      N/A
Verification: HEALTHY
  Scope:      BACKEND
  Console:    N/A
  Load time:  N/A
  Screenshot: none

VERDICT: DEPLOYED AND VERIFIED
```

Evidence:

- Production PostgreSQL active model list: 12 providers.
- Production batch and task snapshot prompt audit: `nonempty_prompt_paths=0`.
- Offline production payload audit: `payload_audit=ok`.
- Runtime source audit: `runtime_prompt_residuals=0`.
- Public readiness endpoint: HTTP 200, database/Redis/queue/workers all healthy, 10 main workers available.
- Rollback copy: `/opt/manufacturing-geo-audit/src/adapters.py.bak-no-system-prompts-20260714-0004`.
