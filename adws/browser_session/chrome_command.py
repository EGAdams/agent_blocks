"""IChromeCommandBuilder: a chrome.exe command line, built immutably."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ChromeCommandBuilder( BaseModel ):
    """Each step returns a new builder; `build()` refuses an incomplete one.

    Chrome only honours `--remote-debugging-port` with a non-default
    `--user-data-dir` (136+), so `build()` insists on a profile too.
    """
    model_config = ConfigDict( frozen=True )

    exe:     str = ""
    port:    int | None = None
    profile_dir: str = ""
    flags:   tuple[ str, ... ] = Field( default=())
    target:  str = ""

    def executable( self, path: str ) -> "ChromeCommandBuilder":
        return self.model_copy( update={ "exe": path })

    def debugging_port( self, port: int ) -> "ChromeCommandBuilder":
        return self.model_copy( update={ "port": port })

    def profile( self, directory: str ) -> "ChromeCommandBuilder":
        return self.model_copy( update={ "profile_dir": directory })

    def flag( self, flag: str ) -> "ChromeCommandBuilder":
        return self.model_copy( update={ "flags": self.flags + ( flag, )})

    def url( self, url: str ) -> "ChromeCommandBuilder":
        return self.model_copy( update={ "target": url })

    def build( self ) -> list[ str ]:
        missing = [ n for n, v in (( "executable", self.exe ), ( "debugging_port", self.port ),
                                   ( "profile", self.profile_dir ), ( "url", self.target )) if not v ]
        if missing:
            raise ValueError( f"chrome command is missing: { ', '.join( missing )}" )
        return [ self.exe, f"--remote-debugging-port={ self.port }",
                 f"--user-data-dir={ self.profile_dir }", *self.flags, self.target ]
