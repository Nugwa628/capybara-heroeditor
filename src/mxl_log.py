"""Log file of the Capybara Editor: capybara.log in the settings folder (%LOCALAPPDATA%\\MXL Item Collector), next to
settings.json. Kept to 1 MB, with the 3 previous files as capybara.log.1 .. .3.

What is written:
- start of the program (exe, Python, Windows) and its end;
- every save through the collector's safe writers (mxl_save.commit_file / edit_stash): the file, what was changed,
  the backup made, or the error when the write was refused or failed;
- character imports (mxl_import logs the page, the result and the report lines);
- errors: uncaught exceptions (also in Tk callbacks and threads), every error box shown to the user, and the
  print() warnings of the modules (D2 look disabled...), which the windowed exe had nowhere to show.

install() is called first in mxl_creator_gui.run(); wrap_saves() again once every module is imported, so that the
names bound with "from mxl_save import edit_stash" use the logging versions too. Nothing here may stop the program:
every failure of the log itself is swallowed."""
import functools
import logging
import logging.handlers
import os
import platform
import sys
import threading
import traceback

FILE_NAME = 'capybara.log'
log = logging.getLogger('capybara')
_state = threading.local()
_installed = False
_path = None


def path():
    """Full path of the log file (None before install())."""
    return _path


def _folder():
    try:
        import paths
        return paths.USER_DIR
    except Exception:
        return os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), 'MXL Item Collector')


class _Stream:
    """sys.stdout / sys.stderr of the windowed exe (None there): print() lines go to the log."""

    def __init__(self, level):
        self.level = level
        self.buf = ''

    def write(self, s):
        if getattr(_state, 'busy', False):
            return len(s)
        self.buf += str(s)
        while '\n' in self.buf:
            line, self.buf = self.buf.split('\n', 1)
            if line.strip():
                log.log(self.level, '[print] %s', line.rstrip())
        return len(s)

    def flush(self):
        pass


def install():
    """Opens the log and hooks the error reports and the safe writers. Safe to call more than once."""
    global _installed, _path
    if _installed:
        return
    _installed = True
    try:
        folder = _folder()
        os.makedirs(folder, exist_ok=True)
        _path = os.path.join(folder, FILE_NAME)
        h = logging.handlers.RotatingFileHandler(_path, maxBytes=1_000_000, backupCount=3, encoding='utf-8', delay=True)
        h.setFormatter(logging.Formatter('%(asctime)s %(levelname)-7s %(message)s', '%Y-%m-%d %H:%M:%S'))
        log.addHandler(h)
        log.setLevel(logging.INFO)
        log.propagate = False
        logging.raiseExceptions = False          # a locked / full log file is never an error box
    except Exception:
        return
    log.info('---- start: %s (Python %s, %s %s)', sys.executable, platform.python_version(), platform.system(),
             platform.version())
    if sys.stdout is None:
        sys.stdout = _Stream(logging.INFO)
    if sys.stderr is None:
        sys.stderr = _Stream(logging.WARNING)
    _hook_errors()
    wrap_saves()
    import atexit
    atexit.register(lambda: log.info('---- end'))


# ------------------------------------------------------------------------------------------------ errors
def _quiet(func, *args):
    """The previous handler, without its printed copy of the traceback reaching the log a second time."""
    _state.busy = True
    try:
        func(*args)
    finally:
        _state.busy = False


def _hook_errors():
    old_hook = sys.excepthook

    def excepthook(t, v, tb):
        log.error('uncaught error:\n%s', ''.join(traceback.format_exception(t, v, tb)).rstrip())
        _quiet(old_hook, t, v, tb)
    sys.excepthook = excepthook

    old_thread = threading.excepthook

    def thread_hook(args):
        log.error('uncaught error in thread %s:\n%s', getattr(args.thread, 'name', '?'),
                  ''.join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)).rstrip())
        _quiet(old_thread, args)
    threading.excepthook = thread_hook

    try:
        import tkinter
        from tkinter import messagebox
    except Exception:
        return
    old_tk = tkinter.Tk.report_callback_exception

    def report_callback_exception(self, t, v, tb):
        log.error('error in a window action:\n%s', ''.join(traceback.format_exception(t, v, tb)).rstrip())
        _quiet(old_tk, self, t, v, tb)
    tkinter.Tk.report_callback_exception = report_callback_exception

    for name, level in (('showerror', logging.ERROR), ('showwarning', logging.WARNING)):
        original = getattr(messagebox, name)

        def shown(title=None, message=None, _original=original, _level=level, **kw):
            log.log(_level, 'message box "%s": %s', title, message)
            return _original(title, message, **kw)
        setattr(messagebox, name, shown)


# ------------------------------------------------------------------------------------------------ saves
def _describe(name, args, kwargs):
    if name == 'edit_stash':
        parts = []
        if kwargs.get('remove'):
            parts.append(f"remove {len(kwargs['remove'])} item(s)")
        if kwargs.get('insert'):
            parts.append(f"insert {len(kwargs['insert'])} item(s)")
        return 'stash edit' + (f" ({', '.join(parts)})" if parts else '')
    content = args[1] if len(args) > 1 else kwargs.get('content')
    return f'write ({len(content)} bytes)' if isinstance(content, (bytes, bytearray)) else 'write'


def _wrap(name, func):
    if getattr(func, '_capy_logged', False):
        return func

    @functools.wraps(func)
    def logged(*args, **kwargs):
        target = args[0] if args else kwargs.get('path')
        outer = not getattr(_state, 'saving', False)      # edit_stash calls commit_file: logged once
        _state.saving = True
        try:
            bak = func(*args, **kwargs)
        except Exception as err:
            if outer:
                log.error('save FAILED: %s: %s — %s: %s', _describe(name, args, kwargs), target, type(err).__name__, err)
            raise
        finally:
            if outer:
                _state.saving = False
        if outer:
            log.info('saved: %s: %s', _describe(name, args, kwargs), target)
            if isinstance(bak, str) and bak:
                log.info('backup: %s', bak)
        return bak
    logged._capy_logged = True
    return logged


def wrap_saves():
    """Logging versions of mxl_save's writers, also where a module bound them by name."""
    try:
        import mxl_save
    except Exception:
        return
    for name in ('commit_file', 'edit_stash'):
        func = getattr(mxl_save, name, None)
        if func is None:
            continue
        orig = getattr(func, '__wrapped__', func) if getattr(func, '_capy_logged', False) else func
        new = _wrap(name, orig) if func is orig else func
        setattr(mxl_save, name, new)
        for mod in list(sys.modules.values()):
            try:
                if mod is not None and getattr(mod, name, None) is orig:
                    setattr(mod, name, new)
            except Exception:
                pass
