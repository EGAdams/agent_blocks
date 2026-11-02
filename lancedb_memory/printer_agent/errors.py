"""The printer agent's errors. Every message is safe to show: it names a rule, an id or a code, never content."""
from __future__ import annotations


class PrinterError( Exception ):
    """Base for everything the workflow reports as a refusal or failure."""


class RequestRefused( PrinterError ):
    """The request itself is not acceptable (shape, limits, unknown printer or document). Nothing was sent."""


class PrinterBusy( PrinterError ):
    """Another print job holds the one print slot on this machine."""


class SpoolerRefused( PrinterError ):
    """The spooler rejected the job before anything was printed (for example, the queue no longer exists)."""


class SpoolerError( PrinterError ):
    """The spooler call failed in a way that does not prove nothing printed. Treated as ambiguous."""


class StaleState( PrinterError ):
    """A job was not in the state a change expected, because something else moved it first."""
