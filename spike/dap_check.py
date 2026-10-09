"""Attach to a debugpy listener like an IDE does (Debug Adapter Protocol), set a breakpoint, make Odoo hit it with an
HTTP request, check the frame, then continue and detach without stopping Odoo.

    python3 dap_check.py DEBUG_PORT FILE LINE URL
"""

import json
import socket
import sys
import threading
import time
import urllib.request

port, file, line, url = int(sys.argv[1]), sys.argv[2], int(sys.argv[3]), sys.argv[4]
sock = socket.create_connection(("127.0.0.1", port), timeout=60)
buf = b""
seq = 0


def send(command, args=None):
    global seq
    seq += 1
    body = json.dumps({"seq": seq, "type": "request", "command": command, "arguments": args or {}}).encode()
    sock.sendall(b"Content-Length: %d\r\n\r\n" % len(body) + body)
    return seq


def recv():
    global buf
    while b"\r\n\r\n" not in buf:
        buf += sock.recv(65536)
    head, buf = buf.split(b"\r\n\r\n", 1)
    length = int(head.split(b":")[1])
    while len(buf) < length:
        buf += sock.recv(65536)
    msg, buf = json.loads(buf[:length]), buf[length:]
    return msg


def wait(pred, what, limit=90):
    end = time.time() + limit
    while time.time() < end:
        msg = recv()
        if pred(msg):
            return msg
    raise SystemExit(f"timeout waiting for {what}")


send("initialize", {"adapterID": "odp", "clientID": "odp-check", "pathFormat": "path", "linesStartAt1": True})
wait(lambda m: m.get("command") == "initialize", "initialize response")
attach = send("attach", {"justMyCode": False})
wait(lambda m: m.get("event") == "initialized", "initialized event")
bp = send("setBreakpoints", {"source": {"path": file}, "breakpoints": [{"line": line}]})
r = wait(lambda m: m.get("request_seq") == bp, "setBreakpoints response")
print("breakpoint:", r["body"]["breakpoints"])
send("configurationDone")
wait(lambda m: m.get("request_seq") == attach, "attach response")
print("attached")

result = {}


def hit():
    req = urllib.request.Request(url, data=b'{"jsonrpc":"2.0","params":{}}', headers={"Content-Type": "application/json"})
    result["body"] = urllib.request.urlopen(req, timeout=120).read().decode()


t = threading.Thread(target=hit)
t.start()
stopped = wait(lambda m: m.get("event") == "stopped", "stopped event")
st = send("stackTrace", {"threadId": stopped["body"]["threadId"], "levels": 1})
frame = wait(lambda m: m.get("request_seq") == st, "stackTrace response")["body"]["stackFrames"][0]
print("stopped at", frame["source"]["path"], frame["line"], frame["name"])
assert frame["source"]["path"] == file and frame["line"] == line, frame
send("continue", {"threadId": stopped["body"]["threadId"]})
t.join(60)
assert "result" in result.get("body", ""), result
print("request finished after continue:", result["body"][:80])
send("disconnect", {"terminateDebuggee": False})
sock.close()
print("DAP CHECK PASSED")
