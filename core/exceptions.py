"""Application exceptions exposed through safe HTTP responses."""


class AppError(Exception):
    """Base class for expected application failures."""


class ConfigurationError(AppError):
    """Raised when configuration or a required external service is unavailable."""


class UnsupportedFileError(AppError):
    """Raised when a file extension or signature is unsupported."""


class FileTooLargeError(AppError):
    """Raised when an upload exceeds its configured size limit."""


class InvalidMediaError(AppError):
    """Raised when media cannot be opened or validated."""


class MediaDurationExceededError(AppError):
    """Raised when audio or video is longer than configured."""


class IngestionError(AppError):
    """Raised when indexing a file fails."""


class RetrievalError(AppError):
    """Raised when semantic retrieval fails."""


class ExternalServiceError(AppError):
    """Raised when Gemini or Pinecone fails after retries."""


class DeletionError(ExternalServiceError):
    """Raised when a destructive operation remains pending and retryable."""


class FileNotFoundInCatalogError(AppError):
    """Raised when a requested catalog document does not exist."""


class UploadNotCompleteError(AppError):
    """Raised when the object for a pending upload is not available yet."""


class UploadConflictError(AppError):
    """Raised when an existing content hash has incompatible metadata."""


class QuotaExceededError(AppError):
    """Raised when a public client reaches a daily quota."""

    def __init__(self, message: str, retry_after_seconds: int | None = None) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(message)


class CapacityExceededError(AppError):
    """Raised when the shared public storage capacity is exhausted."""


class CatalogError(AppError):
    """Raised when a catalog operation cannot be completed safely."""


class ObjectStorageError(ExternalServiceError):
    """Base error for safe object-storage failures."""


class ObjectStorageConfigurationError(ConfigurationError):
    """Raised when the backend cannot construct the R2 client safely."""


class ObjectStorageTimeoutError(ObjectStorageError):
    """Raised when an R2 request exceeds the configured timeout."""


class ObjectNotFoundError(ObjectStorageError):
    """Raised when a requested object is absent."""


class ObjectStoragePartialDeleteError(ObjectStorageError):
    """Raised when an S3 multi-delete reports only a partial success."""

    def __init__(self, failed_keys: list[str], deleted_keys: list[str]) -> None:
        self.failed_keys = tuple(failed_keys)
        self.deleted_keys = tuple(deleted_keys)
        super().__init__("A exclusão de objetos no storage foi parcialmente concluída")


class ObjectStorageProviderError(ObjectStorageError):
    """Raised when R2 returns an invalid or unusable response."""
