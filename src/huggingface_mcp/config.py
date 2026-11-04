from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Non-credential configuration only.

    The Hugging Face access token is never read from an environment variable
    or a config file -- it arrives per request in the X-Huggingface-Token
    header (see middleware.py). `extra="ignore"` is required so the runtime
    environment can inject unrelated variables without crashing the service
    (SOP §1.2).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mcp_http_host: str = "0.0.0.0"
    mcp_http_port: int = 8080

    # Name of the HTTP header carrying the Hugging Face user access token.
    # Not a secret value itself -- just the header name (SOP §3.1).
    credential_header: str = "X-Huggingface-Token"


def get_settings() -> Settings:
    return Settings()
