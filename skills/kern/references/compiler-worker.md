# Compiler worker contract (enrichment)

The deterministic IL is authoritative and already committed. Your job is ONLY to
append intent summaries. Output the baseline IL verbatim, then:

    ENRICHMENT model=<your-model-name>
    INTENT <qualified-symbol>: <one-line summary of purpose>

Rules:
- Never modify, reorder, or omit any deterministic line. The commit will be
  rejected if the baseline is not a byte-exact prefix of your output.
- Only `INTENT` lines may follow the `ENRICHMENT` header.
- One INTENT line per symbol, at most; skip symbols whose purpose is obvious.
- Never include secrets, credentials, or long literals.
