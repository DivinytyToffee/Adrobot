from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, PositiveInt, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8")

    keitaro_api_url: AnyHttpUrl = "https://tlgk.host/admin_api/v1/"
    adrobot_mode: Literal["demo", "live"] = "demo"
    demo_database_url: str = "sqlite:///./adrobot-demo.sqlite3"
    keitaro_api_key: SecretStr = SecretStr("")
    default_domain_id: PositiveInt | None = None
    default_group_id: PositiveInt | None = None
    default_traffic_source_id: PositiveInt | None = None
    database_url: str = "sqlite:///./adrobot.sqlite3"

    @property
    def active_database_url(self):
        return self.demo_database_url if self.adrobot_mode == "demo" else self.database_url
