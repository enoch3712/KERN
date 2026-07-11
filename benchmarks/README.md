# KERN benchmark pilot

This directory records KERN's initial representation-compression pilot. The result is evidence about one code artifact and one rendering policy, not a general claim about complete coding-agent cost or accuracy.

## Result

The redacted input contained 188,329 characters across 3,704 lines of Python.

| Stage | Accounting method | Estimated tokens | Compression vs. source |
|---|---|---:|---:|
| Raw source | `o200k` text estimate | 36,674 | 1.00× |
| Semantic KERN IL | `o200k` text estimate | 5,795 | 6.33× |
| Dense visual pages | 32 × 32 image-patch estimate | 2,877 | **12.75×** |

The dense representation is approximately 92.2% smaller than the raw-source estimate and 50.4% smaller than the semantic-IL text estimate.

## Rendering variants

| Variant | Font | Columns | Pages | Image-token estimate | Fidelity gate |
|---|---:|---:|---:|---:|---|
| Ultra | 9 px | 5 | 2 | 2,544 | **DRIFT** |
| Dense | 10 px | 4 | 2 | 2,877 | **CLEAR** |
| Balanced | 13 px | 3 | 2 | 5,000 | **CLEAR** |
| Safe | 16 px | 2 | 4 | 7,734 | **CLEAR** |

Dense was the best observed quality/token tradeoff. Ultra misread one exact list and invented an HTTP status in a retry set, demonstrating a practical fidelity cliff. Safe cost more estimated tokens than sending the semantic IL as text.

## Fidelity check

The compression worker selected five exact facts from the source. The answer key was removed before rendering, and fresh agents were asked to retrieve:

1. The minimum supported dependency version.
2. Documented versus executed rendering defaults.
3. Cache recreation thresholds.
4. Page and worker limits.
5. Retry attempts, initial backoff, and transient status handling.

Dense and balanced passed this check; ultra did not. Because the compressor chose the facts and the sample was small, this is a retrieval smoke test—not an independent benchmark, semantic-equivalence proof, or patch-correctness result.

## Representation cost is not loop cost

The dense pages themselves were estimated at 2,877 image tokens. The fresh dense-agent run used 18,107 uncached input tokens and accumulated 73,403 input tokens across four model turns. The balanced run used 15,623 uncached input tokens and accumulated 35,591 across two turns. System instructions, tool definitions, cached prefixes, repeated turns, and resident pages dominate those totals.

KERN therefore reports representation estimates separately from complete agent-run input. Related pages should be faulted together so fixed context costs are paid fewer times.

## Method

- Credential-like literals were redacted from all derived artifacts.
- Semantic IL was model-assisted and independently checked against authoritative source.
- Text estimates used the `o200k` tokenizer family.
- Image estimates used `ceil(width / 32) × ceil(height / 32)` per page at original detail.
- Sparse final pages were cropped to 32-pixel boundaries.
- The rendered format was lossless WebP; pixel dimensions, not file byte size, determined the patch estimate.

The machine-readable record is [`results/python-pilot-v1.json`](results/python-pilot-v1.json).

The v1 record explicitly lists fields that were not captured, including exact
tool versions, page dimensions, hashes, per-question expected/actual rows,
seeds, and latency. Do not treat the `CLEAR` label as independently auditable
until a future fixture records those fields.

## Reproduction limits

The original pilot source is intentionally not published, so the exact figures cannot be reproduced byte-for-byte from this repository. This is a privacy constraint, not a reproducibility claim.

The public cache and rendering pipeline can be exercised on this repository:

```bash
python3 skills/kern/scripts/kern_cache.py --repo . scan
python3 skills/kern/scripts/kern_cache.py --repo . ensure README.md
python3 skills/kern/scripts/kern_cache.py --repo . render README.md
python3 skills/kern/scripts/kern_cache.py --repo . status
```

Results will differ with source content, compiler model, tokenizer, renderer codec, page dimensions, and agent harness. Future benchmarks should publish redistributable fixtures and task-specific verification wherever possible.
