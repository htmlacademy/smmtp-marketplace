"""The failure every command reports as the "error" object of its output."""

from __future__ import annotations


class Fault(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def as_object(self):
        return {"code": self.code, "message": self.message, **self.details}
