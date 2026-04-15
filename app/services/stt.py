from pathlib import Path


class STTService:
    def transcribe(self, audio_path: str | Path) -> str:
        path = Path(audio_path)
        if not path.exists():
            raise FileNotFoundError(f'Audio file not found: {path}')

        return (
            f"Audio '{path.name}' received. "
            'This MVP currently runs in text-first mode.'
        )
