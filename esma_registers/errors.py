"""The errors of esma-registers: one base, and the outcomes a caller may want to tell apart."""


class EsmaRegistersError(Exception):
    """Base of every error of the library."""


class InvalidIsin(EsmaRegistersError, ValueError):
    """The text is not an ISIN: twelve characters, a country prefix, nine letters or digits and a valid check digit."""


class RegisterUnavailable(EsmaRegistersError):
    """The register did not answer usably: a network error or a timeout, an HTTP error, a body that is not the
    register's JSON, or a search the register reported as failed. ``status`` is the HTTP status when there was one."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class RateLimited(RegisterUnavailable):
    """The register refused the call for too many requests (HTTP 429). ``retry_after`` is the wait it asked for, in
    seconds, when it said one. The library never waits or retries by itself: pacing is the caller's."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message, status=429)
        self.retry_after = retry_after
