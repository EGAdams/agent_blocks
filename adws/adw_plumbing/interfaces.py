"""The interfaces adw_plumbing's pieces satisfy."""
from __future__ import annotations

from typing import Protocol

from sssf_roles import ILifecycleLog

from adw_modules.runner import Run


class ISessionLogFactory( Protocol ):
    """Makes the lifecycle log a run's phases write to."""

    def create( self, run: Run ) -> ILifecycleLog: ...


class IMessageSink( Protocol ):
    """Where the operator's one line goes, besides the terminal."""

    def deliver( self, message: str ) -> None: ...


class IMessageSinkFactory( Protocol ):
    """Turns a `--message-out` value into a sink: a file, or nowhere."""

    def for_path( self, path: str ) -> IMessageSink: ...
