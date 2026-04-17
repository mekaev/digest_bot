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
) -> float:
    normalized_text = text.lower().strip()
    if not normalized_text:
        return 0.0

    length_bonus = min(len(normalized_text) / 280.0, 1.5)
    recency_bonus = max(0.0, 3.0 - min(age_hours / 24.0, 3.0))
    views_bonus = min(_normalize_metric(views) / 300.0, 2.0)
    reactions_bonus = min(_normalize_metric(reactions) / 40.0, 1.5)
    forwards_bonus = min(_normalize_metric(forwards) / 20.0, 1.5)
    comments_bonus = min(_normalize_metric(comments) / 15.0, 1.0)
    return 1.0 + length_bonus + recency_bonus + views_bonus + reactions_bonus + forwards_bonus + comments_bonus


def _normalize_metric(value: int | float | None) -> float:
    try:
        normalized = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return max(normalized, 0.0)
