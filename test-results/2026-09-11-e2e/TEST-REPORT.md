# Funder Finder end-to-end test and repair report

## Outcome

- Run status: COMPLETE for this repository's bounded E2E rollout
- Tool and purpose: Funder Finder browser-to-FastAPI grant-search and PDF workflow
- Run date and report location: 2026-09-11, `test-results/2026-09-11-e2e/TEST-REPORT.md`
- Initial revision: `558893c` (`origin/main`)
- Final tested state: branch `test/e2e-harness-2026-09-11`, including the files listed below
- Environment: macOS, Python 3.12.13, FastAPI 0.115.6, Playwright 1.55.0, Chromium 140.0.7339.16
- Authorization and isolation: localhost server, synthetic proposal, deterministic provider and Anthropic boundary, no real keys or external calls
- Fix location: local branch, ready to commit and open as a pull request
- Remaining failures, blockers, or decisions: none for the deterministic E2E contract; live provider and paid model behavior remain outside PR CI by design

| Coverage | Initial PASS | Initial FAIL | Initial BLOCKED | Initial NOT RUN | Final PASS | Final FAIL | Final BLOCKED | Final NOT RUN |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| User behavior, 10 categories | 9 | 1 | 0 | 0 | 10 | 0 | 0 | 0 |
| Adversarial, 10 categories | 10 | 0 | 0 | 0 | 10 | 0 | 0 | 0 |

The first sandboxed attempt could not bind localhost and is an environment-level blocked run. The first executable run outside that sandbox produced 19 passes and one harness failure. The final full rerun produced 20 passes in 6.10 seconds.

## Scope and expectations

The harness starts the real FastAPI application through Uvicorn and drives the shipped `frontend/index.html` in Chromium. Only the paid Anthropic and external grant-provider boundary is replaced by deterministic SSE events in `tests/e2e_app.py`. The browser still submits through `/api/chat`, processes the streamed response, renders result cards, attaches PDFs through `/api/upload`, and exercises the application validation boundary.

Expected behavior comes from the README, endpoint documentation, UI copy, and stated security promise that results link to real sources and that the service degrades honestly. Synthetic inputs are used throughout. The test environment sets a clearly fake Anthropic key only because the SDK constructs a client during module import; the fixture agent prevents any model call.

Authentication, private multi-user records, durable persistence, downloads, and stateful transactions do not exist in this prototype. A01, A02, A05, A06, A07, and A08 therefore use applicable public-token, cross-origin, file-boundary, generated-link, and request-schema substitutions.

## Primary test matrix

| ID | Category | Initial | Final | Defect | Evidence |
| --- | --- | --- | --- | --- | --- |
| U01 | First use and discoverability | PASS | PASS | | Heading, input, and Send control visible in Chromium |
| U02 | Core browser-to-server workflow | PASS | PASS | | UI POSTs to `/api/chat`, consumes SSE, and renders cited fixture result |
| U03 | Input mistakes and recovery | PASS | PASS | | Empty send is inert; corrected input succeeds without reload |
| U04 | Valid boundaries and representation | PASS | PASS | | Unicode, apostrophe, emoji, and literal `$0` survive the round trip |
| U05 | Persistence and resumption | PASS | PASS | | Reload predictably clears the documented in-memory UI session |
| U06 | Editing, cancellation, and reversal | PASS | PASS | | Extractable PDF attaches and clear removes its token from the UI |
| U07 | Interrupted service and recovery | FAIL | PASS | D03 | Provider failure is visible; retry succeeds; repaired assertion selects the latest panel |
| U08 | Accessible interaction | PASS | PASS | | Keyboard Enter submits and focus returns to the composer |
| U09 | Supported environment compatibility | PASS | PASS | | Core flow passes at a 390 by 844 viewport without horizontal overflow |
| U10 | Timing and repeated actions | PASS | PASS | | Double-click sends exactly one `/api/chat` request |
| A01 | Unknown proposal token | PASS | PASS | D02 | Invented proposal token returns 404 and no analysis |
| A02 | Type-confused proposal token | PASS | PASS | D02 | Object-valued token returns 422 |
| A03 | Script and markup injection | PASS | PASS | D01 | Hostile title, source, deadline, and tool label remain text; script marker is unset |
| A04 | Interpreter injection | PASS | PASS | D02 | SQL-shaped message remains visible data through the full UI flow |
| A05 | Forged cross-origin credentials | PASS | PASS | | Public CORS preflight never grants credentialed access |
| A06 | File-boundary escape | PASS | PASS | | Traversal filename uploads in memory and creates no filesystem file |
| A07 | Unsafe generated link | PASS | PASS | D01 | `javascript:` provider URL is rendered as `#` |
| A08 | History-rule tampering | PASS | PASS | D02 | Invalid system-role history returns 422 before the agent boundary |
| A09 | Resource abuse | PASS | PASS | D02 | Oversized PDF and message both return 413 |
| A10 | Confidential error exposure | PASS | PASS | D02 | Synthetic internal exception returns a generic SSE error without the key name, secret, or traceback |

## Test evidence

Every category is implemented as a distinct `test_uNN_*` or `test_aNN_*` case in `tests/test_e2e.py`. Preconditions, inputs, and expected observations are visible in the test body. The stable command is:

```bash
pytest -q tests/test_e2e.py
```

Final output:

```text
....................                                                     [100%]
20 passed in 6.10s
```

The test server log is written to ignored `test-results/e2e/server.log`. GitHub Actions uploads that directory when CI fails.

## Defects and repairs

### D01: Provider-controlled URLs could create executable links

- Affected tests: A03, A07
- Severity and impact: medium; a compromised or malformed provider result could supply a non-HTTP link that executes in the page when clicked
- Root cause: result-card URLs were HTML-escaped but their protocol was not constrained
- Fix: `safeUrl` permits only HTTP and HTTPS; source and tool labels plus deadline values are escaped; source CSS classes use an allowlist
- Regression evidence: hostile markup in four provider-controlled fields remains literal text, the JavaScript marker stays unset, and the rendered link is `#`
- Original-state verification: identified directly in revision `558893c`; a pre-fix dynamic run was not retained because the guard and harness were introduced together
- Final status: FIXED AND VERIFIED

### D02: Chat and upload inputs lacked bounded schema enforcement

- Affected tests: A01, A02, A04, A08, A09, A10
- Severity and impact: medium; object-valued fields could raise internal errors, unbounded upload reads could consume unnecessary memory, and oversized prompt history could reach a paid provider
- Root cause: `/api/chat` assumed a JSON object with string fields, while `/api/upload` read the entire stream before checking its size
- Fix: bounded upload reads, explicit JSON-object checks, string and role validation, configurable message/history limits, and generic client-facing internal/model errors
- Regression evidence: malformed and oversized cases return stable 400, 413, 422, or 404 responses; an internal exception does not expose its synthetic secret; valid and hostile-looking text still completes the legitimate workflow
- Original-state verification: identified directly in revision `558893c`; a pre-fix dynamic run was not retained because the guard and harness were introduced together
- Final status: FIXED AND VERIFIED

### D03: Retry test used a strict locator that matched two result panels

- Affected test: U07
- Severity and impact: harness-only; the first executable run reported one false failure after the application successfully retried
- Root cause: the shared result helper selected every `.agent.reasoning` panel after two submissions
- Fix: assert against the latest panel while retaining the unique result-link check
- Regression evidence: U07 and the full 20-category rerun pass
- Final status: FIXED AND VERIFIED

## Final verification and handoff

- All 20 final category reruns passed against the final local code state.
- `python -m compileall -q backend tests` passed.
- The repository had no pre-existing automated test, build, or lint command to run.
- The workflow uses Python 3.12, installs pinned test dependencies, installs Chromium, compiles the code, and runs the same suite on pull requests and `main`.
- No real credentials, production services, user records, or external provider traffic were used.
- The local virtual environment and failure logs are ignored; the fixture code and report are retained intentionally.
- Live Grants.gov, Kindora, Tavily, Anthropic, Render, and Wayan.com checks are not PR-blocking because they are external, unstable, or metered. Existing manual live verification remains documented in the README.

This evidence establishes the deterministic browser-to-server release contract. It does not establish that every external provider is continuously available or that a paid model will always return relevant results.
