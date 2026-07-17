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


class FileNotFoundInCatalogError(AppError):
    """Raised when a requested catalog document does not exist."""
