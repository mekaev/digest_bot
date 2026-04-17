from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Channel, Digest, DigestItem, Post, Subscription
from app.services.user_service import UserService

MAX_SNIPPET_LENGTH = 220
DEFAULT_TOP_K = 5
MIN_RELEVANCE_SCORE = 0.35
TOKEN_PATTERN = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+")
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "but",
    "by",
    "for",
    "from",
    "have",
    "how",
    "in",
    "into",
    "is",
    "it",
    "its",
    "latest",
    "new",
    "of",
    "on",
    "or",
    "over",
    "recent",
    "the",
    "their",
    "them",
    "there",
    "these",
    "this",
    "to",
    "was",
    "were",
    "what",
    "which",
    "who",
    "with",
    "за",
    "без",
    "было",
    "были",
    "быть",
    "в",
    "вам",
    "вас",
    "все",
    "вчера",
    "где",
    "день",
    "дня",
    "для",
    "до",
    "его",
    "ее",
    "ещё",
    "же",
    "за",
    "и",
    "из",
    "или",
    "им",
    "их",
    "как",
    "какая",
    "какие",
    "какой",
    "каком",
    "какую",
    "канал",
    "каналах",
    "канале",
    "каналы",
    "когда",
    "кто",
    "ли",
    "мне",
    "можно",
    "мой",
    "мы",
    "на",
    "над",
    "нам",
    "нас",
    "не",
    "него",
    "нее",
    "нет",
    "но",
    "новое",
    "нового",
    "новости",
    "новость",
    "о",
    "об",
    "один",
    "она",
    "они",
    "оно",
    "по",
    "под",
    "после",
    "писал",
    "писали",
    "писало",
    "про",
    "раз",
    "с",
    "со",
    "сегодня",
    "среди",
    "так",
    "там",
    "тема",
    "теме",
    "темы",
    "то",
    "только",
    "у",
    "уж",
    "что",
    "чтобы",
    "эта",
    "это",
    "эту",
    "я",
}
RUSSIAN_SUFFIXES = (
    "иями",
    "ями",
    "ами",
    "ого",
    "ему",
    "ому",
    "ее",
    "ие",
    "ые",
    "ий",
    "ый",
    "ой",
    "ое",
    "ая",
    "яя",
    "ам",
    "ям",
    "ах",
    "ях",
    "ов",
    "ев",
    "ом",
    "ем",
    "ую",
    "юю",
    "а",
    "я",
    "ы",
    "и",
    "е",
    "о",
    "у",
    "ю",
)
ENGLISH_SUFFIXES = ("ingly", "edly", "ing", "ers", "ies", "ied", "ed", "es", "s")


@dataclass(slots=True)
class RetrievedEvidence:
    post_id: int
    channel_name: str
    channel_handle: str
    published_at: datetime
    source_url: str
    source_label: str
    snippet: str
    score: float
    matched_terms: tuple[str, ...]


@dataclass(slots=True)
class RetrievalResult:
    question: str
    window_days: int
    query_terms: tuple[str, ...]
    evidence: list[RetrievedEvidence]
    weak_evidence: bool


class SQLiteRAGRetriever:
    def __init__(self, session: Session) -> None:
        self.session = session

    def retrieve(
        self,
        user_id: int,
        question: str,
        window_days: int | None = None,
        limit: int = DEFAULT_TOP_K,
    ) -> RetrievalResult:
        normalized_question = " ".join(question.split()).strip()
        resolved_window_days = self._resolve_window_days(user_id, window_days)
        query_terms = tuple(_extract_query_terms(normalized_question))
        if not normalized_question or not query_terms:
            return RetrievalResult(
                question=normalized_question,
                window_days=resolved_window_days,
                query_terms=query_terms,
                evidence=[],
                weak_evidence=True,
            )

        accessible_channels = self._load_accessible_channels(user_id)
        if not accessible_channels:
            return RetrievalResult(
                question=normalized_question,
                window_days=resolved_window_days,
                query_terms=query_terms,
                evidence=[],
                weak_evidence=True,
            )

        threshold = datetime.now(timezone.utc) - timedelta(days=resolved_window_days)
        posts = self._load_candidate_posts(accessible_channels.keys(), threshold)
        if not posts:
            return RetrievalResult(
                question=normalized_question,
                window_days=resolved_window_days,
                query_terms=query_terms,
                evidence=[],
                weak_evidence=True,
            )

        digest_hints = self._load_digest_hints(user_id, [post.id for post in posts])
        query_phrase = " ".join(query_terms)
        scored_items: list[RetrievedEvidence] = []
        for post in posts:
            channel = accessible_channels.get(post.channel_id)
            if channel is None:
                continue

            score, matched_terms = self._score_post(
                post=post,
                channel=channel,
                query_terms=query_terms,
                query_phrase=query_phrase,
                window_days=resolved_window_days,
                digest_hints=digest_hints.get(post.id, ()),
            )
            if score < MIN_RELEVANCE_SCORE:
                continue

            scored_items.append(
                RetrievedEvidence(
                    post_id=post.id,
                    channel_name=channel.title,
                    channel_handle=channel.telegram_handle,
                    published_at=_coerce_utc(post.published_at),
                    source_url=(post.source_url or "").strip(),
                    source_label=_build_source_label(channel, post),
                    snippet=_build_snippet(post.cleaned_text or post.raw_text, query_terms),
                    score=score,
                    matched_terms=matched_terms,
                )
            )

        scored_items.sort(
            key=lambda item: (item.score, item.published_at, item.post_id),
            reverse=True,
        )
        evidence = scored_items[: max(limit, 1)]
        weak_evidence = not evidence or evidence[0].score < 0.75
        return RetrievalResult(
            question=normalized_question,
            window_days=resolved_window_days,
            query_terms=query_terms,
            evidence=evidence,
            weak_evidence=weak_evidence,
        )

    def _resolve_window_days(self, user_id: int, window_days: int | None) -> int:
        if window_days in {1, 3, 7}:
            return int(window_days)
        return UserService(self.session).get_digest_window_days(user_id)

    def _load_accessible_channels(self, user_id: int) -> dict[int, Channel]:
        statement = (
            select(Channel)
            .join(Subscription, Subscription.channel_id == Channel.id)
            .where(
                Subscription.user_id == user_id,
                Subscription.enabled.is_(True),
                Channel.is_active.is_(True),
                Channel.is_user_added.is_(True),
                Channel.added_by_user_id == user_id,
            )
            .order_by(Channel.title.asc(), Channel.id.asc())
        )
        channels = list(self.session.scalars(statement))
        return {channel.id: channel for channel in channels}

    def _load_candidate_posts(self, channel_ids: list[int], threshold: datetime) -> list[Post]:
        statement = (
            select(Post)
            .where(
                Post.channel_id.in_(channel_ids),
                Post.published_at >= threshold,
            )
            .order_by(Post.published_at.desc(), Post.id.desc())
        )
        return list(self.session.scalars(statement))

    def _load_digest_hints(self, user_id: int, post_ids: list[int]) -> dict[int, list[str]]:
        if not post_ids:
            return {}

        statement = (
            select(DigestItem)
            .join(Digest, Digest.id == DigestItem.digest_id)
            .where(
                Digest.user_id == user_id,
                DigestItem.post_id.in_(post_ids),
            )
            .order_by(Digest.created_at.desc(), DigestItem.id.desc())
        )
        digest_hints: dict[int, list[str]] = defaultdict(list)
        for digest_item in self.session.scalars(statement):
            hint_text = " ".join(
                part.strip()
                for part in (digest_item.title, digest_item.summary)
                if part and part.strip()
            )
            if hint_text:
                digest_hints[digest_item.post_id].append(hint_text)
        return digest_hints

    def _score_post(
        self,
        post: Post,
        channel: Channel,
        query_terms: tuple[str, ...],
        query_phrase: str,
        window_days: int,
        digest_hints: list[str] | tuple[str, ...],
    ) -> tuple[float, tuple[str, ...]]:
        post_text = " ".join((post.cleaned_text or post.raw_text or "").split())
        channel_text = f"{channel.title} {channel.telegram_handle}"
        post_term_set = set(_extract_query_terms(post_text))
        channel_term_set = set(_extract_query_terms(channel_text))
        matched_terms = tuple(sorted(set(query_terms) & (post_term_set | channel_term_set)))

        digest_term_set: set[str] = set()
        digest_phrase_match = False
        if digest_hints:
            digest_text = " ".join(digest_hints)
            digest_term_set = set(_extract_query_terms(digest_text))
            digest_phrase_match = bool(query_phrase and query_phrase in _normalize_text(digest_text))

        if not matched_terms and not (set(query_terms) & digest_term_set):
            return 0.0, ()

        overlap_terms = set(query_terms) & post_term_set
        channel_overlap_terms = set(query_terms) & channel_term_set
        digest_overlap_terms = set(query_terms) & digest_term_set

        normalized_post_text = _normalize_text(post_text)
        phrase_bonus = 0.35 if query_phrase and query_phrase in normalized_post_text else 0.0
        if not phrase_bonus and digest_phrase_match:
            phrase_bonus = 0.15

        overlap_score = (len(overlap_terms) / len(query_terms)) * 1.6
        density_bonus = min(len(overlap_terms) * 0.12, 0.36)
        channel_bonus = min(len(channel_overlap_terms) * 0.18, 0.36)
        digest_bonus = min(len(digest_overlap_terms) * 0.1, 0.25)

        age_days = max(
            (_coerce_utc(datetime.now(timezone.utc)) - _coerce_utc(post.published_at)).total_seconds()
            / 86400.0,
            0.0,
        )
        recency_ratio = max(0.0, 1.0 - (age_days / max(window_days, 1)))
        recency_bonus = recency_ratio * 0.35

        score = overlap_score + density_bonus + phrase_bonus + channel_bonus + digest_bonus + recency_bonus
        matched = tuple(sorted(overlap_terms | channel_overlap_terms | digest_overlap_terms))
        return score, matched


SimpleRetriever = SQLiteRAGRetriever


def _extract_query_terms(text: str) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for raw_token in TOKEN_PATTERN.findall(text):
        normalized = _normalize_token(raw_token)
        if not normalized or normalized in seen:
            continue
        if normalized in STOPWORDS:
            continue
        seen.add(normalized)
        tokens.append(normalized)
    return tokens


def _normalize_text(text: str) -> str:
    normalized_tokens: list[str] = []
    for token in TOKEN_PATTERN.findall(text):
        normalized = _normalize_token(token)
        if normalized:
            normalized_tokens.append(normalized)
    return " ".join(normalized_tokens)


def _normalize_token(value: str) -> str:
    token = value.strip().lower().replace("ё", "е")
    if len(token) <= 1 and not token.isdigit():
        return ""
    if token.isdigit() and len(token) <= 1:
        return ""
    token = _strip_suffixes(token)
    if len(token) <= 1 and not token.isdigit():
        return ""
    return token


def _strip_suffixes(token: str) -> str:
    if re.search(r"[а-я]", token):
        for suffix in RUSSIAN_SUFFIXES:
            if len(token) > len(suffix) + 2 and token.endswith(suffix):
                return token[: -len(suffix)]
        return token

    if re.search(r"[a-z]", token):
        for suffix in ENGLISH_SUFFIXES:
            if len(token) > len(suffix) + 2 and token.endswith(suffix):
                if suffix == "ies":
                    return f"{token[: -len(suffix)]}y"
                return token[: -len(suffix)]
    return token


def _build_source_label(channel: Channel, post: Post) -> str:
    published_label = _coerce_utc(post.published_at).strftime("%Y-%m-%d %H:%M UTC")
    return f"{channel.title} / msg {post.telegram_message_id} / {published_label}"


def _build_snippet(text: str, query_terms: tuple[str, ...], max_length: int = MAX_SNIPPET_LENGTH) -> str:
    collapsed = " ".join((text or "").split()).strip()
    if not collapsed:
        return ""
    if len(collapsed) <= max_length:
        return collapsed

    lowercase = collapsed.lower().replace("ё", "е")
    best_index = -1
    for term in query_terms:
        index = lowercase.find(term)
        if index == -1:
            continue
        if best_index == -1 or index < best_index:
            best_index = index

    if best_index == -1:
        return f"{collapsed[: max_length - 3].rstrip()}..."

    start = max(best_index - (max_length // 4), 0)
    end = min(start + max_length, len(collapsed))
    snippet = collapsed[start:end].strip()
    if start > 0:
        snippet = f"...{snippet}"
    if end < len(collapsed):
        snippet = f"{snippet.rstrip()}..."
    return snippet


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
