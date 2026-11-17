"""Stable, non-secret errors crossing the service IPC boundary."""

class AppError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)

class Conflict(AppError):
    def __init__(self, message: str = "The note changed. Reload before applying this change."):
        super().__init__("CONFLICT", message)

class Busy(AppError):
    def __init__(self, message: str = "A recording or delivery is already active."):
        super().__init__("BUSY", message)
