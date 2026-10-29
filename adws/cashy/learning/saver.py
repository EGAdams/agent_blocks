"""RegisteringWorkflowSaver: IWorkflowSaver -- learn, register, remember.

1. the trail becomes steps (IWorkflowLearner);
2. they are saved as an ordinary recording, so the Smart Menu can play,
   inspect and edit them like any other;
3. a statement trail is turned into a template by the existing
   `parameterize_playback` operation, so it fetches any month;
4. the version is registered as a candidate -- a later live run that
   succeeds marks it verified -- and an older version is never touched;
5. LanceDB is told, in a sentence, which version does what.
"""
from __future__ import annotations

from agent_comms.operations.base import IOperation
from browser_workflows.errors import BrowserWorkflowError
from browser_workflows.interfaces.storage import IRecordingRepository
from browser_workflows.interfaces.templating import IPeriodVocabulary, IRecordingBinder
from browser_workflows.models import Period

from ..interfaces.learning import IProcedureMemory, IWorkflowLearner, IWorkflowRegistry
from ..models import CashySession, ProcedureMemory, WorkflowVersion

PERIOD_BLANKS = ( "month_name", "month_abbr", "month_2", "month", "year", "yy" )


class RegisteringWorkflowSaver:
    def __init__( self, learner: IWorkflowLearner, repository: IRecordingRepository, parameterize: IOperation,
                  binder: IRecordingBinder, registry: IWorkflowRegistry, memory: IProcedureMemory,
                  periods: IPeriodVocabulary ) -> None:
        self._learner      = learner
        self._repository   = repository
        self._parameterize = parameterize
        self._binder       = binder
        self._registry     = registry
        self._memory       = memory
        self._periods      = periods

    def save( self, session: CashySession, period: Period | None ) -> WorkflowVersion:
        steps = self._learner.steps( session )
        if not session.bank or not session.task.goal:
            raise BrowserWorkflowError( "there's nothing to remember yet: no bank task has been done" )
        if not steps:
            raise BrowserWorkflowError( "there's nothing to remember yet: I haven't clicked anything this task" )
        bank, goal = session.bank, session.task.goal
        version    = self._registry.next_version( bank, goal )
        name       = f"cashy_{ bank }_{ goal }_v{ version }"
        recorded   = self._repository.create( f"{ name }_recorded", steps,
                                              description=f"Cashy's trail for { goal } at { bank }, as she walked it" )
        playback, needs, note = recorded.name, [], f"learned from { len( steps ) - 1 } click(s)"
        if period:
            v = self._periods.values( period )
            outcome = self._parameterize.run({ "name": recorded.name, "example": f"{ v[ 'month_name' ]} { v[ 'year' ]}",
                                               "new_name": name, "yes": True })
            template = self._repository.by_name( name ) if outcome.ok else None
            needs    = self._binder.placeholders( template ) if template else []
            if any( n in PERIOD_BLANKS for n in needs ):
                playback = name
            else:
                note += "; the month could not be made a blank, so it only fetches the month it learned"
        saved = self._registry.add( WorkflowVersion( bank=bank, goal=goal, version=version, playback=playback,
                                                     start_url=steps[ 0 ].url, needs=needs, note=note ))
        clicks = " → ".join( s.text for s in session.trail if s.action == "click" and not s.undone )
        self._memory.remember( ProcedureMemory( bank=bank, goal=goal, playback=playback, version=version,
                                                summary=f"{ goal.replace( '_', ' ' ) } at { bank }: { clicks }" ))
        return saved
