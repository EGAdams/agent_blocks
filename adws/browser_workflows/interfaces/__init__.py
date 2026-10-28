"""Every interface in browser_workflows, one module per concern:

  browser      IWorkflowElement, IBrowserAdapter and its role slices, IBrowserLease
  locating     ILocatorStrategy, ILocatorStrategyRegistry, IElementResolver, IElementWaiter
  recording    IRecordingState, IRecordingSession, IExtensionLink, IRecordingController, IRequestGuard
  normalizing  IBrowserEventCapture, ISensitivityPolicy, IConsequenceClassifier,
               INormalizationRule, IEventMapper, IActionNormalizer
  storage      IPlaybackRepository, IRecordingRepository, IRecordingSessionStore,
               IPlaybackRunLog, ILocatorRepairStore
  playback     IActionCommand, IActionCommandFactory, IActionExecutor, IConditionWaiter,
               IApprovalGate, ISecretSource, IPlaybackControl, IExecutionObserver,
               IWorkflowValidator, IValidationRule, IFailureDiagnoser, IRepairProposer,
               IRepairVerifier, IPlaybackRecovery, IPlaybackState, IPlaybackExecutor,
               IConditionCheck, IStepRunner, ISimilarityRanker, IRepairApplier
  editing      IRecordingEditor
  presenting   IPlaybackMenuProvider, IConsole, IWorkspaceActions, IRecorderClient,
               IServiceLauncher, IPlaybackMatcher
"""
