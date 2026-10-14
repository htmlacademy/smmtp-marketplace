"""The failure every command reports: a code the caller branches on and a
message that names the next step."""

from __future__ import annotations


class Fault(Exception):
    def __init__(self, code: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def as_object(self) -> dict:
        return {"code": self.code, "message": self.message, **self.details}


class UsageFault(Fault):
    """A command line refused before any request is sent."""

    def __init__(self, message: str) -> None:
        super().__init__("usage", message)
