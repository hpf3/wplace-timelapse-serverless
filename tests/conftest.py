import os
from pathlib import Path

from dotenv import load_dotenv


def pytest_configure() -> None:
    """Load secrets for integration tests if present."""
    secrets_path = Path(__file__).parent / ".env.secrets"
    if secrets_path.exists():
        load_dotenv(secrets_path, override=False)
    # Enable capture integration tests by default when secrets are present.
    if os.getenv("RUN_CAPTURE_TEST") is None and secrets_path.exists():
        os.environ["RUN_CAPTURE_TEST"] = "1"
