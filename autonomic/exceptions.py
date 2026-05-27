# Exception hierarchy for transport, protocol, and command failures.
from __future__ import annotations


class AutonomicError(Exception):
    """Base exception for SDK errors."""


class NotConnectedError(AutonomicError):
    """Raised when a connection is required but unavailable."""


class ConnectionClosedError(AutonomicError):
    """Raised when the device closes the socket unexpectedly."""


class AutonomicTimeoutError(AutonomicError, TimeoutError):
    """Raised when a socket read times out."""


class ProtocolError(AutonomicError, ValueError):
    """Raised when a protocol line cannot be parsed."""


class CommandError(AutonomicError):
    """Raised when a command response indicates failure."""
