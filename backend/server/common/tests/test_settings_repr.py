"""Settings repr must not print secret values."""

from server.common.config import Settings


def test_settings_repr_redacts_secrets() -> None:
    settings = Settings(
        OPENAI_API_KEY="sk-test-secret-value",
        XAI_API_KEY="xai-test-secret-value",
        CAPTURE_API_TOKENS="token-secret-value",
        R2_SECRET_ACCESS_KEY="r2-secret-value",
        UPLOAD_URL_HMAC_SECRET="hmac-secret-value",
        WEBHOOK_HMAC_SECRET="webhook-secret-value",
        DATABASE_URL="postgres://user:password@localhost/db",
        APNS_KEY_P8="apns-key-material",
    )
    rendered = repr(settings) + str(settings)
    for secret in (
        "sk-test-secret-value",
        "xai-test-secret-value",
        "token-secret-value",
        "r2-secret-value",
        "hmac-secret-value",
        "webhook-secret-value",
        "password",
        "apns-key-material",
    ):
        assert secret not in rendered
    assert settings.OPENAI_API_KEY == "sk-test-secret-value"
