"""IConsole on the terminal the ADW was started from. A secret is read with
getpass, so it is never echoed and never reaches the shell's history."""
from __future__ import annotations

import getpass


class TerminalConsole:
    def say( self, text: str ) -> None:
        print( text, flush=True )

    def ask( self, prompt: str ) -> str:
        try:
            return input( prompt )
        except EOFError:                       # no terminal (an agent's subprocess): the answer is no
            return ""

    def ask_secret( self, prompt: str ) -> str:
        try:
            return getpass.getpass( prompt )
        except EOFError:
            return ""
