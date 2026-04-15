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
