from functools import lru_cache
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
