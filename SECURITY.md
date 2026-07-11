# Security

KERN processes local source code and produces derived semantic caches. Treat installed plugins and skills as trusted local code.

KERN rejects pre-existing symlinks and non-canonical paths inside its managed
`.kern` cache before reading, writing, or renderer cleanup. A concurrent process
running as the same OS user and able to swap filesystem entries during a KERN
operation is outside this pathname-preflight guarantee; use normal repository
permissions and host sandboxing against that threat. Directory-FD-relative
`openat`/`unlinkat` hardening remains future work.

## Reporting a vulnerability

Please open a private GitHub security advisory for `enoch3712/KERN` rather than a public issue. Include affected versions, reproduction steps, impact, and any suggested mitigation.

Do not include real credentials or proprietary source in reports. KERN attempts to redact likely secrets from IL, but redaction is defense in depth and not a substitute for secret scanning or repository access controls.
