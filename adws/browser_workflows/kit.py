"""The composition root: the one module that names concrete classes.

Everything else in browser_workflows is written against `interfaces/`; ADWs,
the menu and the recorder service ask a WorkflowKit for what they need and get
it back typed as an interface. Swapping an implementation is an edit here.
"""
from __future__ import annotations

from functools import cached_property
from pathlib import Path

from browser_session.detach_policies import StopDriverServiceDetach
from browser_session.endpoint_probe import DevToolsEndpointProbe
from browser_session.json_fetcher import UrllibJsonFetcher
from browser_session.headless_manager_factory import SeleniumHeadlessManagerFactory
from browser_session.interfaces.system import IClock
from browser_session.selenium_driver_factory import SeleniumAttachedDriverFactory
from browser_session.system_clock import SystemClock

from .editing.editor import RepositoryRecordingEditor
from .extension_identity import extension_origin
from .interfaces.browser import IBrowserAttachment, IBrowserLease
from .interfaces.editing import IRecordingEditor
from .interfaces.locating import IElementResolver
from .interfaces.playback import ( IApprovalGate, IConditionWaiter, IExecutionObserver, IPlaybackControl,
                                   IPlaybackExecutor, IPlaybackRecovery, IRepairApplier, IRepairVerifier,
                                   ISecretSource, IStepRunner )
from .interfaces.presenting import IPlaybackMatcher, IServiceLauncher
from .interfaces.normalizing import IActionNormalizer, ISensitivityPolicy
from .interfaces.playback import IWorkflowValidator
from .interfaces.presenting import IRecorderClient
from .interfaces.storage import ILocatorRepairStore, IPlaybackRunLog, IRecordingRepository, IRecordingSessionStore
from .normalizing.consequences import KeywordConsequenceClassifier
from .normalizing.mapper import RawEventMapper
from .normalizing.pipeline import RulePipelineNormalizer, default_rules
from .normalizing.scrubber import SensitiveValueScrubber
from .normalizing.sensitivity import RuleFileSensitivityPolicy
from .playback.attachment import ProbedBrowserAttachment
from .playback.commands import default_command_factory
from .playback.conditions import PollingConditionWaiter, default_condition_checks
from .playback.control import FilePlaybackControl
from .playback.download_directory import NativeDownloadDirectory
from .playback.executor import SequentialPlaybackExecutor
from .playback.executors import CommandActionExecutor, RetryingExecutor, SecretFillingExecutor
from .playback.lease import BrowserLease
from .playback.locating import PollingElementWaiter, RankedElementResolver, default_strategy_registry
from .playback.observers import CompositeObserver, RunLogObserver, StructuredLogObserver
from .playback.repair import ( ApprovedRepairApplier, DiagnosingRecovery, LocatingRepairVerifier, PageFailureDiagnoser, ProposingRecovery,
                               SimilarElementRepairProposer, TokenOverlapRanker )
from .playback.selenium_browser import ManagerBrowserSessionProvider
from .playback.step_runner import GuardedStepRunner
from .playback.validator import RuleSetValidator, default_validation_rules
from .presenting.matcher import WordPlaybackMatcher
from .recording.client import HttpRecorderClient
from .recording.controller import RecordingController
from .recording.extension_link import WebSocketExtensionLink
from .recording.guard import LoopbackRequestGuard
from .recording.launcher import DetachedServiceLauncher
from .recording.session import StoredRecordingSession
from .storage.database import DEFAULT_DB_PATH, SqliteDatabase
from .storage.recordings import SqliteRecordingRepository
from .storage.repairs import SqliteLocatorRepairStore
from .storage.runs import SqlitePlaybackRunLog
from .storage.sessions import SqliteRecordingSessionStore
from .structured_log import IStructuredLog, JsonLinesLog

DATA_DIR     = DEFAULT_DB_PATH.parent
DEFAULT_PORT = 8781


class WorkflowKit:
    def __init__( self, db_path: Path = DEFAULT_DB_PATH, port: int = DEFAULT_PORT,
                  extension_origins: set[ str ] | None = None, log_path: Path | None = None ) -> None:
        self._db_path  = Path( db_path )
        self.port      = port
        self._origins  = extension_origins if extension_origins is not None else { extension_origin()}
        self._log_path = Path( log_path ) if log_path else self._db_path.parent / "logs" / "recorder.jsonl"

    # -- storage -------------------------------------------------------------

    @cached_property
    def _database( self ) -> SqliteDatabase:
        return SqliteDatabase( self._db_path )

    def repository( self ) -> IRecordingRepository:
        return SqliteRecordingRepository( self._database )

    def session_store( self ) -> IRecordingSessionStore:
        return SqliteRecordingSessionStore( self._database )

    def run_log( self ) -> IPlaybackRunLog:
        return SqlitePlaybackRunLog( self._database )

    def repair_store( self ) -> ILocatorRepairStore:
        return SqliteLocatorRepairStore( self._database )

    # -- recording -----------------------------------------------------------

    def sensitivity( self ) -> ISensitivityPolicy:
        return RuleFileSensitivityPolicy()

    def normalizer( self ) -> IActionNormalizer:
        return RulePipelineNormalizer( default_rules( RawEventMapper(), self.sensitivity(), KeywordConsequenceClassifier()))

    def validator( self ) -> IWorkflowValidator:
        return RuleSetValidator( default_validation_rules())

    def structured_log( self, name: str = "recorder" ) -> IStructuredLog:
        return JsonLinesLog( self._log_path if name == "recorder" else self._log_path.with_name( f"{ name }.jsonl" ))

    def app( self ):
        """The recorder service's aiohttp application, fully wired."""
        from .recording.service import build_recorder_app      # aiohttp is only needed by the service
        link       = WebSocketExtensionLink()
        store      = self.session_store()
        controller = RecordingController( StoredRecordingSession( store ), store, link,
                                          SensitiveValueScrubber( self.sensitivity()), self.normalizer(),
                                          self.validator(), self.repository())
        guard = LoopbackRequestGuard( self.port, self._origins )
        return build_recorder_app( controller, link, guard, self.structured_log())

    def recorder_client( self ) -> IRecorderClient:
        return HttpRecorderClient( f"http://127.0.0.1:{ self.port }" )

    # -- playback ------------------------------------------------------------

    def clock( self ) -> IClock:
        return SystemClock()

    def download_directory( self ) -> str:
        return NativeDownloadDirectory( self._db_path.parent / "download_directory.txt" ).resolve()

    def browser_lease( self ) -> IBrowserLease:
        """Attaches to the user's debug Chrome through selenium-headless's
        WebBrowserManager; released by stopping chromedriver, never by quitting."""
        return BrowserLease( ManagerBrowserSessionProvider( SeleniumAttachedDriverFactory(),
                                                            SeleniumHeadlessManagerFactory(), StopDriverServiceDetach()))

    def resolver( self ) -> IElementResolver:
        return RankedElementResolver( default_strategy_registry())

    def playback_control( self ) -> IPlaybackControl:
        return FilePlaybackControl( self._db_path.parent / "control" )

    def repair_verifier( self ) -> IRepairVerifier:
        return LocatingRepairVerifier( self.resolver())

    def recovery( self, repairs: bool = True ) -> IPlaybackRecovery:
        ranker    = TokenOverlapRanker()
        diagnoser = PageFailureDiagnoser( self._db_path.parent / "evidence", ranker )
        if not repairs:
            return DiagnosingRecovery( diagnoser )
        return ProposingRecovery( diagnoser, SimilarElementRepairProposer( ranker ), self.repair_verifier(), self.repair_store())

    def repair_applier( self ) -> IRepairApplier:
        return ApprovedRepairApplier( self.repair_store(), self.repository())

    def step_runner( self, approvals: IApprovalGate, secrets: ISecretSource, clock: IClock | None = None,
                     repairs: bool = True ) -> IStepRunner:
        clock    = clock or self.clock()
        commands = default_command_factory()
        executor = RetryingExecutor( SecretFillingExecutor( CommandActionExecutor( commands ), secrets ), clock )
        return GuardedStepRunner( commands, PollingElementWaiter( self.resolver(), clock ), executor,
                                  self.condition_waiter( clock ), approvals, self.recovery( repairs ))

    def condition_waiter( self, clock: IClock | None = None ) -> IConditionWaiter:
        return PollingConditionWaiter( default_condition_checks(), clock or self.clock())

    def playback_executor( self, lease: IBrowserLease, approvals: IApprovalGate, secrets: ISecretSource,
                           observers: list[ IExecutionObserver ] | None = None,
                           clock: IClock | None = None ) -> IPlaybackExecutor:
        clock    = clock or self.clock()
        observer = CompositeObserver([ RunLogObserver( self.run_log()), StructuredLogObserver( self.structured_log( "playback" )),
                                       *( observers or [])])
        return SequentialPlaybackExecutor( lease, self.step_runner( approvals, secrets, clock ), self.validator(),
                                           self.run_log(), self.playback_control(), observer, clock )

    def attachment( self ) -> IBrowserAttachment:
        return ProbedBrowserAttachment( DevToolsEndpointProbe( UrllibJsonFetcher()), self.browser_lease )

    def service_launcher( self ) -> IServiceLauncher:
        adws = Path( __file__ ).resolve().parent.parent
        return DetachedServiceLauncher( self.recorder_client(), [ "uv", "run", "--quiet", str( adws / "adw_browser_recorder_service.py" )],
                                        adws.parent, self._db_path.parent / "logs" / "recorder_service.out", self.clock())

    # -- editing and choosing --------------------------------------------------

    def editor( self ) -> IRecordingEditor:
        return RepositoryRecordingEditor( self.repository())

    def matcher( self ) -> IPlaybackMatcher:
        return WordPlaybackMatcher()
