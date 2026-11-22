"""The Windows half of BATI's DevTools relay. Runs under BATI's own Windows Python, stdlib only, for one ADW run.

Why it exists: BATI's WSL runs in NAT networking mode, so WSL cannot reach Windows' 127.0.0.1:9222, where Chrome
binds its DevTools port. This process runs *on Windows* as the signed-in user and speaks to that port for the WSL
side over its own stdin/stdout (one JSON object per line). It opens no listening socket, changes no firewall or
WSL setting, and exits when its stdin closes.

What it refuses, whatever the WSL side asks:
* any host but 127.0.0.1, any port but the one given on its command line;
* any HTTP path outside /json/list, /json/version, /json/new?<url>, /json/activate/<id>  (no /json/close);
* any websocket but /devtools/page/<id>  (no browser-level socket, so no Browser.close: it cannot quit Chrome).
"""
import base64
import json
import os
import re
import socket
import struct
import sys
import urllib.request

HOST = "127.0.0.1"
PORT = int(sys.argv[1])
HTTP_PATH = re.compile(r"^/json/(list|version|new\?\S+|activate/[A-Za-z0-9]+)$")
WS_PATH = re.compile(r"^/devtools/page/[A-Za-z0-9]+$")


class Socket:
    """The smallest masked-text-frame websocket client CDP needs."""

    def __init__(self, path):
        self.sock = socket.create_connection((HOST, PORT), timeout=10)
        self.buf, self.parts = b"", []
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {HOST}:{PORT}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = self._until(b"\r\n\r\n").decode("latin-1")
        if " 101 " not in head.split("\r\n", 1)[0]:
            raise OSError("Chrome refused the websocket: " + head.splitlines()[0])

    def _until(self, mark):
        while mark not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise OSError("connection closed")
            self.buf += chunk
        head, self.buf = self.buf.split(mark, 1)
        return head

    def _send(self, opcode, payload):
        header = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 1 << 16:
            header.append(0x80 | 126)
            header += struct.pack("!H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", n)
        mask = os.urandom(4)
        self.sock.sendall(bytes(header) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def send(self, text):
        self._send(0x1, text.encode("utf-8"))

    def _frame(self):
        """One whole frame off the front of the buffer, or None. Nothing is consumed until a frame is complete,
        so a timeout part-way through a frame loses no bytes."""
        b = self.buf
        if len(b) < 2:
            return None
        n, at = b[1] & 0x7F, 2
        if n == 126:
            if len(b) < 4:
                return None
            n, at = struct.unpack("!H", b[2:4])[0], 4
        elif n == 127:
            if len(b) < 10:
                return None
            n, at = struct.unpack("!Q", b[2:10])[0], 10
        if len(b) < at + n:
            return None
        self.buf = b[at + n:]
        return b[0], b[at:at + n]

    def recv(self, timeout):
        self.sock.settimeout(timeout)
        while True:
            frame = self._frame()
            if frame is None:
                chunk = self.sock.recv(65536)          # socket.timeout propagates; the buffer is intact
                if not chunk:
                    raise OSError("connection closed")
                self.buf += chunk
                continue
            b1, payload = frame
            opcode = b1 & 0x0F
            if opcode == 0x9:
                self._send(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x8:
                raise OSError("Chrome closed the DevTools connection")
            self.parts.append(payload)                 # kept on self: a message split across a timeout survives
            if b1 & 0x80:
                text, self.parts = b"".join(self.parts).decode("utf-8"), []
                return text

    def close(self):
        try:
            self._send(0x8, b"")
        except OSError:
            pass
        self.sock.close()


def http(method, path):
    if method not in ("GET", "PUT") or not HTTP_PATH.match(path):
        raise PermissionError(f"path not allowed: {method} {path}")
    request = urllib.request.Request(f"http://{HOST}:{PORT}{path}", method=method)
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.read().decode("utf-8")


def main():
    sockets, next_id = {}, 0
    out = sys.stdout.buffer
    for raw in sys.stdin.buffer:
        msg = json.loads(raw.decode("utf-8"))
        reply = {"id": msg.get("id"), "ok": True}
        op = msg.get("op")
        try:
            if op == "hello":
                reply.update(computer=os.environ.get("COMPUTERNAME", ""), user=os.environ.get("USERNAME", ""),
                             port=PORT)
            elif op == "http":
                reply["body"] = http(msg.get("method", "GET"), msg["path"])
            elif op == "ws_open":
                if not WS_PATH.match(msg["path"]):
                    raise PermissionError(f"websocket not allowed: {msg['path']}")
                next_id += 1
                sockets[next_id] = Socket(msg["path"])
                reply["sock"] = next_id
            elif op == "ws_send":
                sockets[msg["sock"]].send(msg["text"])
            elif op == "ws_recv":
                try:
                    reply["text"] = sockets[msg["sock"]].recv(float(msg.get("wait", 5)))
                except socket.timeout:
                    reply["timeout"] = True
            elif op == "ws_close":
                s = sockets.pop(msg["sock"], None)
                if s:
                    s.close()
            elif op == "quit":
                break
            else:
                raise PermissionError(f"unknown op {op!r}")
        except Exception as exc:                      # every failure is a reply, never a dead relay
            reply = {"id": msg.get("id"), "ok": False, "error": f"{type(exc).__name__}: {exc}"[:400]}
        out.write((json.dumps(reply) + "\n").encode("utf-8"))
        out.flush()
    for s in sockets.values():
        s.close()


main()
