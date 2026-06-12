from functools import lru_cache
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    PORT: int = 9100
    INTERNAL_KEY: str = "change-me"

    MONGO_URI: str
    NEO4J_URI: str
    NEO4J_USERNAME: str
    NEO4J_PASSWORD: str
    NEO4J_DATABASE: str = "neo4j"

    OLLAMA_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "phi"
    OLLAMA_NUM_PREDICT: int = 200
    OLLAMA_NUM_PREDICT_SUMMARY: int = 350
    OLLAMA_TIMEOUT: int = 60

    POLICY_FILE: str = "data/policy.yaml"

    @field_validator("OLLAMA_URL")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
