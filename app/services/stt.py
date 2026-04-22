from __future__ import annotations

import json
import mimetypes
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from together import Together

from app.config import get_settings

TOGETHER_STT_BASE_URL = "https://api.together.ai/v1"
TOGETHER_STT_HOSTS = {"api.together.ai"}
OPENAI_STT_HOSTS = {"api.openai.com", "api.openai.azure.com"}


class STTConfigurationError(RuntimeError):
    pass


class STTTranscriptionError(RuntimeError):
    pass


class STTService:
    def __init__(
        self,
        api_key: str | None = None,
        api_base_url: str | None = None,
        model: str | None = None,
        language: str | None = None,
        timeout_seconds: int = 90,
    ) -> None:
        settings = get_settings()
        self.api_key = api_key if api_key is not None else (
            settings.stt_api_key or settings.together_api_key
        )
        configured_base_url = (
            api_base_url if api_base_url is not None else settings.stt_api_base_url
        )
        self.api_base_url = _resolve_api_base_url(self.api_key, configured_base_url)
        self.model = model if model is not None else settings.stt_model
        self.language = language if language is not None else settings.stt_language
        self.timeout_seconds = timeout_seconds

    def is_enabled(self) -> bool:
        return bool(self.api_key.strip())

    def transcribe(self, audio_path: str | Path) -> str:
        path = Path(audio_path)
        if not path.exists():
            raise FileNotFoundError(f"Audio file not found: {path}")
        if not self.is_enabled():
            raise STTConfigurationError(
                "STT is not configured. Set STT_API_KEY and, if needed, "
                "STT_API_BASE_URL/STT_MODEL in .env."
            )

        response_payload = self._send_transcription_request(path)
        transcript = _extract_transcript(response_payload)
        if not transcript:
            raise STTTranscriptionError("STT returned an empty transcript.")
        return transcript

    def _send_transcription_request(self, audio_path: Path) -> dict[str, object]:
        if _is_together_base_url(self.api_base_url):
            return self._send_together_transcription_request(audio_path)

        boundary = f"----digest-bot-stt-{uuid4().hex}"
        fields = {"model": self.model.strip() or "openai/whisper-large-v3"}
        if self.language.strip():
            fields["language"] = self.language.strip()

        body = _build_multipart_body(
            fields=fields,
            file_field_name="file",
            file_path=audio_path,
            boundary=boundary,
        )
        request = Request(
            _build_transcription_url(self.api_base_url),
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key.strip()}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Accept": "application/json",
                "User-Agent": "AI-Telegram-Digest-Bot/0.1",
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw_response = response.read()
        except HTTPError as exc:
            error_body = _safe_decode(exc.read())
            host = urlparse(request.full_url).netloc
            raise STTTranscriptionError(
                f"STT request failed with HTTP {exc.code} from {host}: "
                f"{_truncate(_redact_secret(error_body))}"
            ) from exc
        except URLError as exc:
            raise STTTranscriptionError(f"STT request failed: {exc.reason}") from exc
        except OSError as exc:
            raise STTTranscriptionError(f"STT request failed: {exc}") from exc

        try:
            payload = json.loads(raw_response.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise STTTranscriptionError("STT returned invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise STTTranscriptionError("STT returned an unexpected response.")
        return payload

    def _send_together_transcription_request(self, audio_path: Path) -> dict[str, object]:
        client = Together(
            api_key=self.api_key.strip(),
            base_url=self.api_base_url.strip().rstrip("/"),
            timeout=self.timeout_seconds,
            max_retries=0,
        )
        try:
            with audio_path.open("rb") as audio_file:
                response = client.audio.transcriptions.create(
                    file=audio_file,
                    model=self.model.strip() or "openai/whisper-large-v3",
                    language=self.language.strip() or "auto",
                    response_format="json",
                )
        except Exception as exc:  # noqa: BLE001
            raise STTTranscriptionError(
                f"Together STT request failed: {_truncate(_redact_secret(str(exc)))}"
            ) from exc
        finally:
            client.close()

        transcript = _extract_transcript(response)
        if not transcript:
            raise STTTranscriptionError("Together STT returned an empty transcript.")
        return {"text": transcript}


def _build_transcription_url(api_base_url: str) -> str:
    base_url = api_base_url.strip().rstrip("/")
    if not base_url:
        raise STTConfigurationError("STT_API_BASE_URL is empty.")
    if base_url.endswith("/audio/transcriptions"):
        return base_url
    return f"{base_url}/audio/transcriptions"


def _resolve_api_base_url(api_key: str, api_base_url: str) -> str:
    key = api_key.strip()
    base_url = api_base_url.strip()
    host = urlparse(base_url).netloc.lower()
    if key.startswith("tgp_") and host in OPENAI_STT_HOSTS:
        return TOGETHER_STT_BASE_URL
    return base_url


def _is_together_base_url(api_base_url: str) -> bool:
    host = urlparse(api_base_url.strip()).netloc.lower()
    return host in TOGETHER_STT_HOSTS


def _build_multipart_body(
    fields: dict[str, str],
    file_field_name: str,
    file_path: Path,
    boundary: str,
) -> bytes:
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )

    content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    chunks.extend(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            (
                f'Content-Disposition: form-data; name="{file_field_name}"; '
                f'filename="{file_path.name}"\r\n'
            ).encode("utf-8"),
            f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
            file_path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    return b"".join(chunks)


def _extract_transcript(payload: object) -> str:
    if not isinstance(payload, dict):
        for key in ("text", "transcript", "transcription"):
            value = getattr(payload, key, None)
            if isinstance(value, str):
                return " ".join(value.split()).strip()
        model_dump = getattr(payload, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump()
            if isinstance(dumped, dict):
                return _extract_transcript(dumped)
        return ""

    for key in ("text", "transcript", "transcription"):
        value = payload.get(key)
        if isinstance(value, str):
            return " ".join(value.split()).strip()
    return ""


def _safe_decode(value: bytes) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return value.decode("utf-8", errors="replace")


def _truncate(value: str, limit: int = 300) -> str:
    text = " ".join(value.split()).strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3].rstrip()}..."


def _redact_secret(value: str) -> str:
    text = re.sub(r"tgp_[A-Za-z0-9_\-]+", "tgp_***", value)
    return re.sub(r"sk-[A-Za-z0-9_\-]+", "sk-***", text)
