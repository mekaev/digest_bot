from dataclasses import dataclass
import math
import re


TOPIC_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "for",
    "from",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "this",
    "to",
    "was",
    "were",
    "with",
    "будет",
    "было",
    "быть",
    "для",
    "его",
    "если",
    "еще",
    "или",
    "как",
    "когда",
    "который",
    "между",
    "над",
    "нам",
    "них",
    "но",
    "об",
    "они",
    "она",
    "оно",
    "от",
    "по",
    "под",
    "при",
    "про",
    "так",
    "также",
    "только",
    "уже",
    "что",
    "это",
}

TOKEN_PATTERN = re.compile(r"[a-zа-яё0-9]+(?:\.[0-9]+)?", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ChannelMetricBaseline:
    avg_views: float = 0.0
    avg_reactions: float = 0.0
    avg_forwards: float = 0.0
    avg_comments: float = 0.0


def rank_messages(messages: list[str], keywords: list[str] | None = None) -> list[tuple[str, float]]:
    if not messages:
        return []

    normalized_keywords = [word.lower() for word in (keywords or [])]
    scored_items: list[tuple[str, float]] = []

    for text in messages:
        lower_text = text.lower()
        score = 1.0
        if normalized_keywords:
            score += float(sum(lower_text.count(word) for word in normalized_keywords))
        scored_items.append((text, score))

    scored_items.sort(key=lambda item: item[1], reverse=True)
    return scored_items


def score_post_text(
    text: str,
    age_hours: float,
    views: int = 0,
    reactions: int = 0,
    forwards: int = 0,
    comments: int = 0,
    baseline: ChannelMetricBaseline | None = None,
) -> float:
    normalized_text = text.lower().strip()
    if not normalized_text:
        return 0.0

    topic_tokens = extract_topic_tokens(normalized_text)
    text_density_bonus = min(len(normalized_text) / 360.0, 0.9)
    informative_bonus = min(len(topic_tokens) / 14.0, 0.9)
    recency_bonus = max(0.0, 2.8 - min(age_hours / 18.0, 2.8))
    baseline = baseline or ChannelMetricBaseline()

    relative_bonus = (
        _relative_metric_bonus(views, baseline.avg_views, weight=1.4, cap=1.8)
        + _relative_metric_bonus(reactions, baseline.avg_reactions, weight=1.2, cap=1.6)
        + _relative_metric_bonus(forwards, baseline.avg_forwards, weight=1.1, cap=1.4)
        + _relative_metric_bonus(comments, baseline.avg_comments, weight=1.0, cap=1.2)
    )
    absolute_bonus = (
        min(math.log1p(_normalize_metric(views)) / 8.0, 0.45)
        + min(math.log1p(_normalize_metric(reactions)) / 5.0, 0.35)
        + min(math.log1p(_normalize_metric(forwards)) / 4.0, 0.3)
        + min(math.log1p(_normalize_metric(comments)) / 4.0, 0.25)
    )
    return 1.0 + text_density_bonus + informative_bonus + recency_bonus + relative_bonus + absolute_bonus


def extract_topic_tokens(text: str, limit: int = 16) -> tuple[str, ...]:
    tokens: list[str] = []
    for raw_token in TOKEN_PATTERN.findall(text.lower()):
        token = raw_token.strip(".")
        if not token:
            continue
        if token in TOPIC_STOPWORDS:
            continue
        if len(token) < 3 and not any(char.isdigit() for char in token):
            continue
        tokens.append(token)

    deduplicated: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        deduplicated.append(token)
        if len(deduplicated) >= limit:
            break
    return tuple(deduplicated)


def posts_are_similar(left_text: str, right_text: str) -> bool:
    left_tokens = set(extract_topic_tokens(left_text))
    right_tokens = set(extract_topic_tokens(right_text))
    if not left_tokens or not right_tokens:
        return False

    shared_tokens = left_tokens & right_tokens
    if len(shared_tokens) < 3:
        return False

    union_size = len(left_tokens | right_tokens)
    overlap_ratio = len(shared_tokens) / union_size if union_size else 0.0
    containment_ratio = len(shared_tokens) / min(len(left_tokens), len(right_tokens))
    return overlap_ratio >= 0.5 or containment_ratio >= 0.75


def _relative_metric_bonus(value: int, baseline_value: float, weight: float, cap: float) -> float:
    normalized_value = _normalize_metric(value)
    normalized_baseline = max(_normalize_metric(baseline_value), 1.0)
    if normalized_value <= 0.0:
        return 0.0

    ratio = normalized_value / normalized_baseline
    return min(math.log1p(ratio) * weight, cap)


def _normalize_metric(value: int | float | None) -> float:
    try:
        normalized = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return max(normalized, 0.0)
