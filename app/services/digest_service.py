from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Channel, Digest, DigestItem, Post, Subscription
from app.digest.ranking import score_post_text
from app.services.llm import TogetherLLM


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
        max_items: int = 5,
        since_hours: int = 72,
    ) -> DigestGenerationResult:
        subscribed_channels = list(
            self.session.scalars(
                select(Channel)
                .join(Subscription, Subscription.channel_id == Channel.id)
                .where(Subscription.user_id == user_id, Subscription.enabled.is_(True))
                .order_by(Channel.title.asc())
            )
        )
        if not subscribed_channels:
            return DigestGenerationResult(
                digest=None,
                message_text="No active subscriptions yet. Use /channels to select curated sources.",
                has_content=False,
            )

        channel_ids = [channel.id for channel in subscribed_channels]
        threshold = datetime.now(timezone.utc) - timedelta(hours=since_hours)
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
                message_text="No fresh posts found for your current subscriptions.",
                has_content=False,
            )

        digest = Digest(
            user_id=user_id,
            status="ready",
            delivery_status="pending",
            body_text="",
            source_post_count=len(ranked_posts),
        )
        self.session.add(digest)
        self.session.flush()

        selected_items: list[tuple[Post, float]] = ranked_posts[:max_items]
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

        now = datetime.now(timezone.utc)
        scored = []
        for post in unique_posts:
            published_at = _coerce_utc(post.published_at)
            age_hours = max((now - published_at).total_seconds() / 3600.0, 0.0)
            score = score_post_text(post.cleaned_text, age_hours=age_hours)
            scored.append((post, score))

        scored.sort(key=lambda item: (item[1], item[0].published_at), reverse=True)
        return scored

    def _build_digest_text(
        self,
        items: list[tuple[Post, float]],
        channels: list[Channel],
    ) -> str:
        channel_titles = {channel.id: channel.title for channel in channels}
        fallback_lines = ["Your latest digest:"]
        for index, (post, _score) in enumerate(items, start=1):
            fallback_lines.append(
                f"{index}. {channel_titles[post.channel_id]}: {self._build_item_summary(post.cleaned_text)}"
            )
            fallback_lines.append(f"Source: {post.source_url}")
        fallback_text = "\n".join(fallback_lines)

        if not self.llm.is_enabled():
            return fallback_text

        snippets = []
        for post, _score in items:
            snippets.append(f"- {channel_titles[post.channel_id]}: {post.cleaned_text[:500]}")
        prompt = (
            "Create a concise Telegram digest in plain text.\n"
            "Keep the output compact and readable.\n"
            "Each item must include the channel name and a short takeaway.\n\n"
            f"Items:\n{'\n'.join(snippets)}"
        )
        llm_text = self.llm.generate(prompt, max_tokens=350, temperature=0.2).strip()
        if not llm_text or llm_text.startswith("LLM "):
            return fallback_text
        return f"{llm_text}\n\n{fallback_text}"

    def _build_title(self, text: str) -> str:
        trimmed = text.strip()
        if len(trimmed) <= 80:
            return trimmed or "Untitled post"
        return f"{trimmed[:77].rstrip()}..."

    def _build_item_summary(self, text: str) -> str:
        cleaned = " ".join(text.split()).strip()
        if len(cleaned) <= 180:
            return cleaned
        return f"{cleaned[:177].rstrip()}..."


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
