"""IWorkflowValidator: a Composite of IValidationRule, each checking one thing.
Run before a recording is saved and before every playback."""
from __future__ import annotations

from ..interfaces.playback import IValidationRule
from ..models import Recording, ValidationIssue, ValidationReport

ELEMENT_ACTIONS = ( "click", "enter_text", "select_option", "set_checked" )


class HasName:
    def check( self, recording: Recording, report: ValidationReport ) -> None:
        if not recording.name.strip():
            report.issues.append( ValidationIssue( severity="error", message="the playback has no name" ))


class HasSteps:
    def check( self, recording: Recording, report: ValidationReport ) -> None:
        if not recording.steps:
            report.issues.append( ValidationIssue( severity="error", message="nothing was recorded" ))


class StartsOnAPage:
    def check( self, recording: Recording, report: ValidationReport ) -> None:
        if recording.steps and recording.steps[ 0 ].action_type != "navigate":
            report.issues.append( ValidationIssue( position=1, severity="warning",
                                                   message="does not start with a page; it will run on whatever tab is showing" ))


class NavigationsHaveUrls:
    def check( self, recording: Recording, report: ValidationReport ) -> None:
        for step in recording.steps:
            if step.action_type == "navigate" and not step.url.startswith(( "http://", "https://" )):
                report.issues.append( ValidationIssue( position=step.position, severity="error",
                                                       message=f"step { step.position } navigates to no http(s) address" ))


class ElementStepsHaveLocators:
    def check( self, recording: Recording, report: ValidationReport ) -> None:
        for step in recording.steps:
            if step.action_type in ELEMENT_ACTIONS and not step.locators:
                report.issues.append( ValidationIssue( position=step.position, severity="error",
                                                       message=f"step { step.position } ({ step.action_type }) has no way to find its element" ))


class SecretsCarryNoValue:
    def check( self, recording: Recording, report: ValidationReport ) -> None:
        for step in recording.steps:
            if step.value_is_secret and step.value:
                report.issues.append( ValidationIssue( position=step.position, severity="error",
                                                       message=f"step { step.position } stores a secret value" ))


class ValuesWhereNeeded:
    def check( self, recording: Recording, report: ValidationReport ) -> None:
        for step in recording.steps:
            if step.action_type in ( "select_option", "press_key" ) and not step.value and not step.value_is_secret:
                report.issues.append( ValidationIssue( position=step.position, severity="error",
                                                       message=f"step { step.position } ({ step.action_type }) has no value" ))


class RuleSetValidator:
    def __init__( self, rules: list[ IValidationRule ]) -> None:
        self._rules = list( rules )

    def validate( self, recording: Recording ) -> ValidationReport:
        report = ValidationReport()
        for rule in self._rules:
            rule.check( recording, report )
        return report


def default_validation_rules() -> list[ IValidationRule ]:
    return [ HasName(), HasSteps(), StartsOnAPage(), NavigationsHaveUrls(), ElementStepsHaveLocators(),
             SecretsCarryNoValue(), ValuesWhereNeeded()]
