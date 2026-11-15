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

## Dynamic IP and front proxy update

User asked to mirror RegFlow's dynamic IP adaptation and front proxy, following
a screenshot showing a transport failure at HTTP session initialization.
Screenshot alone does not establish the failing proxy/protocol or root cause.
RegFlow files were read only; no integration, dependency or edits were added there.

- `app/proxies.py`: URL / four-part normalization, known SOCKS entrypoints,
  percent-encoded credentials and IPv6 URL support. Dynamic mode resolves
  `{session}` or lsid/sessid/session/sid once per task, preserving token format,
  country and TTL. Fixed mode preserves credentials.
- `app/proxy_engine.py`: independent Mihomo v1.19.29 downloader with pinned
  archive SHA256 for six platforms. Stored in this project's ignored data/bin.
  Per-task chain uses TASK dialer-proxy FRONT, localhost random authenticated
  listener and controller. HTTPS nodes keep TLS certificate verification.
  Errors never fall back to DIRECT. Task close removes its process/config.
- ProtocolClient owns one resolved route, curl Session and chain lifecycle.
  All Muse/Meta calls share that route. Environment proxies are disabled.
  OTP source keeps its own direct Session without Muse cookies.
- New optional IP checks sample api.ipify.org before starting Muse and again
  after OTP, before the completion stage. Failed or changed IP stops before
  register-completion. Equal samples do not establish continuous IP stability.
  No automatic rotation, retry, OTP resend or creation replay was added.
- POST /api/proxy/test only queries IP twice and GETs Muse homepage; no native
  restart, OTP or creation. Dynamic probe and registration have distinct session
  identities. POST /api/proxy/install downloads using optional front proxy.
  Same-origin API guard applies to both. Health reports engine installation.
- UI adds mode, front switch/URL, check switch, test/install and sanitized result.
  Batch pool accepts raw format and shares mode/front options. Proxy credentials
  are not saved in history, logs or browser storage. Init failures now explicitly
  state no OTP or account creation; completion uncertainty behavior is retained.

Verification checkpoint:

- 64 backend tests and 4 Edge UI tests passed in the final rerun. Ruff lint,
  JavaScript syntax and Git whitespace checks passed. Expanded desktop/mobile
  proxy controls have no horizontal overflow. Numeric/non-IP responses fail closed.
- Real downloaded Mihomo passed loopback HTTP -> SOCKS5, SOCKS5 -> HTTP and
  authentication rejection tests. Order/authentication/resource cleanup and no
  direct fallback were checked. These three tests skip when no engine installed;
  CI now installs the pinned engine to run them on Linux as well.
- Real read-only probe through local front http://127.0.0.1:7897 returned two
  equal exit samples and Muse homepage HTTP 200, approximately 6.6 seconds.
  No actual mailbox, OTP, account creation or invite submission was used.
- Production 8787 process has NOT been restarted by this update. An earlier
  stop/reload was rejected by automatic approval (`blocked by policy`). Do not
  retry or work around that rejected process action. A normal user restart of
  start.bat is needed to load the new backend. Isolated fixture service only
  was launched for UI tests.
- Implementation pushed to origin/main as
  `41ecdfcc08e2ba2f67a22b408851a2bf851495bc`; ls-remote confirmed the exact SHA.
  GitHub Actions run `43740127921` completed successfully on Linux, including
  pinned engine installation, loopback chain tests and Chromium UI tests:
  https://github.com/HoshinoAz/museai-register/actions/runs/43740127921
- Production health GET still returns the old capability set without
  proxy_support, confirming that a user restart is still required. Local runtime
  keys/database/engine and screenshots remain ignored; no runtime data was pushed.
