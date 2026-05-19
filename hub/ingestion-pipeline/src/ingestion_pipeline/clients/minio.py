from __future__ import annotations

from io import BytesIO
from dataclasses import dataclass
from urllib.parse import urlparse

from minio import Minio
from minio.error import S3Error


@dataclass(frozen=True)
class MinioTextObject:
    object_name: str
    content: str


class MinioDocumentClient:
    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        secure: bool,
    ) -> None:
        self._bucket = bucket
        self._client = Minio(
            _normalize_endpoint(endpoint),
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )

    def ensure_bucket(self) -> None:
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)

    def put_text_object_if_missing(self, object_name: str, content: str) -> bool:
        if self.object_exists(object_name):
            return False
        data = content.encode("utf-8")
        self._client.put_object(
            self._bucket,
            object_name,
            BytesIO(data),
            length=len(data),
            content_type="text/markdown; charset=utf-8",
        )
        return True

    def object_exists(self, object_name: str) -> bool:
        try:
            self._client.stat_object(self._bucket, object_name)
            return True
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
                return False
            raise

    def load_text_object(self, object_name: str) -> str:
        response = None
        try:
            response = self._client.get_object(self._bucket, object_name)
            return response.read().decode("utf-8")
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
                raise FileNotFoundError(object_name) from exc
            raise
        finally:
            if response is not None:
                response.close()
                response.release_conn()

    def load_prefix_text_objects(self, prefix: str) -> list[MinioTextObject]:
        objects: list[MinioTextObject] = []
        for obj in self._client.list_objects(self._bucket, prefix=prefix, recursive=True):
            objects.append(
                MinioTextObject(
                    object_name=obj.object_name,
                    content=self.load_text_object(obj.object_name),
                )
            )
        return objects


def _normalize_endpoint(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    if parsed.scheme and parsed.netloc:
        return parsed.netloc
    return endpoint
