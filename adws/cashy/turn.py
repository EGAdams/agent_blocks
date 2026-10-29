"""Turn: everything one message needs, and what it leaves behind.

A turn holds the session, the services (the interfaces the composition root
filled), the reply being built and -- attached only if something asks for
it -- the browser. The browser is always released at the end of a turn:
Cashy holds no socket between messages, and never closes a tab.
"""
from __future__ import annotations

from collections.abc import Callable

from browser_workflows.interfaces.locating import IElementResolver
from browser_workflows.interfaces.templating import IPeriodVocabulary

from .interfaces.banks import IBankDirectory
from .interfaces.browser import IBrowserConnection, ICashyBrowser
from .interfaces.documents import IDownloadLedger, IDownloadVerifier, IDownloadWatcher
from .interfaces.finance import IAccountBanks, ITransferRecords
from .interfaces.learning import IProcedureMemory, IWorkflowRegistry, IWorkflowResolver, IWorkflowSaver
from .interfaces.navigation import IExplorer, IFailureReporter, IOptionClicker, IPlaybackRunner, IStatementLocator
from .interfaces.observing import ICashyObserver
from .interfaces.page import IArrivalCheck, IDomReader, IOptionRanker, IRiskPolicy
from .interfaces.session import IActionRegistry, ISessionStateMachine
from .models import Activity, BankProfile, CashyEvent, CashySession, Intent, PageSnapshot, Question, SessionStateName


class CashyServices:
    """The bundle a turn reaches into. Filled by the composition root only."""

    def __init__( self, *, banks: IBankDirectory, machine: ISessionStateMachine, actions: IActionRegistry,
                  connection: IBrowserConnection, reader: IDomReader, ranker: IOptionRanker, risk: IRiskPolicy,
                  arrival: IArrivalCheck, resolver_of_elements: IElementResolver, clicker: IOptionClicker,
                  explorer: IExplorer, playback: IPlaybackRunner, workflows: IWorkflowResolver,
                  saver: IWorkflowSaver, registry: IWorkflowRegistry, memory: IProcedureMemory,
                  statements: IStatementLocator, watcher: IDownloadWatcher, verifier: IDownloadVerifier,
                  ledger: IDownloadLedger, transfers: ITransferRecords, accounts: IAccountBanks,
                  reporter: IFailureReporter, observers: list[ ICashyObserver ], periods: IPeriodVocabulary,
                  download_directory: Callable[[], str ], port: int ) -> None:
        self.banks      = banks
        self.machine    = machine
        self.actions    = actions
        self.connection = connection
        self.reader     = reader
        self.ranker     = ranker
        self.risk       = risk
        self.arrival    = arrival
        self.elements   = resolver_of_elements
        self.clicker    = clicker
        self.explorer   = explorer
        self.playback   = playback
        self.workflows  = workflows
        self.saver      = saver
        self.registry   = registry
        self.memory     = memory
        self.statements = statements
        self.watcher    = watcher
        self.verifier   = verifier
        self.ledger     = ledger
        self.transfers  = transfers
        self.accounts   = accounts
        self.reporter   = reporter
        self.observers  = observers
        self.periods    = periods
        self.download_directory = download_directory
        self.port       = port


class Turn:
    def __init__( self, session: CashySession, services: CashyServices, actor: str = "user" ) -> None:
        self.session  = session
        self.services = services
        self.actor    = actor
        self.lines:  list[ str ] = []
        self.events: list[ CashyEvent ] = []
        self.ok      = True
        self.halted  = False                    # asked a question or failed: later sentences wait
        self._browser: ICashyBrowser | None = None
        self._downloads_set = False

    # -- context -----------------------------------------------------------------

    @property
    def bank( self ) -> BankProfile | None:
        return self.services.banks.by_key( self.session.bank ) if self.session.bank else None

    # -- the reply ---------------------------------------------------------------

    def say( self, *lines: str ) -> None:
        """A line already just said (two sentences reaching the same page) is said once."""
        for line in lines:
            if not self.lines or self.lines[ -1 ] != line:
                self.lines.append( line )

    def fail( self, line: str ) -> None:
        self.ok, self.halted = False, True
        self.say( line )
        self.event( "failed", line )

    def event( self, activity: Activity | SessionStateName, detail: str = "" ) -> None:
        happened = CashyEvent( activity=activity, detail=detail )
        self.events.append( happened )
        for observer in self.services.observers:
            observer.notify( self.session.id, happened )

    # -- the conversation ----------------------------------------------------------

    def handle( self, intent: Intent ) -> None:
        self.services.machine.state( self.session.state ).handle( intent, self )

    def run( self, intent: Intent ) -> None:
        """Run an intent's command directly (a state already decided it may)."""
        self.services.actions.action( intent.kind ).run( intent, self )

    def move( self, to: SessionStateName ) -> None:
        if to != self.session.state:
            self.services.machine.move( self.session, to )
            self.event( to )

    def ask( self, question: Question ) -> None:
        if self.session.state != "awaiting_user_input":
            self.session.asked_from = self.session.state
        self.session.question = question
        self.say( question.prompt )
        self.halted = True
        self.move( "awaiting_user_input" )

    # -- the browser -----------------------------------------------------------------

    def browser( self ) -> ICashyBrowser:
        if self._browser is None:
            self.event( "connecting", f"debug Chrome on port { self.services.port }" )
            hosts = self.bank.hosts if self.bank else []
            self._browser = self.services.connection.attach( self.session.target_id, hosts )
            self.session.target_id = self._browser.target_id
        return self._browser

    def prepare_downloads( self ) -> str:
        directory = self.services.download_directory()
        if not self._downloads_set:
            self.browser().configure_downloads( directory )
            self._downloads_set = True
        return directory

    def read_page( self ) -> PageSnapshot:
        self.event( "inspecting_dom" )
        snapshot = self.services.reader.snapshot( self.browser(), self.bank )
        self.session.options = snapshot.options
        self.session.url     = snapshot.url
        self.session.target_id = self.browser().target_id
        return snapshot

    def release( self ) -> None:
        """Let go of the browser (the next playback or turn attaches afresh)."""
        if self._browser is not None:
            self._browser.release()
            self._browser = None
            self._downloads_set = False
