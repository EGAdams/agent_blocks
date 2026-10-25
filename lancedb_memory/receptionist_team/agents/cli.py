"""`python -m receptionist_team.agents.cli` -- inspect the agent folders.

    cd lancedb_memory
    .venv/bin/python -m receptionist_team.agents.cli list
    .venv/bin/python -m receptionist_team.agents.cli show toyota
    .venv/bin/python -m receptionist_team.agents.cli validate
    .venv/bin/python -m receptionist_team.agents.cli show toyota --trace

No LLM, no network, no LanceDB -- it only reads the folders, so it is the
fastest way to find out why a new agent isn't being picked up.

Structure follows adw_interface_prompt.py: `IArgumentParser` turns argv into a
frozen request value, one class per command, and a composition root at the
bottom that is the only place naming concretes. `IApplication`,
`IArgumentParser`, `ILifecycleLog` and `LifecycleLogged` are imported from the
template via ../../sssf_interfaces rather than restated.

`IPhase`/`IPhaseExecutor` are deliberately *not* used: their contract is
`execute( run: Run, phase )`, which would make a read-only folder listing
create an SSSF session and a tracer database. The right time to adopt them is
when these commands become steps of a real ADW.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TextIO

from sssf_interfaces import IApplication, IArgumentParser, ILifecycleLog, LifecycleLogged

from . import build_catalog
from .catalog import UnknownAgent
from .interfaces import IAgentCatalog, IAgentDefinition
from .layout import PART_DIRS
from .lifecycle import SilentLifecycleLog, StdoutLifecycleLog


@dataclass( frozen=True )
class CatalogRequest:
    """Parsed command-line intent. A value, not a behavior -- no interface needed."""
    command: str
    agent: str | None
    root: Path | None
    trace: bool


class IReport( Protocol ):
    """Where a command's output goes. One line at a time, no formatting knowledge."""

    def line( self, text: str = "" ) -> None: ...


class TextReport:
    """IReport over a text stream."""

    def __init__( self, stream: TextIO | None = None ) -> None:
        self._stream = stream if stream is not None else sys.stdout

    def line( self, text: str = "" ) -> None:
        print( text, file=self._stream )


class ICommand( Protocol ):
    """One thing the CLI can do. Returns a process exit code."""

    def act( self, catalog: IAgentCatalog, report: IReport ) -> int: ...


class ListAgentsCommand( LifecycleLogged ):
    """Every agent folder, one per line, with its description."""

    def __init__( self, logger: ILifecycleLog ) -> None:
        LifecycleLogged.__init__( self, logger )

    def act( self, catalog: IAgentCatalog, report: IReport ) -> int:
        names = catalog.names()
        if not names:
            report.line( "No agent folders found." )
            return 1
        for name in names:
            description = catalog.definition( name ).model_configuration().description
            report.line( f"{ name:<24} { description }" )
        return 0


class ShowAgentCommand( LifecycleLogged ):
    """One agent in full: prompts, placeholders, model config, tools, docs."""

    def __init__( self, agent: str, logger: ILifecycleLog ) -> None:
        LifecycleLogged.__init__( self, logger )
        self._agent = agent

    def act( self, catalog: IAgentCatalog, report: IReport ) -> int:
        try:
            definition = catalog.definition( self._agent )
        except UnknownAgent as error:
            report.line( str( error ))
            return 1

        configuration = definition.model_configuration()
        report.line( f"agent:            { definition.name }" )
        report.line( f"description:      { configuration.description }" )
        report.line( f"model:            { configuration.model or '(composition root decides)' }" )
        report.line( f"max_turns:        { configuration.max_turns }" )
        report.line( f"permission_mode:  { configuration.permission_mode }" )
        report.line( f"allowed_tools:    { ', '.join( configuration.allowed_tools ) or '(none)' }" )
        report.line( f"output_path:      { configuration.output_path or '(none)' }" )
        if configuration.notes:
            report.line( f"notes:            { configuration.notes }" )

        report.line()
        self._describe_template( "system prompt", definition.system_prompt(), report )
        self._describe_template( "user prompt", definition.user_prompt(), report )

        report.line()
        tools = definition.tools()
        report.line( f"tools ({ len( tools )}):" )
        for tool in tools:
            report.line( f"  { tool.name:<20} { tool.kind }/{ tool.status }  { tool.description }" )
        if not tools:
            report.line( "  (none declared)" )

        documentation = definition.documentation()
        report.line()
        report.line( f"documentation:    { len( documentation.present )} written, "
                     f"{ len( documentation.empty )} empty" )
        return 0

    def _describe_template( self, label: str, template, report: IReport ) -> None:
        try:
            raw = template.raw()
        except FileNotFoundError:
            report.line( f"{ label }: MISSING ({ getattr( template, 'path', '?' )})" )
            return
        placeholders = ", ".join( template.placeholders()) or "(none)"
        report.line( f"{ label }: { len( raw )} chars, placeholders: { placeholders }" )


class ValidateCommand( LifecycleLogged ):
    """What each agent folder is still missing. Exit 1 if any agent is incomplete.

    Checks the part directories *and* the two prompt files, because the
    directories are what a `mkdir` leaves behind and the files are what makes
    the folder a usable definition -- a folder with all five directories and
    no prompts at all (as `toyota_mechanic` was until its migration finished)
    reporting "ok" would be a lie.
    """

    def __init__( self, logger: ILifecycleLog ) -> None:
        LifecycleLogged.__init__( self, logger )

    def act( self, catalog: IAgentCatalog, report: IReport ) -> int:
        names = catalog.names()
        if not names:
            report.line( "No agent folders found." )
            return 1
        incomplete = 0
        for name in names:
            missing = self._missing( catalog.definition( name ))
            if missing:
                incomplete += 1
                report.line( f"{ name:<24} missing: { ', '.join( missing )}" )
            else:
                report.line( f"{ name:<24} ok" )
        report.line()
        report.line( f"{ len( names ) - incomplete }/{ len( names )} complete" )
        return 1 if incomplete else 0

    def _missing( self, definition: IAgentDefinition ) -> list[ str ]:
        missing = [ part for part in PART_DIRS if not ( definition.root / part ).is_dir() ]
        for label, template in (
            ( "system prompt", definition.system_prompt()),
            ( "user prompt", definition.user_prompt()),
        ):
            try:
                template.raw()
            except FileNotFoundError:
                missing.append( label )
        return missing


class CatalogArgumentParser( LifecycleLogged ):
    """IArgumentParser: the only class here that knows this CLI is argparse."""

    def __init__( self, description: str, logger: ILifecycleLog ) -> None:
        LifecycleLogged.__init__( self, logger )
        self._description = description

    def parse( self, argv: list[ str ] ) -> CatalogRequest:
        parser = argparse.ArgumentParser(
            description=self._description,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        parser.add_argument( "command", choices=[ "list", "show", "validate" ] )
        parser.add_argument( "agent", nargs="?", default=None, help="required by `show`" )
        parser.add_argument( "--root", default=None, help="an agents/ directory other than this one" )
        parser.add_argument( "--trace", action="store_true", help="report object lifecycles" )
        parsed = parser.parse_args( argv )
        return CatalogRequest(
            command=parsed.command,
            agent=parsed.agent,
            root=Path( parsed.root ) if parsed.root else None,
            trace=parsed.trace,
        )


class ICommandFactory( Protocol ):
    def command( self, request: CatalogRequest, logger: ILifecycleLog ) -> ICommand: ...


class CatalogCommandFactory:
    """Maps a request to its command. A registry, not an if/elif chain -- a new
    command is one dict entry plus one class, same posture as
    adapters/embedding_profiles.py's PROFILE_BUILDERS."""

    def command( self, request: CatalogRequest, logger: ILifecycleLog ) -> ICommand:
        builders = {
            "list": lambda: ListAgentsCommand( logger ),
            "validate": lambda: ValidateCommand( logger ),
            "show": lambda: ShowAgentCommand( request.agent or "", logger ),
        }
        return builders[ request.command ]()


class AgentCatalogApplication( LifecycleLogged ):
    """IApplication: parse, build the catalog, run one command. Interfaces only."""

    def __init__(
        self,
        argument_parser: IArgumentParser,
        command_factory: ICommandFactory,
        report: IReport,
        logger: ILifecycleLog,
    ) -> None:
        LifecycleLogged.__init__( self, logger )
        self._argument_parser = argument_parser
        self._command_factory = command_factory
        self._report = report

    def run( self, argv: list[ str ] ) -> int:
        request = self._argument_parser.parse( argv )
        if request.command == "show" and not request.agent:
            self._report.line( "show needs an agent name, e.g. `show toyota`" )
            return 2

        logger = StdoutLifecycleLog() if request.trace else SilentLifecycleLog()
        catalog = build_catalog( root=request.root, logger=logger )
        return self._command_factory.command( request, logger ).act( catalog, self._report )


def build_application() -> IApplication:
    """Wire the concrete adapters into the application. The one spot that may."""
    bootstrap_log: ILifecycleLog = SilentLifecycleLog()
    return AgentCatalogApplication(
        argument_parser = CatalogArgumentParser( __doc__, bootstrap_log ),
        command_factory = CatalogCommandFactory(),
        report          = TextReport(),
        logger          = bootstrap_log,
    )


if __name__ == "__main__":
    sys.exit( build_application().run( sys.argv[ 1: ]))
