"""Character window of the MXL Item Collector: equipment (character and mercenary, weapon sets I / II), inventory,
and progress (level, quests, waypoints, copy of the character).

Items can be dragged between the equipment slots and the inventory, sent to / taken from the stash shown in the
main window, edited or created directly in a slot with the Item Creator. Every save goes through
mxl_char.Character.save (game closed, verification, backup)."""
import os
import struct
import tkinter as tk
from tkinter import ttk, messagebox

import mxl_save
from mxl_save import parse_stash, free_spot, edit_stash, item_blob, read_file, commit_file, EditError, STASH_PAGES
from mxl_format import FormatError
from mxl_gui_detail import setup_detail_tags, item_color, LAYERS_SHOWN
from mxl_tooltip import requirements_unmet
from mxl_theme import BG, GRID_LINE, EMPTY, CELL_OK, CELL_KO, SELECTION, blend
import mxl_char as MC
from mxl_char import Character, CharError, SLOT_NAMES, MERC_SLOTS, INV_W, INV_H, ACTS, WAYPOINTS, DIFFICULTIES
from mxl_char import slot_name, difficulty_name
from mxl_lang import T
from mxl_creator_gui import CreatorWindow, PreviewPanel, PreviewHost, clean

# paperdoll: slot -> (x, y, w, h) in cells; weapon set II uses the same boxes as set I
DOLL = {1: (4.5, 0, 2, 2), 2: (7, 1, 1, 1), 3: (4.5, 2.5, 2, 3), 8: (4.5, 6, 2, 1),
        4: (0.5, 0.75, 2, 4), 5: (8.5, 0.75, 2, 4), 6: (3, 5, 1, 1), 7: (7, 5, 1, 1),
        10: (0.5, 5.25, 2, 2), 9: (8.5, 5.25, 2, 2)}
SET2 = {4: 11, 5: 12}
DOLL_W, DOLL_H = 11, 7.5


class CharacterWindow:
    def __init__(self, app, tab='stats'):
        self.app = app
        self.data = app.data
        path = app.character_file()
        if not path or not os.path.exists(path):
            messagebox.showinfo(T('ch.title', 'Character'), T('ch.choose_first', 'Choose a character in the main window first.'))
            return
        try:
            self.ch = Character(path, self.data)
        except (FormatError, CharError, OSError, KeyError) as e:
            messagebox.showerror(T('ch.title', 'Character'), f'{path}\n\n{e}')
            return
        self.merc = tk.BooleanVar(value=False)
        self.wset = tk.IntVar(value=1)
        self.selected = None          # Entry
        self.sel_slot = None          # slot number of the selected box (even when empty)
        self.drag = None
        self.icons = {}
        self.top = tk.Toplevel(app.root)
        self.top.title(f'Median XL Capybara Editor — {self.ch.name}')
        self.top.geometry('1180x930')
        self.top.configure(bg=HE_BG)
        try:
            import mxl_he_art
            import sys
            if sys.platform == 'win32':
                import mxl_app_icon
                self.top.wm_iconbitmap(mxl_app_icon.ico_path('hero_editor.ico', mxl_he_art.ICO_FILE))
            self._icon = tk.PhotoImage(master=self.top, data=mxl_he_art.ICON)
            self.top.iconphoto(False, self._icon)
        except Exception:
            pass
        self.cell = app.cell
        # tab row and stone buttons at the bottom, like the Hero Editor
        bottom = tk.Frame(self.top, bg='#000000')
        bottom.pack(side='bottom', fill='x')
        tabbar = tk.Frame(bottom, bg='#000000')
        tabbar.pack(side='top', fill='x')
        actions = tk.Frame(bottom, bg='#000000')
        actions.pack(side='top', fill='x', padx=8, pady=(4, 6))
        StoneButton(actions, 'SAVE', self.save_progress).pack(side='left', padx=(0, 10))
        StoneButton(actions, 'RELOAD', self.reload).pack(side='left', padx=(0, 10))
        StoneButton(actions, 'CLOSE', self.close).pack(side='right')
        tk.Label(actions, text=T('ch.glory', 'Glory to the real Heroes ZonFire99 and Mastadex'), font=('Times New Roman', 12, 'italic'),
                 bg='#000000', fg=HE_GOLD).pack(side='right', padx=16)
        self.bar_status = tk.Label(actions, text='', font=HE_FONT, bg='#000000', fg=HE_GOLD, anchor='w')
        self.bar_status.pack(side='left', fill='x', expand=True, padx=8)
        self.top.protocol('WM_DELETE_WINDOW', self.close)
        body = tk.Frame(self.top, bg=HE_BG)
        body.pack(fill='both', expand=True, padx=6, pady=(6, 0))
        self.body = body
        self.st_tab = tk.Frame(body, bg=HE_BG)
        self.eq_tab = ttk.Frame(body, padding=6)
        self.wp_tab = tk.Frame(body, bg=HE_BG)
        self.q_tab = tk.Frame(body, bg=HE_BG)
        self.pages = {'stats': self.st_tab, 'inventory': self.eq_tab, 'waypoints': self.wp_tab, 'quests': self.q_tab}
        self.tab = tk.StringVar(value='stats')
        self.tab_buttons = []
        for key, text in (('stats', T('ch.tab.stats', 'Stats')), ('inventory', T('ch.tab.inventory', 'Inventory')),
                          ('waypoints', T('ch.tab.waypoints', 'Waypoints')), ('quests', T('ch.tab.quests', 'Quests'))):
            rb = tk.Radiobutton(tabbar, text=text, value=key, variable=self.tab, indicatoron=0, font=('Times New Roman', 12),
                                underline=0, bd=1, relief='raised', padx=8, pady=2, bg='#e8e8e8', selectcolor='#ffffff',
                                activebackground='#ffffff', command=self.show_tab)
            rb.pack(side='left', padx=(2 if key == 'stats' else 0, 0), pady=(0, 3))
            self.tab_buttons.append(rb)
            ch0 = text[:1].lower()
            if ch0.isascii() and ch0.isalpha():              # Alt + first letter (a translated label may not have one)
                try:
                    self.top.bind(f'<Alt-{ch0}>', lambda e, k=key: (self.tab.set(k), self.show_tab()))
                except tk.TclError:
                    pass
        try:
            import settings
            dark = bool(settings.get('hero_dark_mode'))
        except Exception:
            dark = False
        self.dark = tk.BooleanVar(value=dark)
        tk.Checkbutton(tabbar, text=T('ch.dark', 'Dark mode'), variable=self.dark, command=self.apply_theme, font=('Times New Roman', 12),
                       bg='#000000', fg=HE_GOLD, selectcolor='#000000', activebackground='#000000',
                       activeforeground=HE_GOLD, bd=0, highlightthickness=0).pack(side='left', padx=(14, 0), pady=(0, 3))
        self._build_equipment()
        self._build_progress()
        self.apply_theme(save=False)
        self.tab.set({'equipment': 'inventory', 'progress': 'stats'}.get(tab, tab))
        self.show_tab()
        self.redraw()

    def close(self):
        if self.dirty():
            ans = messagebox.askyesnocancel(T('ch.close', 'Close'), T('ch.save_before_close', 'Save the changes before closing?'),
                                            parent=self.top)
            if ans is None:
                return
            if ans:
                self.save_progress()
                if self.dirty():
                    return
        self.top.destroy()

    def apply_theme(self, save=True):
        """Light (Hero Editor grey) or dark colours for the Stats, Inventory, Waypoints and Quests tabs."""
        dark = bool(self.dark.get())
        t = THEMES[dark]
        if save:
            try:
                import settings
                settings.put('hero_dark_mode', dark)
            except Exception:
                pass

        def walk(w):
            cls = w.winfo_class()
            try:
                if cls in ('Frame', 'Toplevel'):
                    w.configure(bg=t['bg'])
                elif cls == 'Labelframe':
                    w.configure(bg=t['bg'], fg=t['frame_fg'])
                elif cls == 'Label':
                    w.configure(bg=t['bg'], fg=t['fg'])
                elif cls in ('Checkbutton', 'Radiobutton'):
                    w.configure(bg=t['bg'], fg=t['fg'], activebackground=t['bg'], activeforeground=t['fg'],
                                disabledforeground=t['fg'], selectcolor=t['entry'])
                elif cls == 'Button':
                    w.configure(bg=t['btn'], fg=t['fg'], activebackground=t['btn'], activeforeground=t['fg'])
                elif cls == 'Entry':
                    w.configure(bg=t['entry'], fg=t['entry_fg'], readonlybackground=t['entry'],
                                insertbackground=t['entry_fg'])
            except tk.TclError:
                pass
            for c in w.winfo_children():
                walk(c)
        self.top.configure(bg=t['bg'])
        self.body.configure(bg=t['bg'])
        for page in (self.st_tab, self.wp_tab, self.q_tab):
            walk(page)
        for tree in self.wp_trees + self.q_trees:
            tree.set_theme(dark)
        for rb in self.tab_buttons:
            rb.configure(bg=t['tab'], fg=t['tab_fg'], selectcolor=t['tab_sel'], activebackground=t['tab_sel'],
                         activeforeground=t['tab_fg'])
        # Inventory tab: drawn with the collector's own dark theme (dark in both modes)

    def show_tab(self):
        for key, page in self.pages.items():
            if key == self.tab.get():
                page.pack(fill='both', expand=True)
            else:
                page.pack_forget()

    # ================================================================== equipment tab
    def _build_equipment(self):
        f = self.eq_tab
        left = ttk.Frame(f)
        left.pack(side='left', fill='y')
        row = ttk.Frame(left)
        row.pack(fill='x', pady=(0, 4))
        for text, val in ((T('ch.character', 'Character'), False), (T('ch.mercenary', 'Mercenary'), True)):
            b = ttk.Radiobutton(row, text=text, value=val, variable=self.merc, command=self.on_view)
            b.pack(side='left', padx=2)
            if val and not self.ch.merc_hired:
                b.state(['disabled'])
        ttk.Label(row, text='   ' + T('ch.weapon_set', 'Weapon set')).pack(side='left')
        for n in (1, 2):
            ttk.Radiobutton(row, text='I' if n == 1 else 'II', value=n, variable=self.wset,
                            command=self.redraw).pack(side='left', padx=2)
        c = self.cell
        self.doll = tk.Canvas(left, width=int(DOLL_W * c) + 1, height=int(DOLL_H * c) + 1, bg=BG, highlightthickness=0)
        self.doll.pack(anchor='w')
        self.inv_label = ttk.Label(left, text=T('ch.tab.inventory', 'Inventory'))
        self.inv_label.pack(anchor='w', pady=(8, 2))
        self.inv = tk.Canvas(left, width=INV_W * c + 1, height=INV_H * c + 1, bg=BG, highlightthickness=0)
        self.inv.pack(anchor='w')
        for cv in (self.doll, self.inv):
            cv.bind('<Button-1>', self.on_press)
            cv.bind('<B1-Motion>', self.on_drag)
            cv.bind('<ButtonRelease-1>', self.on_release)
        btns = ttk.Frame(left)
        btns.pack(fill='x', pady=(8, 0))
        for text, cmd in ((T('ch.btn.to_stash', 'To stash'), self.to_stash), (T('ch.btn.from_stash', 'From stash'), self.from_stash),
                          (T('ch.btn.edit', 'Edit…'), self.edit), (T('ch.btn.create_here', 'Create here…'), self.create_here),
                          (T('ch.btn.delete', 'Delete'), self.delete)):
            ttk.Button(btns, text=text, command=cmd).pack(side='left', padx=2)
        self.status = ttk.Label(left, text=T('ch.inv_hint', 'Drag items between the slots and the inventory. '
                                             '"From stash" takes the item selected in the main window.'),
                                wraplength=620, justify='left')
        self.status.pack(anchor='w', pady=(6, 0))
        right = ttk.Frame(f)
        right.pack(side='left', fill='both', expand=True, padx=(10, 0))
        d = self.app.detail
        self.preview = tk.Text(right, width=int(d.cget('width')), wrap='word', bg=d.cget('bg'), fg=d.cget('fg'), bd=0,
                               padx=int(str(d.cget('padx'))), pady=8, font=d.cget('font'), cursor='arrow',
                               highlightthickness=0)
        sb = ttk.Scrollbar(right, orient='vertical', command=self.preview.yview)
        self.preview.configure(yscrollcommand=sb.set)
        self.preview.pack(side='left', fill='both', expand=True)
        sb.pack(side='left', fill='y')
        setup_detail_tags(self.preview)
        self.panel = PreviewPanel(self.preview, PreviewHost(self.app))

    def is_merc(self):
        return bool(self.merc.get())

    def slot_of(self, box):
        return SET2.get(box, box) if self.wset.get() == 2 else box

    def visible_boxes(self):
        return [b for b in DOLL if not self.is_merc() or b in MERC_SLOTS]

    def on_view(self):
        self.selected = None
        self.sel_slot = None
        self.redraw()

    # ---- drawing
    def icon(self, it, bg):
        try:
            return self.app.icon(it, bg)
        except Exception:
            return None

    def draw_item(self, cv, it, x0, y0, x1, y1, selected):
        d = self.data
        unmet = (not self.is_merc()) and self.app.char is not None and requirements_unmet(it, d, self.app.char)
        bg = CELL_KO if unmet else CELL_OK
        fill = blend(SELECTION, bg, 0.12) if selected else bg
        col = item_color(it, d)
        cv.create_rectangle(x0 + 1, y0 + 1, x1 - 1, y1 - 1, fill=fill, outline=col if selected else '',
                            width=2 if selected else 0)
        imgs = self.icon(it, fill)
        if imgs:
            for k in (1, 2, 3):
                if imgs.get(k) is not None and k in LAYERS_SHOWN:
                    cv.create_image((x0 + x1) / 2, (y0 + y1) / 2, image=imgs[k])
        else:
            cv.create_text((x0 + x1) / 2, (y0 + y1) / 2, text=clean(it['name']), fill=col,
                           width=max(20, x1 - x0 - 6), font=('Segoe UI', 8), justify='center')

    def redraw(self):
        c = self.cell
        merc = self.is_merc()
        eq = self.ch.equipped(merc)
        cv = self.doll
        cv.delete('all')
        for box in self.visible_boxes():
            x, y, w, h = DOLL[box]
            slot = self.slot_of(box)
            x0, y0, x1, y1 = x * c, y * c, (x + w) * c, (y + h) * c
            cv.create_rectangle(x0, y0, x1, y1, fill=EMPTY, outline='#d4b062' if slot == self.sel_slot else GRID_LINE,
                                width=2 if slot == self.sel_slot else 1)
            e = eq.get(slot)
            if e is not None:
                self.draw_item(cv, e.it, x0, y0, x1, y1, e is self.selected)
            elif box in (4, 5):
                cv.create_text((x0 + x1) / 2, (y0 + y1) / 2, text='I' if self.wset.get() == 1 else 'II',
                               fill='#444444', font=('Segoe UI', 14))
        inv = self.inv
        inv.delete('all')
        if merc:
            self.inv_label.configure(text=T('ch.merc_no_inv', '(the mercenary has no inventory)'))
            inv.configure(height=1)
        else:
            self.inv_label.configure(text=T('ch.inv_label', 'Inventory — {name}, level {level}', name=self.ch.name, level=self.ch.level))
            inv.configure(height=INV_H * c + 1)
            for x in range(INV_W):
                for y in range(INV_H):
                    inv.create_rectangle(x * c, y * c, (x + 1) * c, (y + 1) * c, fill=EMPTY, outline=GRID_LINE)
            for e in self.ch.inventory():
                w, h = self.data.base(e.it['code']).size
                x0, y0 = e.it['x'] * c, e.it['y'] * c
                self.draw_item(inv, e.it, x0, y0, x0 + w * c, y0 + h * c, e is self.selected)
        self.panel.show(self.selected.it if self.selected else None)

    # ---- hit tests
    def target_at(self, cv, ev, size=(1, 1), dx=0, dy=0):
        c = self.cell
        if cv is self.doll:
            for box in self.visible_boxes():
                x, y, w, h = DOLL[box]
                if x * c <= ev.x < (x + w) * c and y * c <= ev.y < (y + h) * c:
                    return ('slot', self.slot_of(box))
            return None
        if self.is_merc():
            return None
        return ('inv', ev.x // c - dx, ev.y // c - dy)

    def entry_at(self, cv, ev):
        t = self.target_at(cv, ev)
        if t is None:
            return None, None
        if t[0] == 'slot':
            return self.ch.equipped(self.is_merc()).get(t[1]), t[1]
        for e in self.ch.inventory():
            w, h = self.data.base(e.it['code']).size
            if e.it['x'] <= t[1] < e.it['x'] + w and e.it['y'] <= t[2] < e.it['y'] + h:
                return e, None
        return None, None

    # ---- mouse
    def on_press(self, ev):
        cv = ev.widget
        e, slot = self.entry_at(cv, ev)
        self.selected = e
        self.sel_slot = slot
        self.drag = None
        if e is not None:
            dx = dy = 0
            if cv is self.inv:
                dx, dy = ev.x // self.cell - e.it['x'], ev.y // self.cell - e.it['y']
            self.drag = dict(entry=e, dx=dx, dy=dy, moved=False)
        self.redraw()

    def on_drag(self, ev):
        if self.drag:
            self.drag['moved'] = True
            self.top.configure(cursor='fleur')

    def on_release(self, ev):
        self.top.configure(cursor='')
        d, self.drag = self.drag, None
        if not d or not d['moved']:
            return
        x = ev.x_root - self.doll.winfo_rootx()
        y = ev.y_root - self.doll.winfo_rooty()
        cv = self.doll
        if not (0 <= x < self.doll.winfo_width() and 0 <= y < self.doll.winfo_height()):
            cv = self.inv
            x = ev.x_root - self.inv.winfo_rootx()
            y = ev.y_root - self.inv.winfo_rooty()
            if not (0 <= x < self.inv.winfo_width() and 0 <= y < self.inv.winfo_height()):
                return
        fake = type('E', (), {'x': x, 'y': y})()
        where = self.target_at(cv, fake, dx=d['dx'], dy=d['dy'])
        if where is None:
            return
        e = d['entry']
        try:
            self.ch.move(e, where, self.is_merc())
            self.save(T('ch.moved', '{item} moved', item=clean(e.it['name'])))
        except CharError as err:
            self.set_status(str(err), True)
            self.reload()

    # ---- actions
    def set_status(self, text, error=False):
        self.status.configure(text=text, foreground='#e06060' if error else '')

    def reload(self):
        self.ch = Character(self.ch.path, self.data)
        self.selected = None
        self.redraw()
        self._init_state()
        self.refresh_progress()

    def save(self, msg):
        app = self.app
        try:
            bak = self.ch.save(backup=app.needs_backup(self.ch.path))
        except CharError as e:
            messagebox.showerror(T('ch.title', 'Character'), str(e), parent=self.top)
            self.reload()
            return False
        app.backup_made(self.ch.path, bak)
        self.selected = None
        self.redraw()
        self.refresh_progress()
        if app.path:
            app.load(app.path, keep_selection=True)
        self.set_status(msg + (T('cr.backup', ' — backup: {file}', file=os.path.basename(bak)) if bak else ''))
        return True

    def to_stash(self):
        app, e = self.app, self.selected
        if e is None:
            self.set_status(T('ch.select_first', 'Select an item of the character first.'), True)
            return
        if not app.path:
            self.set_status(T('cr.open_stash', 'Open a stash in the main window first.'), True)
            return
        merc = self.is_merc()
        blob = self.ch.stash_blob(e)
        before = read_file(app.path)
        try:
            items = parse_stash(app.path, self.data)
            new = mxl_save.blob_item(blob, self.data)
            pages = [app.page()] + [i for i in range(STASH_PAGES) if i != app.page()]
            spot = free_spot(items, new, self.data, pages)
            if spot is None:
                raise EditError(T('ch.err.stash_full', 'no free place in the stash'))
            bak = edit_stash(app.path, self.data, insert=[(spot[0], spot[1], spot[2], blob)],
                             backup=app.needs_backup(app.path))
            app.backup_made(app.path, bak)
        except (EditError, FormatError, OSError) as err:
            messagebox.showerror(T('ch.btn.to_stash', 'To stash'), str(err), parent=self.top)
            return
        self.ch.remove(e, merc)
        if not self.save(T('ch.sent', '{item} sent to {file}, page {page}', item=clean(e.it['name']),
                           file=os.path.basename(app.path), page=spot[0] + 1)):
            try:                       # character not written: put the stash back as it was
                commit_file(app.path, before, lambda tmp: None, backup=False)
            except Exception:
                pass
            app.load(app.path, keep_selection=True)

    def from_stash(self):
        app = self.app
        it = app.selected
        if it is None or not app.path:
            self.set_status(T('ch.select_stash_first', 'Select an item in the stash of the main window first.'), True)
            return
        merc = self.is_merc()
        try:
            buf = read_file(app.path)
            blob = item_blob(buf, it)
            cur_slot = self.sel_slot
            if cur_slot is not None and self.ch.equipped(merc).get(cur_slot) is None:
                where = ('slot', cur_slot)
            else:
                if merc:
                    raise CharError(T('ch.err.merc_slot', 'select an empty mercenary slot first'))
                spot = self.ch.free_inventory_spot(mxl_save.blob_item(blob, self.data))
                if spot is None:
                    raise CharError(T('ch.err.inv_full', 'no free place in the inventory'))
                where = ('inv',) + spot
            if where[0] == 'slot' and not merc and app.char is not None and requirements_unmet(it, self.data, app.char):
                if not messagebox.askyesno(T('ch.equip', 'Equip'), T('ch.equip_ask', 'The character does not meet the requirements '
                                           'of this item: it will be inactive until he does. Equip anyway?'), parent=self.top):
                    return
            self.ch.place(blob, where, merc)
        except (CharError, EditError, FormatError, OSError) as err:
            self.set_status(str(err), True)
            self.reload()
            return
        before_char = self.ch.raw
        if not self.save(T('ch.taken', '{item} taken from the stash', item=clean(it['name']))):
            return
        try:
            bak = edit_stash(app.path, self.data, remove=[it['_offset']], backup=app.needs_backup(app.path))
            app.backup_made(app.path, bak)
        except (EditError, FormatError, OSError) as err:
            try:                       # stash not written: put the character back as it was
                commit_file(self.ch.path, before_char, lambda tmp: None, backup=False)
            except Exception:
                pass
            messagebox.showerror(T('ch.btn.from_stash', 'From stash'), str(err), parent=self.top)
            self.reload()
        app.load(app.path, keep_selection=False)

    def delete(self):
        e = self.selected
        if e is None:
            self.set_status(T('ch.select_first', 'Select an item of the character first.'), True)
            return
        name = clean(e.it['name'])
        if not messagebox.askyesno(T('ch.btn.delete', 'Delete'), T('ch.delete_ask', 'Delete {item} (and its socketed items) from '
                                   '{name}?\nA backup of the character is made first.', item=name, name=self.ch.name), parent=self.top):
            return
        self.ch.remove(e, self.is_merc())
        self.save(T('cr.deleted', '{name} deleted', name=name))

    def edit(self):
        e = self.selected
        if e is None:
            self.set_status(T('ch.select_first', 'Select an item of the character first.'), True)
            return
        merc = self.is_merc()
        where = ('slot', e.it['equipped']) if e.it['location'] == MC.LOC_EQUIPPED else ('inv', e.it['x'], e.it['y'])

        def write(blob):
            self.ch.remove(e, merc)
            try:
                self.ch.place(blob, where, merc)
            except CharError:
                if where[0] == 'inv':
                    spot = self.ch.free_inventory_spot(mxl_save.blob_item(blob, self.data))
                    if spot is None:
                        self.reload()
                        raise
                    self.ch.place(blob, ('inv',) + spot, merc)
                else:
                    self.reload()
                    raise
            if not self.save(T('ch.replaced', '{item} replaced', item=clean(e.it['name']))):
                raise CharError(T('ch.err.not_saved', 'character not saved'))
            return T('ch.item_replaced', '{name}: item replaced', name=self.ch.name)
        CreatorWindow(self.app, edit_item=e.it, target=write, target_label=f'{self.ch.name}')

    def create_here(self):
        merc = self.is_merc()
        slot = self.sel_slot

        def write(blob):
            if slot is not None and self.ch.equipped(merc).get(slot) is None:
                where = ('slot', slot)
            else:
                if merc:
                    raise CharError(T('ch.err.merc_slot', 'select an empty mercenary slot first'))
                spot = self.ch.free_inventory_spot(mxl_save.blob_item(blob, self.data))
                if spot is None:
                    raise CharError(T('ch.err.inv_full', 'no free place in the inventory'))
                where = ('inv',) + spot
            try:
                self.ch.place(blob, where, merc)
            except CharError:
                self.reload()
                raise
            if not self.save(T('ch.item_created', 'item created')):
                raise CharError(T('ch.err.not_saved', 'character not saved'))
            return T('ch.created_in', '{name}: item created in {where}', name=self.ch.name,
                     where=slot_name(where[1]) if where[0] == 'slot' else T('ch.the_inventory', 'the inventory'))
        label = (f"{self.ch.name}{T('ch.merc_suffix', ' (mercenary)') if merc else ''} — "
                 + (slot_name(slot, T('ch.inventory_l', 'inventory')) if slot else T('ch.inventory_l', 'inventory')))
        CreatorWindow(self.app, target=write, target_label=label)

    # ================================================================== Hero Editor style tabs
    # Stats / Waypoints / Quests tabs in the look of the old Hero Editor (Mastadex, ZonFire99): classic grey
    # frames, Times New Roman, white tree panes with check boxes, the tab row at the bottom of the window.
    def _build_progress(self):
        ch, d = self.ch, self.data
        self._init_state()
        # ---------------------------------------------------------------- Stats
        f = self.st_tab
        self.sv = {}

        def frame(parent, text, **grid):
            fr = tk.LabelFrame(parent, text=text, font=HE_FONT_I, bg=HE_BG, fg='#000000', bd=2, relief='groove',
                               padx=8, pady=4)
            fr.grid(sticky='nsew', padx=6, pady=4, **grid)
            return fr

        def field(parent, r, label, key, width=12, col=0):
            tk.Label(parent, text=label, font=HE_FONT, bg=HE_BG).grid(row=r, column=col, sticky='e', padx=(0, 4), pady=1)
            e = tk.Entry(parent, width=width, font=HE_FONT, relief='sunken', bd=2, readonlybackground='#ffffff',
                         fg='#000000', justify='right')
            e.grid(row=r, column=col + 1, sticky='w', pady=1)
            e.configure(state='readonly')
            self.sv[key] = e
            return e

        for c in range(3):
            f.grid_columnconfigure(c, weight=1, uniform='he')
        fr = frame(f, T('he.name', 'Name'), row=0, column=0)
        self.sv_name = tk.Label(fr, text='', font=('Times New Roman', 14, 'bold'), bg=HE_BG, anchor='w')
        self.sv_name.grid(row=0, column=0, columnspan=3, sticky='w')
        tk.Label(fr, text=T('he.copy_as', 'Copy as'), font=HE_FONT, bg=HE_BG).grid(row=1, column=0, sticky='w', pady=(4, 0))
        self.copy_name = tk.StringVar()
        tk.Entry(fr, textvariable=self.copy_name, width=16, font=HE_FONT, relief='sunken', bd=2).grid(row=1, column=1, pady=(4, 0))
        tk.Button(fr, text=T('he.copy', 'Copy'), font=HE_FONT, command=self.copy_char, bg=HE_BTN).grid(row=1, column=2, padx=4, pady=(4, 0))

        fr = frame(f, T('he.class', 'Class'), row=1, column=0)
        field(fr, 0, T('he.class_c', 'Class:'), 'class', 14)
        fr = frame(f, T('he.level', 'Level'), row=2, column=0)
        tk.Label(fr, text=T('he.level_c', 'Level:'), font=HE_FONT, bg=HE_BG).grid(row=0, column=0, sticky='e', padx=(0, 4))
        self.new_level = tk.StringVar(value=str(ch.level))
        self.level_box = ttk.Combobox(fr, state='readonly', width=6, textvariable=self.new_level)
        self.level_box.grid(row=0, column=1, sticky='w')
        tk.Label(fr, text=T('he.level_note', 'Raising the level works like the game: experience, 5 stat points\n'
                            'and 1 skill point per level, life / mana / stamina per level.'),
                 font=HE_FONT_S, bg=HE_BG, justify='left').grid(row=1, column=0, columnspan=2, sticky='w', pady=(2, 0))
        fr = frame(f, T('he.experience', 'Experience'), row=3, column=0)
        field(fr, 0, T('he.experience_c', 'Experience:'), 'exp', 14)

        fr = frame(f, T('he.characteristics', 'Characteristics'), row=0, column=1, rowspan=2)
        for r, (lab, key) in enumerate(((T('he.str', 'Strength:'), 'str'), (T('he.dex', 'Dexterity:'), 'dex'),
                                        (T('he.vit', 'Vitality:'), 'vit'), (T('he.ene', 'Energy:'), 'ene'),
                                        (T('he.statpts', 'Stat Points Left:'), 'statpts'),
                                        (T('he.skillpts', 'Skill Points Left:'), 'skillpts'))):
            field(fr, r, lab, key, 8)
        fr = frame(f, T('he.constitution', 'Constitution'), row=2, column=1, rowspan=2)
        tk.Label(fr, text=T('he.current', 'Current'), font=HE_FONT_S, bg=HE_BG).grid(row=0, column=1)
        tk.Label(fr, text=T('he.max', 'Max'), font=HE_FONT_S, bg=HE_BG).grid(row=0, column=3)
        for r, (lab, key) in enumerate(((T('he.life', 'Life:'), 'life'), (T('he.mana', 'Mana:'), 'mana'),
                                        (T('he.stamina', 'Stamina:'), 'stamina')), start=1):
            field(fr, r, lab, key + '_cur', 8)
            tk.Label(fr, text='/', font=HE_FONT, bg=HE_BG).grid(row=r, column=2, padx=2)
            e = tk.Entry(fr, width=8, font=HE_FONT, relief='sunken', bd=2, readonlybackground='#ffffff', justify='right')
            e.grid(row=r, column=3, sticky='w')
            e.configure(state='readonly')
            self.sv[key + '_max'] = e

        fr = frame(f, T('he.gold', 'Gold'), row=0, column=2)
        field(fr, 0, T('he.person', 'Person:'), 'gold', 12)
        field(fr, 1, T('he.stash', 'Stash:'), 'goldbank', 12)
        fr = frame(f, T('he.char_type', 'Character Type'), row=1, column=2)
        self.sv_type = {}
        for r, (text, bit) in enumerate(((T('he.expansion', 'Expansion'), 0x20), (T('he.ladder', 'Ladder'), 0x40),
                                         (T('he.hardcore', 'Hardcore'), 0x04), (T('he.dead', 'You were Dead'), 0x08))):
            v = tk.BooleanVar()
            tk.Checkbutton(fr, text=text, variable=v, font=HE_FONT, bg=HE_BG, state='disabled',
                           disabledforeground='#000000').grid(row=r, column=0, sticky='w')
            self.sv_type[bit] = v
        fr = frame(f, T('he.char_diff', 'Character Difficulty'), row=2, column=2)
        self.sv_diff = tk.IntVar()
        for r, n in enumerate(DIFFICULTIES):
            tk.Radiobutton(fr, text=difficulty_name(r), value=r, variable=self.sv_diff, font=HE_FONT, bg=HE_BG, state='disabled',
                           disabledforeground='#000000').grid(row=r, column=0, sticky='w')
        tk.Label(fr, text=T('he.diff_note', '(highest difficulty unlocked)'), font=HE_FONT_S, bg=HE_BG).grid(row=3, column=0, sticky='w')
        fr = frame(f, T('he.misc', 'Miscellaneous Info.'), row=3, column=2)
        field(fr, 0, T('he.file_size', 'File Size:'), 'size', 10)
        field(fr, 1, T('he.version', 'Version:'), 'version', 10)
        tk.Label(f, text=T('he.stats_note', 'Only the level can be raised here: every other value is shown as the game '
                           'stores it (stat and skill points are spent in game).'), font=HE_FONT_S, bg=HE_BG).grid(
            row=4, column=0, columnspan=3, sticky='w', padx=8, pady=(6, 0))

        # ---------------------------------------------------------------- Waypoints / Quests
        self.wp_trees = []
        self.q_trees = []
        for k, _dn in enumerate(DIFFICULTIES):
            dn = difficulty_name(k)
            self.wp_tab.grid_columnconfigure(k, weight=1, uniform='he')
            self.q_tab.grid_columnconfigure(k, weight=1, uniform='he')
            fr = tk.LabelFrame(self.wp_tab, text=dn, font=HE_FONT_I, bg=HE_BG, bd=2, relief='groove', padx=4, pady=4)
            fr.grid(row=0, column=k, sticky='nsew', padx=6, pady=4)
            t = CheckTree(fr, self.on_tree_change)
            root = t.add('', T('he.mode_waypoints', '{mode} Mode Waypoints', mode=dn))
            for act, wps in WAYPOINTS:
                a = t.add(root, act)
                for n, name in wps:
                    if n in MC.HELL_ONLY_WAYPOINTS and k != 2:
                        continue
                    t.add(a, name + (T('he.hell_suffix', ' (Hell)') if n in MC.HELL_ONLY_WAYPOINTS else ''), key=('wp', k, n), locked=(n == 0))
            t.open(root)
            self.wp_trees.append(t)
            fr = tk.LabelFrame(self.q_tab, text=dn, font=HE_FONT_I, bg=HE_BG, bd=2, relief='groove', padx=4, pady=4)
            fr.grid(row=0, column=k, sticky='nsew', padx=6, pady=4)
            t = CheckTree(fr, self.on_tree_change)
            root = t.add('', T('he.mode_quests', '{mode} Mode Quests', mode=dn))
            for act, intro, quests, done in ACTS:
                a = t.add(root, act)
                for word, key in quests:
                    r = MC.QUEST_REWARDS.get(word)
                    extra = ''
                    if r:
                        extra = {'skill': (T('he.reward.skill1', '  (+{n} skill point)', n=r[1]) if r[1] == 1 else
                                           T('he.reward.skill', '  (+{n} skill points)', n=r[1])),
                                 'stats': T('he.reward.stats', '  (+{n} stat points)', n=r[1]),
                                 'life': T('he.reward.life', '  (+{n} life)', n=r[1])}[r[0]]
                    t.add(a, clean(d.key(key)) + extra, key=('q', k, word))
            t.open(root)
            self.q_trees.append(t)
        self.wp_tab.grid_rowconfigure(0, weight=1)
        self.q_tab.grid_rowconfigure(0, weight=1)
        tk.Label(self.wp_tab, text=T('he.wp_note', 'Waypoints marked (Hell) are Median XL areas that only exist in Hell. '
                                     'Click a box to tick it; a mode or act box ticks everything under it.'),
                 font=HE_FONT_S, bg=HE_BG).grid(row=1, column=0, columnspan=3, sticky='w', padx=8)
        tk.Label(self.q_tab, text=T('he.q_note', 'Ticking a quest gives its reward in that difficulty (shown in brackets). '
                                    'Quests completed in game stay ticked and cannot be changed here.'),
                 font=HE_FONT_S, bg=HE_BG).grid(row=1, column=0, columnspan=3, sticky='w', padx=8)
        self.refresh_progress()

    def _init_state(self):
        ch = self.ch
        self.q0 = {k: {w for _, _, quests, _ in ACTS for w, _ in quests if ch.quest_done(k, w)} for k in range(3)}
        self.w0 = {k: {n for n in MC.waypoint_ids(k) if ch.waypoint(k, n)} | {0} for k in range(3)}
        self.qstate = {k: set(v) for k, v in self.q0.items()}
        self.wstate = {k: set(v) for k, v in self.w0.items()}
        # quests completed in game (not by this tool): never unticked
        self.qlocked = {k: {w for w in self.q0[k] if ch.quest_word(k, w) != MC.QUEST_DONE} for k in range(3)}

    def on_tree_change(self, key, value):
        kind, k, n = key
        st = self.wstate[k] if kind == 'wp' else self.qstate[k]
        (st.add if value else st.discard)(n)
        self.update_dirty()

    def dirty(self):
        return (self.qstate != self.q0 or self.wstate != self.w0
                or self.new_level.get() != str(self.ch.level))

    def update_dirty(self):
        if self.dirty():
            self.set_bar(T('he.unsaved', 'Unsaved changes: click Save.'))
        else:
            self.set_bar('')

    def refresh_progress(self):
        ch, d = self.ch, self.data
        self.sv_name.configure(text=ch.name)
        self.level_box.configure(values=[str(v) for v in range(ch.level, MC.max_level(d, ch.cls) + 1)])
        self.new_level.set(str(ch.level))
        try:
            from mxl_rules import CLASSES
            cname = CLASSES[ch.cls]
        except Exception:
            cname = str(ch.cls)
        vals = {'class': cname, 'exp': f'{ch.stat(13):,}', 'str': ch.stat(0), 'ene': ch.stat(1), 'dex': ch.stat(2),
                'vit': ch.stat(3), 'statpts': ch.stat(4), 'skillpts': ch.stat(5),
                'life_cur': ch.stat(6) >> 8, 'life_max': ch.stat(7) >> 8, 'mana_cur': ch.stat(8) >> 8,
                'mana_max': ch.stat(9) >> 8, 'stamina_cur': ch.stat(10) >> 8, 'stamina_max': ch.stat(11) >> 8,
                'gold': f'{ch.stat(14):,}', 'goldbank': f'{ch.stat(15):,}', 'size': f'{len(ch.raw):,}',
                'version': str(struct.unpack_from('<I', ch.raw, 4)[0])}
        for key, e in self.sv.items():
            e.configure(state='normal')
            e.delete(0, 'end')
            e.insert(0, str(vals.get(key, '')))
            e.configure(state='readonly')
        status = ch.head[0x24]
        for bit, v in self.sv_type.items():
            v.set(bool(status & bit))
        self.sv_diff.set(min(2, ch.progression // 5) if ch.progression >= 5 else 0)
        for k in range(3):
            self.wp_trees[k].set_values({('wp', k, n): n in self.wstate[k] for n in MC.waypoint_ids(k)})
            self.q_trees[k].set_values({('q', k, w): w in self.qstate[k] for _, _, qs, _ in ACTS for w, _ in qs})
            for _, _, qs, _ in ACTS:
                for w, _ in qs:
                    self.q_trees[k].set_locked(('q', k, w), w in self.qlocked[k])
        self.update_dirty()

    def all_waypoints(self):
        for k in range(3):
            self.wstate[k] = set(MC.waypoint_ids(k))
        self.refresh_progress()

    def save_progress(self):
        ch = self.ch
        try:
            lvl = int(self.new_level.get())
            before = ch.level
            ch.set_level(lvl)
            for k in range(3):
                if self.qstate[k] != self.q0[k]:
                    ch.set_quests(k, self.qstate[k])
                if self.wstate[k] != self.w0[k]:
                    ch.set_waypoints(k, self.wstate[k])
        except CharError as e:
            messagebox.showerror(T('ch.title', 'Character'), str(e), parent=self.top)
            self.reload()
            return
        log, ch.reward_log = getattr(ch, 'reward_log', {}), {}
        names = {'skill': (T('he.n_skill1', 'skill point'), T('he.n_skill', 'skill points')),
                 'stats': (T('he.n_stat1', 'stat point'), T('he.n_stat', 'stat points')),
                 'life': (T('he.n_life', 'life'), T('he.n_life', 'life'))}
        rewards = ', '.join(f'{v:+d} {names[k][0] if abs(v) == 1 else names[k][1]}' for k, v in log.items() if v)
        if self.save(T('he.saved', '{name} saved', name=ch.name)):
            self._init_state()
            self.refresh_progress()
            self.set_bar(T('he.saved_level', '{name} saved: level {level}', name=ch.name, level=ch.level)
                         + (T('he.saved_up', ' (+{n}: stat and skill points to spend in game)', n=ch.level - before)
                            if ch.level > before else '')
                         + (T('he.saved_rewards', ' — quest rewards: {rewards}', rewards=rewards) if rewards else ''))
            self.top.title(f'Median XL Capybara Editor — {ch.name}')

    def copy_char(self):
        name = self.copy_name.get().strip()
        try:
            dst = MC.copy_character(self.ch.path, name, self.data)
        except (CharError, EditError, OSError) as e:
            messagebox.showerror(T('he.copy_title', 'Copy character'), str(e), parent=self.top)
            return
        self.set_bar(T('he.copied', '{name} copied as {new} ({file}).', name=self.ch.name, new=name, file=os.path.basename(dst)))
        self.app.select_character(self.app.char_combo.get(), self.app.view.get())

    def set_bar(self, text, error=False):
        self.bar_status.configure(text=text, fg='#ff6060' if error else HE_GOLD)


HE_BG = '#f0f0f0'
HE_BTN = '#e1e1e1'
HE_GOLD = '#d8c27a'


class StoneButton(tk.Label):
    """The Hero Editor's own stone button images (normal / hover / pressed)."""

    def __init__(self, parent, text, command):
        import mxl_he_art as A
        self.imgs = [tk.PhotoImage(master=parent, data=d) for d in A.BUTTONS[text]]
        super().__init__(parent, image=self.imgs[0], bd=0, bg='#000000', cursor='hand2', padx=0, pady=0)
        self.command = command
        self.inside = False
        self.bind('<Enter>', lambda e: self._state(1, True))
        self.bind('<Leave>', lambda e: self._state(0, False))
        self.bind('<ButtonPress-1>', lambda e: self.configure(image=self.imgs[2]))
        self.bind('<ButtonRelease-1>', self._release)

    def _state(self, i, inside):
        self.inside = inside
        self.configure(image=self.imgs[i])

    def _release(self, e):
        self.configure(image=self.imgs[1] if self.inside else self.imgs[0])
        if self.inside:
            self.command()


HE_FONT = ('Times New Roman', 11)
HE_FONT_I = ('Times New Roman', 11, 'italic')
HE_FONT_S = ('Times New Roman', 10)


THEMES = {
    False: dict(bg='#f0f0f0', fg='#000000', entry='#ffffff', entry_fg='#000000', btn='#e1e1e1', frame_fg='#000000',
                pane='#ffffff', pane_fg='#000000', tab='#e8e8e8', tab_fg='#000000', tab_sel='#ffffff',
                box_fill='#ffffff', box_mark='#000000', box_dark='#404040', box_mid='#808080', box_light='#d4d0c8',
                part_fill='#d4d0c8', part_mark='#808080', lock_fill='#b8b8b8', lock_mark='#404040'),
    True: dict(bg='#1e1e1e', fg='#d6d6d6', entry='#2b2b2b', entry_fg='#ececec', btn='#3a3a3a', frame_fg='#d8c27a',
               pane='#252526', pane_fg='#e0e0e0', tab='#3a3a3a', tab_fg='#d6d6d6', tab_sel='#5a5a5a',
               box_fill='#161616', box_mark='#f0f0f0', box_dark='#707070', box_mid='#a0a0a0', box_light='#808080',
               part_fill='#4a4a4a', part_mark='#a0a0a0', lock_fill='#555555', lock_mark='#c8c8c8'),
}


def _paint_box(im, kind, t):
    """Draws a 13×13 check box (kind: off / on / part / lock) into the 15×13 image im, in theme t."""
    fill, mark = {'off': (t['box_fill'], None), 'on': (t['box_fill'], t['box_mark']),
                  'part': (t['part_fill'], t['part_mark']), 'lock': (t['lock_fill'], t['lock_mark'])}[kind]
    im.put(t['pane'], to=(0, 0, 15, 13))
    im.put(t['box_mid'], to=(0, 0, 13, 1))
    im.put(t['box_mid'], to=(0, 0, 1, 13))
    im.put(t['box_dark'], to=(1, 1, 12, 2))
    im.put(t['box_dark'], to=(1, 1, 2, 12))
    im.put(t['pane'], to=(12, 0, 13, 13))
    im.put(t['pane'], to=(0, 12, 13, 13))
    im.put(t['box_light'], to=(11, 1, 12, 12))
    im.put(t['box_light'], to=(1, 11, 12, 12))
    im.put(fill, to=(2, 2, 11, 11))
    if mark:
        for x, y in ((3, 5), (3, 6), (3, 7), (4, 6), (4, 7), (4, 8), (5, 7), (5, 8), (5, 9), (6, 6), (6, 7),
                     (6, 8), (7, 5), (7, 6), (7, 7), (8, 4), (8, 5), (8, 6), (9, 3), (9, 4), (9, 5)):
            im.put(mark, to=(x, y, x + 1, y + 1))


def _box_images(master, dark=False):
    """Check box images: unchecked, checked, partly checked, locked (checked, grey)."""
    imgs = {k: tk.PhotoImage(master=master, width=15, height=13) for k in ('off', 'on', 'part', 'lock')}
    for k, im in imgs.items():
        _paint_box(im, k, THEMES[dark])
    return imgs


class CheckTree:
    """Tree with check boxes (like the Hero Editor's TreeView): leaves carry a value; a mode / act box shows
    whether everything, nothing or part of what is under it is ticked, and ticks / unticks all of it."""

    def __init__(self, parent, on_change):
        style = ttk.Style()
        style.configure('HE.Treeview', background='#ffffff', fieldbackground='#ffffff', foreground='#000000',
                        font=HE_FONT, rowheight=27, borderwidth=0, indent=26)
        style.map('HE.Treeview', background=[('selected', '#ffffff')], foreground=[('selected', '#000000')])
        style.layout('HE.Treeview', [('Treeview.treearea', {'sticky': 'nswe'})])
        wrap = tk.Frame(parent, bg='#ffffff', bd=2, relief='sunken')
        self.wrap = wrap
        wrap.pack(fill='both', expand=True)
        self.tv = ttk.Treeview(wrap, show='tree', style='HE.Treeview', selectmode='browse')
        sb = ttk.Scrollbar(wrap, orient='vertical', command=self.tv.yview)
        self.tv.configure(yscrollcommand=sb.set)
        self.tv.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        self.img = _box_images(parent)
        self.on_change = on_change
        self.keys = {}          # iid -> key (leaves)
        self.iids = {}          # key -> iid
        self.value = {}         # iid -> bool (leaves)
        self.locked = set()
        self.tv.bind('<Button-1>', self.on_click)
        self.tv.bind('<space>', lambda e: self.toggle(self.tv.focus()))

    def add(self, parent, text, key=None, locked=False):
        iid = self.tv.insert(parent, 'end', text=' ' + text, image=self.img['off'])
        if key is not None:
            self.keys[iid] = key
            self.iids[key] = iid
            self.value[iid] = False
            if locked:
                self.locked.add(iid)
        return iid

    def set_theme(self, dark):
        t = THEMES[dark]
        style = ttk.Style()
        style.configure('HE.Treeview', background=t['pane'], fieldbackground=t['pane'], foreground=t['pane_fg'])
        style.map('HE.Treeview', background=[('selected', t['pane'])], foreground=[('selected', t['pane_fg'])])
        self.wrap.configure(bg=t['pane'])
        for k, im in self.img.items():
            _paint_box(im, k, t)

    def open(self, iid):
        self.tv.item(iid, open=True)

    def leaves(self, iid):
        if iid in self.keys:
            return [iid]
        out = []
        for c in self.tv.get_children(iid):
            out += self.leaves(c)
        return out

    def set_values(self, values):
        for key, v in values.items():
            iid = self.iids.get(key)
            if iid is not None:
                self.value[iid] = bool(v) or iid in self.locked
        self.paint()

    def set_locked(self, key, locked):
        iid = self.iids.get(key)
        if iid is None:
            return
        (self.locked.add if locked else self.locked.discard)(iid)
        if locked:
            self.value[iid] = True
        self.paint()

    def paint(self, iid=''):
        for c in self.tv.get_children(iid):
            if c in self.keys:
                img = 'lock' if c in self.locked else ('on' if self.value[c] else 'off')
            else:
                self.paint(c)
                vals = [self.value[l] for l in self.leaves(c)]
                img = 'on' if vals and all(vals) else ('part' if any(vals) else 'off')
            self.tv.item(c, image=self.img[img])

    def toggle(self, iid):
        if not iid:
            return
        leaves = [l for l in self.leaves(iid) if l not in self.locked]
        if not leaves:
            return
        new = not all(self.value[l] for l in leaves)
        for l in leaves:
            if self.value[l] != new:
                self.value[l] = new
                self.on_change(self.keys[l], new)
        self.paint()

    def on_click(self, ev):
        iid = self.tv.identify_row(ev.y)
        if not iid:
            return
        elem = self.tv.identify_element(ev.x, ev.y)
        if 'image' in elem:
            self.tv.focus(iid)
            self.tv.selection_set(iid)
            self.toggle(iid)
            return 'break'


def install(App):
    original = App._build

    def _build(self):
        original(self)
        top = self.btn_library.master
        bar = ttk.Frame(top)
        bar.pack(side='left', padx=(6, 0))
        ttk.Button(bar, text=T('ch.title', 'Character'), command=lambda: CharacterWindow(self, 'stats')).pack(side='left', padx=2)

    App._build = _build
