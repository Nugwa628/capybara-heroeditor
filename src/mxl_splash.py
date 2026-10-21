"""Loading screen of the Median XL Capybara Editor (homage to Udie's editor): Diablo II's own "door opening" loading
animation (data\\global\\ui\\loading\\loadingscreen.dc6, 10 frames, palette "loading"), read from the player's game
files like the D2 look, with the robed figure replaced by the capybara of the program icon (lit by the door's
light) and "Median XL Capybara Editor" on the plate instead of "LOADING...".

It runs in its own small process (the editor's exe started again with --capy-splash): the editor loads its tables
in one block, which would freeze an animation in its own window. The editor tells it to close through a flag file:
when its main window is up, or before any question / progress window of the start (game folder, data rebuild), so
that nothing is ever hidden behind the splash. Without the game files, a plain dark splash with the capybara."""
import io
import os
import sys
import time

FLAG_ARG = '--capy-splash'
DOOR_FILE = 'data\\global\\ui\\loading\\loadingscreen.dc6'
DOOR_PAL = 'data\\global\\palette\\loading\\pal.dat'
SIZE = 256
FIGURE = (78, 110, 112, 190)        # the robed figure (left of the door) with a margin, painted over
FEATHER = 5                         # border of that box blended back into the picture (no visible edge)
CAPY_BOX = (70, 146, 122, 188)      # where the capybara stands (feet on the stone platform, facing the door)
TEXT_BOX = (90, 235, 170, 250)      # "LOADING..." on the plate
PLATE_ROW = 234                     # a clean row of the plate's dark, just above the text
TITLE_GOLD = (240, 200, 90)          # solid gold of the title
TITLE_Y = 243                       # centre of the title on the plate
FRAME_MS = 85
TEXTURE = 0.15                      # share of the wall texture added over the smooth fill
VERSION = 7                         # bump to rebuild the cached frames


# ---------------------------------------------------------------------------------------------- images
def _px(buf, w, x, y):
    i = (y * w + x) * 4
    return buf[i], buf[i + 1], buf[i + 2]


def _put(buf, w, x, y, rgb, a=255):
    i = (y * w + x) * 4
    buf[i:i + 4] = bytes((max(0, min(255, int(rgb[0]))), max(0, min(255, int(rgb[1]))),
                          max(0, min(255, int(rgb[2]))), a))


def _lum(rgb):
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


def _inpaint_figure(buf, w):
    """Paints over the robed figure: a smooth fill of the light around it (the region relaxed to the average of
    its neighbours, so the door's glow runs through it without a seam), then a little of the wall / floor texture
    found a figure-width further left in the same frame."""
    x0, y0, x1, y1 = FIGURE
    W, H = x1 - x0, y1 - y0
    shift = W + 4
    ch = []
    for c in range(3):
        g = [[buf[((y0 + y) * w + x0 + x) * 4 + c] for x in range(-1, W + 1)] for y in range(-1, H + 1)]
        for y in range(1, H + 1):                            # start: left-right blend of the border
            for x in range(1, W + 1):
                t = x / (W + 1)
                g[y][x] = g[y][0] * (1 - t) + g[y][W + 1] * t
        for _ in range(260):                                 # relax (over-relaxed Gauss-Seidel: converges)
            for y in range(1, H + 1):
                row, up, dn = g[y], g[y - 1], g[y + 1]
                for x in range(1, W + 1):
                    v = (row[x - 1] + row[x + 1] + up[x] + dn[x]) * 0.25
                    row[x] += (v - row[x]) * 1.9
        ch.append(g)
    for y in range(H):
        src = [_px(buf, w, x0 + x - shift, y0 + y) for x in range(W)]
        mean = [sum(c[i] for c in src) / W for i in range(3)]
        for x in range(W):
            d = min(x + 1, y + 1, W - x, H - y)
            k = min(1.0, d / (FEATHER + 1.0))
            orig = _px(buf, w, x0 + x, y0 + y)
            fill = [ch[i][y + 1][x + 1] + (src[x][i] - mean[i]) * TEXTURE for i in range(3)]
            _put(buf, w, x0 + x, y0 + y, [fill[i] * k + orig[i] * (1 - k) for i in range(3)])


def _clear_text(buf, w):
    x0, y0, x1, y1 = TEXT_BOX
    for y in range(y0, y1):
        for x in range(x0, x1):
            _put(buf, w, x, y, _px(buf, w, x, PLATE_ROW - (y - y0) % 2))


def _capybara(icon_png, box):
    """The icon's capybara, facing right (the door), fitted in box: (w, h, rgba)."""
    from PIL import Image
    im = Image.open(io.BytesIO(icon_png)).convert('RGBA')
    w, h = im.width, im.height
    raw = bytearray(im.tobytes())
    # tight box of the opaque pixels
    xs = [x for x in range(w) if any(raw[(y * w + x) * 4 + 3] > 40 for y in range(0, h, 2))]
    ys = [y for y in range(h) if any(raw[(y * w + x) * 4 + 3] > 40 for x in range(0, w, 2))]
    im = im.crop((xs[0], ys[0], xs[-1] + 1, ys[-1] + 1))
    bw, bh = box[2] - box[0], box[3] - box[1]
    k = min(bw / im.width, bh / im.height)
    tw, th = max(1, round(im.width * k)), max(1, round(im.height * k))
    im = im.resize((tw, th), getattr(Image, 'LANCZOS', 1))
    src = im.tobytes()
    out = bytearray(len(src))
    for y in range(th):                                      # mirror: the icon faces left, the door is right
        for x in range(tw):
            i, j = (y * tw + x) * 4, (y * tw + (tw - 1 - x)) * 4
            out[j:j + 4] = src[i:i + 4]
    return tw, th, out


def _place_capybara(buf, w, cap, light, glow):
    """The capybara on the platform, in the scene's light: darkened like the figure was, warm rim light on its
    door side, washed by the door's glare in the last frames."""
    tw, th, px = cap
    bx0, _by0, bx1, by1 = CAPY_BOX
    ox = bx0 + (bx1 - bx0 - tw) // 2
    oy = by1 - th
    amb = max(0.35, min(1.0, _lum(light) / 140.0))
    cx, cy, rx, ry = ox + tw / 2.0, by1 - 1.5, tw * 0.48, 3.5     # contact shadow on the platform
    for Y in range(int(cy - ry), int(cy + ry) + 1):
        for X in range(int(cx - rx), int(cx + rx) + 1):
            q = ((X - cx) / rx) ** 2 + ((Y - cy) / ry) ** 2
            if q < 1 and 0 <= X < w and 0 <= Y < SIZE:
                f = 1 - (1 - q) * (0.35 + 0.35 * glow)
                _put(buf, w, X, Y, [v * f for v in _px(buf, w, X, Y)])
    for y in range(th):
        for x in range(tw):
            i = (y * tw + x) * 4
            a = px[i + 3]
            if a < 30:
                continue
            r, g, b = px[i], px[i + 1], px[i + 2]
            # distance to the silhouette's right edge (the door side): rim light
            edge = 0
            for d in range(1, 5):
                xx = x + d
                if xx >= tw or px[(y * tw + xx) * 4 + 3] < 30:
                    edge = 5 - d
                    break
            rim = edge / 4.0 * (0.3 + 0.7 * glow)
            side = 0.55 + 0.45 * (x / max(1, tw - 1))             # lit from the door (right), shadow on the left
            col = []
            for c, l in zip((r, g, b), light):
                v = c * amb * side * (0.55 + 0.45 * l / 255.0)
                v = v + (l * 1.15 - v) * rim * 0.75
                v = v + (l - v) * glow * 0.18                          # glare of the open door
                col.append(v)
            X, Y = ox + x, oy + y
            if 0 <= X < w and 0 <= Y < SIZE:
                if a < 255:
                    bg = _px(buf, w, X, Y)
                    f = a / 255.0
                    col = [col[k] * f + bg[k] * (1 - f) for k in range(3)]
                _put(buf, w, X, Y, col)


def frame_paths(cache_dir):
    return [os.path.join(cache_dir, f'door-v{VERSION}-{i}.png') for i in range(10)]


def build_frames(art, icon_png, cache_dir, on_frame=None):
    """PNG paths of the 10 loading frames with the capybara (built once, kept in cache_dir). on_frame(i, path) is
    called as each one is ready (the splash starts playing before all are built)."""
    import mxl_d2ui_dc6 as DC6
    os.makedirs(cache_dir, exist_ok=True)
    paths = frame_paths(cache_dir)
    if all(os.path.exists(p) for p in paths):
        if on_frame:
            for i, p in enumerate(paths):
                on_frame(i, p)
        return paths
    pal = DC6.palette(art.read(DOOR_PAL))
    frames = DC6.dc6_frames(art.read(DOOR_FILE))
    cap = _capybara(icon_png, CAPY_BOX)
    base_l = None
    for i, (p, fr) in enumerate(zip(paths, frames)):
        w, h, rgba = DC6.to_rgba(fr, pal)
        buf = bytearray(rgba)
        _inpaint_figure(buf, w)
        _clear_text(buf, w)
        # light around the capybara (after the figure is gone): its colour and how much the door lights it
        x0, y0, x1, y1 = FIGURE
        samp = [_px(buf, w, x, y) for y in range(y0, y1, 3) for x in range(x0 + 4, x1 + 10, 3)]
        light = [sum(c[k] for c in samp) / len(samp) for k in range(3)]
        lum = _lum(light)
        if base_l is None:
            base_l = lum
        glow = max(0.0, min(1.0, (lum - base_l) / max(1.0, 200 - base_l)))
        if not os.path.exists(p):
            _place_capybara(buf, w, cap, light, glow)
            tmp = f'{p}.{os.getpid()}.tmp'
            with open(tmp, 'wb') as f:
                f.write(DC6.png(w, h, buf))
            os.replace(tmp, p)
        if on_frame:
            on_frame(i, p)
    return paths


# ---------------------------------------------------------------------------------------------- splash process
def _flag_path(pid):
    import tempfile
    return os.path.join(tempfile.gettempdir(), f'capybara-editor-splash-{pid}.flag')


def run_splash(parent_pid):
    """Body of the splash process. The window shows at once (plain capybara); the game art is read and the frames
    are built (first time only) on a thread, and the door plays as they come. Told to close, it fades out at once;
    a frame build still running finishes hidden, so the next start has them all."""
    import threading
    import tkinter as tk
    flag = _flag_path(parent_pid)
    root = tk.Tk()
    root.overrideredirect(True)
    root.configure(bg='black')
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    scale = 2 if sh >= 700 else 1
    W = H = SIZE * scale
    root.geometry(f'{W}x{H}+{(sw - W) // 2}+{(sh - H) // 2}')
    try:
        root.attributes('-topmost', True)
    except tk.TclError:
        pass
    cv = tk.Canvas(root, width=W, height=H, bg='black', highlightthickness=0)
    cv.pack()
    plain = []
    try:
        import mxl_app_icon
        mxl_app_icon.apply(root)
        pic = tk.PhotoImage(data=mxl_app_icon.ICONS[0])
        plain.append(cv.create_image(W // 2, H // 2 - 30, image=pic))
        root._pic = pic
    except Exception:
        pass
    plain.append(cv.create_text(W // 2, H - 60, text='Median XL Capybara Editor', fill='#f0c85a',
                                font=('Palatino Linotype', 18, 'bold')))
    root.update()                                            # on screen now, before anything slow

    got = {'frames': {}, 'title': None, 'done': False}

    def work():
        try:
            import base64
            import settings
            import paths
            import mxl_app_icon
            import mxl_d2look as D2
            import mxl_d2look_art as A
            gd = settings.get('game_dir')
            if not D2.wanted() or not gd or not os.path.isdir(gd):
                raise RuntimeError('no game folder')
            a = A.get_art(gd, settings.get(D2.THEME_SETTING) or A.THEMES[0],
                          os.path.join(paths.USER_DIR, 'ui_art'))
            got['title'] = a.text('Median XL Capybara Editor', 'fontexocet10', TITLE_GOLD, solid=True)
            build_frames(a, base64.b64decode(mxl_app_icon.ICONS[0]), os.path.join(paths.USER_DIR, 'ui_art', 'splash'),
                         lambda i, p: got['frames'].__setitem__(i, p))
        except Exception as e:                               # no game art: the plain splash stays
            print('splash: plain', e)
        got['done'] = True

    th = threading.Thread(target=work)                       # not a daemon: a started build is finished
    th.start()

    imgs, state = [], {'i': -1, 'closing': False, 'alpha': 1.0, 'start': time.time(), 'item': None, 'title': False}

    def parent_alive():
        if sys.platform != 'win32':
            try:
                os.kill(parent_pid, 0)
                return True
            except OSError:
                return False
        try:
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, parent_pid)   # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return False
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(h)
            return code.value == 259                                          # STILL_ACTIVE
        except Exception:
            return True

    def tick():
        if not state['closing'] and (os.path.exists(flag) or not parent_alive() or time.time() - state['start'] > 90):
            state['closing'] = True
        if state['closing']:                                 # the editor is up: fade out now
            state['alpha'] -= 0.15
            if state['alpha'] <= 0:
                root.destroy()
                return
            try:
                root.attributes('-alpha', max(0.0, state['alpha']))
            except tk.TclError:
                pass
            root.after(40, tick)
            return
        while len(imgs) in got['frames']:                    # frames ready so far
            try:
                imgs.append(tk.PhotoImage(file=got['frames'][len(imgs)]).zoom(scale))
            except tk.TclError:
                break
        if imgs:
            if state['item'] is None:
                for it in plain:
                    cv.delete(it)
                state['item'] = cv.create_image(0, 0, anchor='nw', image=imgs[0])
            if not state['title'] and got['title']:
                try:
                    t = tk.PhotoImage(file=got['title'])
                    cv.create_image(W // 2, TITLE_Y * scale, image=t)
                    root._title = t
                except tk.TclError:
                    pass
                state['title'] = True
            last = 9
            if state['i'] < len(imgs) - 1:
                state['i'] += 1
            elif state['i'] >= last - 1 and len(imgs) == 10:
                state['i'] = last - 1 if (int(time.time() * 2.5) % 2) else last   # the door's light flickers
            cv.itemconfigure(state['item'], image=imgs[state['i']])
        root.after(FRAME_MS, tick)

    root.after(10, tick)
    root.mainloop()
    try:
        os.remove(flag)
    except OSError:
        pass


def main():
    """Entry of the splash process (exe --capy-splash <pid>)."""
    i = sys.argv.index(FLAG_ARG)
    run_splash(int(sys.argv[i + 1]))


# ---------------------------------------------------------------------------------------------- editor side
_proc = {'p': None}


def start():
    """Starts the splash process (the exe again, or this script), unless the D2 look is off."""
    if _proc['p'] is not None or _proc.get('started'):
        return
    _proc['started'] = True
    try:
        import mxl_d2look as D2
        if not D2.wanted():
            return
        import subprocess
        flag = _flag_path(os.getpid())
        try:
            os.remove(flag)
        except OSError:
            pass
        if getattr(sys, 'frozen', False):
            cmd = [sys.executable, FLAG_ARG, str(os.getpid())]
        else:
            cmd = [sys.executable, os.path.abspath(sys.argv[0]), FLAG_ARG, str(os.getpid())]
        kw = {}
        if sys.platform == 'win32':
            kw['creationflags'] = 0x08000000                     # CREATE_NO_WINDOW
        _proc['p'] = subprocess.Popen(cmd, **kw)
    except Exception as e:
        print('splash not started:', e)


def close():
    """Tells the splash to finish its animation and fade out (idempotent)."""
    if _proc['p'] is None:
        return
    try:
        with open(_flag_path(os.getpid()), 'w') as f:
            f.write('done')
    except OSError:
        pass
    _proc['p'] = None


def install(App):
    """Closes the splash when the main window is up, and before any window the start opens (game folder choice,
    data rebuild question / progress), so none is hidden behind it."""
    from tkinter import messagebox, filedialog
    for mod, names in ((messagebox, ('showinfo', 'showwarning', 'showerror', 'askyesno', 'askokcancel',
                                     'askyesnocancel', 'askquestion', 'askretrycancel')),
                       (filedialog, ('askdirectory', 'askopenfilename', 'askopenfilenames', 'asksaveasfilename'))):
        for n in names:
            f = getattr(mod, n, None)
            if f is None or getattr(f, '_splash', False):
                continue

            def wrap(*a, _f=f, **k):
                close()
                return _f(*a, **k)
            wrap._splash = True
            setattr(mod, n, wrap)
    try:
        import mxl_gui_install
        GI = mxl_gui_install.GameInstall
        o_prog = GI.progress_window

        def progress_window(self, *a, **k):
            close()
            return o_prog(self, *a, **k)
        GI.progress_window = progress_window
    except Exception:
        pass
    o_build = App._build

    def _build(self):
        o_build(self)
        self.root.after(200, close)              # the main window is drawn: the splash fades out
    App._build = _build
