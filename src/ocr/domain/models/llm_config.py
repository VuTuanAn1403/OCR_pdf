import os
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field


def _load_dotenv_if_present():
    """Lightweight .env parser that loads variables into os.environ if not already set."""
    candidates = [
        Path(".env"),
        Path(__file__).resolve().parent.parent.parent.parent / ".env",
    ]
    for env_path in candidates:
        if env_path.is_file():
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k and k not in os.environ:
                            os.environ[k] = v
                break
            except Exception:
                pass


class LLMConfig(BaseModel):
    """Configuration for LLM verification."""
    provider: str = Field(default="auto", description="'openai', 'gemini', 'ollama', or 'auto'")
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model_name: Optional[str] = None
    timeout_sec: int = 15

    @classmethod
    def from_env(cls) -> "LLMConfig":
        _load_dotenv_if_present()

        provider = os.getenv("LLM_PROVIDER", "auto").lower()
        api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("GEMINI_API_KEY")
        base_url = os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")
        model = os.getenv("LLM_MODEL")

        if provider == "auto":
            if os.getenv("GEMINI_API_KEY"):
                provider = "gemini"
                model = model or "gemini-1.5-flash"
            elif os.getenv("OPENAI_API_KEY"):
                provider = "openai"
                model = model or "gpt-4o-mini"
            elif base_url:
                provider = "openai"  # compatible base_url
                model = model or "llama3"
            else:
                provider = "heuristic"

        return cls(
            provider=provider,
            api_key=api_key,
            base_url=base_url,
            model_name=model,
        )
