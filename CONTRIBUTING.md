# Contributing

Thanks for helping improve KERN.

## Before opening a pull request

1. Keep source authoritative and treat `.kern/` as disposable output.
2. Preserve hash checks and the exact-source write gate.
3. Do not add benchmark answer keys, credentials, or proprietary source artifacts.
4. Keep host-specific model identifiers out of the canonical skill workflow.
5. Add or update tests when changing cache behavior or rendering.

Run:

```bash
npm ci
npm run build
python3 -m py_compile skills/kern/scripts/kern_cache.py skills/kern/scripts/render_ir.py
python3 skills/kern/scripts/kern_cache.py --repo . scan
```

Use focused commits and explain behavioral, compatibility, or benchmark implications in the pull request.
