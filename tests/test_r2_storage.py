from pathlib import Path
from unittest.mock import MagicMock

import pytest
from botocore.stub import Stubber

from core.config import Settings
from core.exceptions import InvalidMediaError, ObjectStorageConfigurationError, ObjectStoragePartialDeleteError, ObjectStorageProviderError
from core.visitor import new_visitor_id
from services.storage import R2ObjectStorage, derived_object_key, normalize_object_key, object_storage_base_prefix, original_object_key, original_object_metadata
from services.storage import is_managed_object_key


def make_settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, app_env="test", pinecone_namespace="namespace-a", r2_object_prefix="private-rag", r2_endpoint_url="https://account.r2.cloudflarestorage.com", r2_access_key_id="access-key", r2_secret_access_key="secret-key", r2_bucket_name="private-bucket", temp_processing_dir=tmp_path / "processing")


def test_key_builders_sanitize_user_names(tmp_path: Path):
    settings = make_settings(tmp_path)
    original = original_object_key("doc-123", r"..older/relatório final.pdf", settings)
    derived = derived_object_key("doc-123", r"..page/scan.png", settings)
    root = object_storage_base_prefix(settings)
    assert original == f"{root}/doc-123/original/relat_rio_final.pdf"
    assert derived == f"{root}/doc-123/derived/scan.png"
    with pytest.raises(InvalidMediaError):
        normalize_object_key("private-rag/../secret")


def test_managed_key_check_enforces_exact_visitor_and_document_scope(tmp_path: Path):
    settings = make_settings(tmp_path)
    first, second = new_visitor_id(), new_visitor_id()
    key = original_object_key("doc-123", "evidence.txt", settings, first)
    assert is_managed_object_key(key, settings, first, "doc-123")
    assert not is_managed_object_key(key, settings, second, "doc-123")
    assert not is_managed_object_key(key, settings, first, "doc-999")


@pytest.mark.asyncio
async def test_put_file_allowlists_metadata_and_uploads_off_event_loop(tmp_path: Path):
    settings = make_settings(tmp_path)
    client = MagicMock()
    source = tmp_path / "document.txt"
    source.write_text("conteúdo", encoding="utf-8")
    storage = R2ObjectStorage(settings, client=client)
    result = await storage.put_file("private-rag/test/namespace-a/documents/doc/original/document.txt", source, content_type="text/plain", metadata={**original_object_metadata("doc", "a" * 64, "document.txt", "text/plain", settings), "secret": "must-not-be-persisted"})
    call = client.upload_file.call_args
    assert result.size_bytes == source.stat().st_size
    assert call.kwargs["ExtraArgs"]["Metadata"]["doc-id"] == "doc"
    assert "secret" not in call.kwargs["ExtraArgs"]["Metadata"]


@pytest.mark.asyncio
async def test_head_download_and_exists_use_safe_provider_contract(tmp_path: Path):
    settings = make_settings(tmp_path)
    client = MagicMock()
    client.head_object.return_value = {"ContentLength": 5, "ContentType": "text/plain", "ETag": "etag"}
    client.download_file.side_effect = lambda _bucket, _key, destination: Path(destination).write_bytes(b"hello")
    storage = R2ObjectStorage(settings, client=client)
    key = original_object_key("doc", "document.txt", settings)
    metadata = await storage.head_object(key)
    destination = await storage.download_to_path(key, tmp_path / "downloaded.txt")
    assert metadata and metadata.size_bytes == 5
    assert await storage.object_exists(key)
    assert destination.read_bytes() == b"hello"


@pytest.mark.asyncio
async def test_head_missing_object_returns_none_and_delete_is_stubbed(tmp_path: Path):
    settings = make_settings(tmp_path)
    client = settings_client(settings)
    key = original_object_key("doc", "document.txt", settings)
    with Stubber(client) as stubber:
        stubber.add_client_error("head_object", service_error_code="NoSuchKey", http_status_code=404, expected_params={"Bucket": settings.r2_bucket_name, "Key": key})
        stubber.add_response("delete_object", {"ResponseMetadata": {"HTTPStatusCode": 204}}, {"Bucket": settings.r2_bucket_name, "Key": key})
        stubber.add_client_error("head_object", service_error_code="AccessDenied", http_status_code=403, expected_params={"Bucket": settings.r2_bucket_name, "Key": key})
        storage = R2ObjectStorage(settings, client=client)
        assert await storage.head_object(key) is None
        await storage.delete_object(key)
        with pytest.raises(ObjectStorageConfigurationError):
            await storage.head_object(key)


@pytest.mark.asyncio
async def test_multi_delete_surfaces_partial_failure(tmp_path: Path):
    settings = make_settings(tmp_path)
    client = settings_client(settings)
    keys = [original_object_key("doc", "a.txt", settings), original_object_key("doc", "b.txt", settings)]
    with Stubber(client) as stubber:
        stubber.add_response("delete_objects", {"Deleted": [{"Key": keys[0]}], "Errors": [{"Key": keys[1], "Code": "AccessDenied", "Message": "denied"}], "ResponseMetadata": {"HTTPStatusCode": 200}}, {"Bucket": settings.r2_bucket_name, "Delete": {"Objects": [{"Key": keys[0]}, {"Key": keys[1]}], "Quiet": False}})
        with pytest.raises(ObjectStoragePartialDeleteError) as error:
            await R2ObjectStorage(settings, client=client).delete_objects(keys)
    assert error.value.failed_keys == (keys[1],)
    assert error.value.deleted_keys == (keys[0],)


@pytest.mark.asyncio
async def test_list_and_presigned_urls_are_generated_on_demand(tmp_path: Path, caplog):
    settings = make_settings(tmp_path)
    client = settings_client(settings)
    key = original_object_key("doc", "document.txt", settings)
    client.generate_presigned_url = MagicMock(return_value="https://account.r2.cloudflarestorage.com/private-bucket?X-Amz-Signature=do-not-log")
    with Stubber(client) as stubber:
        stubber.add_response("list_objects_v2", {"Contents": [{"Key": key, "Size": 5, "ETag": "etag"}], "IsTruncated": False, "KeyCount": 1, "MaxKeys": 1000, "Name": settings.r2_bucket_name, "Prefix": f"{object_storage_base_prefix(settings)}/"}, {"Bucket": settings.r2_bucket_name, "Prefix": f"{object_storage_base_prefix(settings)}/", "MaxKeys": 1000})
        storage = R2ObjectStorage(settings, client=client)
        objects = await storage.list_objects_by_prefix(object_storage_base_prefix(settings))
        url = await storage.generate_presigned_get_url(key)
        put_url = await storage.generate_presigned_put_url(key, content_type="text/plain", metadata={"sha256": "a" * 64})
    assert objects[0].key == key
    assert "X-Amz-Signature" in url and put_url == url
    put_params = client.generate_presigned_url.call_args_list[1].kwargs["Params"]
    assert put_params["ContentType"] == "text/plain"
    assert put_params["Metadata"]["sha256"] == "a" * 64
    assert "do-not-log" not in caplog.text


@pytest.mark.asyncio
async def test_health_check_is_cached_and_invalid_response_is_rejected(tmp_path: Path):
    settings = make_settings(tmp_path)
    client = MagicMock()
    storage = R2ObjectStorage(settings, client=client)
    assert await storage.health_check() is True
    assert await storage.health_check() is True
    client.head_bucket.assert_called_once_with(Bucket=settings.r2_bucket_name)
    client.head_object.return_value = {"ContentLength": "invalid", "ContentType": "text/plain"}
    with pytest.raises(ObjectStorageProviderError):
        await storage.head_object(original_object_key("doc", "bad.txt", settings))


def test_client_configuration_uses_r2_s3v4_and_redacts_credentials(tmp_path: Path, monkeypatch):
    settings = make_settings(tmp_path)
    client = MagicMock()
    captured = {}

    def fake_client(*args, **kwargs):
        captured.update(kwargs)
        return client

    monkeypatch.setattr("services.storage.boto3.client", fake_client)
    storage = R2ObjectStorage(settings)
    assert storage.bucket == settings.r2_bucket_name
    assert captured["region_name"] == "auto"
    assert captured["config"].signature_version == "s3v4"
    assert captured["config"].retries["max_attempts"] == 3
    assert settings.r2_secret_access_key not in repr(settings)


def settings_client(settings: Settings):
    import boto3

    return boto3.client("s3", endpoint_url=settings.r2_endpoint_url, region_name="auto", aws_access_key_id=settings.r2_access_key_id, aws_secret_access_key=settings.r2_secret_access_key)
