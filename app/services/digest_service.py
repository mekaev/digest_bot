from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Channel, Digest, DigestItem, Post, Subscription
from app.digest.ranking import ChannelMetricBaseline, extract_topic_tokens, score_post_text
from app.services.llm import TogetherLLM
from app.services.user_service import UserService

DEFAULT_DIGEST_MAX_ITEMS = 5
MAX_DIGEST_SUMMARY_LENGTH = 220
MAX_DIGEST_SNIPPET_LENGTH = 420
DIGEST_SYSTEM_PROMPT = (
    "Ты редактор AI Telegram Digest Bot. "
    "Всегда отвечай только на русском языке. "
    "Никогда не используй китайский язык и не вставляй китайские иероглифы. "
    "Не смешивай языки, кроме названий продуктов, брендов и оригинальных терминов. "
    "Сохраняй формат компактным, стабильным и пригодным для чтения в Telegram."
)


@dataclass(slots=True)
class DigestGenerationResult:
    digest: Digest | None
    message_text: str
    has_content: bool


class DigestService:
    def __init__(self, session: Session, llm: TogetherLLM | None = None) -> None:
        self.session = session
        self.llm = llm or TogetherLLM()

    def list_digests_for_user(self, user_id: int, limit: int | None = None) -> list[Digest]:
        statement = (
            select(Digest)
            .where(Digest.user_id == user_id)
            .order_by(Digest.created_at.desc(), Digest.id.desc())
        )
        if limit is not None:
            statement = statement.limit(limit)
        return list(self.session.scalars(statement))

    def generate_digest_for_user(
        self,
        user_id: int,
        max_items: int = DEFAULT_DIGEST_MAX_ITEMS,
        since_hours: int | None = None,
    ) -> DigestGenerationResult:
        window_days = self._resolve_window_days(user_id, since_hours)
        subscribed_channels = list(
            self.session.scalars(
                select(Channel)
                .join(Subscription, Subscription.channel_id == Channel.id)
                .where(
                    Subscription.user_id == user_id,
                    Subscription.enabled.is_(True),
                    Channel.is_user_added.is_(True),
                )
                .order_by(Channel.title.asc())
            )
        )
        if not subscribed_channels:
            return DigestGenerationResult(
                digest=None,
                message_text="No enabled channels yet. Use /addchannel to add a public source.",
                has_content=False,
            )

        channel_ids = [channel.id for channel in subscribed_channels]
        threshold = datetime.now(timezone.utc) - timedelta(days=window_days)
        posts = list(
            self.session.scalars(
                select(Post)
                .where(Post.channel_id.in_(channel_ids), Post.published_at >= threshold)
                .order_by(Post.published_at.desc())
            )
        )
        ranked_posts = self._rank_posts(posts)
        if not ranked_posts:
            return DigestGenerationResult(
                digest=None,
                message_text=f"No posts found for the last {window_days} day(s) in your enabled channels.",
                has_content=False,
            )

        selected_items = self._select_digest_items(ranked_posts, max_items=max_items)
        digest = Digest(
            user_id=user_id,
            status="ready",
            delivery_status="pending",
            body_text="",
            source_post_count=len(selected_items),
        )
        self.session.add(digest)
        self.session.flush()

        for post, score in selected_items:
            channel = next(channel for channel in subscribed_channels if channel.id == post.channel_id)
            digest_item = DigestItem(
                digest_id=digest.id,
                post_id=post.id,
                channel_title=channel.title,
                title=self._build_title(post.cleaned_text),
                summary=self._build_item_summary(post.cleaned_text),
                source_url=post.source_url,
                score=score,
                published_at=post.published_at,
            )
            self.session.add(digest_item)

        body_text = self._build_digest_text(selected_items, subscribed_channels)
        digest.body_text = body_text
        self.session.commit()
        self.session.refresh(digest)

        return DigestGenerationResult(
            digest=digest,
            message_text=body_text,
            has_content=True,
        )

    def mark_delivered(self, digest_id: int) -> None:
        digest = self.session.get(Digest, digest_id)
        if digest is None:
            return

        digest.delivery_status = "sent"
        digest.delivered_at = datetime.now(timezone.utc)
        self.session.commit()

    def _rank_posts(self, posts: list[Post]) -> list[tuple[Post, float]]:
        unique_posts: list[Post] = []
        seen_texts: set[str] = set()
        for post in posts:
            normalized_text = " ".join(post.cleaned_text.lower().split())
            if not normalized_text or normalized_text in seen_texts:
                continue
            seen_texts.add(normalized_text)
            unique_posts.append(post)

        baselines = self._build_channel_baselines(unique_posts)
        now = datetime.now(timezone.utc)
        scored: list[tuple[Post, float]] = []
        for post in unique_posts:
            published_at = _coerce_utc(post.published_at)
            age_hours = max((now - published_at).total_seconds() / 3600.0, 0.0)
            score = score_post_text(
                post.cleaned_text,
                age_hours=age_hours,
                views=post.views_count,
                reactions=post.reactions_count,
                forwards=post.forwards_count,
                comments=post.comments_count,
                baseline=baselines.get(post.channel_id),
            )
            scored.append((post, score))

        scored.sort(key=lambda item: (item[1], item[0].published_at), reverse=True)
        return self._deduplicate_topics(scored)

    def _select_digest_items(
        self,
        ranked_posts: list[tuple[Post, float]],
        max_items: int,
    ) -> list[tuple[Post, float]]:
        if max_items <= 0:
            return []

        selected: list[tuple[Post, float]] = []
        selected_post_ids: set[int] = set()
        used_channels: set[int] = set()

        for post, score in ranked_posts:
            if post.channel_id in used_channels:
                continue
            selected.append((post, score))
            selected_post_ids.add(post.id)
            used_channels.add(post.channel_id)
            if len(selected) >= max_items:
                return selected

        for post, score in ranked_posts:
            if post.id in selected_post_ids:
                continue
            selected.append((post, score))
            if len(selected) >= max_items:
                break

        return selected

    def _build_channel_baselines(self, posts: list[Post]) -> dict[int, ChannelMetricBaseline]:
        grouped_posts: dict[int, list[Post]] = defaultdict(list)
        for post in posts:
            grouped_posts[post.channel_id].append(post)

        baselines: dict[int, ChannelMetricBaseline] = {}
        for channel_id, channel_posts in grouped_posts.items():
            count = max(len(channel_posts), 1)
            baselines[channel_id] = ChannelMetricBaseline(
                avg_views=sum(max(post.views_count or 0, 0) for post in channel_posts) / count,
                avg_reactions=sum(max(post.reactions_count or 0, 0) for post in channel_posts) / count,
                avg_forwards=sum(max(post.forwards_count or 0, 0) for post in channel_posts) / count,
                avg_comments=sum(max(post.comments_count or 0, 0) for post in channel_posts) / count,
            )
        return baselines

    def _deduplicate_topics(self, scored_posts: list[tuple[Post, float]]) -> list[tuple[Post, float]]:
        token_sets: dict[int, set[str]] = {
            post.id: set(extract_topic_tokens(post.cleaned_text))
            for post, _score in scored_posts
        }
        token_frequencies: dict[str, int] = defaultdict(int)
        for token_set in token_sets.values():
            for token in token_set:
                token_frequencies[token] += 1

        unique_topics: list[tuple[Post, float]] = []
        for post, score in scored_posts:
            if any(
                self._posts_share_topic(post, existing_post, token_sets, token_frequencies)
                for existing_post, _ in unique_topics
            ):
                continue
            unique_topics.append((post, score))
        return unique_topics

    def _posts_share_topic(
        self,
        left_post: Post,
        right_post: Post,
        token_sets: dict[int, set[str]],
        token_frequencies: dict[str, int],
    ) -> bool:
        left_tokens = token_sets.get(left_post.id, set())
        right_tokens = token_sets.get(right_post.id, set())
        if not left_tokens or not right_tokens:
            return False

        shared_tokens = left_tokens & right_tokens
        distinctive_shared_tokens = {
            token for token in shared_tokens if token_frequencies.get(token, 0) <= 2
        }
        if len(distinctive_shared_tokens) < 2:
            return False

        shared_weight = sum(1.0 / token_frequencies[token] for token in distinctive_shared_tokens)
        left_weight = sum(1.0 / token_frequencies[token] for token in left_tokens)
        right_weight = sum(1.0 / token_frequencies[token] for token in right_tokens)
        reference_weight = min(left_weight, right_weight)
        if reference_weight <= 0.0:
            return False
        return shared_weight / reference_weight >= 0.5

    def _build_digest_text(
        self,
        items: list[tuple[Post, float]],
        channels: list[Channel],
    ) -> str:
        channel_titles = {channel.id: channel.title for channel in channels}
        fallback_text = self._build_fallback_digest_text(items, channel_titles)

        if not self.llm.is_enabled():
            return fallback_text

        prompt = self._build_digest_prompt(items, channel_titles)
        llm_text = self.llm.generate(
            prompt,
            max_tokens=450,
            temperature=0.1,
            system_prompt=DIGEST_SYSTEM_PROMPT,
        ).strip()
        if not self._is_valid_digest_response(llm_text, expected_items=len(items)):
            return fallback_text
        return llm_text

    def _build_fallback_digest_text(
        self,
        items: list[tuple[Post, float]],
        channel_titles: dict[int, str],
    ) -> str:
        lines = ["Краткий дайджест по вашим каналам:", ""]
        for index, (post, _score) in enumerate(items, start=1):
            lines.append(f"{index}. Канал: {channel_titles[post.channel_id]}")
            lines.append(f"Кратко: {self._build_item_summary(post.cleaned_text)}")
            lines.append(f"Source: {post.source_url}")
            if index != len(items):
                lines.append("")
        return "\n".join(lines)

    def _build_digest_prompt(
        self,
        items: list[tuple[Post, float]],
        channel_titles: dict[int, str],
    ) -> str:
        item_blocks = []
        for index, (post, _score) in enumerate(items, start=1):
            snippet = " ".join(post.cleaned_text.split()).strip()[:MAX_DIGEST_SNIPPET_LENGTH]
            item_blocks.append(
                "\n".join(
                    [
                        f"Item {index}",
                        f"Channel: {channel_titles[post.channel_id]}",
                        f"Source URL: {post.source_url}",
                        f"Post text: {snippet}",
                    ]
                )
            )
        rendered_items = "\n\n".join(item_blocks)

        return (
            "Собери компактный Telegram digest по материалам ниже.\n"
            "Жесткие требования:\n"
            "- итоговый текст должен быть только на русском языке;\n"
            "- не использовать китайский язык;\n"
            "- не смешивать языки, кроме названий продуктов, брендов и оригинальных терминов;\n"
            "- для каждого пункта дай 1-2 коротких предложения summary на русском;\n"
            "- обязательно сохрани строку `Source: <ссылка>` для каждого пункта;\n"
            "- формат должен быть стабильным и компактным.\n\n"
            "Верни ответ строго в таком формате:\n"
            "Краткий дайджест по вашим каналам:\n\n"
            "1. Канал: <название>\n"
            "Кратко: <1-2 предложения на русском>\n"
            "Source: <ссылка>\n\n"
            "2. Канал: <название>\n"
            "Кратко: <1-2 предложения на русском>\n"
            "Source: <ссылка>\n\n"
            f"Используй только {len(items)} лучших материалов.\n\n"
            f"Материалы:\n\n{rendered_items}"
        )

    def _is_valid_digest_response(self, text: str, expected_items: int) -> bool:
        if not text or text.startswith("LLM "):
            return False
        if _contains_cjk(text):
            return False
        if "Source:" not in text:
            return False
        if text.count("Source:") < expected_items:
            return False
        return re.search(r"(?m)^1\.\s", text) is not None

    def _build_title(self, text: str) -> str:
        trimmed = text.strip()
        if len(trimmed) <= 80:
            return trimmed or "Untitled post"
        return f"{trimmed[:77].rstrip()}..."

    def _build_item_summary(self, text: str) -> str:
        cleaned = " ".join(text.split()).strip()
        if not cleaned:
            return "Подробностей в посте почти нет."

        sentence_candidates = re.split(r"(?<=[.!?])\s+", cleaned)
        summary = " ".join(part.strip() for part in sentence_candidates[:2] if part.strip())
        if not summary:
            summary = cleaned
        if len(summary) <= MAX_DIGEST_SUMMARY_LENGTH:
            return summary
        return f"{summary[: MAX_DIGEST_SUMMARY_LENGTH - 3].rstrip()}..."

    def _resolve_window_days(self, user_id: int, since_hours: int | None) -> int:
        if since_hours is None:
            return UserService(self.session).get_digest_window_days(user_id)
        normalized_hours = max(int(since_hours), 24)
        derived_days = (normalized_hours + 23) // 24
        return max(derived_days, 1)


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _contains_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)
