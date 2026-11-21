"""X11 / Win32 window activation using PID ancestry; no shell or terminal input injection."""
import ctypes as C
import ctypes.util
import os
from pathlib import Path
from agent_activity import claude_project


def agent_pids(agent, target):
    exact, fallback, parents = windows_scan(agent, target) if os.name == 'nt' else proc_scan(agent, target)
    return ancestors(exact, fallback, parents)


def windows_scan(agent, target):
    import psutil
    from agent_activity import windows_agents
    exact, fallback, parents, shells = set(), set(), {}, set()
    wanted = os.path.normcase(os.path.normpath(target))
    for pid, ppid, name, kind in windows_agents():
        parents[pid] = ppid
        if name.lower() == 'explorer.exe':
            shells.add(pid)
        # agent_kind names Antigravity's CLI "agy"; the activity rows call it "antigravity".
        if not (kind == agent or kind == 'agy' and agent == 'antigravity'):
            continue
        try:
            process = psutil.Process(pid)
            if agent == 'claude':
                if claude_project(process.cwd()) != Path(target).parent.name:
                    continue
            elif any(os.path.normcase(item.path) == wanted for item in process.open_files()):
                exact.add(pid)
            fallback.add(pid)
        except (psutil.Error, OSError):
            continue

    def created(pid):
        try:
            return psutil.Process(pid).create_time()
        except psutil.Error:
            return None
    # Windows reuses the pid of an exited parent; a "parent" newer than its child is a stranger, so the chain ends.
    for child in fallback:
        for _ in range(32):
            parent = parents.get(child, 0)
            if parent <= 1:
                break
            older, newer = created(parent), created(child)
            if older is None or newer is None or older > newer:
                parents[child] = 0
                break
            child = parent
    # The ancestry stops below the desktop shell, so File Explorer windows are never offered.
    return exact, fallback, {pid: 0 if parent in shells else parent for pid, parent in parents.items()}

def proc_scan(agent, target):
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
            elif agent == 'claude':
                # Claude keeps no transcript open; match the CLI by its project working directory.
                matches = ((name == 'claude' or any(a.rsplit(b'/', 1)[-1] == b'claude' for a in args[:2]))
                           and claude_project(os.readlink(process / 'cwd')) == Path(target).parent.name)
            elif agent == 'opencode':
                matches = 'opencode' in name
            elif agent == 'codex':
                matches = name in ('codex', 'codex-desktop')
            else:
                # An agent without a rule must not focus another agent's window.
                matches = False
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
    return exact, fallback, parents


def ancestors(exact, fallback, parents):
    def own(pid):
        # Quota checks such as `claude --print /usage` run as this widget's children.
        for _ in range(32):
            if pid == os.getpid():
                return True
            pid = parents.get(pid, 0)
        return False
    exact, fallback = {pid for pid in exact if not own(pid)}, {pid for pid in fallback if not own(pid)}
    chosen = exact or fallback
    found = set(chosen)
    for pid in chosen:
        for _ in range(32):
            pid = parents.get(pid, 0)
            if pid <= 1:
                break
            found.add(pid)
    return found


class WinWindows:
    """Windows: top-level windows owned by the agent or an ancestor (its terminal, e.g. Windows Terminal)."""

    def __init__(self):
        from ctypes import wintypes as W
        self.user = C.WinDLL('user32', use_last_error=True)
        self.enum_proc = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
        self.user.EnumWindows.argtypes = [self.enum_proc, W.LPARAM]
        self.user.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
        self.user.GetWindowTextLengthW.argtypes = [W.HWND]
        self.user.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
        for name in ('IsWindowVisible', 'IsIconic', 'SetForegroundWindow'):
            getattr(self.user, name).argtypes = [W.HWND]
        self.user.GetWindow.argtypes = [W.HWND, C.c_uint]
        self.user.GetWindow.restype = W.HWND
        self.user.ShowWindow.argtypes = [W.HWND, C.c_int]
        self.W = W

    def candidates(self, agent, target):
        pids = agent_pids(agent, target)
        windows = []

        def visit(hwnd, _):
            # Visible, unowned (GW_OWNER = 4) windows with a title are what the taskbar shows.
            if self.user.IsWindowVisible(hwnd) and not self.user.GetWindow(hwnd, 4):
                owner = self.W.DWORD()
                self.user.GetWindowThreadProcessId(hwnd, C.byref(owner))
                length = self.user.GetWindowTextLengthW(hwnd)
                if owner.value in pids and length:
                    title = C.create_unicode_buffer(length + 1)
                    self.user.GetWindowTextW(hwnd, title, length + 1)
                    windows.append((hwnd, title.value or '작업 창'))
            return True
        self.user.EnumWindows(self.enum_proc(visit), 0)
        return windows

    def activate(self, wid):
        if self.user.IsIconic(wid):
            self.user.ShowWindow(wid, 9)  # SW_RESTORE
        if not self.user.SetForegroundWindow(wid):
            raise OSError('창 이동 요청을 보내지 못했습니다.')

    def close(self):
        pass


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


if os.name == 'nt':
    # widget.py uses one name for the platform's window switcher.
    XWindows = WinWindows
