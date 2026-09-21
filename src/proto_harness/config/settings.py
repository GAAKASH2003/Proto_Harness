import logging
from pathlib import Path
from typing import Literal


# pyrefly: ignore [missing-import]
from pydantic import SecretStr
# pyrefly: ignore [missing-import]
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

def _find_env_files() -> tuple[str, ...]:
    candidates = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parents[1] / ".env",       # src/proto_harness/.env
        Path(__file__).resolve().parents[2] / ".env",       # Proto_Harness/.env
    ]
    existing = [str(p) for p in candidates if p.is_file()]
    return tuple(existing) if existing else (".env",)


class Settings(BaseSettings):
    """Runtime configuration for Proto Harness."""

    model_config = SettingsConfigDict(
        env_file=_find_env_files(),
        extra="ignore",
    )

    llm_provider: Literal["gemini", "openrouter"] = "openrouter"

    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini-3.5-flash-lite"
   
    openrouter_api_key: SecretStr = SecretStr("")
    # openrouter_model: str = "x-ai/grok-4.3-fast:free"
    # openrouter_model: str = "openrouter/free"
    # openrouter_model: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
    openrouter_model: str = "nvidia/nemotron-3.5-lightning:free"
    
    memory_filename: str = "MEMORY.md"
    memory_max_lines: int = 200
    memory_max_bytes: int = 20_000

    log_level: str = "INFO"

    # Tool execution
    bash_timeout_s: float = 120.0
    skills_dir: Path = Path(".proto_harness/skills")
    @property
    def active_model(self) -> str:
        if self.llm_provider == "openrouter":
            return self.openrouter_model
        return self.gemini_model

settings = Settings()




# if __name__ == "__main__":
#     import httpx

#     PROMPT = "Explain what an AI agent is in exactly 2 sentences."

#     ── OpenRouter test ──────────────────────────────────────────────
#     print("\n── OpenRouter test ──")
#     or_key = settings.openrouter_api_key.get_secret_value()
#     if not or_key:
#         print("❌ OPENROUTER_API_KEY not set — skipping")
#     else:
#         print(f"Model: {settings.openrouter_model}")
#         try:
#             r = httpx.post(
#                 "https://openrouter.ai/api/v1/chat/completions",
#                 headers={"Authorization": f"Bearer {or_key}", "Content-Type": "application/json"},
#                 json={"model": settings.openrouter_model, "messages": [{"role": "user", "content": PROMPT}]},
#                 timeout=60.0,
#             )
#             if r.status_code == 200:
#                 print("✅", r.json()["choices"][0]["message"]["content"])
#             else:
#                 print(f"❌ {r.status_code}: {r.text}")
#         except httpx.RequestError as e:
#             print(f"❌ Connection error: {e}")

#     ── Gemini test ──────────────────────────────────────────────────
#     print("\n── Gemini test ──")
#     g_key = settings.gemini_api_key.get_secret_value()
#     if not g_key:
#         print("❌ GEMINI_API_KEY not set — skipping")
#     else:
#         print(f"Model: {settings.gemini_model}")
#         try:
#             url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent?key={g_key}"
#             r = httpx.post(
#                 url,
#                 headers={"Content-Type": "application/json"},
#                 json={"contents": [{"parts": [{"text": PROMPT}]}]},
#                 timeout=60.0,
#             )
#             if r.status_code == 200:
#                 text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
#                 print("✅", text)
#             else:
#                 print(f"❌ {r.status_code}: {r.text}")
#         except httpx.RequestError as e:
#             print(f"❌ Connection error: {e}")
