"""Bot exceptions"""

from enum import Enum


class BrowserLaunchError(Exception):
    """Raised when Chromium fails to launch, with classified failure kind."""

    class Kind(Enum):
        RESOURCE_PRESSURE = "resource_pressure"
        CONFIGURATION = "configuration"
        TRANSIENT = "transient"

    def __init__(self, message: str, kind: Kind, original: Exception | None = None):
        super().__init__(message)
        self.kind = kind
        if original:
            self.__cause__ = original


class QuotaExhaustedError(Exception):
    """Raised when a user's free usage allowance is exhausted."""

    pass


class QueueFullError(Exception):
    """Raised when the crawl queue is at capacity."""

    pass


class DuplicateCrawlError(Exception):
    """Raised when a user already has a crawl in progress or queued."""

    pass


class QueueExpiredError(Exception):
    """Raised when a queued job exceeds the maximum wait time."""

    pass