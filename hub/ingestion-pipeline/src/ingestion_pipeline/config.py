import os
from dataclasses import dataclass
from pathlib import Path


def _get_bool_env(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).lower() == "true"


@dataclass(frozen=True)
class Settings:
    llamastack_host: str
    llamastack_port: int
    vector_store_name: str
    runbooks_dir: Path
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_bucket: str
    minio_secure: bool
    minio_runbook_prefix: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            llamastack_host=os.environ.get("LLAMASTACK_HOST", "llamastack"),
            llamastack_port=int(os.environ.get("LLAMASTACK_PORT", "8321")),
            vector_store_name=os.environ.get("VECTOR_STORE_NAME", ""),
            runbooks_dir=Path(os.environ.get("RUNBOOKS_DIR", "/app/runbooks")),
            minio_endpoint=os.environ.get("MINIO_ENDPOINT", ""),
            minio_access_key=os.environ.get("MINIO_ACCESS_KEY", ""),
            minio_secret_key=os.environ.get("MINIO_SECRET_KEY", ""),
            minio_bucket=os.environ.get("MINIO_BUCKET", ""),
            minio_secure=_get_bool_env("MINIO_SECURE"),
            minio_runbook_prefix=os.environ.get("MINIO_RUNBOOK_PREFIX", "runbooks/"),
        )

    @property
    def llamastack_base_url(self) -> str:
        return f"http://{self.llamastack_host}:{self.llamastack_port}"

    @property
    def minio_is_configured(self) -> bool:
        return all(
            (
                self.minio_endpoint,
                self.minio_access_key,
                self.minio_secret_key,
                self.minio_bucket,
            )
        )


settings = Settings.from_env()
