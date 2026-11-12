"""Infrastructure failures exposed to callers without database details."""


class DatabaseUnavailable(Exception):
    def __init__(self, retry_after=1):
        super().__init__("Database temporarily unavailable")
        self.retry_after = retry_after


class CircuitOpen(DatabaseUnavailable):
    pass
