from __future__ import annotations

import hashlib
import math
import re
from collections import Counter

STOP = set(
    "的 了 和 与 或 在 是 我 你 他 她 它 这 那 有 也 都 就 把 被 让 从 对 为 "
    "以及 如果 因为 所以 但是 然后 可以 一个 一些 这个 那个 什么 怎么 如何 "
    "the a an of to in on for and or is are was be as at by with".split()
)

WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{1,}|[\u4e00-\u9fff]{2,8}")


def tokens(text: str) -> list[str]:
    return [item.lower() for item in WORD_RE.findall(text or "") if item.lower() not in STOP]


def char_ngrams(text: str, n: int = 2) -> set[str]:
    compact = re.sub(r"\s+", "", text or "")
    if len(compact) < n:
        return {compact} if compact else set()
    return {compact[i : i + n] for i in range(len(compact) - n + 1)}


def jaccard(a: str, b: str) -> float:
    left, right = char_ngrams(a), char_ngrams(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def hashed_vector(text: str, dim: int = 256) -> list[float]:
    vec = [0.0] * dim
    grams = char_ngrams(text, 2) | set(tokens(text))
    for gram in grams:
        digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest, "little") % dim
        vec[index] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return float(sum(x * y for x, y in zip(a, b)))


def clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def join_turns(turns: list[dict], limit: int = 20000) -> str:
    parts = []
    for turn in turns:
        role = "我" if turn.get("role") == "user" else "助手"
        parts.append(f"{role}：{turn.get('text', '')}")
    return clip("\n".join(parts), limit)


def top_terms(text: str, k: int = 12) -> list[str]:
    counts = Counter(tokens(text))
    return [word for word, _ in counts.most_common(k)]
