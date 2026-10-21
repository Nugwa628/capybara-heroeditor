"""Import of a character from a page of median-xl.com (NotArmory "/char/<name>", saved from the browser as .html).

The page holds two JSON blocks: the build planner export (class, level, skill points, quests) and the item list
(every equipped / inventory / belt / stash item with its tooltip lines, socketed items and position).

Items are copied as shown ("exact copy", the user's choice): the base item, quality, unique / set / runeword id,
magic affixes, sockets, ethereal and picture come from the page, and the stats are read back from the tooltip
lines with mxl_statparse (socketed items' own lines are taken off the parent's). Lines that cannot be read are
listed in the report. The character itself is made from an existing save of the same version (any class):
name, class, base attributes of the class, empty skills, then the game's level-up, the quests and their
rewards, and the skill points of the export."""
import html
import json
import os
import random
import re
import shutil
import struct
from collections import Counter

import mxl_char as MC
from mxl_char import Character, CharError, Entry, INV_W, INV_H
from mxl_creator import (Creator, encode_item, build_blob, _rows, mods_stats, FLAG_IDENTIFIED, FLAG_ALWAYS, FLAG_SIMPLE,
                         FLAG_ETHEREAL, FLAG_SOCKETED, FLAG_RUNEWORD, LOC_STORED, PANEL_STASH)
import stat_ids as S
from mxl_format_fix import body_kind
from mxl_lang import T
from mxl_log import log
from mxl_statparse import StatParser, subtract, merge, _norm, canon

CLASS_INDEX = {'Amazon': 0, 'Sorceress': 1, 'Necromancer': 2, 'Paladin': 3, 'Barbarian': 4, 'Druid': 5, 'Assassin': 6}
GEAR_SLOTS = {'Head': 1, 'Neck': 2, 'Torso': 3, 'RightHandMain': 4, 'LeftHandMain': 5, 'RightFinger': 6,
              'LeftFinger': 7, 'Waist': 8, 'Feet': 9, 'Hands': 10, 'RightHandAlternate': 11, 'LeftHandAlternate': 12}
QUEST_WORDS = {'den_of_evil': 1, 'radament': 9, 'izual': 25, "lam_essen's_tome": 17, 'golden_bird': 20,
               'lam_esens_tome': 17, "lam_esen's_tome": 17}
STAT_COLOURS = {1, 2, 3, 5, 8, 9}
SKIP = re.compile(r'^(Required (Level|Strength|Dexterity)|Defense:|Quantity:|Socketed \(|Durability|'
                  r'(One|Two)-Hand Damage|Throw Damage|Damage:|\(.* Only\)$|Keep in Inventory|\(Right Click|'
                  r'Right-Click|Can be Inserted|Limit per item)', re.I)
RANGE = re.compile(r'\(\d+-\d+\)|\(\d+ to \d+\)|\[\d+\]')
LOC_BELT = 2
PANEL_INVENTORY = 1


class ImportError_(Exception):
    pass


def _err_text(err):
    """Error of a skipped item for the report: our own errors as written, others with their type."""
    return str(err) if isinstance(err, ImportError_) else f'{type(err).__name__}: {err}'


# ---------------------------------------------------------------------------------------------- page
def _mhtml_html(text):
    """HTML part of a page saved as a single file (.mhtml, quoted-printable / base64), else None."""
    if 'multipart/related' not in text[:4096].lower():
        return None
    import email
    msg = email.message_from_string(text)
    for part in msg.walk():
        if part.get_content_type() == 'text/html':
            raw = part.get_payload(decode=True) or b''
            return raw.decode(part.get_content_charset() or 'utf-8', errors='replace')
    return None


def _json_body(body):
    """JSON of a <script> block, read as is or html-escaped (&quot; / &#34; ...), else None."""
    body = body.strip()
    for txt in (body, html.unescape(body)):
        try:
            return json.loads(txt)
        except ValueError:
            continue
    return None


def load_page(path):
    """(build dict or None, [item dicts]) of a saved NotArmory page (or of a plain items .json).
    The page's blocks are found by their ids (notarmory-export / notarmory-items), else by their content."""
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        text = f.read()
    if text.lstrip().startswith('['):
        return None, json.loads(text)
    text = _mhtml_html(text) or text
    build, items = None, None
    for m in re.finditer(r'<script([^>]*)>(.*?)</script>', text, re.S):
        attrs, body = m.group(1), m.group(2).strip()
        if not body.startswith(('{', '[')):
            continue
        ident = re.search(r'id=["\']?notarmory-(export|items)\b', attrs)
        kind = ident.group(1) if ident else None
        if kind is None and 'description_lines' not in body and 'skillPoints' not in body:
            continue
        val = _json_body(body)
        if isinstance(val, list) and (kind == 'items' or val and isinstance(val[0], dict) and 'description_lines' in val[0]):
            items = val
        elif isinstance(val, dict) and (kind == 'export' or 'skillPoints' in val) and (val.get('class') or val.get('level')):
            build = val
    if items is None:
        raise ImportError_(T('im.err.no_items', 'no item list found in this page (save the character page with "Save as… Web page")'))
    return build, items


def _lines(e):
    """Tooltip lines of an item entry as (text, colour) without the name line."""
    out = []
    for k, segs in enumerate(e.get('description_lines') or []):
        if k == 0 or not segs:
            continue
        txt = ''.join(str(t) for t, c in segs).strip()
        col = segs[0][1]
        out.append((txt, col))
    return out


def _stat_lines(e):
    return [(t, c) for t, c in _lines(e) if c in STAT_COLOURS and t and not SKIP.match(t) and not RANGE.search(t)]


# ---------------------------------------------------------------------------------------------- importer
class Importer:
    def __init__(self, data, level=1):
        self.d = data
        self.cr = Creator(data)
        self.sp = StatParser(data, level)
        self.level = max(1, int(level or 1))
        self.guessed = 0                 # items without an item level on the page
        self.report = []
        self._affix_names = None

    def note(self, text):
        self.report.append(text)

    # ---- lookups
    def _code(self, code):
        code = (code or '').ljust(4)[:4]
        if code not in self.d.bases:
            raise ImportError_(T('im.err.code', 'unknown item code {code}', code=repr(code)))
        return code

    @staticmethod
    def _alnum(t):
        return re.sub(r'[^a-z0-9]', '', canon(t))

    def _row_names(self, u):
        key = self.d.key(u['name']) if u['name'] in self.d._bykey else u['name']
        parts = [p for p in re.split(r'\\n|\n', key) if p.strip()]
        return {self._alnum(key), self._alnum(' '.join(parts)), self._alnum(' '.join(reversed(parts)))} | \
            {self._alnum(p) for p in parts[-1:]}

    def _unique_row(self, name, code, stats=(), sets=False):
        """Row of the unique / set item: by name (any line of its name string), else / among several by the stats
        (the row whose mods give the most of the page's stats)."""
        lst = self.d.set_items if sets else self.d.uniques
        rows = [i for i, u in enumerate(lst) if u['code'] == code]
        want = self._alnum(name)
        hits = [i for i in rows if want in self._row_names(lst[i])]
        if len(hits) == 1:
            return hits[0]
        cand = hits or rows
        if not cand:
            hits = [i for i, u in enumerate(lst) if want in self._row_names(u)]
            return hits[0] if len(hits) == 1 else None
        if len(cand) == 1:
            return cand[0]
        have = {(s['id'], s.get('param')) for s in stats}
        have_ids = {s['id'] for s in stats}
        best, score = None, None
        for i in cand:
            try:
                mods = self.cr.mods(self.cr.new_spec('set' if sets else 'unique', row=i))
                got = [st for m in mods for st in m.stats(self.d)]
            except Exception:
                continue
            gk = {(st['id'], st.get('param')) for st in got}
            gi = {st['id'] for st in got}
            sc = (2 * len(gk & have) + len(gi & have_ids) - len(gi ^ have_ids), -i)
            if score is None or sc > score:
                best, score = i, sc
        return best

    def _runeword(self, name):
        for i, (n, runes, ok, ex) in enumerate(self.d.runewords):
            if n in self.d._bykey and canon(self.d.key(n)) == canon(name):
                return i
        return None

    def _affixes(self):
        if self._affix_names is None:
            self._affix_names = {}
            for kind in ('magicprefix', 'magicsuffix'):
                names = []
                for r in _rows(self.d, kind):
                    key = r[:32].split(b'\0')[0].decode('latin1')
                    names.append(canon(self.d.key(key)) if key in self.d._bykey else canon(key))
                self._affix_names[kind] = names
        return self._affix_names

    def _magic_ids(self, display, code):
        """(prefix id, suffix id) from "Prefix Base of Suffix"."""
        base = canon(self.cr.display_name(code).split(' — ')[0])
        name = canon(re.sub(r'\s*\[\d+x\]\s*$', '', display))
        pre_txt, suf_txt = '', ''
        if base and base in name:
            pre_txt, suf_txt = name.split(base, 1)
        else:
            pre_txt = name
        pre_txt, suf_txt = pre_txt.strip(), suf_txt.strip()
        anc = self.cr.types_of(code)
        out = []
        for kind, txt in (('magicprefix', pre_txt), ('magicsuffix', suf_txt)):
            if not txt:
                out.append(0)
                continue
            names = self._affixes()[kind]
            cand = [i for i, n in enumerate(names) if n == txt]
            ok = [i for i in cand if self.d.affixes[kind][i]['itypes'] & anc]
            pick = (ok or cand or [None])[0]
            if pick is None:
                self.note(T('im.affix', '{item}: magic affix "{affix}" not found', item=display, affix=txt))
                out.append(0)
            else:
                out.append(pick + 1)
        return tuple(out)

    def _image(self, e, code):
        b = self.d.base(code)
        gfx = self.d.itype_gfx[b.type] if 0 <= b.type < len(self.d.itype_gfx) else []
        img = (e.get('image') or '').lower()
        for i, g in enumerate(gfx):
            if str(g).lower() == img:
                return i
        return 0 if gfx else None

    def _ilvl(self, e, code):
        """(item level, guessed) of a page item: the page's "ilvl" (the save's value), else its "ilvl_original",
        else (runes, keys, trophies, potions... have none on the page) the character's level, at least the base's
        level. Clamped to 1-127 (7 bits in the save)."""
        for key in ('ilvl', 'ilvl_original'):
            try:
                v = int(e.get(key) or 0)
            except (TypeError, ValueError):
                v = 0
            if v > 0:
                return min(127, v), False
        try:
            floor = int(self.cr.qlvl(code) or 1)
        except Exception:
            floor = 1
        self.guessed += 1
        return max(1, min(127, max(floor, min(self.level, 99)))), True

    def _defense(self, e):
        """(shown defense, base defense in parentheses or None) of the "Defense: 4123 (1494)" line."""
        for t, c in _lines(e):
            if t.lower().startswith('defense:'):
                nums = [int(x) for x in re.findall(r'\d+', t)]
                if nums:
                    return nums[0], (nums[1] if len(nums) > 1 else None)
        return None, None

    def _sockets(self, e):
        for t, c in _lines(e):
            m = re.match(r'Socketed \((\d+)/(\d+)\)', t)
            if m:
                return int(m.group(2))
        socks = e.get('socketables') or []
        return len(socks)

    def _rare_names(self, display, code):
        """Save values of the two rare name words (the page shows them when the item has its rare name)."""
        ch = self.cr.rare_name_choices(code)
        pre, suf = ch.get('rareprefix') or [(0, '')], ch.get('raresuffix') or [(0, '')]
        words = canon(display).split()
        a = next((i for i, n in pre if words and canon(n) == words[0]), pre[0][0])
        c = next((i for i, n in suf if len(words) > 1 and canon(n) == ' '.join(words[1:])), suf[0][0])
        return a + len(self.d.rare_suffix), c

    def _base_defense(self, code, stats, shown, eth):
        """Base defense of the save so that the game shows the page's value: shown = base * (100 + ED%) / 100
        + flat defense (per level stats included), clamped to the base item's range (x1.5 ethereal)."""
        t = self.cr.t.item.get(code, {})
        lo, hi = t.get('minac', 0), t.get('maxac', 0) + 1
        if eth:
            lo, hi = lo * 3 // 2, hi * 3 // 2
        if shown is None:
            return hi
        ed = sum(s['value'] for s in stats if s['id'] == S.ENH_DEFENSE)
        flat = sum(s['value'] for s in stats if s['id'] == S.DEFENSE)
        lvl = sum(s['value'] * self.sp.char['level'] // 8 for s in stats if s['id'] == 214)
        base = (shown - flat - lvl) * 100 // (100 + ed) if ed > -100 else hi
        return max(lo, min(hi, base))

    # ---- items
    def item_record(self, e, where=None):
        """(rec, children recs) of one page item; where = dict(location, equipped, x, y, panel)."""
        d, cr = self.d, self.cr
        code = self._code(e.get('code'))
        b = d.base(code)
        name = _norm(e.get('display_name') or e.get('name') or e.get('item') or code)
        q = (e.get('quality') or '').lower()
        t = cr.t.item.get(code, {})
        lines = _stat_lines(e)
        children, child_stats = [], []
        for s in e.get('socketables') or []:
            if not isinstance(s, dict) or not s.get('code'):
                continue
            try:
                crec, _ = self.item_record(s, None)
                encode_item(crec, self.d)                  # a socketed item the save cannot hold fails here, not the parent
            except Exception as err:
                self.note(T('im.socket_skip', '{item}: socketed item skipped ({error})', item=name, error=_err_text(err)))
                continue
            children.append(crec)
            st, _miss = self.sp.parse(_stat_lines(s))
            child_stats += st
        simple = bool(t.get('compact')) and q in ('', 'normal') and b.kind == 'misc' and not children
        rec = dict(code=code, flags=FLAG_IDENTIFIED | FLAG_ALWAYS)
        if where:
            rec.update(where)
        if simple:
            rec['flags'] |= FLAG_SIMPLE
            return rec, []
        quality0 = (e.get('quality') or '').lower()
        self._texts = []
        if quality0 in ('unique', 'set'):
            # the orange / grey text of a unique ("Gematria ... +25% Bonus to Defense", Azurewrath's rules...) is
            # one text stat of the item, not stats: its lines are taken out before the lines are read
            pre, _m = self.sp.parse(lines)                # first reading: picks the row among same-name rows
            row0 = self._unique_row(name, code, pre, sets=quality0 == 'set')
            if row0 is not None:
                self._texts = self._page_texts(e, quality0, row0)
                drop = Counter(l for _st, ls in self._texts for l in ls)
                kept = []
                for txt, col in lines:
                    k = canon(txt)
                    if drop.get(k):
                        drop[k] -= 1
                        continue
                    kept.append((txt, col))
                lines = kept
        stats, missing = self.sp.parse(lines)
        missing = self._per_level(missing, stats)
        if child_stats:
            stats = subtract(stats, merge(child_stats))
            neg = [s for s in stats if s['value'] < 0 and not any(x['id'] == s['id'] for x in self.sp.parse(lines)[0])]
            stats = [s for s in stats if s not in neg]
            # flags (a line without a number, e.g. "Already Upgraded" = stat 443) are shown once however many
            # socketed items have them: the parent's line is the children's, so nothing is left on the parent
            stats = [s for s in stats if not (s['value'] <= 0 and self._is_flag(s['id']))]
        blue ={t for t, c in lines if c == 3}
        for m in missing:
            if (m[0] if isinstance(m, tuple) else m) in blue and (b.kind != 'misc' or not cr.item_desc(code)):
                self.note(T('im.line', '{item}: line not read: {line}', item=name, line=m[0] if isinstance(m, tuple) else m))
        # NotArmory's "quality" is the save's: "High" is Superior (its display_quality)
        quality = {'unique': 'unique', 'set': 'set', 'rw': 'normal', 'rare': 'rare', 'magic': 'magic',
                   'crafted': 'crafted', 'superior': 'superior', 'high': 'superior', 'low': 'low', 'normal': 'normal',
                   '': 'normal'}.get(q, 'normal')
        ilvl, guessed = self._ilvl(e, code)
        rec.update(id=random.getrandbits(32), ilvl=ilvl, quality=quality)
        if e.get('eth'):
            rec['flags'] |= FLAG_ETHEREAL
        img = self._image(e, code)
        if img is not None:
            rec['image'] = img
        if quality in ('unique', 'set'):
            row = self._unique_row(name, code, stats, sets=quality == 'set')
            if row is None:
                self.note(T('im.no_row', '{item}: {quality} not found in the game tables, made as a normal item', item=name,
                            quality=T(f'cr.quality.{quality}', quality)))
                rec['quality'] = 'normal'
            else:
                rec['set_unique_id'] = row
                if guessed:                      # an item level the item could not drop at
                    lst = d.set_items if quality == 'set' else d.uniques
                    rec['ilvl'] = max(rec['ilvl'], min(127, int(lst[row].get('level') or 1)))
                stats, _added = self._hidden_and_text(e, quality, row, stats)
        elif quality == 'magic':
            rec['prefix'], rec['suffix'] = self._magic_ids(name, code)
        elif quality in ('rare', 'crafted'):
            rec['rare_names'] = self._rare_names(name, code)
            rec['rare_affixes'] = [None] * 6
        elif quality in ('superior', 'low'):
            rec['subquality'] = 0
        if q == 'rw' and str(e.get('type') or '').startswith('Superior ') and b.kind in ('armor', 'weapons'):
            rec['quality'] = 'superior'
            rec['subquality'] = 0
        if q == 'rw':
            rw = self._runeword(name)
            if rw is None:
                self.note(T('im.no_rw', '{item}: runeword not found, kept as a socketed normal item', item=name))
            else:
                key = d.runewords[rw][0]
                rec['flags'] |= FLAG_RUNEWORD
                rec['runeword_id'] = d._bykey.get(key, 0)
                rec['stats_runeword'] = []
        if not simple and rec['quality'] != 'normal' or rec['quality'] == 'normal' and b.kind in ('weapons', 'armor'):
            auto, stats = self._auto_affix(e, code, rec['ilvl'], stats)
            if auto:
                rec['auto_affix'] = auto
        rec['stats'] = [s for s in stats if d.isc.get(s['id'], {}).get('bits')]
        if b.kind == 'misc' and 'Shrine Vessel' in str(e.get('type') or ''):
            rec['stats'] = []          # shrine vessels: the lines are what the affix gives to each item class
        self._fit_ranges(rec, name)
        if body_kind(d, code) == 'armor':
            de = self._defense(e)
            shown, base_def = de
            rec['defense'] = base_def if base_def is not None else self._base_defense(code, stats, shown, bool(e.get('eth')))
        if body_kind(d, code) in ('armor', 'weapons'):
            cm, cdur = d.isc[S.MAX_DURABILITY], d.isc[S.DURABILITY]
            dur = 0 if t.get('nodur') else t.get('dur', 0)
            if dur:
                if quality == 'set':
                    dur = min(dur * 2, 255)
                elif quality == 'unique':
                    dur = min(dur * 3, 255)
                if e.get('eth'):
                    dur = dur // 2 + 1
            rec['max_durability'] = dur + cm['add']
            rec['durability'] = dur + cdur['add']
        if b.stack_max is not None:
            rec['quantity'] = max(1, min(511, int(e.get('quantity') or 1)))
        elif e.get('quantity') and 501 in d.isc and d.isc[501]['bits']:
            # containers / shrine vessels: the count is the stat "Quantity:" (ModItemShrineCount)
            rec['stats'] = [s for s in rec['stats'] if s['id'] != 501] + [
                dict(id=501, param=None, value=max(1, min(1000, int(e['quantity']))))]
        n_sock = self._sockets(e)
        if n_sock or children:
            rec['flags'] |= FLAG_SOCKETED
            rec['sockets'] = max(n_sock, len(children))
        rec['socketed_count'] = len(children)
        return rec, children

    def _per_level(self, missing, stats):
        """Lines of stats based on the character level ("(Based on Character Level)") the parser left: the value
        whose shown number (at the export's level) is the page's."""
        if not hasattr(self, '_lvl_stats'):
            self._lvl_stats = [(sid, canon(re.sub(r'%[+]?d', '', str(c.get('pos'))))) for sid, c in self.d.isc.items()
                               if c.get('op') == 2 and c.get('bits') and not c.get('param')
                               and 'Based on' in str(c.get('pos'))]
        left = []
        for m in missing:
            t = m[0] if isinstance(m, tuple) else m
            want = canon(t)
            found = None
            for sid, txt in self._lvl_stats:
                if txt.strip() and txt.strip() in want:
                    c = self.d.isc[sid]
                    for v in range(1, (1 << c['bits']) - c['add']):
                        r = self.sp.render([dict(id=sid, param=None, value=v)])
                        if r and canon(r[0]) == want:
                            found = dict(id=sid, param=None, value=v)
                            break
                if found:
                    break
            if found:
                stats.append(found)
            else:
                left.append(m)
        return left

    def _fit_ranges(self, rec, name):
        """Values above what one stat list can hold: the rest goes into the runeword list (runewords), else the
        value is cut to the limit (noted)."""
        rw = rec['flags'] & FLAG_RUNEWORD
        extra = []
        for st in rec['stats']:
            c = self.d.isc[st['id']]
            lo, hi = -c['add'], (1 << c['bits']) - 1 - c['add']
            v = st['value']
            if lo <= v <= hi:
                continue
            cut = max(lo, min(hi, v))
            if rw and lo <= v - cut <= hi:
                extra.append(dict(st, value=v - cut))
            else:
                self.note(T('im.cut', '{item}: stat {stat} = {value} is above what the save can hold, set to {cut}', item=name,
                            stat=st['id'], value=v, cut=cut))
            st['value'] = cut
        if extra:
            rec['stats_runeword'] = merge(rec.get('stats_runeword', []) + extra)

    # ---- stats the tooltip does not show (unlock flags, hidden skills) and cube upgrades
    TEXT_FUNCS = (31, 34)

    def _sublines(self, stats):
        """Comparison form of each tooltip line of stats (multi-line texts split, as the page shows them)."""
        out = []
        try:
            from mxl_tooltip import stat_lines
            for l in stat_lines(stats, self.d, self.sp.char):
                for part in str(l[1]).replace('\\n', '\n').split('\n'):
                    t = canon(part)
                    if t:
                        out.append(t)
        except Exception:
            pass
        return out

    def _sources(self, e, quality, row):
        """Stat lists the item can have: its unique / set row, and the cube upgrades the page shows. Trophy and
        class upgrades exist once per class with the same lines: only one of them is taken, the page's class
        ("(Druid Only)") first, then the any-class one — their class restriction (stat 463 = class + 1) is that
        version's."""
        sources = [self._row_stats(quality, row)]
        if quality != 'unique':
            return sources
        page = {canon(t) for t, c in _lines(e)}
        only = None
        for t, c in _lines(e):
            m = re.fullmatch(r'\((\w+) Only\)', t.strip())
            if m and m.group(1) in CLASS_INDEX:
                only = CLASS_INDEX[m.group(1)]
        picked = {}
        for cls, mods in self._upgrades().get(row, []):
            try:
                st = mods_stats(mods, self.d, 'min')
            except Exception:
                continue
            pos = [x for x in st if x['value'] > 0]
            shown = [l for x in pos if self._is_text(x['id']) for l in self._sublines([x])] or self._sublines(pos)
            if not shown or not all(l in page for l in shown):
                continue
            key = frozenset(shown)
            rank = 0 if cls == (only if only is not None else 255) else 1 if cls == 255 else 2
            if key not in picked or rank < picked[key][0]:
                picked[key] = (rank, st)
        sources += [st for rank, st in picked.values()]
        return sources

    def _page_texts(self, e, quality, row):
        """[(text stat, its lines)] of the item's sources whose whole text is on the page."""
        page = Counter(canon(t) for t, c in _lines(e))
        out, seen = [], set()
        for st in reversed(self._sources(e, quality, row)):       # cube upgrades first: they replace the row's text
            for x in st:
                if not self._is_text(x['id']) or x['value'] <= 0 or (x['id'], x.get('param')) in seen:
                    continue
                ls = self._sublines([x])
                need = Counter(ls)
                if ls and all(page[k] >= n for k, n in need.items()):
                    out.append((dict(x), ls))
                    seen.add((x['id'], x.get('param')))
                    page.subtract(need)
        return out

    def _auto_affix(self, e, code, ilvl, stats):
        """Innate (automagic) affix of the base: every Sacred / Angelic item has one, uniques and set items too.
        The one whose shown stats are on the page within their ranges (most of them), else the base's only kind
        (e.g. shields: hidden only). Its hidden stats (innate element, Innate X Damage value...) are added.
        Returns (auto id or None, stats)."""
        d = self.d
        try:
            autos = self.cr.auto_choices(code, ilvl)
        except Exception:
            autos = []
        if not autos:
            return None, stats
        have = {}
        for x in stats:
            k = (x['id'], x.get('param'))
            have[k] = have.get(k, 0) + x['value']
        page = [canon(t) for t, c in _lines(e)]
        best = None
        for i, _lab, a in autos:
            lo, hi = {}, {}
            for m in a['mods']:
                for src, how in ((lo, 'min'), (hi, 'max')):
                    try:
                        for x in mods_stats([(m['prop'], m['param'], m['min'], m['max'])], d, how):
                            k = (x['id'], x.get('param'))
                            src[k] = src.get(k, 0) + x['value']
                    except Exception:
                        pass
            vis = [k for k in hi if self._sublines([dict(id=k[0], param=k[1], value=hi[k] or 1)])]
            got = 0
            for k in vis:
                a_, b_ = sorted((lo.get(k, 0), hi.get(k, 0)))
                if k in have and have[k] >= a_:
                    got += 1
                elif self._page_value(dict(id=k[0], param=k[1], value=b_), page) is not None:
                    got += 1
            ok = got == len(vis) if vis else True
            key = (ok, got, len(vis) == 0, i)
            if ok and (best is None or key > best[0]):
                best = (key, i, lo, hi, vis)
        if best is None:
            return None, stats
        _key, i, lo, hi, vis = best
        if vis and not best[0][1]:
            return None, stats                    # nothing of it on the page and it has shown stats: unknown
        out = list(stats)
        for k, v in hi.items():
            if k in have:
                continue
            st = dict(id=k[0], param=k[1], value=v)
            if k in vis:                          # shown but not read (e.g. "Innate Cold Damage: 35 (49.0% ...)")
                pv = self._page_value(st, page)
                if pv is None:
                    continue
                st['value'] = pv
            elif not v:
                continue
            out.append(st)
        return i, out

    def _page_value(self, st, page):
        """Value of a stat read from its page line (the rendered line as a pattern around the value), or None."""
        ls = self._sublines([dict(st, value=777)])
        if len(ls) != 1 or '777' not in ls[0]:
            return None
        head, tail = ls[0].split('777', 1)

        def esc(t):
            return re.sub(r'\d+(?:\\\.\d+)?', r'[\\d.]+', re.escape(t))
        pat = esc(head) + r'(-?\d+)' + esc(tail)
        for line in page:
            m = re.fullmatch(pat, line)
            if m:
                return int(m.group(1))
        return None

    def _is_flag(self, sid):
        """Stat shown as a line without its value ("Already Upgraded")."""
        if not hasattr(self, '_flags'):
            self._flags = {}
        if sid not in self._flags:
            out = self.sp.render([dict(id=sid, param=None, value=3)])
            self._flags[sid] = bool(out) and not any(re.search(r'\d', t) for t in out) and not self._is_text(sid)
        return self._flags[sid]

    def _is_text(self, sid):
        return self.d.isc.get(sid, {}).get('func') in self.TEXT_FUNCS

    def _upgrades(self):
        """unique row -> [mods of each cube recipe that upgrades that unique in place (output "useitem")]."""
        if not hasattr(self, '_upg'):
            self._upg = {}
            try:
                rows = _rows(self.d, 'cubemain')
            except OSError:
                rows = []
            for r in rows:
                if len(r) < 0x4C + 0x54:
                    continue
                _fl, _item, iid, q, _qty = struct.unpack_from('<HHHBB', r, 0x14)
                if q != 7 or not iid or r[0x4C + 8] != 254:          # input 1 a unique, output = the same item
                    continue
                mods = []
                for j in range(5):
                    p, pa, lo, hi, _ch = struct.unpack_from('<iHhhH', r, 0x4C + 24 + 12 * j)
                    if 0 <= p < len(self.d.props):
                        mods.append((p, pa, lo, hi))
                if mods:
                    self._upg.setdefault(iid - 1, []).append((r[3], mods))      # (class, 255 = any; mods)
        return self._upg

    def _row_stats(self, quality, row):
        try:
            return [st for m in self.cr.mods(self.cr.new_spec(quality, row=row)) for st in m.stats(self.d)]
        except Exception:
            return []

    def _hidden_and_text(self, e, quality, row, stats):
        """Page stats completed with what the game has but the page reading missed: the unique / set row's (and
        shown cube upgrades') stats without a tooltip line (mastery unlock flags, hidden skills...), those whose
        lines are on the page but were not read (e.g. "+1 to The Collector" / "Gematria", "Orb Effects Applied to
        this Item are Doubled"), and the text stats whose whole text is on the page (their lines were not read as
        stats). Returns (stats, hidden stats added)."""
        page = Counter(canon(t) for t, c in _lines(e))
        want = {}
        for st in self._sources(e, quality, row):
            for x in st:
                k = (x['id'], x.get('param'))
                want[k] = want.get(k, 0) + x['value']
        want = {k: v for k, v in want.items() if v > 0 and self.d.isc.get(k[0], {}).get('bits')}
        texts = getattr(self, '_texts', None) or []
        text_keys = {(x['id'], x.get('param')) for x, _ls in texts}
        row_fixed = set()
        try:
            for m in self.cr.mods(self.cr.new_spec(quality, row=row)):
                if not m.variable:
                    row_fixed |= {(x['id'], x.get('param')) for x in m.stats(self.d)}
        except Exception:
            pass
        row_text = bool(text_keys & row_fixed)
        out = [x for x in stats if not (self._is_text(x['id']) and (x['id'], x.get('param')) in text_keys)]
        out = [x for x in out if not self._is_text(x['id']) or not texts]
        have = {(x['id'], x.get('param')) for x in out}
        read = Counter(l for x in out for l in self._sublines([x]))
        added = []
        for x, _ls in texts:
            if (x['id'], x.get('param')) not in have:
                out.append(dict(x))
                have.add((x['id'], x.get('param')))
        for (sid, p), v in want.items():
            if (sid, p) in have or self._is_text(sid):
                continue
            # read with another param of the same look (two skills of one name...): the item's own is the one
            for x in out:
                if x['id'] == sid and x.get('param') != p and (sid, x.get('param')) not in want and \
                        self._sublines([x]) == self._sublines([dict(x, param=p)]):
                    have.discard((sid, x.get('param')))
                    x['param'] = p
                    have.add((sid, p))
                    break
            if (sid, p) in have:
                continue
            st = dict(id=sid, param=p, value=v)
            ls = self._sublines([st])
            if not ls:                                  # no tooltip line: the game has it, the page cannot show it
                out.append(st)
                added.append(st)
                have.add((sid, p))
            elif all(page[l] > read.get(l, 0) for l in ls):    # shown on the page, missed by the reading
                out.append(st)
                have.add((sid, p))
                read.update(ls)
            elif row_text and (sid, p) in row_fixed and any(page[l] for l in ls):
                # the item shows its row's text, so it is in the row's state: a fixed row stat of which the page
                # shows a part is the item's (The Collector's hidden skill "+1 to The Collector" / "Gematria":
                # the page shows its "Gematria" once, with the text's)
                out.append(st)
                have.add((sid, p))
        if not texts:                               # text stats of the page that no source explains: kept as read
            pass
        return out, added

    def item_blob(self, e, where):
        rec, children = self.item_record(e, where)
        blob = build_blob(rec, children, self.d) if not rec['flags'] & FLAG_SIMPLE else encode_item(rec, self.d)
        return blob


# ---------------------------------------------------------------------------------------------- character
def valid_name(name):
    """A name the game accepts: 2 to 15 letters A-Z (- and _ allowed). Letters such as é or ư pass isalpha() but
    cannot be written in the save."""
    return 2 <= len(name) <= 15 and all(ch.isascii() and ch.isalpha() or ch in '-_' for ch in name)


def new_character(template_path, name, cls_name, data):
    """Character (not saved) made from an existing save: name, class, the class's starting attributes / life /
    mana / stamina, no skills, no items, level 1."""
    if not valid_name(name):
        raise CharError(T('ch.err.name', 'name: 2 to 15 letters (- and _ allowed)'))
    cls = CLASS_INDEX.get(cls_name)
    if cls is None:
        raise CharError(T('im.err.class', 'unknown class {cls}', cls=repr(cls_name)))
    ch = Character(template_path, data)
    ch.head[0x14:0x24] = name.encode('latin1').ljust(16, b'\0')
    ch.head[0x28] = cls
    ch.head[0x2B] = 1
    ch.head[0x25] = 0
    rows = _rows(data, 'charstats')
    r = rows[cls]
    strength, dex, ene, vit, stamina, hpadd = r[0x30], r[0x31], r[0x32], r[0x33], r[0x34], r[0x35]
    life = vit + hpadd
    for sid, v in ((0, strength), (1, ene), (2, dex), (3, vit), (4, 0), (5, 0), (12, 1), (13, 0), (14, 0), (15, 0)):
        ch.set_stat(sid, v)
    for cur, mx, v in ((6, 7, life), (8, 9, ene), (10, 11, stamina)):
        ch.set_stat(mx, v << 8)
        ch.set_stat(cur, v << 8)
    ch.skills = ch.skills[:2] + bytes(len(ch.skills) - 2)
    ch.items = []
    for k in range(0x38, 0x78, 4):                # skill hot keys: none
        ch.head[k:k + 4] = struct.pack('<I', 0xFFFF)
    for k in range(0x78, 0x88):                   # left / right mouse skills: attack
        ch.head[k] = 0
    for k in range(3):                            # quests and waypoints of a new character
        base = MC.QUEST_DATA + 96 * k
        ch.head[base:base + 96] = bytes(96)
    return ch


def class_skills(data, cls):
    return sorted(k for k, v in data.skill_class.items() if v == cls)


ATTRS = (('strength', 0), ('energy', 1), ('dexterity', 2), ('vitality', 3))


def _apply_attributes(ch, build, data, report):
    """Base attributes of the export ({{strength}}=69 ...): the points are spent like in game (life per vitality,
    mana per energy, stamina per vitality of the class)."""
    vals = dict(re.findall(r'\{\{(\w+)\}\}=(-?\d+)', str(build.get('stats') or '')))
    if not any(k in vals for k, _ in ATTRS):
        return
    r = _rows(data, 'charstats')[ch.cls]
    life_vit, stam_vit, mana_ene = r[0x46], r[0x47], r[0x48]
    spent = 0
    for key, sid in ATTRS:
        if key not in vals:
            continue
        cur = ch.stat(sid)
        add = int(vals[key]) - cur
        if add <= 0:
            continue
        ch.set_stat(sid, cur + add)
        spent += add
        if sid == 3:
            for c_id, m_id, per in ((6, 7, life_vit), (10, 11, stam_vit)):
                ch.set_stat(m_id, ch.stat(m_id) + ((per * add) << 6))
                ch.set_stat(c_id, ch.stat(m_id))
        elif sid == 1:
            ch.set_stat(9, ch.stat(9) + ((mana_ene * add) << 6))
            ch.set_stat(8, ch.stat(9))
    pool = ch.stat(4)
    if spent > pool:
        report.append(T('im.attrs', 'attributes: the export spends {spent} points, the level and quests give {pool} '
                        '(the rest comes from other rewards); set as exported', spent=spent, pool=pool))
    ch.set_stat(4, max(0, pool - spent))


STAT_LABELS = {0: 'strength', 1: 'energy', 2: 'dexterity', 3: 'vitality', 4: 'unused stat points',
               5: 'unused skill points', 7: 'base life', 9: 'base mana', 11: 'base stamina'}


def _cap_stats(ch, data, report):
    """Values above what the save's fields can hold (e.g. base life 21 bits = 8191.99, attributes 11 bits = 2047):
    set to the maximum and reported."""
    widths = MC._csv(data)
    for sid, label in STAT_LABELS.items():
        if sid not in widths:
            continue
        top = (1 << widths[sid][1]) - 1
        v = ch.stat(sid)
        if v > top:
            scale = 256 if sid in (7, 9, 11) else 1
            report.append(T('im.cap', '{what}: {value} is more than a save can hold, set to {top}',
                            what=T(f'im.stat.{sid}', label), value=f'{v / scale:.0f}', top=top // scale))
            ch.set_stat(sid, top)
            if sid in (7, 9, 11):
                ch.set_stat(sid - 1, top)


def apply_build(ch, build, data, report):
    """Level, quests (with rewards) and skill points of a build planner export."""
    if not build:
        return
    lvl = int(build.get('level') or 1)
    try:
        ch.set_level(min(lvl, MC.max_level(data, ch.cls)))
    except CharError as e:
        report.append(T('im.level', 'level: {error}', error=e))
    q = build.get('questsCompleted') or {}
    done = {0: set(), 1: set(), 2: set()}
    for key, diffs in q.items():
        word = QUEST_WORDS.get(key)
        if word is None:
            if any((diffs or {}).values()):
                report.append(T('im.quest', 'quest "{quest}" is not a quest of the quest log: not set', quest=key))
            continue
        for k, dn in enumerate(('normal', 'nightmare', 'hell')):
            if (diffs or {}).get(dn):
                done[k].add(word)
    every = {w for _, _, qs, _ in MC.ACTS for w, _ in qs}
    for k in range(3):
        if done[k]:
            # the export only lists the quests with rewards: a difficulty where any is done is played through,
            # so all its quests and waypoints are set (the next difficulty opens like in game)
            ch.set_quests(k, every)
            ch.set_waypoints(k, set(MC.waypoint_ids(k)))
    if any(done.values()):
        report.append(T('im.quests', 'quests: every quest and waypoint of the difficulties with quests done in the export '
                        '({modes}) is set', modes=', '.join(MC.difficulty_name(k) for k in range(3) if done[k])))
    _apply_attributes(ch, build, data, report)
    skills = class_skills(data, ch.cls)
    names = {canon(data.skill_names.get(s, '')): i for i, s in enumerate(skills)}
    raw = bytearray(ch.skills)
    spent = 0
    for sname, pts in (build.get('skillPoints') or {}).items():
        i = names.get(canon(sname))
        if i is None or 2 + i >= len(raw):
            report.append(T('im.skill', 'skill "{skill}" is not a {cls} class skill: not set', skill=sname, cls=ch.cls))
            continue
        raw[2 + i] = max(0, min(255, int(pts)))
        spent += raw[2 + i]
    ch.skills = bytes(raw)
    pool = ch.stat(5)
    if spent > pool:
        report.append(T('im.skillpts', 'skill points: the export uses {spent}, the level and quests give {pool} '
                        '(the rest comes from items / other rewards); set as exported', spent=spent, pool=pool))
    ch.set_stat(5, max(0, pool - spent))
    _cap_stats(ch, data, report)


def import_character(page_path, template_path, save_dir, name, data, cls_name=None, backup=True, level=None):
    """Builds name.d2s (+ .stash, .key, .map, .ma*) in save_dir from a saved character page.
    cls_name / level: the user's choice when the page has no build planner export (they win over the page's).
    Returns (d2s path, report lines)."""
    from mxl_save import commit_file, check_game_closed, load_character, EditError
    build, items = load_page(page_path)
    no_build = not build
    cls_name = cls_name or (build or {}).get('class')
    if not cls_name:
        raise ImportError_(T('im.err.no_class', 'the page has no class: choose the class of the character'))
    if no_build or level:
        build = dict(build or {}, level=int(level or (build or {}).get('level') or 1))
    level = int(build.get('level') or 1)
    log.info('import: %s as "%s" (%s level %s, %d items on the page%s), template %s', page_path, name, cls_name, level,
             len(items), ', no build data on the page' if no_build else '', template_path)
    check_game_closed()
    dst = os.path.join(save_dir, name + '.d2s')
    if os.path.exists(dst):
        raise CharError(T('im.exists', '{name} already exists in the save folder', name=name))
    ch = new_character(template_path, name, cls_name, data)
    report = []
    if no_build:
        report.append(T('im.no_build', 'the page has no build data: class and level as chosen; skills, quests and '
                        'attributes are not set'))
    apply_build(ch, build, data, report)
    imp = Importer(data, level)
    stash_blobs = []
    placed = 0
    for e in items:
        loc = e.get('location')
        try:
            if loc == 'Gear':
                slot = GEAR_SLOTS.get(e.get('slot') or '')
                if slot is None:
                    raise ImportError_(T('im.err.slot', 'unknown slot {slot}', slot=repr(e.get('slot'))))
                where = dict(location=MC.LOC_EQUIPPED, equipped=slot, x=slot, y=0, panel=0)
            elif loc == 'Belt':
                where = dict(location=LOC_BELT, equipped=0, x=int(e.get('col') or 0), y=0, panel=0)
            elif loc == 'Inventory':
                where = dict(location=LOC_STORED, equipped=0, x=int(e.get('col') or 0), y=int(e.get('row') or 0),
                             panel=PANEL_INVENTORY)
            elif loc == 'Stash':
                where = dict(location=LOC_STORED, equipped=0, x=int(e.get('col') or 0), y=int(e.get('row') or 0),
                             panel=PANEL_STASH)
            else:
                raise ImportError_(T('im.err.loc', 'location {loc} not supported', loc=repr(loc)))
            blob = imp.item_blob(e, where)
            it = MC.blob_item(blob, data)
        except Exception as err:                 # one item the save cannot hold is skipped, not the whole import
            report.append(T('im.not_imported', '{item}: not imported ({error})', item=e.get('display_name'),
                            error=_err_text(err)))
            continue
        if loc == 'Stash':
            stash_blobs.append(blob)
        else:
            ch.items.append(Entry(it, blob))
        placed += 1
    if imp.guessed:
        report.append(T('im.ilvl_guess', '{n} items have no item level on the page (runes, keys, trophies, potions...): '
                        'set to the character level, at least their base level', n=imp.guessed))
    report = report + imp.report
    content = ch.build()
    n_items = len(ch.items)

    def verify(tmp):
        try:
            c = Character(tmp, data)
            load_character(tmp, data)
        except Exception as err:
            raise EditError(T('ch.err.verify', 'check after writing failed: {error}', error=err))
        if len(c.items) != n_items:
            raise EditError(T('ch.err.verify_count', 'check after writing failed: item count'))
    commit_file(dst, content, verify, False)
    # stash: page 1 of the character stash
    tmpl_dir = os.path.dirname(template_path)
    tmpl = os.path.splitext(os.path.basename(template_path))[0]
    stash_src = os.path.join(tmpl_dir, tmpl + '.stash')
    header = b''
    if os.path.exists(stash_src):
        with open(stash_src, 'rb') as f:
            header = f.read()[:8]
    if stash_blobs:
        from mxl_save import build_stash, parse_stash
        content = build_stash(header or b'', {0: stash_blobs})
        stash_dst = os.path.join(save_dir, name + '.stash')

        def verify_stash(tmp):
            try:
                n = len(parse_stash(tmp, data))
            except Exception as err:
                raise EditError(T('im.err.stash_verify', 'stash check after writing failed: {error}', error=err))
            if n != len(stash_blobs):
                raise EditError(T('im.err.stash_count', 'stash check after writing failed: item count'))
        commit_file(stash_dst, content, verify_stash, False)
    elif os.path.exists(stash_src):
        shutil.copy2(stash_src, os.path.join(save_dir, name + '.stash'))
    for fn in os.listdir(tmpl_dir):
        stem, ext = os.path.splitext(fn)
        if stem == tmpl and (ext.lower() in ('.key', '.map') or ext.lower().startswith('.ma')):
            target = os.path.join(save_dir, name + ext)
            if not os.path.exists(target):
                shutil.copy2(os.path.join(tmpl_dir, fn), target)
    report.insert(0, T('im.summary', '{name}: {cls} level {level}, {placed} of {total} items imported '
                       '({on_char} on the character, {in_stash} in the stash).', name=name, cls=cls_name, level=ch.level,
                       placed=placed, total=len(items), on_char=n_items, in_stash=len(stash_blobs)))
    log.info('import done: %s\n%s', report[0], '\n'.join('    • ' + r for r in report[1:]))
    return dst, report


# ---------------------------------------------------------------------------------------------- GUI
def _template(app):
    """A save of the same game version to start from: the selected character, else any character."""
    try:
        cur = app.character_file()
    except Exception:
        cur = None
    if cur and os.path.exists(cur):
        return cur
    for n in app.characters():
        p = os.path.join(app.save_dir, n + '.d2s')
        if os.path.exists(p):
            return p
    return None


def _ask_class_level(root, data, cls=None, level=None):
    """Class and level of a page without the build planner export (item list only): (class name, level) or None."""
    import tkinter as tk
    from tkinter import ttk, messagebox
    names = list(CLASS_INDEX)
    win = tk.Toplevel(root)
    win.title(T('im.title', 'Import character'))
    win.transient(root)
    win.resizable(False, False)
    tk.Label(win, text=T('im.ask_class', 'This page has no build data (class, level, skills, quests).\n'
                         'Choose the class and level of the new character:'), justify='left').grid(
        row=0, column=0, columnspan=2, padx=12, pady=(12, 8), sticky='w')
    tk.Label(win, text=T('im.class', 'Class')).grid(row=1, column=0, padx=(12, 6), pady=4, sticky='w')
    cv = tk.StringVar(value=cls if cls in CLASS_INDEX else '')
    ttk.Combobox(win, textvariable=cv, values=names, state='readonly', width=16).grid(row=1, column=1, padx=(0, 12), pady=4,
                                                                                       sticky='w')
    tk.Label(win, text=T('im.lvl', 'Level')).grid(row=2, column=0, padx=(12, 6), pady=4, sticky='w')
    lv = tk.StringVar(value=str(level or 1))
    ttk.Spinbox(win, from_=1, to=255, textvariable=lv, width=6).grid(row=2, column=1, padx=(0, 12), pady=4, sticky='w')
    out = []

    def ok():
        c = cv.get()
        if c not in CLASS_INDEX:
            messagebox.showerror(T('im.title', 'Import character'), T('im.err.pick_class', 'Choose a class.'), parent=win)
            return
        try:
            top = MC.max_level(data, CLASS_INDEX[c])
        except Exception:
            top = 255
        try:
            n = int(lv.get())
        except ValueError:
            n = 0
        if not 1 <= n <= top:
            messagebox.showerror(T('im.title', 'Import character'), T('im.err.pick_level', 'Level: 1 to {top}.', top=top),
                                 parent=win)
            return
        out.append((c, n))
        win.destroy()

    row = tk.Frame(win)
    row.grid(row=3, column=0, columnspan=2, pady=(8, 12))
    tk.Button(row, text='OK', width=10, command=ok).pack(side='left', padx=4)
    tk.Button(row, text=T('library.cancel', 'Cancel'), width=10, command=win.destroy).pack(side='left', padx=4)
    win.bind('<Return>', lambda _e: ok())
    win.bind('<Escape>', lambda _e: win.destroy())
    win.grab_set()
    root.wait_window(win)
    return out[0] if out else None


def import_dialog(app):
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog
    root = app.root
    if not getattr(app, 'save_dir', None) or not os.path.isdir(app.save_dir):
        messagebox.showerror(T('im.title', 'Import character'), T('im.no_save_dir', 'The game save folder is not set.'), parent=root)
        return
    tmpl = _template(app)
    if not tmpl:
        messagebox.showerror(T('im.title', 'Import character'),
                             T('im.need_template', 'Make one character in game first: the import starts from an '
                               'existing save of this game version.'), parent=root)
        return
    path = filedialog.askopenfilename(
        parent=root, title=T('im.pick_title', 'Character page saved from median-xl.com (Save as… Web page)'),
        initialdir=os.path.join(os.path.expanduser('~'), 'Desktop'),
        filetypes=[(T('im.ft.page', 'Saved character page'), '*.html *.htm'), (T('im.ft.json', 'Item list'), '*.json'),
                   (T('im.ft.all', 'All files'), '*.*')])
    if not path:
        return
    try:
        build, items = load_page(path)
    except (ImportError_, OSError, ValueError) as e:
        messagebox.showerror(T('im.title', 'Import character'), str(e), parent=root)
        return
    default = re.sub(r'[^A-Za-z_-]', '', str((build or {}).get('name') or ''))[:15]
    cls = (build or {}).get('class')
    level = None
    if cls not in CLASS_INDEX or not (build or {}).get('level'):
        picked = _ask_class_level(root, app.data, cls if cls in CLASS_INDEX else None, (build or {}).get('level'))
        if not picked:
            return
        cls, level = picked
    while True:
        name = simpledialog.askstring(
            T('im.title', 'Import character'),
            T('im.ask_name', '{cls} level {level}, {n} items.\n\nName of the new character:', cls=cls,
              level=level or (build or {}).get('level', '?'), n=len(items)),
            initialvalue=default, parent=root)
        if not name:
            return
        name = name.strip()
        if not valid_name(name):
            messagebox.showerror(T('im.title', 'Import character'), T('ch.err.name', 'name: 2 to 15 letters (- and _ allowed)'),
                                 parent=root)
            default = name
            continue
        if os.path.exists(os.path.join(app.save_dir, name + '.d2s')):
            messagebox.showerror(T('im.title', 'Import character'), T('im.exists_dot', '{name} already exists in the save folder.', name=name),
                                 parent=root)
            default = name
            continue
        break
    root.configure(cursor='watch')
    root.update_idletasks()
    try:
        dst, report = import_character(path, tmpl, app.save_dir, name, app.data, cls, level=level)
    except Exception as e:                                   # nothing is written when the build fails
        log.exception('import FAILED: %s as "%s"', path, name)
        root.configure(cursor='')
        import mxl_log
        more = '\n\n' + T('log.details', 'Details: {file}', file=mxl_log.path()) if mxl_log.path() else ''
        messagebox.showerror(T('im.title', 'Import character'), f'{type(e).__name__}: {e}{more}', parent=root)
        return
    root.configure(cursor='')
    try:
        app.select_character(name, app.view.get())
    except Exception:
        pass
    win = tk.Toplevel(root)
    win.title(T('im.report_title', 'Import — {name}', name=name))
    win.geometry('760x460')
    txt = tk.Text(win, wrap='word', bg='#1e1e1e', fg='#e0e0e0', insertbackground='#e0e0e0', bd=0, padx=8, pady=8)
    sb = tk.Scrollbar(win, command=txt.yview)
    txt.configure(yscrollcommand=sb.set)
    sb.pack(side='right', fill='y')
    txt.pack(fill='both', expand=True)
    txt.insert('end', report[0] + '\n\n')
    if len(report) > 1:
        txt.insert('end', T('im.notes', 'Notes:') + '\n' + '\n'.join('• ' + r for r in report[1:]))
    txt.configure(state='disabled')
    tk.Button(win, text=T('cr.btn.close', 'Close'), command=win.destroy).pack(pady=6)


def install(App):
    original = App._build

    def _build(self):
        original(self)
        import ttkbootstrap as ttk
        top = self.btn_library.master
        ttk.Button(top, text=T('im.btn', 'Import character…'), command=lambda: import_dialog(self)).pack(side='left', padx=(4, 0))

    App._build = _build
