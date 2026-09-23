"""X11 window activation using PID ancestry; no shell or terminal input injection."""
import ctypes as C
import ctypes.util
import os
from pathlib import Path


def agent_pids(agent, target):
    exact, fallback, parents = set(), set(), {}
    for process in Path('/proc').glob('[0-9]*'):
        try:
            pid = int(process.name)
            stat = (process / 'stat').read_text().rsplit(')', 1)[1].split()
            parents[pid] = int(stat[1])
            name = (process / 'comm').read_text().strip().lower()
            args = (process / 'cmdline').read_bytes().split(b'\0')
            if agent == 'antigravity':
                matches = name == 'agy' or name == 'antigravity'
            elif agent == 'copilot':
                matches = 'copilot' in name or any(b'/copilot' in a for a in args[:2])
            else:
                matches = name in ('codex', 'codex-desktop')
            if not matches:
                continue
            fallback.add(pid)
            for fd in (process / 'fd').iterdir():
                try:
                    if os.readlink(fd) == target:
                        exact.add(pid)
                        break
                except OSError:
                    pass
        except (OSError, ValueError, IndexError):
            continue
    chosen = exact or fallback
    ancestors = set(chosen)
    for pid in chosen:
        for _ in range(32):
            pid = parents.get(pid, 0)
            if pid <= 1:
                break
            ancestors.add(pid)
    return ancestors


class XWindows:
    def __init__(self):
        library = ctypes.util.find_library('X11')
        if not library or not os.environ.get('DISPLAY'):
            raise OSError('X11 창 이동을 사용할 수 없습니다.')
        self.x = C.CDLL(library)
        self.x.XSetErrorHandler.argtypes = [C.c_void_p]
        self.x.XSetErrorHandler.restype = C.c_void_p
        self.error_handler = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(lambda *_: 0)
        self.previous_handler = self.x.XSetErrorHandler(self.error_handler)
        signatures = {
            'XOpenDisplay': ([C.c_char_p], C.c_void_p),
            'XDefaultRootWindow': ([C.c_void_p], C.c_ulong),
            'XInternAtom': ([C.c_void_p, C.c_char_p, C.c_int], C.c_ulong),
            'XGetWindowProperty': ([C.c_void_p,C.c_ulong,C.c_ulong,C.c_long,C.c_long,C.c_int,C.c_ulong,C.POINTER(C.c_ulong),C.POINTER(C.c_int),C.POINTER(C.c_ulong),C.POINTER(C.c_ulong),C.POINTER(C.POINTER(C.c_ubyte))], C.c_int),
            'XFree': ([C.c_void_p], C.c_int),
            'XSendEvent': ([C.c_void_p,C.c_ulong,C.c_int,C.c_long,C.c_void_p], C.c_int),
            'XFlush': ([C.c_void_p], C.c_int),
            'XCloseDisplay': ([C.c_void_p], C.c_int),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.x, name)
            fn.argtypes, fn.restype = args, result
        self.display = self.x.XOpenDisplay(None)
        if not self.display:
            self.x.XSetErrorHandler(self.previous_handler)
            raise OSError('화면 연결을 열 수 없습니다.')
        self.root = self.x.XDefaultRootWindow(self.display)

    def atom(self, name):
        return self.x.XInternAtom(self.display, name.encode(), 0)

    def property(self, window, name):
        actual, fmt, count, remaining = C.c_ulong(), C.c_int(), C.c_ulong(), C.c_ulong()
        data = C.POINTER(C.c_ubyte)()
        result = self.x.XGetWindowProperty(self.display, window, self.atom(name), 0, 4096, 0, 0,
                                           C.byref(actual), C.byref(fmt), C.byref(count), C.byref(remaining), C.byref(data))
        if result or not data:
            return []
        try:
            if fmt.value == 32:
                return list(C.cast(data, C.POINTER(C.c_ulong))[:count.value])
            return C.string_at(data, count.value).decode('utf-8', errors='replace')
        finally:
            self.x.XFree(data)

    def candidates(self, agent, target):
        pids = agent_pids(agent, target)
        windows = []
        for wid in self.property(self.root, '_NET_CLIENT_LIST'):
            owner = self.property(wid, '_NET_WM_PID')
            if owner and owner[0] in pids:
                title = self.property(wid, '_NET_WM_NAME') or '작업 창'
                windows.append((wid, title))
        return windows

    def activate(self, wid):
        class Message(C.Structure):
            _fields_ = [('type', C.c_int), ('serial', C.c_ulong), ('send_event', C.c_int),
                        ('display', C.c_void_p), ('window', C.c_ulong), ('message_type', C.c_ulong),
                        ('format', C.c_int), ('data', C.c_long * 5)]
        class Event(C.Union):
            _fields_ = [('message', Message), ('pad', C.c_long * 24)]
        event = Event()
        event.message = Message(33, 0, 1, self.display, wid, self.atom('_NET_ACTIVE_WINDOW'), 32, (C.c_long * 5)(2, 0, 0, 0, 0))
        if not self.x.XSendEvent(self.display, self.root, 0, (1 << 20) | (1 << 19), C.byref(event)):
            raise OSError('창 이동 요청을 보내지 못했습니다.')
        self.x.XFlush(self.display)

    def close(self):
        self.x.XCloseDisplay(self.display)
        self.x.XSetErrorHandler(self.previous_handler)
