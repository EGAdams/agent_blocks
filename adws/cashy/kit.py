"""CashyKit: the composition root -- the one module that names Cashy's concretes.

It reuses the browser-workflow kit whole: the same SQLite file, the same
recordings, the same `execute_playback` and `parameterize_playback`
operations the Smart Menu runs, over the DevTools-protocol transport,
pinned to the tab the conversation is in.
"""
from __future__ import annotations

from pathlib import Path

from browser_session.models import DebugEndpoint
from browser_workflows.kit import DEFAULT_DB_PATH, WorkflowKit
from browser_workflows.normalizing.consequences import KeywordConsequenceClassifier
from browser_workflows.operations.catalog import operations as workflow_operations
from browser_workflows.presenting.terminal import TerminalConsole
from browser_workflows.storage.database import SqliteDatabase
from cdp_browser.choosers import PreferredTargetChooser

from .actions.bank_actions import LoggedInAction, OpenBankAction
from .actions.learning_actions import RememberAction
from .actions.page_actions import ClickAction, ClickNumberAction, GoBackAction, GoToAction, ListOptionsAction, TryAnotherAction
from .actions.question_actions import AnswerAction, ConfirmAction, DenyAction
from .actions.registry import MapActionRegistry
from .actions.session_actions import HelpAction, PauseAction, ResumeAction, StatusAction, StopAction, UnknownAction
from .actions.statement_actions import GetStatementAction
from .actions.transaction_actions import FindTransactionAction
from .banks.directory import WordBankDirectory
from .banks.profiles import ALL_BANKS
from .connection import PinnedTabConnection
from .launching import AdwDebugChromeLauncher, LaunchingConnection
from .conversation.interpreter import RuleChainInterpreter
from .conversation.rules import default_rules
from .documents.folder import FileDownloadVerifier, FolderDownloadWatcher
from .finance.transfers import TextTransferRecords
from .interfaces.observing import ICashyObserver
from .learning.learner import TrailWorkflowLearner
from .learning.memory import LanceDBProcedureMemory
from .learning.resolver import FirstResolver, RegistryResolver, StatementCatalogResolver
from .learning.saver import RegisteringWorkflowSaver
from .mediator import CashyAgent
from .models import BankProfile
from .navigation.clicker import GuardedOptionClicker
from .navigation.explorer import GuidedExplorer
from .navigation.playback import OperationPlaybackRunner
from .navigation.statements import CalendarStatementLocator
from .observing.mechanic import CaseFileReporter
from .observing.observers import JsonlCashyObserver
from .page.arrival import HeadingArrivalCheck
from .presenting.desk import CashyDesk, HttpDebugChromeProbe
from .presenting.handoff import WordHandoffDetector
from .page.ranker import TokenOptionRanker
from .page.reader import DomPageReader
from .banks.profiles import COMMON_GOALS
from .page.risk import NamedDestinationRiskPolicy, ReadOnlyAwareRiskPolicy
from .session.machine import TableStateMachine
from .session.states import ALL_STATES
from .storage.accounts import SqliteAccountBanks
from .storage.database import cashy_database
from .storage.downloads import SqliteDownloadLedger
from .storage.sanitizer import MaskingSanitizer
from .storage.sessions import SqliteSessionStore
from .storage.workflows import SqliteWorkflowRegistry
from .turn import CashyServices

ADWS_DIR       = Path( __file__ ).resolve().parent.parent
DATA_DIR       = ADWS_DIR / "adw_data" / "cashy"
TRANSFERS_FILE = Path( "/home/adamsl/rol_finances/readable_documents/transfer_pay_activity.txt" )


class CashyKit:
    def __init__( self, port: int = 9222, db_path: Path = DEFAULT_DB_PATH, data_dir: Path = DATA_DIR,
                  transfers_file: Path = TRANSFERS_FILE, observers: list[ ICashyObserver ] | None = None,
                  banks: list[ BankProfile ] | None = None, launch_chrome: bool = False ) -> None:
        self.port        = port
        self._launch     = launch_chrome           # start the debug Chrome when it isn't up
        self._db         = cashy_database( SqliteDatabase( db_path ))
        self.data_dir    = Path( data_dir )
        self._transfers  = Path( transfers_file )
        self._banks      = list( banks or ALL_BANKS )
        self._observers  = observers if observers is not None else [ JsonlCashyObserver( self.data_dir / "events.jsonl" )]
        self.connection  = PinnedTabConnection( DebugEndpoint( port=port ))
        self.workflows   = WorkflowKit( db_path, transport="cdp",
                                        cdp_chooser=PreferredTargetChooser( self.connection.preference ))
        self.connection.use( self.workflows.browser_provider())
        self._ops        = workflow_operations( self.workflows, TerminalConsole())

    # -- storage -----------------------------------------------------------------

    def registry( self ) -> SqliteWorkflowRegistry:
        return SqliteWorkflowRegistry( self._db )

    def ledger( self ) -> SqliteDownloadLedger:
        return SqliteDownloadLedger( self._db )

    def memory( self ) -> LanceDBProcedureMemory:
        return LanceDBProcedureMemory( self.data_dir / "lancedb" )

    def sessions( self ) -> SqliteSessionStore:
        return SqliteSessionStore( self._db, MaskingSanitizer())

    def banks( self ) -> WordBankDirectory:
        return WordBankDirectory( self._banks )

    # -- the agent ---------------------------------------------------------------

    def services( self ) -> CashyServices:
        wk         = self.workflows
        registry   = self.registry()
        memory     = self.memory()
        statements = CalendarStatementLocator( wk.period_vocabulary())
        repository = wk.repository()
        return CashyServices(
            banks=self.banks(), machine=TableStateMachine( ALL_STATES ), actions=self.actions(),
            connection=self.attaching(), reader=DomPageReader(), ranker=TokenOptionRanker(),
            risk=self.risk(), arrival=HeadingArrivalCheck(),
            resolver_of_elements=wk.resolver(), clicker=GuardedOptionClicker(), explorer=GuidedExplorer(),
            playback=OperationPlaybackRunner( self._ops[ "execute_playback" ], registry, wk.period_vocabulary()),
            workflows=FirstResolver([ RegistryResolver( registry, repository ),
                                      StatementCatalogResolver( wk.statement_catalog(), repository )]),
            saver=RegisteringWorkflowSaver( TrailWorkflowLearner( statements ), repository,
                                            self._ops[ "parameterize_playback" ], wk.binder(), registry, memory,
                                            wk.period_vocabulary()),
            registry=registry, memory=memory, statements=statements,
            watcher=FolderDownloadWatcher( wk.download_directory ), verifier=FileDownloadVerifier(),
            ledger=self.ledger(), transfers=TextTransferRecords( self._transfers ),
            accounts=SqliteAccountBanks( self._db ), reporter=CaseFileReporter( self.data_dir / "mechanic" ),
            observers=self._observers, periods=wk.period_vocabulary(), download_directory=wk.download_directory,
            port=self.port )

    def attaching( self ) -> PinnedTabConnection | LaunchingConnection:
        if not self._launch:
            return self.connection
        return LaunchingConnection( self.connection, HttpDebugChromeProbe( port=self.port ),
                                    AdwDebugChromeLauncher( port=self.port ))

    def risk( self ) -> NamedDestinationRiskPolicy:
        """Every destination word the profiles name, except how to download."""
        goals = [ COMMON_GOALS ] + [ b.goals for b in self._banks ]
        words = { w for g in goals for key, v in g.items() if key != "download" for w in v.words }
        return NamedDestinationRiskPolicy( ReadOnlyAwareRiskPolicy( KeywordConsequenceClassifier()), words )

    def actions( self ) -> MapActionRegistry:
        return MapActionRegistry({
            "open_bank": OpenBankAction(), "logged_in": LoggedInAction(), "go_to": GoToAction(),
            "click": ClickAction(), "click_number": ClickNumberAction(), "list_options": ListOptionsAction(),
            "go_back": GoBackAction(), "try_another": TryAnotherAction(), "stop": StopAction(),
            "pause": PauseAction(), "resume": ResumeAction(), "remember": RememberAction(),
            "save_adw": RememberAction(), "get_statement": GetStatementAction(),
            "find_transaction": FindTransactionAction(), "status": StatusAction(), "help": HelpAction(),
            "confirm": ConfirmAction(), "deny": DenyAction(), "answer": AnswerAction(),
        }, fallback=UnknownAction())

    def agent( self ) -> CashyAgent:
        interpreter = RuleChainInterpreter( default_rules( self.banks(), self.workflows.period_parser()))
        return CashyAgent( interpreter, self.sessions(), self.services())

    def desk( self, session_id: str = "default" ) -> CashyDesk:
        """The conversation the dashboard's sidecar serves."""
        return CashyDesk( self.agent(), self.sessions(), WordHandoffDetector(),
                          HttpDebugChromeProbe( port=self.port ), session_id=session_id, port=self.port )
