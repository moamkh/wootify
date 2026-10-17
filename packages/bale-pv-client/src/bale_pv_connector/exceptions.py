"""Custom exceptions for Bale gRPC client."""


class BaleRpcError(Exception):
    """Base exception for Bale RPC errors."""

    def __init__(self, message: str, code: int = None, *, delivery_rejected: bool = False):
        super().__init__(message)
        self.message = message
        self.code = code
        # Only a matched server-side negative ACK establishes definite
        # rejection. A timeout/locally constructed RPC error cannot do so.
        self.delivery_rejected = delivery_rejected


class BaleAuthError(BaleRpcError):
    """Authentication-specific error."""
    pass


class BaleNotImplementedError(BaleRpcError):
    """Raised when a feature is not yet implemented in this client."""
    pass


class BaleConnectionError(BaleRpcError):
    """WebSocket or HTTP connection error."""
    pass


class BaleRequestNotDispatchedError(ConnectionError):
    """Transport was unavailable before a request could leave this process."""

    retryable = True
    delivery_uncertain = False


class BaleRequestUnconfirmedError(ConnectionError):
    """A request may have reached Bale, but no successful RPC reply was received.

    Retrying automatically could create duplicates. A generated request RID
    alone is not proof that Bale accepted a message.
    """

    retryable = False
    delivery_uncertain = True
