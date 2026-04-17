from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re

from sqlalchemy.orm import Session

from app.rag.retriever import RetrievalResult, SQLiteRAGRetriever
from app.services.llm import TogetherLLM

MAX_ANSWER_SOURCES = 5
MAX_FALLBACK_DETAIL_SOURCES = 3
RAG_QA_SYSTEM_PROMPT = (
    "Ты отвечаешь как RAG-ассистент AI Telegram Digest Bot. "
    "Всегда отвечай только на русском языке. "
    "Используй только факты из переданных источников. "
    "Если данных мало, прямо скажи об этом и не додумывай детали."
)
CYRILLIC_PATTERN = re.compile(r"[А-Яа-яЁё]")


@dataclass(slots=True)
class QASource:
    index: int
    channel_name: str
    published_at: datetime
    published_at_text: str
    source_url: str
    source_label: str
    snippet: str


@dataclass(slots=True)
class QAResponse:
    question: str
    window_days: int
    answer_text: str
    sources: list[QASource]
    used_fallback: bool
    weak_evidence: bool


class QAService:
    def __init__(
        self,
        session: Session,
        retriever: SQLiteRAGRetriever | None = None,
        llm: TogetherLLM | None = None,
    ) -> None:
        self.session = session
        self.retriever = retriever or SQLiteRAGRetriever(session)
        self.llm = llm or TogetherLLM()

    def answer(
        self,
        user_id: int,
        question: str,
        window_days: int | None = None,
        limit: int = MAX_ANSWER_SOURCES,
    ) -> QAResponse:
        normalized_question = " ".join(question.split()).strip()
        retrieval = self.retriever.retrieve(
            user_id=user_id,
            question=normalized_question,
            window_days=window_days,
            limit=limit,
        )
        sources = [
            QASource(
                index=index,
                channel_name=item.channel_name,
                published_at=item.published_at,
                published_at_text=item.published_at.strftime("%Y-%m-%d %H:%M UTC"),
                source_url=item.source_url,
                source_label=item.source_label,
                snippet=item.snippet,
            )
            for index, item in enumerate(retrieval.evidence, start=1)
        ]
        if not sources:
            return QAResponse(
                question=normalized_question,
                window_days=retrieval.window_days,
                answer_text=(
                    f"Недостаточно данных по запросу «{normalized_question}». "
                    f"За последние {retrieval.window_days} дн. в ваших доступных постах и дайджестах "
                    "релевантных совпадений не найдено."
                ),
                sources=[],
                used_fallback=True,
                weak_evidence=True,
            )

        if not retrieval.weak_evidence and self._llm_is_enabled():
            llm_answer = self._answer_with_llm(normalized_question, retrieval, sources)
            if llm_answer is not None:
                return QAResponse(
                    question=normalized_question,
                    window_days=retrieval.window_days,
                    answer_text=llm_answer,
                    sources=sources,
                    used_fallback=False,
                    weak_evidence=False,
                )

        return QAResponse(
            question=normalized_question,
            window_days=retrieval.window_days,
            answer_text=self._build_fallback_answer(normalized_question, retrieval, sources),
            sources=sources,
            used_fallback=True,
            weak_evidence=retrieval.weak_evidence,
        )

    def _llm_is_enabled(self) -> bool:
        is_enabled = getattr(self.llm, "is_enabled", None)
        if callable(is_enabled):
            return bool(is_enabled())
        return False

    def _answer_with_llm(
        self,
        question: str,
        retrieval: RetrievalResult,
        sources: list[QASource],
    ) -> str | None:
        prompt = self._build_prompt(question, retrieval, sources)
        raw_answer = self.llm.generate(
            prompt,
            max_tokens=320,
            temperature=0.1,
            system_prompt=RAG_QA_SYSTEM_PROMPT,
        ).strip()
        return self._validate_llm_answer(raw_answer, source_count=len(sources))

    def _build_prompt(
        self,
        question: str,
        retrieval: RetrievalResult,
        sources: list[QASource],
    ) -> str:
        source_blocks = []
        for source in sources:
            source_blocks.append(
                "\n".join(
                    [
                        f"[{source.index}] Канал: {source.channel_name}",
                        f"[{source.index}] Дата: {source.published_at_text}",
                        f"[{source.index}] Идентификатор: {source.source_label}",
                        f"[{source.index}] Ссылка: {source.source_url or 'нет прямой ссылки'}",
                        f"[{source.index}] Фрагмент: {source.snippet}",
                    ]
                )
            )

        certainty_note = (
            "Контекст уверенный: достаточно прямых совпадений."
            if not retrieval.weak_evidence
            else "Контекст слабый: лучше честно сказать, что данных недостаточно."
        )
        rendered_sources = "\n\n".join(source_blocks)
        return (
            f"Вопрос пользователя: {question}\n"
            f"Окно поиска: последние {retrieval.window_days} дн.\n"
            f"{certainty_note}\n\n"
            "Жесткие правила ответа:\n"
            "- отвечай только на русском;\n"
            "- используй только факты из источников ниже;\n"
            "- дай 2-6 коротких предложений;\n"
            "- каждое существенное утверждение должно иметь ссылки вида [1], [2];\n"
            "- не добавляй отдельный блок 'Источники';\n"
            "- если фактов мало или они косвенные, прямо скажи, что данных недостаточно.\n\n"
            f"Источники:\n\n{rendered_sources}"
        )

    def _validate_llm_answer(self, value: str, source_count: int) -> str | None:
        if not value or value.startswith("LLM "):
            return None

        text = value.strip().strip("`")
        if "Источники" in text:
            text = re.split(r"\n\s*Источники\s*:?", text, maxsplit=1)[0].strip()
        if not text or not CYRILLIC_PATTERN.search(text):
            return None
        if _contains_cjk(text):
            return None

        cited_sources = {int(match) for match in re.findall(r"\[(\d+)\]", text)}
        if not cited_sources:
            return None
        if min(cited_sources) < 1 or max(cited_sources) > source_count:
            return None
        return " ".join(text.split())

    def _build_fallback_answer(
        self,
        question: str,
        retrieval: RetrievalResult,
        sources: list[QASource],
    ) -> str:
        distinct_channels = []
        seen_channels: set[str] = set()
        for source in sources:
            if source.channel_name in seen_channels:
                continue
            seen_channels.add(source.channel_name)
            distinct_channels.append(source.channel_name)

        sentences: list[str] = []
        if retrieval.weak_evidence:
            sentences.append(
                f"Данных за последние {retrieval.window_days} дн. недостаточно для уверенного ответа на вопрос «{question}»."
            )
            sentences.append("Ниже привожу ближайшие найденные упоминания без лишних выводов.")
        else:
            channel_list = ", ".join(distinct_channels[:3])
            sentences.append(
                f"За последние {retrieval.window_days} дн. нашёл релевантные упоминания в {len(distinct_channels)} каналах: {channel_list}."
            )

        for source in sources[:MAX_FALLBACK_DETAIL_SOURCES]:
            snippet = _summarize_snippet(source.snippet)
            sentences.append(f"{source.channel_name}: {snippet} [{source.index}].")

        return " ".join(sentences[: 2 + MAX_FALLBACK_DETAIL_SOURCES]).strip()


def _summarize_snippet(value: str, limit: int = 160) -> str:
    snippet = " ".join(value.split()).strip()
    if not snippet:
        return "подходящего фрагмента почти нет"

    sentence = re.split(r"(?<=[.!?])\s+", snippet, maxsplit=1)[0].strip()
    if not sentence:
        sentence = snippet
    if len(sentence) <= limit:
        return sentence
    return f"{sentence[: limit - 3].rstrip()}..."


def _contains_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)
