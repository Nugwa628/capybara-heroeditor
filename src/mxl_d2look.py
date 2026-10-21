""""D2 look" of the main window (homage to Udie's Ultimate Median XL Item Editor): the stash and the character's
equipment / inventory drawn on Median XL Sigma's own in-game panels (Tartarus or Classic theme), with the game's
gear frames, weapon-set tabs and fonts, at the same cell size as each other, like the game shows them side by side.

The artwork is read from the player's game files (mxl_d2look_art), never shipped. Without the game files (or with
the "D2 look" box unticked) the editor keeps its plain layout. Layout values below are in the panels' own pixels
(512 × 704, cells of 29 px), measured on the game's art and checked against an in-game screenshot."""
import os
import tkinter as tk

import settings
import paths
from mxl_lang import T

SETTING = 'd2_look'
THEME_SETTING = 'd2_theme'
ART_CELL = 29
PANEL_W, PANEL_H = 512, 704
STASH_GRID = (53, 150)            # top-left of the 14 × 14 stash grid in stash.dc6
INV_GRID = (37, 329)              # top-left of the 15 × 10 inventory grid in character-inventory.dc6
STASH_GOLD = (256, 102)           # centre of the gold line / storage page line of the stash header
STASH_PAGE = (256, 124)
INV_GOLD = (78, 651)              # gold of the character (inventory panel, bottom left box), left end
CLOSE_STASH = (477, 8)            # the panels' close button (buttons\\close.dc6, 23 × 23) fills their top corner hole
CLOSE_INV = (6, 8)
GEAR_FILE = 'panel-elements\\inventory-gear'
# slot -> (frame with the slot's silhouette (empty), plain frame (holding an item), x, y) in panel pixels
GEAR = {1: (5, 17, 218, 90), 2: (6, 18, 296, 153), 3: (3, 15, 218, 168), 4: (2, 14, 93, 110), 5: (4, 16, 344, 110),
        6: (7, 19, 169, 250), 7: (8, 20, 296, 250), 8: (9, 21, 218, 276), 9: (10, 22, 344, 246), 10: (11, 23, 93, 246)}
GEAR_SIZE = {2: (76, 134), 3: (76, 105), 4: (76, 134), 5: (76, 76), 6: (47, 47), 7: (47, 47), 8: (47, 47), 9: (75, 46),
             10: (76, 76), 11: (76, 76)}
GEAR_INSET = 4                    # frame border: items are drawn inside it
WSET_TABS = ((100, 91), (351, 91))  # "I II" weapon-set tabs above both weapon slots (63 × 25, frames 24 / 25)
WSET_TAB = (24, 25, 63, 25)
CELL_BG = '#0d0c0b'               # colour under the icons (the grid's own dark)
UNMET_BG = '#5b1d12'              # red background of an item whose requirements are not met (as in game)
GOLD_TXT = (199, 179, 119)

_state = {'checked': False, 'art': None, 'error': None}
_photos = {}


# ---------------------------------------------------------------------------------------------- availability
def wanted():
    v = settings.get(SETTING)
    return True if v is None else bool(v)


def art():
    """The game's artwork, or None (D2 look off, game folder unknown, files missing)."""
    if not _state['checked']:
        _state['checked'] = True
        if wanted():
            try:
                import mxl_d2look_art as A
                gd = settings.get('game_dir')
                if not gd or not os.path.isdir(gd):
                    raise A.ArtError('game folder not set')
                theme = settings.get(THEME_SETTING) or A.THEMES[0]
                a = A.get_art(gd, theme, os.path.join(paths.USER_DIR, 'ui_art'))
                a.panel('stash')
                a.panel('character-inventory')
                _state['art'] = a
            except Exception as e:                       # plain look, never a crash
                _state['error'] = e
                print('D2 look disabled:', e)
    return _state['art']


def active():
    return art() is not None


# ---------------------------------------------------------------------------------------------- images
def photo(path, size=None, crop=None):
    """tk image of a cached PNG, cropped (panel pixels) and resized to size (w, h)."""
    from PIL import Image, ImageTk
    key = (path, size, crop)
    if key not in _photos:
        im = Image.open(path).convert('RGBA')
        if crop:
            im = im.crop(crop)
        if size and (im.width, im.height) != tuple(size):
            im = im.resize((max(1, int(size[0])), max(1, int(size[1]))), Image.LANCZOS)
        _photos[key] = ImageTk.PhotoImage(im)
    return _photos[key]


def gear_photo(index, k):
    w, h = GEAR_SIZE.get(index if index < 12 else index - 12, (WSET_TAB[2], WSET_TAB[3]))   # 14-23: plain frames
    return photo(art().frame(GEAR_FILE, index), (round(w * k), round(h * k)))


def draw_text(cv, x, y, text, k=1.0, font='font16', color=GOLD_TXT, anchor='center', tags=()):
    """Text in the game's font (an image) when every character exists in it, else a tk text in the same colour."""
    a = art()
    text = str(text)
    try:
        glyphs, _lh = a.font(font)
        if all(ord(c) in glyphs for c in text):
            p = a.text(text, font, color)
            from PIL import Image
            im = Image.open(p)
            img = photo(p, (max(1, round(im.width * k)), max(1, round(im.height * k))))
            return cv.create_image(x, y, image=img, anchor=anchor, tags=tags)
    except Exception:
        pass
    return cv.create_text(x, y, text=text, fill='#%02x%02x%02x' % color, anchor=anchor, tags=tags,
                          font=('Palatino Linotype', max(8, round(10 * k)), 'bold'))


def fmt_gold(v):
    try:
        return f'{int(v):,}'.replace(',', ' ')
    except (TypeError, ValueError):
        return ''


# ---------------------------------------------------------------------------------------------- sizes
def panel_cell(root, cell):
    """Cell size for the D2 look: the two 512 × 704 panels side by side and the detail panel fit the screen."""
    try:
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    except tk.TclError:
        return cell
    k = min(cell / ART_CELL, (sh - 210) / PANEL_H, (sw - 620) / (2 * PANEL_W))
    return max(20, int(ART_CELL * k))


# ---------------------------------------------------------------------------------------------- stash (collector)
def _relayout_stash(app):
    cv = app.canvas
    left = cv.master
    s = app.cell
    k = s / ART_CELL
    a = art()
    bg = tk.Canvas(left, width=round(PANEL_W * k), height=round(PANEL_H * k), bg='#000000', highlightthickness=0)
    cv.pack_forget()
    bg.pack(side='top', anchor='nw', pady=(4, 0))
    bg.create_image(0, 0, anchor='nw', image=photo(a.panel('stash'), (round(PANEL_W * k), round(PANEL_H * k))))
    cv.configure(bg=CELL_BG)
    try:
        bg.create_image(round(CLOSE_STASH[0] * k), round(CLOSE_STASH[1] * k), anchor='nw',
                        image=photo(a.frame('buttons\\close', 0), (round(23 * k), round(23 * k))))
    except Exception:
        pass
    cv.place(in_=bg, x=round(STASH_GRID[0] * k), y=round(STASH_GRID[1] * k))
    cv.tk.call('raise', cv._w)           # above the panel (Canvas.lift raises items, not the widget)
    app.d2_bg = bg
    app.d2_k = k


def _stash_header(app):
    bg = getattr(app, 'd2_bg', None)
    if bg is None:
        return
    k = app.d2_k
    bg.delete('hdr')
    try:
        from mxl_save import STASH_PAGES
        n = app.page() + 1
    except Exception:
        n, STASH_PAGES = 1, 10
    gold = ''
    inv = getattr(app, 'inventory', None)
    try:
        if inv is not None and inv.ch is not None and app.view.get() != 'shared':
            gold = fmt_gold(inv.ch.stat(15))
    except Exception:
        gold = ''
    if gold:
        draw_text(bg, STASH_GOLD[0] * k, STASH_GOLD[1] * k, gold, k, tags=('hdr',))
    draw_text(bg, STASH_PAGE[0] * k, STASH_PAGE[1] * k,
              T('d2.storage_page', 'Storage Page: {n} / {total}', n=n, total=STASH_PAGES), k, tags=('hdr',))


def _draw_stash(app, mxl_gui):
    """The collector's draw_grid on the game's stash art: no cell boxes, items on the grid's own dark, red when
    the character cannot use them, outlined when selected."""
    c, s = app.canvas, app.cell
    c.delete('all')
    a = art()
    c.create_image(0, 0, anchor='nw', image=photo(
        a.panel('stash'), (mxl_gui.COLS * s, mxl_gui.ROWS * s),
        (STASH_GRID[0], STASH_GRID[1], STASH_GRID[0] + mxl_gui.COLS * ART_CELL, STASH_GRID[1] + mxl_gui.ROWS * ART_CELL)))
    for it in app.items:
        if it['page'] != app.page():
            continue
        w, h = app.size(it)
        x0, y0 = it['x'] * s, it['y'] * s
        x1, y1 = x0 + w * s, y0 + h * s
        col = mxl_gui.item_color(it, app.data)
        sel = it is app.selected
        try:
            unmet = mxl_gui.requirements_unmet(it, app.data, app.char)
        except Exception:
            unmet = False
        bgc = UNMET_BG if unmet else CELL_BG
        fill = mxl_gui.blend(mxl_gui.SELECTION, bgc, 0.14) if sel else bgc
        if unmet or sel:
            c.create_rectangle(x0 + 1, y0 + 1, x1 - 1, y1 - 1, fill=fill, outline=col if sel else '',
                               width=2 if sel else 0)
        imgs = app.icon(it, fill)
        if imgs:
            for kk in (1, 2, 3):
                if imgs.get(kk) is not None and kk in mxl_gui.LAYERS_SHOWN:
                    c.create_image((x0 + x1) / 2, (y0 + y1) / 2, image=imgs[kk], tags=('calque%d' % kk,))
        else:
            lines = mxl_gui.fit_lines(mxl_gui.short_name(it), app.cell_font, w * s - 6, max(1, (h * s - 4) // 12))
            c.create_text((x0 + x1) / 2, (y0 + y1) / 2, text='\n'.join(lines), fill=col, font=app.cell_font,
                          justify='center', width=w * s - 4)
    _stash_header(app)


def install(App):
    """Installed first (innermost): the cell size is chosen before the window is built, so the equipment panel
    (mxl_inv_gui) and the icons use it too."""
    import mxl_gui
    o_build, o_draw = App._build, App.draw_grid

    def _build(self):
        self.d2 = active()
        if self.d2:
            self.cell = panel_cell(self.root, self.cell)
        o_build(self)
        if self.d2:
            try:
                _relayout_stash(self)
            except Exception as e:                        # plain look
                print('D2 look (stash) disabled:', e)
                self.d2 = False
        _add_toggle(self)

    def draw_grid(self):
        if getattr(self, 'd2', False):
            try:
                return _draw_stash(self, mxl_gui)
            except Exception as e:
                print('D2 look (stash) drawing failed:', e)
                self.d2 = False
        return o_draw(self)

    App._build = _build
    App.draw_grid = draw_grid


def _add_toggle(app):
    """"D2 look" checkbox in the top bar (applied at the next start)."""
    from tkinter import ttk, messagebox
    try:
        bar = app.char_combo.master
    except AttributeError:
        return
    var = tk.BooleanVar(value=wanted())

    def changed():
        settings.put(SETTING, bool(var.get()))
        messagebox.showinfo(T('d2.title', 'D2 look'),
                            T('d2.restart', 'The new look is used the next time the editor starts.'), parent=app.root)
    ttk.Checkbutton(bar, text=T('d2.toggle', 'D2 look'), variable=var, command=changed).pack(side='left', padx=(10, 2))
    if wanted() and not active() and _state['error'] is not None:
        app.root.after(500, lambda: app.status.configure(
            text=T('d2.unavailable', 'D2 look unavailable (game art not found: {error})', error=_state['error'])))


# ---------------------------------------------------------------------------------------------- Item Creator
BTN = dict(bg='#2b2620', fg='#c7b377', activebackground='#3d362b', activeforeground='#f2dc9c', disabledforeground='#5e5646',
           relief='ridge', bd=3, highlightthickness=0, font=('Palatino Linotype', 10, 'bold'), padx=10, pady=2,
           cursor='hand2')


class D2Button(tk.Button):
    """A stone-and-gold button that also answers the ttk state([...]) calls the windows make."""

    def state(self, spec=None):
        if spec is None:
            return ('disabled',) if str(self.cget('state')) == 'disabled' else ()
        for s in spec:
            if s == 'disabled':
                self.configure(state='disabled')
            elif s == '!disabled':
                self.configure(state='normal')
        return ()


def button(parent, text, command, width=None):
    """Game-style button in the D2 look, the theme's ttk button otherwise."""
    from tkinter import ttk
    if not active():
        kw = {'width': width} if width else {}
        return ttk.Button(parent, text=text, command=command, **kw)
    kw = dict(BTN)
    if width:
        kw['width'] = width
    return D2Button(parent, text=text, command=command, **kw)


def _hex_rgb(col):
    try:
        col = col.lstrip('#')
        return tuple(int(col[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return GOLD_TXT


class CreatorHeader:
    """Top of the Item Creator / Editor in the D2 look: the game's stone, the title in the game's font, the item
    in a gear frame like Udie's editor, its name in its quality colour."""
    TEXTURE = ('stash', (60, 566, 452, 642))          # plain stone under the stash grid

    def __init__(self, parent, title, cell):
        self.title, self.cell = title, cell
        self.cv = tk.Canvas(parent, height=96, bg='#141210', highlightthickness=0)
        self.cv.pack(side='top', fill='x')
        self.cv.bind('<Configure>', lambda e: self.draw())
        self.item, self.layers, self.name, self.sub, self.color = None, None, '', '', GOLD_TXT

    def show(self, size=None, layers=None, name='', sub='', color=None):
        self.item, self.layers, self.name, self.sub = size, layers, name, sub
        self.color = _hex_rgb(color) if color else GOLD_TXT
        self.draw()

    def draw(self):
        cv = self.cv
        a = art()
        cv.delete('all')
        c = self.cell
        w_cells, h_cells = self.item or (0, 0)
        box_w, box_h = (max(2, w_cells) * c + 12, max(2, h_cells) * c + 12) if self.item else (0, 0)
        H = max(96, box_h + 12)
        if int(cv.cget('height')) != H:
            cv.configure(height=H)
        W = max(cv.winfo_width(), 400)
        name, box = self.TEXTURE
        tw, th = box[2] - box[0], box[3] - box[1]
        k = H / th
        tile = photo(a.panel(name), (round(tw * k), H), box)
        x = 0
        while x < W:
            cv.create_image(x, 0, anchor='nw', image=tile)
            x += round(tw * k)
        cv.create_rectangle(1, 1, W - 2, H - 2, outline='#5e4c2c', width=2)
        x0 = 14
        if self.item:
            y0 = (H - box_h) // 2
            frame = 23 if (w_cells, h_cells) != (1, 1) else 19     # plain gear frame (square / small)
            if h_cells >= 3:
                frame = 14 if h_cells >= 4 else 15
            try:
                cv.create_image(x0, y0, anchor='nw', image=photo(a.frame(GEAR_FILE, frame), (box_w, box_h)))
            except Exception:
                cv.create_rectangle(x0, y0, x0 + box_w, y0 + box_h, fill=CELL_BG, outline='#5e4c2c')
            for kk in (1, 2, 3):
                img = (self.layers or {}).get(kk)
                if img is not None:
                    cv.create_image(x0 + box_w / 2, y0 + box_h / 2, image=img)
            x0 += box_w + 16
        draw_text(cv, x0, 26, self.title, 1.0, font='font30', anchor='w')
        if self.name:
            draw_text(cv, x0, 58, self.name, 1.0, font='font16', color=self.color, anchor='w')
        if self.sub:
            draw_text(cv, x0, 80, self.sub, 1.0, font='font16', color=(150, 150, 150), anchor='w')
