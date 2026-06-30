"""Custom exceptions for Bale gRPC client."""


class BaleRpcError(Exception):
    """Base exception for Bale RPC errors."""

    def __init__(self, message: str, code: int = None):
        super().__init__(message)
        self.message = message
        self.code = code


class BaleAuthError(BaleRpcError):
    """Authentication-specific error."""
    pass


class BaleNotImplementedError(BaleRpcError):
    """Raised when a feature is not yet implemented in this client."""
    pass


class BaleConnectionError(BaleRpcError):
    """WebSocket or HTTP connection error."""
    pass
