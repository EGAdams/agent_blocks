"""The two ways a handoff fails before anybody works on it."""


class Undelivered(RuntimeError):
    """The other machine could not be reached, or did not answer in time."""


class Refused(ValueError):
    """What arrived is not a handoff this machine accepts."""
