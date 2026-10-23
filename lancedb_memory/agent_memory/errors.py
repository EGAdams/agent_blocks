"""Errors that cross the port boundary.

Separate from ports.py, which stays Protocol-only, and from models.py, which
is pydantic wire data. An adapter raises these; a decorator reads them.
"""


class ModelUnavailable( RuntimeError ):
    """The model refused and waiting a moment will not help.

    `retry_after` is how long the provider says to wait, in seconds, when it
    says at all. A daily token limit reports minutes -- retrying five times
    over fifteen seconds cannot clear it, and burns fifteen seconds of the
    user's time to produce a worse message than the provider already gave us.
    RetryingModel reads this and gives up straight away.
    """

    def __init__( self, message: str, retry_after: float | None = None ) -> None:
        super().__init__( message )
        self.retry_after = retry_after
