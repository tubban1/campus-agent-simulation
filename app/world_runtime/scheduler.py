"""Deterministic scheduling helpers for bounded world-tick work."""

import hashlib


def bounded_agent_batch_size(base_size, eligible_count, seed):
    """Return a reproducible 25%/50%/25% batch around the configured base."""
    base_size = max(1, int(base_size or 1))
    eligible_count = max(0, int(eligible_count or 0))
    if not eligible_count:
        return 0
    variation = int.from_bytes(
        hashlib.sha256(str(seed).encode("utf-8")).digest()[:8], "big"
    ) % 4
    offset = -1 if variation == 0 else 1 if variation == 3 else 0
    return min(eligible_count, max(1, base_size + offset))
