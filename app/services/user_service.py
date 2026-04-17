import secrets
import string
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DigestSchedule, TelegramLinkCode, User


class UserService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_id(self, user_id: int) -> User | None:
        return self.session.get(User, user_id)

    def upsert_telegram_user(
        self,
        telegram_user_id: int,
        username: str = "",
        display_name: str = "",
    ) -> User:
        user = self.session.scalar(
            select(User).where(User.telegram_user_id == telegram_user_id)
        )
        if user is None:
            user = User(
                telegram_user_id=telegram_user_id,
                username=username,
                display_name=display_name,
            )
            self.session.add(user)
            self.session.flush()
        else:
            user.username = username
            user.display_name = display_name

        self._ensure_digest_schedule(user.id)
        self.session.commit()
        self.session.refresh(user)
        return user

    def get_by_telegram_user_id(self, telegram_user_id: int) -> User | None:
        return self.session.scalar(
            select(User).where(User.telegram_user_id == telegram_user_id)
        )

    def get_or_create_link_code(self, user_id: int, ttl_minutes: int = 30) -> TelegramLinkCode:
        now = datetime.now(timezone.utc)
        active_code = self.session.scalar(
            select(TelegramLinkCode)
            .where(
                TelegramLinkCode.user_id == user_id,
                TelegramLinkCode.used_at.is_(None),
                TelegramLinkCode.expires_at > now,
            )
            .order_by(TelegramLinkCode.created_at.desc())
        )
        if active_code is not None:
            return active_code

        link_code = TelegramLinkCode(
            user_id=user_id,
            code=self._generate_code(),
            expires_at=now + timedelta(minutes=ttl_minutes),
        )
        self.session.add(link_code)
        self.session.commit()
        self.session.refresh(link_code)
        return link_code

    def redeem_link_code(self, raw_code: str) -> User:
        normalized_code = self._normalize_link_code(raw_code)
        if not normalized_code:
            raise ValueError("Enter a valid link code from Telegram.")

        link_code = self.session.scalar(
            select(TelegramLinkCode).where(TelegramLinkCode.code == normalized_code)
        )
        if link_code is None:
            raise ValueError("Link code not found. Request a fresh code with /link.")

        now = datetime.now(timezone.utc)
        if link_code.used_at is not None:
            raise ValueError("This link code was already used. Request a new one with /link.")
        if self._coerce_utc(link_code.expires_at) <= now:
            raise ValueError("This link code has expired. Request a new one with /link.")

        user = self.get_by_id(link_code.user_id)
        if user is None:
            raise ValueError("This link code is not attached to a valid user.")

        link_code.used_at = now
        self.session.commit()
        self.session.refresh(user)
        return user

    def _ensure_digest_schedule(self, user_id: int) -> None:
        schedule = self.session.scalar(
            select(DigestSchedule).where(DigestSchedule.user_id == user_id)
        )
        if schedule is None:
            self.session.add(
                DigestSchedule(
                    user_id=user_id,
                    enabled=True,
                    frequency="daily",
                )
            )

    def _generate_code(self, length: int = 8) -> str:
        alphabet = string.ascii_uppercase + string.digits
        while True:
            code = "".join(secrets.choice(alphabet) for _ in range(length))
            exists = self.session.scalar(
                select(TelegramLinkCode).where(TelegramLinkCode.code == code)
            )
            if exists is None:
                return code

    def _normalize_link_code(self, value: str) -> str:
        return value.strip().upper()

    def _coerce_utc(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
