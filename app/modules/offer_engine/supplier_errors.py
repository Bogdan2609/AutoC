"""Errors exposed by the single live supplier adapter boundary."""

class Unavailable(Exception):
    """Legacy compatibility base. Do not leak upstream payloads into API errors."""

class NetworkError(Unavailable):
    """Transient network failure, safe to retry for *reads* only."""

class UpstreamBusinessError(Unavailable):
    """Upstream business rejection or authentication/4xx; never retry."""

class MalformedResponseError(Unavailable):
    """A response that does not conform to the verified supplier contract."""

class UnsupportedCurrencyError(MalformedResponseError):
    """Unknown or non-RUB quote; never convert or assume RUB."""
