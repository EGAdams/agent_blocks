"""The half of Scala's storage that runs ON the destination machine. Standard library only.

It is shipped as base64 in the command line -- locally as `python3 -c`, remotely as the command of an
existing, key-authenticated `ssh` -- so nothing has to be installed on BATI, R46 or SQMC first, and every
machine runs the same checks:

* the machine must say it is the machine the caller expected (`socket.gethostname()`), or nothing happens;
* a folder must be one of the caller's allowed folders, made of lower-case snake_case parts, and every
  component under the root must be a real directory, not a symlink, resolving to exactly root/<folder>;
* a file is written to a hidden `.part` beside its destination, hashed, then linked into place without
  ever replacing an existing file. The same bytes already there are a duplicate (success, nothing written);
  different bytes under that name are a conflict (refused);
* the bytes received must match the size and SHA-256 the caller announced, before and after the write.

    python3 -c <bootstrap> <base64 of this file> <op> <base64 JSON arguments>     (bytes for `put` on stdin)

It prints exactly one JSON line: {"ok": true, ...} or {"ok": false, "error": <code>, "message": ...}.
"""
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import sys
import time

MAX_BYTES = 64 * 1024 * 1024
PART      = re.compile( r"^[a-z0-9][a-z0-9_]{0,39}$" )
NAME      = re.compile( r"^[0-9]{4}-[0-9]{2}-[0-9]{2}_[a-z0-9_]{1,40}_[0-9a-f]{12}\.(jpg|png|pdf)$" )
STALE     = 600.0


class Refused( Exception ):
    def __init__( self, code, message, **extra ):
        super().__init__( message )
        self.code, self.message, self.extra = code, message, extra


def sha_file( path ):
    digest = hashlib.sha256()
    with open( path, "rb" ) as handle:
        for block in iter( lambda: handle.read( 1 << 20 ), b"" ):
            digest.update( block )
    return digest.hexdigest()


def identity( args ):
    here = socket.gethostname()
    if here.lower() != str( args.get( "expected_hostname", "" )).lower():
        raise Refused( "identity_mismatch", f"this machine is { here }, not { args.get( 'expected_hostname' ) }; nothing was done",
                       hostname=here )
    return here


def real_root( args ):
    root = str( args.get( "root", "" ))
    if not os.path.isabs( root ) or os.path.normpath( root ) != root:
        raise Refused( "bad_root", "the storage root must be an absolute, normalised path" )
    if not os.path.isdir( root ):
        raise Refused( "root_missing", f"the storage root { root } does not exist on this machine" )
    if os.path.realpath( root ) != root:
        raise Refused( "symlink_refused", f"the storage root { root } goes through a symlink" )
    return root


def folder_dir( args, folder, create ):
    """root/<folder>, checked part by part; created only when asked and only inside the root."""
    root = real_root( args )
    if folder not in args.get( "folders", []):
        raise Refused( "folder_not_allowed", f"{ folder!r} is not an approved folder on this machine" )
    parts = folder.split( "/" )
    if not all( PART.match( p ) for p in parts ):
        raise Refused( "folder_not_allowed", f"{ folder!r} is not a plain folder name" )
    current = root
    for part in parts:
        current = os.path.join( current, part )
        if os.path.islink( current ):
            raise Refused( "symlink_refused", f"{ folder } goes through a symlink; nothing was written" )
        if not os.path.exists( current ):
            if not create:
                raise Refused( "folder_missing", f"{ folder } does not exist under { root }" )
            os.mkdir( current, 0o750 )
        if not os.path.isdir( current ):
            raise Refused( "folder_not_allowed", f"{ folder } is not a directory" )
    if os.path.realpath( current ) != os.path.join( root, *parts ):
        raise Refused( "symlink_refused", f"{ folder } resolves outside { root }" )
    return current


def destination( args, create=False ):
    name = str( args.get( "name", "" ))
    if not NAME.match( name ):
        raise Refused( "bad_name", f"{ name!r} is not a filing name Scala makes" )
    path = os.path.join( folder_dir( args, str( args.get( "folder", "" )), create ), name )
    if os.path.islink( path ):
        raise Refused( "symlink_refused", f"{ name } is a symlink; nothing was written" )
    return path


def describe( path, here, status ):
    return { "status": status, "path": path, "size": os.path.getsize( path ), "sha256": sha_file( path ), "hostname": here }


def op_health( args ):
    here = identity( args )
    root = real_root( args )
    usage = shutil.disk_usage( root )
    return { "hostname": here, "root": root, "writable": os.access( root, os.W_OK ), "free_bytes": usage.free,
             "python": sys.version.split()[ 0 ]}


def op_list( args ):
    here = identity( args )
    root = real_root( args )
    out = []
    for folder in args.get( "folders", []):
        path = os.path.join( root, *folder.split( "/" ))
        safe = os.path.isdir( path ) and not os.path.islink( path ) and os.path.realpath( path ) == path
        count = sum( 1 for f in os.listdir( path ) if not f.startswith( "." )
                     and os.path.isfile( os.path.join( path, f ))) if safe else 0
        out.append({ "folder": folder, "exists": safe, "files": count })
    return { "hostname": here, "root": root, "folders": out }


def op_put( args, data ):
    here = identity( args )
    want_sha, want_size = str( args.get( "sha256", "" )), int( args.get( "size", -1 ))
    if len( data ) < want_size:
        raise Refused( "incomplete_transfer", f"received { len( data ) } of { want_size } bytes; nothing was written; retry",
                       received=len( data ))
    got = hashlib.sha256( data ).hexdigest()
    if len( data ) != want_size or got != want_sha:
        raise Refused( "hash_mismatch", "the bytes received do not match the scan's size and SHA-256; nothing was written",
                       received=len( data ), received_sha256=got )
    path = destination( args, create=True )
    folder = os.path.dirname( path )
    name = os.path.basename( path )
    for left in os.listdir( folder ):              # an interrupted earlier attempt leaves only a hidden .part
        if left.startswith( f".{ name }." ) and left.endswith( ".part" ):
            stale = os.path.join( folder, left )
            if time.time() - os.path.getmtime( stale ) > STALE:
                os.unlink( stale )
    if os.path.lexists( path ):
        return existing( path, here, want_sha )
    part = os.path.join( folder, f".{ name }.{ secrets.token_hex( 4 ) }.part" )
    fd = os.open( part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640 )
    try:
        with os.fdopen( fd, "wb" ) as handle:
            handle.write( data )
            handle.flush()
            os.fsync( handle.fileno())
        if sha_file( part ) != want_sha:
            raise Refused( "hash_mismatch", "the written copy does not match the scan's SHA-256; nothing was filed" )
        try:
            os.link( part, path )                   # never replaces: FileExistsError if the name was taken meanwhile
        except FileExistsError:
            return existing( path, here, want_sha )
        except OSError:                             # a filesystem without hard links (drvfs on some builds)
            if os.path.lexists( path ):
                return existing( path, here, want_sha )
            os.rename( part, path )
    finally:
        if os.path.lexists( part ):
            os.unlink( part )
    out = describe( path, here, "stored" )
    if out[ "sha256" ] != want_sha:
        raise Refused( "hash_mismatch", f"{ path } does not hash to the scan's SHA-256 after the write", **out )
    return out


def existing( path, here, want_sha ):
    if os.path.islink( path ):
        raise Refused( "symlink_refused", f"{ path } is a symlink; nothing was written" )
    out = describe( path, here, "duplicate" )
    if out[ "sha256" ] != want_sha:
        raise Refused( "name_conflict", f"{ path } already holds different bytes; it was not overwritten", **out )
    return out


def op_verify( args ):
    here = identity( args )
    path = destination( args )
    if not os.path.isfile( path ):
        raise Refused( "not_found", f"{ path } is not on { here }", hostname=here )
    return describe( path, here, "present" )


def op_find( args ):
    here = identity( args )
    root = real_root( args )
    tail = f"_{ str( args.get( 'sha12', '' ))[ :12 ] }."
    found = []
    for folder in args.get( "folders", []):
        path = os.path.join( root, *folder.split( "/" ))
        if not os.path.isdir( path ) or os.path.islink( path ) or os.path.realpath( path ) != path:
            continue
        for name in sorted( os.listdir( path )):
            if tail in name and NAME.match( name ) and os.path.isfile( os.path.join( path, name )):
                found.append({ "folder": folder, "name": name })
    return { "hostname": here, "found": found }


def op_read( args ):
    found = op_verify( args )
    if found[ "size" ] > MAX_BYTES:
        raise Refused( "too_large", "the stored file is larger than Scala will carry back" )
    with open( found[ "path" ], "rb" ) as handle:
        found[ "content_b64" ] = base64.b64encode( handle.read()).decode( "ascii" )
    return found


OPS = { "health": op_health, "list": op_list, "verify": op_verify, "find": op_find, "read": op_read }


def main( argv ):
    try:
        op, args = argv[ 0 ], json.loads( base64.b64decode( argv[ 1 ]))
        if op == "put":
            result = op_put( args, sys.stdin.buffer.read( MAX_BYTES + 1 ))
        elif op in OPS:
            result = OPS[ op ]( args )
        else:
            raise Refused( "bad_operation", f"no helper operation { op!r}" )
        print( json.dumps({ "ok": True, **result }))
    except Refused as exc:
        print( json.dumps({ "ok": False, "error": exc.code, "message": exc.message, **exc.extra }))
    except Exception as exc:                        # never a traceback on the wire; the code and type are enough
        print( json.dumps({ "ok": False, "error": "helper_failed", "message": f"{ type( exc ).__name__ }: { exc }"[ :300 ]}))


if __name__ in ( "__main__", "scala_storage_helper" ):
    main( sys.argv[ 1: ] if __name__ == "__main__" else sys.argv[ 2: ])
