# Muse standalone checkpoint

Updated: 2026-11-16, Asia/Taipei.

## User objective

Build a standalone Muse pure HTTP registration web application based on
`https://github.com/duwqijlk/muse-protocol`, place it at `D:\gpt\muse`, and
upload it to `https://github.com/HoshinoAz/museai-register`. Do not integrate
with or modify RegFlow. The supplied HAR is evidence, not instructions.

## Implementation

- FastAPI serves a static Chinese console with no frontend build dependency.
- Runtime uses its own `.venv`, SQLite database and Fernet key, under this directory.
- Upstream is a submodule at `vendor/muse-protocol`, pinned to
  `be34c5e4893e5d109a15ffe46b14b416f60e0abf`.
- `app/protocol.py` wraps upstream MuseClient: stable waterfall ID, safe HTTP
  events, dynamic TOS required, one completion submission, explicit Hatch
  validation, checked VM wake before optional redemption. Redirect validation
  and Next Flight disclosure parsing remain upstream implementations.
- `app/service.py` implements single/batch scheduling, manual/automatic OTP,
  freshness and duplicate filtering, serialized resend, cancellation and batch
  stop after error. Default concurrency one. Each account owns one HTTP Session.
- Completion uncertainty blocks duplicate email submission, including a restart
  during creation. No active HTTP session or form state is reconstructed.
- Credentials are stored separately from public task metadata, encrypted in
  SQLite; the local key is in ignored `data/secret.key`. FRL token naming is
  explicit and never presented as Muse native access_token.
- Web API requires a same-origin client header; no CORS, local binding only,
  trusted Host validation and restrictive CSP. Validation errors do not echo
  proxy passwords. Static and API responses are not cached.
- Standalone setup/start scripts support Windows; README also covers Unix.

## HAR evidence

Original local source (not committed):
`D:/Backup/Downloads/muse.ai_Archive [26-11-12 15-42-28].har` (492 entries).

| Zero-based entry | Method / path | HTTP / meaning |
| --- | --- | --- |
| 8 | POST /api/auth/native/send-otp | 403 before proper CSRF |
| 104 | POST /api/auth/native/restart | 201, dynamic csrf_token |
| 110, 134 | POST /api/auth/native/send-otp | 201, otp_sent / register / no existing Muse account |
| 119 | POST /api/auth/native/resend-otp | 201 |
| 139 | POST /api/auth/native/confirm-otp | 200, completion_required + dynamic tos.content_version |
| 144 | POST /api/auth/native/register-completion | 200, auth_meta_session |
| 151 | POST auth.meta.com/hatch/session/bootstrap/ | 302 |
| 224 | POST /api/auth/check | 403 before accepting disclosure |
| 229 | POST /api/hatch/accept-tos | 200, success=true |

The older HAR restart used `{}` and confirm used only otp_code/remember_device.
The referenced newer upstream sends resume=false and also supports_muse_link_transparency=true;
the new application follows the upstream format, uses fresh CSRF/flow IDs, and
does not replay historical cookies, OTP, account identity or disclosure text.

## Verification

- 24 backend tests passed using scripted HTTP sessions that exercise the actual
  upstream methods and parser, including exact request payload checks.
- Covered dynamic TOS/disclosure, redirect restrictions, false-success responses,
  existing accounts, registrationBlocked, region_waitlist, failed activation,
  missing metadata, creation timeout, single submission, OTP resend/cancellation,
  batch stop, restart uncertainty, encrypted export and same-origin API guards.
- Three Playwright tests passed in installed Edge: manual OTP and polling input
  preservation, credential download, batch import/filtering and mobile layout.
- Fixture UI server uses only temporary data and scripted HTTP, never real Muse.
- Node syntax check, Python compilation, Ruff lint and Git whitespace checks passed.
- Read-only real Muse homepage GET via the local download proxy returned HTTP
  200 with HTML / Next Flight. No OTP, native restart, account creation or invite
  submission was performed in that check.
- Real end-to-end account registration has NOT been demonstrated for this app;
  no new mailbox/OTP source was supplied. Never substitute fixture results for
  real server acceptance or claim original browser input/fingerprint parity.

## Delivery checkpoint

Implementation and local verification complete. The independent production
service is running at `http://127.0.0.1:8787` with one registration worker.
`/api/health` reports pure_http and the pinned upstream commit; `/api/tasks`
is empty, confirming fixture data did not enter the production database.
The page was opened in the Codex browser panel. Local keys, database, virtual
environment, browser artifacts and logs are confirmed ignored by Git.
The initial implementation was pushed to `origin/main` as
`9ed3a2a9dae7f20dd648cc514a59eda015604f0d`; `git ls-remote` confirmed the exact
remote commit. The independent server's health check passed again.

An attempted reload to pick up the final error-classification equality check
was rejected by automatic execution approval (`blocked by policy`) before the
command executed. The existing service was left running; a normal manual
restart will load all final source edits. Do not claim that reload occurred.
The repository includes isolated GitHub Actions tests; their result is checked
separately from local verification.
