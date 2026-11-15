# Registration-blocked investigation

Updated: 2026-11-16, Asia/Taipei.

## Finding

The observed failure is a native account-creation refusal, not an OTP failure.
The latest production task actually used Chrome 152 / Windows / Japanese.
Homepage returned 200, restart 201, send-otp 201, confirm-otp 200, and the single
register-completion request returned 403 / registrationBlocked. Two exit-IP
samples agreed. Agreement does not prove that every target connection used the
same IP.

The operator corrected the initial comparison and confirmed that a browser can
register the same mailbox with the same exit IP. This prioritizes differences
in protocol inputs and session context over a blanket mailbox or exit-IP ban.
It does not isolate a particular cookie, header, input value, TLS feature or
telemetry request. The exact successful browser request from this comparison
has not been captured here.

## Established differences and limits

- Account identity UUID and SHA256 are local audit identifiers. The seed selects
  and preserves a browser profile; it is not a site-issued device credential.
  They are not sent as a device token. HTTP sessions and server-issued cookies
  are freshly acquired for each new task, not restored from the identity record.
- The supplied older successful HAR already had datr, dpr and wd cookies before
  recording began. Their original initialization is not in the capture. It also
  contains the AYMH redirect chain, ps_l/ps_n and CAA/Falco requests. The wrapper
  does not call the pinned upstream CAA/Falco collectors. This is a traffic
  difference; neither the upstream comments claiming they are optional nor
  their presence in the HAR proves whether the server uses them for acceptance.
- A fresh, read-only current homepage request set datr through HTTP Set-Cookie.
  Therefore the absence of browser JavaScript does not establish that datr is
  missing. Cookie state of the already-closed failed task was not recorded.
- The current served native request builder was examined in
  `/_next/static/immutable/chunks/0ythp9hrvnt8u.js`: restart uses resume=false;
  confirmation conditionally sends supports_muse_link_transparency; the
  completion payload contains birthday, optional names, dynamic terms version
  and the remember-device answer. The inspected request builder does not add a
  standalone Canvas/WebGL fingerprint token. Other server-side signals remain
  outside this observation.
- That frontend maps registrationBlocked to a generic creation-refusal message,
  separately from invalidName, underageRegistration, tosContentVersionMismatch,
  csrfFailed and checkpointRequired. Its error parser also reads
  onboarding_failure_code. This is not a public explanation of the server rule.
- The previous application retained only HTTP status and the top-level error
  type. Historical task records cannot recover discarded response fields or
  cookie state. Increasing the browser version alone did not resolve the refusal
  in the operator's latest test.

## Observation added

Native requests now retain bounded metadata: known payload field names, boolean
options, CSRF/waterfall header presence, known unexpired Muse cookie names in the
jar before/after each request, response field names, numeric onboarding failure
code, checkpoint-URL presence, status and registration state. Transport failures
retain only the exception class. Only five fixed Muse native paths are observed.

The task page can export this through **Export sanitized diagnostics** (Chinese
UI label) / GET `/api/tasks/{id}/diagnostics`. It excludes mailbox, names,
birthday, proxy credentials, OTP, cookie values, CSRF, response detail, unknown
fields and redirect/checkpoint URL contents. Snapshots persist with task history.
Jar names are not a capture of actual Cookie headers, nor proof of valid cookies.
Old tasks have no retroactively reconstructed snapshots.

The observer preserves request arguments, response objects, exception identity
and existing single-completion behavior. No collector, cookie injection, replay,
browser registration or automated account retry was introduced by this change.

## Next evidence

Compare a fresh successful browser HAR with a failed task's diagnostic export,
keeping email, exit IP, profile inputs, timing and cookie-history differences
explicit. An already-created mailbox must not be used as a fresh registration
control. If both responses remain generic, server-side logs or a controlled
comparison are required to identify the actual decision rule. Do not label any
traffic difference as the cause without an acceptance comparison.
