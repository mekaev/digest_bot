from app.digest.ranking import rank_messages


class DigestSummarizer:
    def build_digest(self, messages: list[str], max_items: int = 5) -> str:
        ranked = rank_messages(messages)
        selected = [text for text, _score in ranked[:max_items]]

        if not selected:
            return 'No fresh messages for digest.'

        lines = [f'{index}. {item}' for index, item in enumerate(selected, start=1)]
        return '\n'.join(lines)
