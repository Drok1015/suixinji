from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    database_path: str = str(ROOT / "data/self-agent.sqlite3")
    harmony_release_path: str = str(ROOT / "data/harmony-release.json")
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    chat_model: str = "deepseek-flash"
    session_secret: str = ""
    login_password_hash: str = ""
    secure_cookie: bool = True
    app_base_path: str = "/self-agent"
    jev_env_file: str = ""
    route_threshold: float = 0.80
    new_topic_threshold: float = 0.85


settings = Settings()
