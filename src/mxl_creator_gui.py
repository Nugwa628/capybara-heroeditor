"""Item Creator / Editor window for the MXL Item Collector (added to the main window by install()).

- Create: choose a unique, set item, runeword or base item (normal, superior, magic, rare), its options and the
  value of every variable mod (always inside the game's own ranges), preview it with the collector's tooltip, and
  write it into the stash shown in the main window (first free place of the chosen page).
- Edit: the selected stash item is loaded into the same window; "Replace item" writes the new version in its place.
- Delete: removes the selected stash item (and its socketed items).
- "Value editing" toggles the collector's built-in in-place sliders (setting editing_enabled).
Every write goes through mxl_save.edit_stash (game must be closed, verification after writing, .bak backup).
"""
import os
import sys
import random
import tkinter as tk
from tkinter import ttk, messagebox

import settings
import mxl_save
from mxl_save import edit_stash, parse_stash, free_spot, blob_item, EditError, STASH_PAGES
from mxl_format import FormatError
from mxl_rules import stat_label, stat_hidden, mods_stats
from mxl_gui_detail import DetailHost, DetailPanel, setup_detail_tags
from mxl_creator import Creator, CreateError, MAX_ILVL, CRAFTED_AFFIXES
from mxl_lang import T
import mxl_d2look as D2

QUALITY_KEYS = [('Unique', 'unique'), ('Set item', 'set'), ('Runeword', 'runeword'), ('Angelic', 'angelic'), ('Rare', 'rare'),
                ('Crafted', 'crafted'), ('Magic', 'magic'), ('Superior', 'superior'), ('Normal', 'normal')]
FILTER_KEYS = [('All', None), ('Weapon', 'weapons'), ('Armor', 'armor'), ('Misc', 'misc'), ('Mystic Orb', 'orb'),
               ('Mystic Dye', 'dye')]


def qualities():
    """[(label in the chosen language, quality key)] of the item type list."""
    return [(T(f'cr.quality.{k}', lab), k) for lab, k in QUALITY_KEYS]


def filters():
    return [(T(f'cr.filter.{k or "all"}', lab), k) for lab, k in FILTER_KEYS]


def none_label():
    return T('cr.none', '(none)')


def empty_label():
    return T('cr.empty', '(empty)')


def kind_label(kind):
    return T(f'cr.kind.{kind}', {'weapons': 'Weapon', 'armor': 'Armor', 'misc': 'Misc'}.get(kind, ''))


class PreviewPanel(DetailPanel):
    """Collector's detail panel without its edit / transfer sections (tooltip only)."""

    def write_edit(self, it):
        return None

    def write_transfer(self, it):
        return None


class PreviewHost(DetailHost):
    """Read-only host of the preview panel."""

    def __init__(self, app):
        self.app = app
        self.saved_priorities = {}
        self.editing = False

    @property
    def data(self):
        return self.app.data

    @property
    def char(self):
        return self.app.char


def clean(name):
    """One-line display name (multi-line game strings joined, colour codes removed)."""
    txt = str(name).replace('\\n', '\n')
    while 'ÿc' in txt:
        i = txt.index('ÿc')
        txt = txt[:i] + txt[i + 3:]
    return ' '.join(part.strip() for part in txt.split('\n') if part.strip())


class ScrollFrame(ttk.Frame):
    """Vertical scroll area; the mouse wheel scrolls it anywhere over its content (comboboxes included: the wheel
    never changes their value by accident)."""

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        bg = ttk.Style().lookup('TFrame', 'background') or None
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, bg=bg)
        self.sb = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.inner.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.win = self.canvas.create_window((0, 0), window=self.inner, anchor='nw')
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(self.win, width=e.width))
        self.canvas.configure(yscrollcommand=self.sb.set)
        self.canvas.pack(side='left', fill='both', expand=True)
        self.sb.pack(side='left', fill='y')
        self.bind('<Enter>', lambda e: self._grab(True))
        self.bind('<Leave>', lambda e: self._grab(self._inside(e)))

    def _inside(self, e):
        try:
            w = self.winfo_containing(e.x_root, e.y_root)
        except (tk.TclError, KeyError):
            return False
        return w is not None and str(w).startswith(str(self))

    def _grab(self, on):
        try:
            if on:
                self.bind_all('<MouseWheel>', self._wheel)
                self.bind_all('<Button-4>', self._wheel)
                self.bind_all('<Button-5>', self._wheel)
            else:
                for ev in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
                    self.unbind_all(ev)
        except tk.TclError:
            pass

    def _wheel(self, e):
        if not self._inside(e):
            return None
        if getattr(e, 'num', None) in (4, 5):
            step = -1 if e.num == 4 else 1
        else:
            step = int(-e.delta / 120) or (-1 if e.delta > 0 else 1)
        top, bottom = self.canvas.yview()
        if top > 0 or bottom < 1:
            self.canvas.yview_scroll(step, 'units')
        return 'break'

    def no_wheel(self, widget):
        """The wheel over this widget scrolls the area instead of changing the widget's value."""
        for ev in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
            widget.bind(ev, self._wheel)

    def scroll_top(self):
        self.canvas.yview_moveto(0)


class CreatorWindow:
    """Toplevel of the creator. edit_item: item of the open stash to edit (None: create a new item)."""

    def __init__(self, app, edit_item=None, target=None, target_label=None):
        self.app = app
        self.target = target              # callable(blob) -> message: writes somewhere else than the stash
        self.target_label = target_label
        self.data = app.data
        self.cr = getattr(app, '_creator', None)
        if self.cr is None or self.cr.data is not app.data:
            self.cr = app._creator = Creator(app.data)
        self.edit_item = edit_item
        self.spec = None
        self.entries = []
        self.mod_vars = {}
        self.top = tk.Toplevel(app.root)
        self.top.title(T('cr.title.editor', 'Item Editor') if edit_item else T('cr.title.creator', 'Item Creator'))
        sw, sh = self.top.winfo_screenwidth(), self.top.winfo_screenheight()
        w, h = min(1750, max(900, sw - 60)), min(1080, max(560, sh - 110))
        w, h = min(w, sw), min(h, sh)
        self.top.geometry(f'{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}')
        self.top.minsize(min(900, sw), min(540, sh))
        self._build()
        self.top.after_idle(self._place_sashes)
        if edit_item is not None:
            try:
                spec = self.cr.spec_from_item(edit_item)
            except CreateError as e:
                messagebox.showerror(T('cr.title.editor', 'Item Editor'),
                                     T('cr.err.not_editable', 'This item cannot be edited here:\n{error}', error=e),
                                     parent=self.top)
                self.top.destroy()
                return
            self.load_spec(spec)
        else:
            self.quality.set(qualities()[0][0])
            self.on_quality()

    # ------------------------------------------------------------------ layout
    def _build(self):
        top = self.top
        # three resizable columns: catalog list | options and values | preview (drag the separators)
        self.paned = ttk.Panedwindow(top, orient='horizontal')
        self.paned.pack(fill='both', expand=True)
        left = ttk.Frame(self.paned, padding=6)
        self.paned.add(left, weight=1)
        ttk.Label(left, text=T('cr.item_type', 'Item type')).pack(anchor='w')
        self.quality = tk.StringVar()
        cb = ttk.Combobox(left, state='readonly', values=[q for q, _ in qualities()], textvariable=self.quality, width=34)
        cb.pack(fill='x')
        cb.bind('<<ComboboxSelected>>', lambda e: self.on_quality())
        self.kind_filter = tk.StringVar(value=filters()[0][0])
        kf = ttk.Combobox(left, state='readonly', values=[lab for lab, _ in filters()],
                          textvariable=self.kind_filter, width=34)
        kf.pack(fill='x', pady=(4, 0))
        kf.bind('<<ComboboxSelected>>', lambda e: self.fill_list())
        ttk.Label(left, text=T('cr.search', 'Search')).pack(anchor='w', pady=(6, 0))
        self.search = tk.StringVar()
        ent = ttk.Entry(left, textvariable=self.search)
        ent.pack(fill='x')
        self.search.trace_add('write', lambda *a: self.fill_list())
        lf = ttk.Frame(left)
        lf.pack(fill='both', expand=True, pady=(4, 0))
        self.listbox = tk.Listbox(lf, width=34, height=10, exportselection=False, activestyle='none',
                                  bg=self.app.detail.cget('bg'), fg=self.app.detail.cget('fg'),
                                  selectbackground='#3a5f8a', selectforeground='white', highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(lf, orient='vertical', command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.pack(side='left', fill='both', expand=True)
        sb.pack(side='left', fill='y')
        self.listbox.bind('<<ListboxSelect>>', lambda e: self.on_pick())
        self.count = ttk.Label(left, text='')
        self.count.pack(anchor='w')

        mid = self._mid = ttk.Frame(self.paned, padding=6)
        self.paned.add(mid, weight=4)
        self.title = ttk.Label(mid, text='', font=('Segoe UI', 12, 'bold'))
        self.header = None
        if D2.active():                                   # D2 look: the game's stone, fonts and gear frame
            try:
                self.header = D2.CreatorHeader(mid, T('cr.title.editor', 'Item Editor') if self.edit_item else
                                               T('cr.title.creator', 'Item Creator'), getattr(self.app, 'cell', 32))
            except Exception as e:
                print('D2 look (creator header) disabled:', e)
                self.header = None
        if self.header is None:
            self.title.pack(side='top', anchor='w')

        bottom = ttk.Frame(mid)                          # packed before the scroll area: never pushed out of sight
        bottom.pack(side='bottom', fill='x', pady=(6, 0))
        self.status = ttk.Label(bottom, text='', wraplength=520, justify='left')
        self.status.pack(side='top', anchor='w', fill='x')
        self.status.bind('<Configure>', lambda e: self.status.configure(wraplength=max(200, e.width - 8)))
        row = ttk.Frame(bottom)
        row.pack(fill='x', pady=(4, 0))
        self.btn_ok = D2.button(row, T('cr.btn.replace', 'Replace item') if self.edit_item else
                                (T('cr.btn.create', 'Create') if self.target else T('cr.btn.create_stash', 'Create in stash')),
                                self.commit)
        self.btn_ok.pack(side='right')            # packed first: never squeezed out of the window
        D2.button(row, T('cr.btn.close', 'Close'), self.top.destroy).pack(side='right', padx=6)
        ttk.Label(row, text=T('cr.stash_page', 'Stash page')).pack(side='left')
        self.page_current, self.page_first = T('cr.page.current', 'Current page'), T('cr.page.first_free', 'First free')
        self.page = tk.StringVar(value=self.page_current)
        ttk.Combobox(row, state='readonly', width=14, textvariable=self.page,
                     values=[self.page_current, self.page_first] + [str(i + 1) for i in range(STASH_PAGES)]).pack(side='left', padx=4)
        self.dest = ttk.Label(row, text='')
        self.dest.pack(side='left', padx=6)

        self.scroll = ScrollFrame(mid)
        self.scroll.pack(side='top', fill='both', expand=True, pady=(4, 0))
        body = self.scroll.inner
        body.columnconfigure(0, weight=1)
        self.opts = ttk.Frame(body)
        self.opts.grid(row=0, column=0, sticky='we', pady=(0, 4))
        self.opts.columnconfigure(1, weight=1)
        bar = ttk.Frame(body)
        bar.grid(row=1, column=0, sticky='we', pady=(6, 0))
        ttk.Label(bar, text=T('cr.variable_values', 'Variable values'), font=('Segoe UI', 10, 'bold')).pack(side='left')
        for t, how in ((T('cr.btn.max', 'Max'), 'max'), (T('cr.btn.random', 'Random'), 'random'), (T('cr.btn.min', 'Min'), 'min')):
            D2.button(bar, t, lambda h=how: self.reroll(h), 8).pack(side='right', padx=2)

        class _Mods:
            pass
        self.mods_frame = _Mods()
        self.mods_frame.inner = ttk.Frame(body)
        self.mods_frame.inner.grid(row=2, column=0, sticky='we', pady=(4, 0))
        self.mods_frame.inner.columnconfigure(1, weight=1)

        right = ttk.Frame(self.paned, padding=6)
        self.paned.add(right, weight=1)
        app_detail = self.app.detail
        self.preview = tk.Text(right, width=int(app_detail.cget('width')), wrap='word', bg=app_detail.cget('bg'),
                               fg=app_detail.cget('fg'), bd=0, padx=int(str(app_detail.cget('padx'))), pady=8,
                               font=app_detail.cget('font'), cursor='arrow', highlightthickness=0)
        psb = ttk.Scrollbar(right, orient='vertical', command=self.preview.yview)
        self.preview.configure(yscrollcommand=psb.set)
        psb.pack(side='right', fill='y')
        self.preview.pack(side='left', fill='both', expand=True)
        setup_detail_tags(self.preview)
        self.panel = PreviewPanel(self.preview, PreviewHost(self.app))
        self.update_dest()

    def _place_sashes(self):
        """Columns: list ~22 %, options ~50 %, preview ~28 % of the window (at least what the preview needs)."""
        try:
            self.top.update_idletasks()
            w = self.paned.winfo_width()
            if w < 50:
                return
            pw = max(300, min(int(w * 0.28), 460))
            lw = max(220, min(int(w * 0.22), 360))
            self.paned.sashpos(0, lw)
            self.paned.sashpos(1, max(lw + 300, w - pw))
        except tk.TclError:
            pass

    def update_dest(self):
        if self.target is not None:
            self.dest.configure(text=f'→ {self.target_label}')
            return
        path = self.app.path
        self.dest.configure(text=f'→ {os.path.basename(path)}' if path else T('cr.no_stash', '→ (no stash open)'))

    # ------------------------------------------------------------------ catalog list
    def qkey(self):
        return dict(qualities()).get(self.quality.get(), getattr(self, '_q_override', 'unique'))

    def on_quality(self):
        self.fill_list()
        self.spec = None
        self.render_options()

    def _rw_has_base(self, i):
        cache = self.__dict__.setdefault('_rw_base_ok', {})
        if i not in cache:
            cache[i] = bool(self.cr.runeword_bases(i))
        return cache[i]

    def fill_list(self):
        q = self.qkey()
        s = self.search.get().strip().lower()
        fk = dict(filters()).get(self.kind_filter.get())
        orbs_only = fk == 'orb'
        dyes_only = fk == 'dye'
        kind = {'orb': 'misc', 'dye': 'misc'}.get(fk, fk)
        cr, d = self.cr, self.data
        rows = []
        if q in ('unique', 'set'):
            for i, name, u in (cr.uniques() if q == 'unique' else cr.set_items()):
                b = d.base(u['code'])
                if kind and b.kind != kind or orbs_only and cr.orb_effects(u['code']) is None or dyes_only:
                    continue
                label = f"{clean(name)} — {clean(b.name)}"
                rows.append((label, i))
        elif q == 'angelic':
            for b in cr.angelic_bases():
                if kind and b.kind != kind:
                    continue
                rows.append((f"{clean(b.name)} [{kind_label(b.kind)}]", b.code))
        elif q == 'runeword':
            for i, name, runes in cr.runewords():
                if not self._rw_has_base(i):
                    continue                                 # no base can hold it (Secret Jewelwords...)
                label = f"{clean(name)} — {' '.join(clean(d.base(r).name).replace(' Rune', '') for r in runes)}"
                rows.append((label, i))
        else:
            for b in (self._craft_bases() if q == 'crafted' else self._bases()):
                if kind and b.kind != kind:
                    continue
                if q != 'normal' and b.kind == 'misc' and not cr.can_have_quality(b.code, q):
                    continue
                if q == 'superior' and not cr.superior_choices(b.code):
                    continue
                if q != 'normal' and not cr.can_have_quality(b.code, q):
                    continue
                if q in ('magic', 'rare') and b.kind == 'misc' and not cr.can_roll_affixes(b.code, q):
                    continue                                 # no affix can spawn on it (quest items, relics...)
                if b.code == 'gld ':
                    continue                                 # gold is not an item (character / stash gold)
                orb = cr.orb_effects(b.code)
                if orbs_only and orb is None or dyes_only and cr.display_name(b.code) != 'Mystic Dye':
                    continue
                label = cr.display_name(b.code)
                if ' / ' in label:                                # long multi-line names: the top line only
                    label = label.split(' — ')[0]
                if orb and orb[0]:                                  # mystic orbs: their effect tells them apart
                    label += ': ' + '; '.join(orb[0])
                rows.append((f"{label} [{kind_label(b.kind)}]", b.code))
            seen = {}
            for label, _ in rows:
                seen[label] = seen.get(label, 0) + 1
            named = []
            for label, code in rows:                          # same name: the description tells them apart
                if seen[label] > 1 and label.endswith(']'):
                    summary = cr.desc_summary(code)
                    if not summary:                           # no description: the picture's name (e.g. dye-rainbow)
                        inv = (d.base(code).inv_file or '').lower()
                        if 'dye' in inv and 'rainbow' in inv:
                            summary = 'Color: Rainbow'
                        else:
                            inv = inv.replace('_', ' ').replace('-', ' ').strip()
                            summary = f'({inv})' if inv else ''
                    if summary:
                        head, tail = label.rsplit(' [', 1)
                        label = f"{head}: {summary} [{tail}"
                named.append((label, code))
            seen = {}
            for label, _ in named:
                seen[label] = seen.get(label, 0) + 1
            rows = [(f"{label[:-1]}, {code.strip()}]" if seen[label] > 1 and label.endswith(']') else label, code)
                    for label, code in named]
            rows.sort(key=lambda r: r[0].lower())
        if s:
            rows = [r for r in rows if s in r[0].lower()]
        self.entries = rows
        self.listbox.delete(0, 'end')
        for label, _ in rows:
            self.listbox.insert('end', label)
        self.count.configure(text=T('cr.entries', '{n} entries', n=len(rows)))

    def _bases(self):
        if not hasattr(self.cr, '_good_bases'):
            out = []
            for b in self.cr.bases():
                t = self.cr.t.item.get(b.code, {})
                name = clean(b.name)
                if not t.get('spawnable') or not name or name.lower() in ('unused', 'dummy'):
                    continue
                out.append(b)
            self.cr._good_bases = out
        return self.cr._good_bases

    def _craft_bases(self):
        """Bases a crafting recipe takes (Angelic bases included, they are not in the drop pool)."""
        if not hasattr(self.cr, '_craft_bases'):
            out = []
            for b in self.cr.bases():
                name = clean(b.name)
                if not name or name.lower() in ('unused', 'dummy'):
                    continue
                if self.cr.craft_rows(b.code):
                    out.append(b)
            self.cr._craft_bases = out
        return self.cr._craft_bases

    def on_pick(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        key = self.entries[sel[0]][1]
        q = self.qkey()
        cr = self.cr
        if q in ('unique', 'set'):
            spec = cr.new_spec(q, row=key)
        elif q == 'runeword':
            bases = cr.runeword_bases(key)
            if not bases:
                self.set_status(T('cr.no_rw_base', 'No base item can hold this runeword.'), error=True)
                return
            spec = cr.new_spec('runeword', bases[0].code, key)
            spec['sockets'] = len(self.data.runewords[key][1])
        elif q == 'angelic':
            spec = cr.new_spec('rare', key)                # Angelic items are rares on the Angelic bases
        else:
            spec = cr.new_spec(q, key)
        spec['ilvl'] = max(spec['ilvl'], min(99, MAX_ILVL))
        spec['max_sockets'] = q in ('unique', 'set', 'crafted')     # as the game gives them (Sacred gear)
        self.prepare_defaults(spec)
        if q == 'crafted':                     # one roll, as the cube gives it: name, affixes, values
            cr.reroll_crafted(spec)
        else:
            cr.roll(spec, 'max')
        self.spec = spec
        self.render_options()

    def prepare_defaults(self, spec):
        """Picks the first legit option wherever one is required (innate affix, superior bonus, names...)."""
        cr, code, q, ilvl = self.cr, spec['code'], spec['quality'], spec['ilvl']
        autos = cr.auto_choices(code, ilvl)              # every quality has its base's innate affix (Sigma)
        ids = [a for a, _, _ in autos]
        keep_none = bool(spec.get('extra')) and not spec.get('auto')   # edited item without one
        if ids and spec.get('auto') not in ids and not keep_none:
            spec['auto'] = ids[-1]
        if not ids:
            spec['auto'] = None
        if keep_none:
            spec['auto'] = None
        if q == 'superior' and spec.get('superior') not in cr.superior_choices(code):
            ch = cr.superior_choices(code)
            spec['superior'] = ch[-1] if ch else None
        if q == 'runeword' and spec.get('superior') is not None and spec['superior'] not in cr.superior_choices(code):
            spec['superior'] = None
        if q == 'magic':
            for kind, key in (('magicprefix', 'prefix'), ('magicsuffix', 'suffix')):
                ok = {i for i, _, _ in cr.affix_choices(kind, code, ilvl)}
                if spec.get(key) not in ok:
                    spec[key] = None
            if not spec.get('prefix') and not spec.get('suffix'):
                ch = cr.affix_choices('magicprefix', code, ilvl) or []
                if ch:
                    spec['prefix'] = ch[-1][0]
                else:
                    ch = cr.affix_choices('magicsuffix', code, ilvl)
                    spec['suffix'] = ch[-1][0] if ch else None
        if q == 'crafted':
            rows = cr.craft_rows(code)
            if spec.get('craft') not in rows and not (spec.get('extra') and not rows):
                spec['craft'] = rows[0] if rows else None
            names = cr.rare_name_choices(code)
            rn = spec.get('rare_names')
            if not rn or rn[0] not in [i for i, _ in names['rareprefix']] or rn[1] not in [i for i, _ in names['raresuffix']]:
                spec['rare_names'] = (names['rareprefix'][0][0] if names['rareprefix'] else 0,
                                      names['raresuffix'][0][0] if names['raresuffix'] else 0)
            for kind, key in (('magicprefix', 'rare_prefixes'), ('magicsuffix', 'rare_suffixes')):
                ok = {i for i, _, _ in cr.affix_choices(kind, code, ilvl, rare=True)}
                spec[key] = [x for x in (spec.get(key) or []) if x in ok]
        if q == 'rare':
            names = cr.rare_name_choices(code)
            rn = spec.get('rare_names')
            if not rn or rn[0] not in [i for i, _ in names['rareprefix']] or rn[1] not in [i for i, _ in names['raresuffix']]:
                spec['rare_names'] = (names['rareprefix'][0][0] if names['rareprefix'] else 0,
                                      names['raresuffix'][0][0] if names['raresuffix'] else 0)
            for kind, key in (('magicprefix', 'rare_prefixes'), ('magicsuffix', 'rare_suffixes')):
                ok = {i for i, _, _ in cr.affix_choices(kind, code, ilvl, rare=True)}
                spec[key] = [x for x in (spec.get(key) or []) if x in ok]
            if not spec['rare_prefixes'] or not spec['rare_suffixes'] or len(spec['rare_prefixes']) + len(spec['rare_suffixes']) < 3:
                for kind, key, n in (('magicprefix', 'rare_prefixes', 2), ('magicsuffix', 'rare_suffixes', 2)):
                    groups = {self.data.affixes[kind][x - 1]['group'] for x in spec[key]}
                    for i, _, a in reversed(cr.affix_choices(kind, code, ilvl, rare=True)):
                        if len(spec[key]) >= n:
                            break
                        if a['group'] not in groups:
                            spec[key].append(i)
                            groups.add(a['group'])
        ms = cr.max_sockets(code, ilvl)
        if q != 'runeword':
            spec['sockets'] = ms if spec.get('max_sockets') else min(spec.get('sockets') or 0, ms)
            spec['sockets'] = max(spec['sockets'], cr.innate_sockets(spec))   # the innate's sockets (Angelic...)
            spec['socketed'] = spec.get('socketed', [])[:spec['sockets']]
        b = self.data.base(code)
        if b.stack_max is not None and not spec.get('quantity'):
            spec['quantity'] = b.stack_max
        if spec.get('ethereal') and (q == 'set' or b.kind not in ('weapons', 'armor')):
            spec['ethereal'] = False
        n_img = cr.image_count(code)
        if n_img and spec.get('image') is None:
            spec['image'] = 0
        if not n_img:
            spec['image'] = None

    def load_spec(self, spec):
        q = spec['quality']
        if q == 'rare' and spec.get('code') and self.cr.is_angelic(spec['code']):
            q = 'angelic'
        self._q_override = q
        self.quality.set(next((l for l, k in qualities() if k == q), T(f'cr.quality.{q}', q.capitalize())))
        self.fill_list()
        self.spec = spec
        if not spec.get('_from_item'):        # an edited item opens exactly as it is (even outside the rules)
            self.prepare_defaults(spec)
        self.render_options()

    # ------------------------------------------------------------------ options
    def render_options(self):
        for w in self.opts.winfo_children():
            w.destroy()
        for w in self.mods_frame.inner.winfo_children():
            w.destroy()
        self.mod_vars = {}
        spec = self.spec
        if spec is None:
            self.title.configure(text=T('cr.choose', 'Choose an item in the list'))
            self._header(None)
            self.panel.show(None)
            self.set_status('')
            self.btn_ok.state(['disabled'])
            return
        cr, d, q, code = self.cr, self.data, spec['quality'], spec['code']
        b = d.base(code)
        self.title.configure(text=f"{clean(cr.describe(spec))} — {clean(b.name)}")
        f = self.opts
        r = 0
        NONE = none_label()

        def row(label):
            nonlocal r
            ttk.Label(f, text=label).grid(row=r, column=0, sticky='nw', padx=(0, 8), pady=2)
            r += 1
            return r - 1

        def combo(label, values, current, on_change, width=40, stretch=True):
            """Read-only combobox; on_change(index in values). Each call keeps its own list (no shared variable)."""
            rr = row(label)
            values = list(values)
            var = tk.StringVar(value=current)
            c = ttk.Combobox(f, state='readonly', values=values, textvariable=var, width=width)
            c.grid(row=rr, column=1, sticky='we' if stretch else 'w', pady=2)
            self.scroll.no_wheel(c)

            def picked(_e, var=var, values=values, on_change=on_change):
                v = var.get()
                if v in values:
                    on_change(values.index(v))
            c.bind('<<ComboboxSelected>>', picked)
            return c, rr

        def spin(label, lo, hi, cur, on_change):
            vals = [str(v) for v in range(lo, hi + 1)]
            return combo(label, vals, str(cur), lambda i, vals=vals: on_change(int(vals[i])), 8, False)

        if q == 'runeword':
            bases = cr.runeword_bases(spec['row'])
            labels = [f"{clean(x.name)} [{kind_label(x.kind)}]" for x in bases]
            cur = next((labels[i] for i, x in enumerate(bases) if x.code == code), '')
            combo(T('cr.base_item', 'Base item'), labels, cur, lambda i, bases=bases: self.set_base(bases[i].code))
            sup = cr.superior_choices(code)
            vals = [NONE] + [self.sup_label(i) for i in sup]
            combo(T('cr.superior', 'Superior'), vals, self.sup_label(spec['superior']) if spec.get('superior') is not None else NONE,
                  lambda i, sup=sup: self.set_field('superior', None if i == 0 else sup[i - 1]))
        spin(T('cr.item_level', 'Item level'), cr.min_ilvl(spec), MAX_ILVL, spec['ilvl'], lambda v: self.set_field('ilvl', v))
        if b.kind in ('weapons', 'armor') and q != 'set':
            rr = row(T('cr.ethereal', 'Ethereal'))
            ev = tk.BooleanVar(value=spec['ethereal'])
            ttk.Checkbutton(f, variable=ev, command=lambda ev=ev: self.set_field('ethereal', ev.get())).grid(row=rr, column=1, sticky='w')
        ms = cr.max_sockets(code, spec['ilvl'])
        if q == 'runeword':
            rr = row(T('cr.sockets', 'Sockets'))
            ttk.Label(f, text=T('cr.rw_sockets', '{n} (runes socketed automatically)', n=spec['sockets'])).grid(row=rr, column=1, sticky='w')
        elif ms:
            rr = row(T('cr.sockets', 'Sockets'))
            box = ttk.Frame(f)
            box.grid(row=rr, column=1, sticky='w', pady=2)
            inn = cr.innate_sockets(spec)                    # given by the innate affix: never fewer
            c2 = ttk.Combobox(box, state='readonly', width=6, values=[str(v) for v in range(inn, ms + 1)])
            c2.set(str(spec['sockets']))
            c2.pack(side='left')
            self.scroll.no_wheel(c2)
            c2.bind('<<ComboboxSelected>>', lambda e, c2=c2: self.set_sockets(int(c2.get())))
            mv = tk.BooleanVar(value=bool(spec.get('max_sockets')))
            ttk.Checkbutton(box, text=T('cr.max_sockets', 'Max sockets ({n})', n=ms), variable=mv,
                            command=lambda mv=mv: self.set_max_sockets(mv.get())).pack(side='left', padx=(10, 0))
            fill = cr.sockets_fillers()
            EMPTY = empty_label()
            vals = [EMPTY] + [clean(n) for _, n in fill]
            for k in range(spec['sockets']):
                cur = spec['socketed'][k] if k < len(spec['socketed']) else None
                curl = next((clean(n) for c_, n in fill if c_ == cur), EMPTY)
                combo(T('cr.socket_n', '  Socket {n}', n=k + 1), vals, curl,
                      lambda i, k=k, fill=fill: self.set_socket(k, None if i == 0 else fill[i - 1][0]), 30, False)
        n_img = cr.image_count(code)
        if n_img > 1:
            spin(T('cr.picture', 'Picture'), 1, n_img, (spec.get('image') or 0) + 1, lambda v: self.set_field('image', v - 1))
        if b.stack_max is not None:
            spin(T('cr.quantity', 'Quantity'), 1, min(b.stack_max, 511), spec.get('quantity') or 1, lambda v: self.set_field('quantity', v))
        if True:
            autos = cr.auto_choices(code, spec['ilvl'])
            if autos:
                vals = [lab for _, lab, _ in autos]
                cur = next((lab for i, lab, _ in autos if i == spec.get('auto')), vals[-1])
                combo(T('cr.innate', 'Innate affix'), vals, cur, lambda i, autos=autos: self.set_field('auto', autos[i][0]))
        if q == 'superior':
            sup = cr.superior_choices(code)
            vals = [self.sup_label(i) for i in sup]
            combo(T('cr.superior_bonus', 'Superior bonus'), vals, self.sup_label(spec['superior']),
                  lambda i, sup=sup: self.set_field('superior', sup[i]))
        if q == 'magic':
            for kind, key, lab in (('magicprefix', 'prefix', T('cr.prefix', 'Prefix')), ('magicsuffix', 'suffix', T('cr.suffix', 'Suffix'))):
                ch = cr.affix_choices(kind, code, spec['ilvl'])
                vals = [NONE] + [l for _, l, _ in ch]
                cur = next((l for i, l, _ in ch if i == spec.get(key)), NONE)
                combo(lab, vals, cur, lambda i, key=key, ch=ch: self.set_field(key, None if i == 0 else ch[i - 1][0]))
        if q == 'crafted':
            ch = cr.craft_choices(code)
            vals = [lab for _, lab in ch]
            cur = next((lab for i, lab in ch if i == spec.get('craft')), NONE)
            combo(T('cr.recipe', 'Shrine / recipe'), vals or [NONE], cur,
                  lambda i, ch=ch: self.set_craft(ch[i][0]) if ch else None)
            if ch:
                rr = row('')
                D2.button(f, T('cr.btn.reroll', 'Reroll item'), self.reroll_crafted).grid(row=rr, column=1, sticky='w', pady=2)
        if q in ('rare', 'crafted', 'tempered'):
            names = cr.rare_name_choices(code)
            rn = spec['rare_names']
            for n, kind, lab in ((0, 'rareprefix', T('cr.name1', 'Name (1st word)')), (1, 'raresuffix', T('cr.name2', 'Name (2nd word)'))):
                lst = names[kind]
                vals = [clean(t) for _, t in lst]
                cur = next((clean(t) for i, t in lst if i == rn[n]), vals[0] if vals else '')
                combo(lab, vals, cur, lambda i, n=n, lst=lst: self.set_rare_name(n, lst[i][0]), 30, False)
            for kind, key, lab in ((('magicprefix', 'rare_prefixes', T('cr.prefix', 'Prefix')), ('magicsuffix', 'rare_suffixes', T('cr.suffix', 'Suffix')))
                                   if q != 'tempered' else ()):
                ch = cr.affix_choices(kind, code, spec['ilvl'], rare=True)
                for k in range(3):
                    cur_id = spec[key][k] if k < len(spec[key]) else None
                    taken = {}                                    # group -> slot name of the other chosen affixes
                    for j, sid in enumerate(spec[key]):
                        if j != k:
                            g = d.affixes[kind][sid - 1]['group']
                            if g:
                                taken[g] = f'{lab} {j + 1}'
                    vals = [NONE]
                    for i, l, a in ch:
                        if a['group'] in taken and i != cur_id:
                            l = f"{l}   ⚠ {T('cr.same_group_mark', 'same group as {slot}', slot=taken[a['group']])}"
                        vals.append(l)
                    cur = next((vals[n + 1] for n, (i, _, _) in enumerate(ch) if i == cur_id), NONE)
                    combo(f'{lab} {k + 1}', vals, cur, lambda i, key=key, k=k, ch=ch:
                          self.set_rare_affix(key, k, None if i == 0 else ch[i - 1][0]))
        corr = cr.corrupt_choices(spec)
        if corr or spec.get('corrupt') is not None:
            vals = [T('cr.cor.none', '(not corrupted)')] + [lab for _, _, _, lab in corr]
            cur = next((lab for rw, _, _, lab in corr if rw == spec.get('corrupt')), vals[0])
            rr = row(T('cr.corruption', 'Corruption'))
            box = ttk.Frame(f)
            box.grid(row=rr, column=1, sticky='we', pady=2)
            box.columnconfigure(0, weight=1)
            var = tk.StringVar(value=cur)
            c = ttk.Combobox(box, state='readonly', values=vals, textvariable=var, width=40)
            c.grid(row=0, column=0, sticky='we')
            self.scroll.no_wheel(c)

            def corrupt_picked(_e, var=var, vals=vals, corr=corr):
                v = var.get()
                if v in vals:
                    i = vals.index(v)
                    self.set_corrupt(None if i == 0 else corr[i - 1][0])
            c.bind('<<ComboboxSelected>>', corrupt_picked)
            if corr:
                D2.button(box, T('cr.btn.corrupt_roll', 'Roll like the cube'), self.roll_corrupt).grid(
                    row=0, column=1, padx=(6, 0))
        if b.kind == 'armor':
            lo, hi = cr.defense_range(spec)
            if lo != hi or spec.get('_defense_raw'):
                if spec.get('defense') is None or not lo <= spec['defense'] <= hi and not spec.get('_defense_raw'):
                    spec['defense'] = hi
                cur = spec['defense']
                self.slider(self.opts, r, T('cr.base_defense', 'Base defense'), min(lo, cur), max(hi, cur), cur,
                            lambda v: self.set_field('defense', v, False))
                r += 1
            else:
                rr = row(T('cr.base_defense', 'Base defense'))
                ttk.Label(f, text=T('cr.def_fixed', '{v} (fixed: Enhanced Defense)', v=lo)).grid(row=rr, column=1, sticky='w')
        self.render_mods()
        self.refresh()

    def sup_label(self, i):
        if i is None:
            return none_label()
        txt = []
        for m in self.data.quality_mods[i]:
            lo, hi = sorted((m['min'], m['max']))
            st = mods_stats([(m['prop'], m['param'], m['min'], m['max'])], self.data)
            desc = next((self.data.isc[x['id']]['desc'] for x in st if self.data.isc[x['id']]['desc']), '?')
            txt.append(f"{desc.replace('%d%%', '').replace('%d', '').strip()} {lo}-{hi}")
        return f"[{i}] " + '; '.join(txt)

    def slider(self, parent, r, label, lo, hi, cur, on_release, fmt=str):
        """Label on its own line, then the slider, its value and its range; saving on release."""
        box = ttk.Frame(parent)
        box.grid(row=r, column=0, columnspan=2, sticky='we', pady=(4, 0))
        ttk.Label(box, text=label).pack(anchor='w')
        fr = ttk.Frame(box)
        fr.pack(fill='x')
        var = tk.IntVar(value=cur)
        val = ttk.Label(fr, text=fmt(cur), width=22)

        def moved(x):
            v = int(round(float(x)))
            if v != var.get():
                var.set(v)
            val.configure(text=fmt(var.get()))

        sc = ttk.Scale(fr, from_=lo, to=hi, orient='horizontal', length=300, command=moved)
        sc.set(cur)
        sc.pack(side='left', padx=(12, 0))
        val.pack(side='left', padx=8)
        ttk.Label(fr, text=T('cr.range_fmt', '{lo} to {hi}', lo=fmt(lo), hi=fmt(hi)) if fmt is not str else
                  T('cr.range', '({lo} to {hi})', lo=lo, hi=hi)).pack(side='left')
        sc.bind('<ButtonRelease-1>', lambda e: on_release(var.get()))
        for key, dv in (('<Left>', -1), ('<Right>', 1)):
            sc.bind(key, lambda e, dv=dv: (sc.set(max(lo, min(hi, var.get() + dv))), on_release(var.get()), 'break')[-1])
        return var

    def value_format(self, m):
        """Display of a rolled value: monster or skill name for the 'random skill / monster' properties."""
        d = self.data
        if d.prop_func[m.prop] != 12:
            return str
        sid = next((x for x in d.props[m.prop] if x >= 0), None)
        desc = (d.isc[sid]['desc'] if sid is not None else '') or ''
        if 'eanimate' in desc and getattr(d, 'monster_names', None):
            return lambda v: clean(d.monster_names[v]) if 0 <= v < len(d.monster_names) and d.monster_names[v] else str(v)
        return lambda v: clean(d.skill(v))

    def mod_label(self, m):
        d = self.data
        stats = m.stats(d)
        for s in stats:
            if stat_hidden(s['id'], d, s['value']):
                continue
            try:
                txt = clean(stat_label(dict(s, desc=d.isc[s['id']]['desc']), d))
            except Exception:
                txt = ''
            if txt and 'BUFFALO' not in txt:
                return txt
        return T('cr.hidden_stat', '(hidden stat {id})', id=stats[0]['id']) if stats else '?'

    def render_mods(self):
        inner = self.mods_frame.inner
        for w in inner.winfo_children():
            w.destroy()
        mods = [m for m in self.cr.mods(self.spec) if m.variable]
        extra = [x for x in self.spec.get('extra') or [] if self.cr._visible(x)]
        if not mods and not extra:
            ttk.Label(inner, text=T('cr.no_variable', 'No variable values: everything on this item is fixed.')).grid(row=0, column=0, sticky='w')
            return
        if extra:
            from mxl_tooltip import stat_lines
            try:
                lines = [str(x[1]).strip() for x in stat_lines(extra, self.data, None) if str(x[1]).strip()]
            except Exception:
                lines = [f"stat {x['id']}: {x['value']}" for x in extra]
            ttk.Label(inner, text=T('cr.kept_list', 'Kept as they are on the item (not from its affixes or its recipe): {lines}',
                                    lines='; '.join(lines)), wraplength=520, justify='left').grid(row=0, column=0, columnspan=2, sticky='w')
        for r, m in enumerate(mods, start=1 if extra else 0):
            label = f"{T('cr.mod.' + m.label.lower(), m.label) + ': ' if m.label else ''}{self.mod_label(m)}"
            self.slider(inner, r, label, m.min, m.max, m.value, lambda v, src=m.source: self.set_roll(src, v),
                        self.value_format(m))

    # ------------------------------------------------------------------ changes
    def _int(self, var, lo, hi):
        try:
            v = int(var.get())
        except (tk.TclError, ValueError):
            v = lo
        v = max(lo, min(hi, v))
        var.set(v)
        return v

    def set_field(self, key, value, rerender=True):
        if self.spec is None or self.spec.get(key) == value:
            return
        self.spec[key] = value
        if key in ('ilvl', 'superior', 'auto', 'prefix', 'suffix', 'ethereal', 'sockets'):
            self.prepare_defaults(self.spec)
            self.fill_new_rolls()
        if rerender:
            self.render_options()
        else:
            self.refresh()

    def set_base(self, code):
        self.spec['code'] = code
        self.spec['defense'] = None
        self.spec['image'] = None
        self.spec['ilvl'] = max(self.spec['ilvl'], self.cr.min_ilvl(self.spec))
        self.prepare_defaults(self.spec)
        self.fill_new_rolls()
        self.render_options()

    def set_socket(self, k, code):
        s = list(self.spec['socketed'])
        while len(s) <= k:
            s.append(None)
        s[k] = code
        self.spec['socketed'] = s
        self.refresh()

    def set_rare_name(self, n, i):
        rn = list(self.spec['rare_names'])
        rn[n] = i
        self.spec['rare_names'] = tuple(rn)
        self.refresh()

    def set_rare_affix(self, key, k, sid):
        spec = self.spec
        kind = 'magicprefix' if key == 'rare_prefixes' else 'magicsuffix'
        slot = T('cr.prefix', 'Prefix') if kind == 'magicprefix' else T('cr.suffix', 'Suffix')
        lst = list(spec[key])
        old = lst[k] if k < len(lst) else None
        if sid == old:
            return
        if sid is not None:
            g = self.data.affixes[kind][sid - 1]['group']
            other = next((j for j, x in enumerate(lst) if j != k and g and self.data.affixes[kind][x - 1]['group'] == g), None)
            if other is not None:
                lab = lambda i: self.cr.affix_label(kind, i).split('   [')[0]
                if not messagebox.askyesno(
                        T('cr.title.creator', 'Item Creator'),
                        T('cr.ask.same_group', '"{new}" is in the same affix group as "{old}" ({slot}).\n\nThe game never '
                          'puts two affixes of one group on an item, so this item could not exist.\n\nReplace {slot} '
                          'with "{new}"?', new=lab(sid), old=lab(lst[other]), slot=f'{slot} {other + 1}'),
                        parent=self.top):
                    self.render_options()                       # the list shows the affix that stays
                    return
                lst[other] = sid                                 # the new affix takes the other one's place
                spec[key] = lst
                self.fill_new_rolls()
                self.render_options()
                return
        if sid is None:
            if k < len(lst):
                lst.pop(k)
        elif k < len(lst):
            lst[k] = sid
        else:
            lst.append(sid)
        if spec['quality'] == 'crafted' and sid is not None and old is None:
            n = len(lst) + len(spec['rare_suffixes' if key == 'rare_prefixes' else 'rare_prefixes'])
            if n > CRAFTED_AFFIXES and not spec.get('extra'):
                messagebox.showwarning(
                    T('cr.title.creator', 'Item Creator'),
                    T('cr.warn.crafted_full', 'A crafted item has exactly {n} affixes (1-3 prefixes, 1-3 suffixes): '
                      'set one of the other affixes to (none) first, or choose this one in its place.', n=CRAFTED_AFFIXES),
                    parent=self.top)
                self.render_options()
                return
        spec[key] = lst
        self.fill_new_rolls()
        self.render_options()

    def set_sockets(self, n):
        if self.spec is None:
            return
        ms = self.cr.max_sockets(self.spec['code'], self.spec['ilvl'])
        self.spec['max_sockets'] = bool(ms) and n == ms
        self.set_field('sockets', n)

    def set_max_sockets(self, on):
        if self.spec is None:
            return
        self.spec['max_sockets'] = bool(on)
        if on:
            self.spec['sockets'] = self.cr.max_sockets(self.spec['code'], self.spec['ilvl'])
        self.prepare_defaults(self.spec)
        self.fill_new_rolls()
        self.render_options()

    def set_corrupt(self, row):
        if self.spec is None:
            return
        self.spec['corrupt'] = row
        kind = next((k for r, k, _p, _l in self.cr.corrupt_choices(self.spec) if r == row), None)
        if kind == 'pending' and not self.spec.get('corrupt_roll'):
            self.spec['corrupt_roll'] = random.randint(1, 10000)
        self.render_options()

    def roll_corrupt(self):
        """A corruption as the game gives it: the Corrupted Crystal's outcome, then (if not revealed) the Oil of
        Craft's, each with the game's chances."""
        if self.spec is None:
            return
        ch = self.cr.corrupt_choices(self.spec)
        step = [(r, k, p) for r, k, p, _l in ch if k != 'reveal']
        if not step:
            return
        r, k, _p = random.choices(step, weights=[p for _r, _k, p in step])[0]
        if k == 'pending':
            rev = [(rr, p) for rr, kk, p, _l in ch if kk == 'reveal']
            if rev:
                r = random.choices(rev, weights=[p for _r, p in rev])[0][0]
            else:
                self.spec['corrupt_roll'] = random.randint(1, 10000)
        self.spec['corrupt'] = r
        self.render_options()

    def set_craft(self, row):
        if self.spec is None or self.spec.get('craft') == row:
            return
        self.spec['craft'] = row
        for m in self.cr.mods(self.spec):                     # the new recipe's values: one roll
            if m.variable and m.source.startswith('craft:') and m.source not in self.spec['rolls']:
                self.spec['rolls'][m.source] = random.randint(m.min, m.max)
        self.render_options()

    def reroll_crafted(self):
        if self.spec is None:
            return
        self.spec['extra'] = []
        for k in ('extra_rw', '_from_item', '_keep_zero', '_keep_zero_rw', '_defense_raw', '_warning'):
            self.spec.pop(k, None)               # a new roll: a normal, legit item
        self.cr.reroll_crafted(self.spec)
        self.render_options()

    def set_roll(self, src, v):
        self.spec['rolls'][src] = v
        self.refresh()

    def fill_new_rolls(self):
        for m in self.cr.mods(self.spec):
            if m.variable and m.source not in self.spec['rolls']:
                self.spec['rolls'][m.source] = m.max

    def reroll(self, how):
        if self.spec is None:
            return
        self.cr.roll(self.spec, how)
        if how == 'max' and self.spec['code'] and self.data.base(self.spec['code']).kind == 'armor':
            self.spec['defense'] = self.cr.defense_range(self.spec)[1]
        self.render_options()

    # ------------------------------------------------------------------ preview / write
    def current_blob(self):
        spec = dict(self.spec)
        spec['socketed'] = [c for c in spec.get('socketed', []) if c]
        return self.cr.build(spec)

    def refresh(self):
        try:
            blob, rec = self.current_blob()
            it = blob_item(blob, self.data)
        except (CreateError, FormatError, KeyError, ValueError) as e:
            self.panel.show(None)
            msg = T('cr.not_possible', 'Not possible: {error}', error=e)
            try:                                  # the preview says why it is empty (the item did not vanish)
                st = self.preview.cget('state')
                self.preview.configure(state='normal')
                self.preview.delete('1.0', 'end')
                self.preview.insert('end', msg + '\n\n' + T('cr.fix_hint', 'Change the option above (or choose (none)) '
                                                                       'to get the item back.'))
                self.preview.configure(state=st)
            except tk.TclError:
                pass
            self.set_status(msg, error=True)
            self.btn_ok.state(['disabled'])
            self._header(None)
            return None
        self.panel.show(it)
        self._header(it)
        self.show_orb(it['code'])
        if self.spec.get('_warning'):
            probs = [T('cr.warn.rules', "outside the game's rules ({error}), kept as it is", error=self.spec['_warning'])]
        else:
            probs = []
        if self.spec.get('quality') == 'crafted':
            probs += self.cr._crafted_problems(it, self.spec)
            kept = {x['id'] for x in self.spec.get('extra') or []}
            if kept:
                probs = [t for t in probs if getattr(t, 'sid', None) not in kept]
                n = len([x for x in self.spec['extra'] if self.cr._visible(x)])
                if n:
                    probs.append(T('cr.warn.kept_crafted', '{n} stat(s) kept as they were on the item (shrine blessing, '
                                   'corruption, other upgrades or an imported copy)', n=n))
        else:
            probs += self.cr.check_legit(self._without_corruption(it))
            if self.spec.get('extra'):
                n = len([x for x in self.spec['extra'] if self.cr._visible(x)])
                if n:
                    probs.append(T('cr.warn.kept', "{n} stat(s) kept as they were on the item (outside the game's ranges "
                                   'or from upgrades the editor does not model)', n=n))
        if probs:
            self.set_status(T('cr.warning', 'Warning: {text}', text='; '.join(str(p) for p in probs)), error=True)
        else:
            w, h = self.data.base(it['code']).size
            self.set_status(T('cr.legit', "Legit: every value is within the game's ranges. Size {w}×{h}.", w=w, h=h))
        self.btn_ok.state(['!disabled'])
        return blob

    def _header(self, it):
        """D2 look: the item (icon in a gear frame, name in its colour) at the top of the window."""
        if self.header is None:
            return
        try:
            if self.spec is None:
                self.header.show()
                return
            b = self.data.base(self.spec['code'])
            name = clean(self.cr.describe(self.spec))
            sub = clean(b.name) if clean(b.name) != name else ''
            if it is None:
                self.header.show(None, None, name, sub)
                return
            from mxl_gui_detail import item_color
            try:
                name = clean(self.app.full_name(it)) or name
                sub = clean(b.name) if clean(b.name) not in name else ''
            except Exception:
                pass
            layers = None
            try:
                layers = self.app.icon(it, D2.CELL_BG)
            except Exception:
                layers = None
            self.header.show(b.size, layers, name, sub, item_color(it, self.data))
        except Exception as e:
            print('D2 look header:', e)

    def _without_corruption(self, it):
        """The item's stats without what its corruption added (checked against the item's own ranges)."""
        if self.spec.get('corrupt') is None:
            return it
        cs = self.cr._corrupt_stats(self.spec)
        stats = []
        for x in it.get('stats') or []:
            k = (x['id'], x.get('param'))
            v = x['value'] - cs.pop(k, 0)
            if v or k not in self.cr._corrupt_stats(self.spec):
                stats.append(dict(x, value=v))
        return dict(it, stats=stats)

    def set_status(self, text, error=False):
        self.status.configure(text=text, foreground='#e06060' if error else '')

    def show_orb(self, code):
        """Mystic orbs keep their effect in the game's mysticorbs table, not on the item: shown under the tooltip."""
        orb = self.cr.orb_effects(code)
        t = self.preview
        if not orb:
            desc = self.cr.item_desc(code)
            if desc and self.data.base(code).kind == 'misc':
                t.configure(state='normal')
                t.insert('end', '\n' + T('cr.item_desc', 'Item description'), 'section')
                t.insert('end', '\n' + '\n'.join(desc) + '\n', 'dim')
                t.configure(state='disabled')
            return
        lines, most = orb
        try:
            from mxl_theme import GAME_COLORS
            blue = self.panel.color_tag(GAME_COLORS['3'])
        except Exception:
            blue = ''
        t.configure(state='normal')
        t.insert('end', '\n' + T('cr.orb_effect', 'Mystic orb effect'), 'section')
        t.insert('end', '\n' + (T('cr.orb_note_max', '(added to the item it is cubed into; up to {n} of this orb per item)', n=most)
                                if most else T('cr.orb_note', '(added to the item it is cubed into)')) + '\n', 'dim')
        for line in lines or [T('cr.orb_none', '(no effect listed)')]:
            t.insert('end', line + '\n', blue)
        t.configure(state='disabled')

    def commit(self):
        app = self.app
        if self.target is not None:
            blob = self.refresh()
            if blob is None:
                return
            try:
                msg = self.target(blob)
            except Exception as e:                       # CharError, EditError, FormatError, OSError...
                messagebox.showerror(T('cr.title.creator', 'Item Creator'), str(e), parent=self.top)
                return
            self.set_status(msg or T('cr.saved', 'Saved.'))
            app.status.configure(text=msg or '')
            if self.edit_item is not None:
                self.top.destroy()
            return
        if not app.path:
            messagebox.showerror(T('cr.title.creator', 'Item Creator'), T('cr.open_stash', 'Open a stash in the main window first.'),
                                 parent=self.top)
            return
        blob = self.refresh()
        if blob is None:
            return
        try:
            items = parse_stash(app.path, self.data)
            new = blob_item(blob, self.data)
            remove = []
            if self.edit_item is not None:
                cur = next((i for i in items if i['_offset'] == self.edit_item['_offset']
                            and i['code'] == self.edit_item['code']), None)
                if cur is None:
                    raise EditError(T('cr.err.changed', 'the item changed on disk since it was opened: reopen it'))
                remove = [cur['_offset']]
                rest = [i for i in items if i is not cur]
                spot = None
                if mxl_save.placement_error(rest, new, cur['page'], cur['x'], cur['y'], self.data) is None:
                    spot = (cur['page'], cur['x'], cur['y'])
                if spot is None:
                    spot = free_spot(rest, new, self.data, self._pages(cur['page']))
            else:
                spot = free_spot(items, new, self.data, self._pages(app.page()))
            if spot is None:
                raise EditError(T('cr.err.no_space', 'no free place in the stash for this item'))
            page, x, y = spot
            bak = edit_stash(app.path, self.data, remove=remove, insert=[(page, x, y, blob)],
                             backup=app.needs_backup(app.path))
        except (EditError, FormatError, OSError, CreateError) as e:
            messagebox.showerror(T('cr.title.creator', 'Item Creator'), str(e), parent=self.top)
            return
        app.backup_made(app.path, bak)
        app.load(app.path, keep_selection=False)
        try:
            app.notebook.select(page)
        except tk.TclError:
            pass
        app.selected = next((i for i in app.items if (i['page'], i['x'], i['y']) == (page, x, y)), None)
        app.draw_grid()
        app.set_detail(app.selected)
        msg = (T('cr.replaced', '{name} replaced: page {page} ({x}, {y})', name=clean(self.cr.describe(self.spec)), page=page + 1, x=x, y=y)
               if self.edit_item else
               T('cr.created', '{name} created: page {page} ({x}, {y})', name=clean(self.cr.describe(self.spec)), page=page + 1, x=x, y=y))
        if bak:
            msg += T('cr.backup', ' — backup: {file}', file=os.path.basename(bak))
        app.status.configure(text=msg)
        self.set_status(msg)
        if self.edit_item is not None:
            self.edit_item = app.selected
            if self.edit_item is None:
                self.top.destroy()

    def _pages(self, first):
        p = self.page.get()
        if p == self.page_current:
            return [first] + [i for i in range(STASH_PAGES) if i != first]
        if p == self.page_first:
            return list(range(STASH_PAGES))
        return [int(p) - 1]


# ---------------------------------------------------------------------------------------- main window hooks

def delete_selected(app):
    it = app.selected
    if it is None or not app.path:
        messagebox.showinfo(T('cr.delete', 'Delete item'), T('cr.select_first', 'Select an item of the stash first.'))
        return
    name = app.full_name(it)
    if not messagebox.askyesno(T('cr.delete', 'Delete item'),
                               T('cr.delete_ask', 'Delete {name} (and its socketed items) from {file}?\n\n'
                                 'A backup of the stash is made first.', name=name, file=os.path.basename(app.path))):
        return
    try:
        bak = edit_stash(app.path, app.data, remove=[it['_offset']], backup=True)
    except (EditError, FormatError, OSError) as e:
        messagebox.showerror(T('cr.delete', 'Delete item'), str(e))
        return
    app.backup_made(app.path, bak)
    app.load(app.path, keep_selection=False)
    app.status.configure(text=T('cr.deleted', '{name} deleted', name=name)
                         + (T('cr.backup', ' — backup: {file}', file=os.path.basename(bak)) if bak else ''))


def edit_selected(app):
    it = app.selected
    if it is None or not app.path:
        messagebox.showinfo(T('cr.edit', 'Edit item'), T('cr.select_first', 'Select an item of the stash first.'))
        return
    CreatorWindow(app, edit_item=it)


def toggle_editing(app, var):
    app.editing = bool(var.get())
    settings.put('editing_enabled', app.editing)
    app.set_detail(app.selected)


def install(App):
    """Adds the Creator / Edit / Delete buttons and the value-editing switch to the collector's main window."""
    original = App._build

    def _build(self):
        original(self)
        top = self.btn_library.master
        bar = ttk.Frame(top)
        bar.pack(side='left', padx=(12, 0))
        ttk.Button(bar, text=T('cr.title.creator', 'Item Creator'), command=lambda: CreatorWindow(self)).pack(side='left', padx=2)
        ttk.Button(bar, text=T('cr.edit', 'Edit item'), command=lambda: edit_selected(self)).pack(side='left', padx=2)
        ttk.Button(bar, text=T('cr.delete', 'Delete item'), command=lambda: delete_selected(self)).pack(side='left', padx=2)
        var = tk.BooleanVar(value=bool(self.editing))
        ttk.Checkbutton(bar, text=T('cr.value_editing', 'Value editing'), variable=var,
                        command=lambda: toggle_editing(self, var)).pack(side='left', padx=(8, 2))
        self._creator = None

    App._build = _build


class _NoLibrary:
    """Replaces the collector's library (collection of found uniques / sets): nothing is tracked or written."""
    readonly = False

    def __init__(self, *a, **k):
        pass

    def release(self):
        pass

    def __getattr__(self, name):
        return lambda *a, **k: None


class _NoLibraryUI:
    window = None

    def __init__(self, app):
        pass

    def __getattr__(self, name):
        return lambda *a, **k: None


def remove_library(mxl_gui):
    """No library in this build: no library.json, no Library button, no 'transfer to the library' button."""
    mxl_gui.Library = _NoLibrary
    mxl_gui.LibraryUI = _NoLibraryUI
    mxl_gui.catalog = lambda data: []
    App = mxl_gui.App
    App.can_transfer = lambda self, it: False
    original = App._build

    def _build(self):
        original(self)
        self.btn_library.pack_forget()

    App._build = _build


APP_NAME = 'Median XL Capybara Editor'


def rename_app(mxl_gui):
    """Window title: "Median XL Capybara Editor — <file>" (the collector's titles start with "Median XL")."""
    tr = mxl_gui.tr

    def tr2(key, *a, **k):
        text = tr(key, *a, **k)
        if key in ('app.title', 'app.title_file') and isinstance(text, str):
            if text.startswith('Median XL') and not text.startswith(APP_NAME):
                text = APP_NAME + text[len('Median XL'):]
            elif APP_NAME not in text:
                text = f'{APP_NAME} — {text}'
        return text
    mxl_gui.tr = tr2


def run():
    import mxl_userdir                    # own settings folder; the launcher did it already (splash process too)
    mxl_userdir.setup()
    import mxl_splash                    # the loading screen: its own process (the exe again with --capy-splash)
    if mxl_splash.FLAG_ARG in sys.argv:
        mxl_splash.main()
        sys.exit(0)
    mxl_splash.start()
    import mxl_log                        # capybara.log in the settings folder: saves, backups, imports, errors
    mxl_log.install()
    import mxl_format_fix                # Sigma item layouts the collector's reader did not know (before anything reads)
    mxl_format_fix.install()
    import mxl_gui
    rename_app(mxl_gui)
    import mxl_d2look                     # first: innermost wrapper (cell size chosen before the window is built)
    mxl_d2look.install(mxl_gui.App)
    import mxl_char_gui
    remove_library(mxl_gui)
    install(mxl_gui.App)
    mxl_char_gui.install(mxl_gui.App)
    import mxl_import
    mxl_import.install(mxl_gui.App)
    import mxl_inv_gui
    mxl_inv_gui.install(mxl_gui.App)
    import mxl_app_icon
    mxl_app_icon.install(mxl_gui.App)
    mxl_splash.install(mxl_gui.App)       # last: outermost, closes the splash once everything is built
    mxl_log.wrap_saves()                  # writers bound by name in the modules imported since install()
    try:
        mxl_gui.main()
    finally:
        mxl_splash.close()
