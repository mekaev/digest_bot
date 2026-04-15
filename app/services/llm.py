from typing import Any

from together import Together

from app.config import get_settings


class TogetherLLM:
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.together_api_key
        self.model = model or settings.together_model
        self._client = Together(api_key=self.api_key) if self.api_key else None

    def is_enabled(self) -> bool:
        return self._client is not None

    def generate(
        self,
        prompt: str,
        max_tokens: int = 300,
        temperature: float = 0.2,
    ) -> str:
        if not self._client:
            return 'LLM is disabled: TOGETHER_API_KEY is not set.'

        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[{'role': 'user', 'content': prompt}],
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as exc:  # noqa: BLE001
            return f'LLM request failed: {exc}'

        return _extract_text(response)


def _extract_text(response: Any) -> str:
    choices = getattr(response, 'choices', None)
    if not choices:
        return 'LLM returned an empty response.'

    message = getattr(choices[0], 'message', None)
    content = getattr(message, 'content', '') if message else ''

    if isinstance(content, str):
        text = content.strip()
        return text or 'LLM returned an empty response.'

    if isinstance(content, list):
        parts = [getattr(item, 'text', str(item)) for item in content]
        text = ' '.join(part for part in parts if part).strip()
        return text or 'LLM returned an empty response.'

    text = str(content).strip()
    return text or 'LLM returned an empty response.'
