"""Token counting backed by a real tokenizer.

Primary backend is tiktoken/cl100k_base. It is an OpenAI BPE encoder, used here
as a stable, reproducible proxy for byte-pair token counts. Absolute counts vary
a few percent across model families, but the *ratios* this benchmark reports are
near-invariant to the choice of BPE tokenizer because both stacks are measured
with the identical encoder on the identical text.
"""
import json

try:
    import tiktoken
    _ENC = tiktoken.get_encoding("cl100k_base")
    BACKEND = "tiktoken/cl100k_base"
except Exception:  # pragma: no cover - fallback only if tiktoken missing
    _ENC = None
    BACKEND = "heuristic/chars-over-4"


def count_text(text: str) -> int:
    """Token count of a raw string."""
    if _ENC is not None:
        return len(_ENC.encode(text))
    return max(1, round(len(text) / 4))


def count_json(obj) -> int:
    """Token count of an object serialized as compact JSON (wire form).

    Uses compact separators and ensure_ascii=False so multibyte content is
    counted as it would actually travel over the wire, not as \\uXXXX escapes.
    """
    return count_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
