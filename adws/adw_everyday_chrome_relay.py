#!/usr/bin/env -S uv run
# /// script
# dependencies = ["aiohttp"]
# ///
"""ADW Everyday Chrome Relay -- one approved DevTools connection to the person's own Chrome, offered to Cashy
on loopback as the endpoints a `--remote-debugging-port` Chrome serves. Read chrome_relay/DESIGN.md first.

Long-running, so not a phase: systemd user unit `everyday-chrome-relay` on R46. It connects to Chrome only when a
request needs it, so the Allow box appears when somebody has asked Cashy for something, and once per Chrome start.

    uv run adws/adw_everyday_chrome_relay.py [--port 9223] [--port-file <Chrome User Data>/DevToolsActivePort]
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert( 0, str( Path( __file__ ).resolve().parent ))

from aiohttp import web

from chrome_relay.locate import PortFileLocator
from chrome_relay.server import Relay
from chrome_relay.upstream import UpstreamBrowser

DEFAULT_PORT_FILE = "/mnt/c/Users/rbarn/AppData/Local/Google/Chrome/User Data/DevToolsActivePort"


def main( argv: list[ str ]) -> int:
    parser = argparse.ArgumentParser( description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter )
    parser.add_argument( "--port", type=int, default=9223 )
    parser.add_argument( "--port-file", default=DEFAULT_PORT_FILE )
    args = parser.parse_args( argv )
    logging.basicConfig( level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout )
    logging.getLogger( "aiohttp.access" ).setLevel( logging.WARNING )   # access lines carry query strings (URLs)

    async def build() -> web.Application:
        upstream = UpstreamBrowser( PortFileLocator( Path( args.port_file )))
        app = Relay( upstream ).app()

        async def closing( _app: web.Application ) -> None:
            await upstream.close()
        app.on_cleanup.append( closing )
        return app

    web.run_app( build(), host="127.0.0.1", port=args.port, print=lambda line: print( line, flush=True ))
    return 0


if __name__ == "__main__":
    sys.exit( main( sys.argv[ 1: ]))
