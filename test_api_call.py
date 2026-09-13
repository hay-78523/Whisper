import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from app.web.server import Handler
import json

def test():
    # Hacky way to instantiate Handler without a real request
    class DummyServer:
        pass
    class DummyRequest:
        def makefile(self, *args, **kwargs):
            import io
            return io.BytesIO(b"GET /api/voices HTTP/1.1\r\nHost: localhost\r\n\r\n")
        def sendall(self, data):
            pass
    try:
        h = Handler(DummyRequest(), ( "127.0.0.1", 1234 ), DummyServer())
    except Exception as e:
        # It might fail in __init__ due to DummyRequest lacking methods
        pass
    
    # We just want to test do_GET for /api/voices
    # Let's bypass the __init__ and just call the method on an empty object
    h = type("Dummy", (), {})()
    h.path = "/api/voices"
    h._require = lambda: {"user": "admin"}
    h.send_response = lambda c: print("Response:", c)
    h.send_header = lambda k, v: None
    h.end_headers = lambda: None
    h.wfile = type("WFile", (), {"write": lambda self, d: print(d.decode("utf-8"))})()
    
    # Bind the method
    Handler.do_GET(h)

test()
