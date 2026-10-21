"""Offline item creator / editor for Median XL (single player).

Builds item records the way the game generates them (legit values only) and encodes them in the exact bit format
that mxl_format.parse_item reads (D2 1.13c / Median XL Sigma). Created items are written into a stash with the
collector's own safe writer (mxl_save.edit_stash: verification, backup, game-closed check).

Rules used (from the game's code and tables):
- automagic (base auto-prefix group, the innate affix) is rolled for every quality, sets and uniques included (Sigma);
- set items: 15-bit set-item row, no per-item set bonus lists (Median has no aprops), durability x2;
- uniques: 15-bit unique row, durability x3 (capped at 255);
- magic: 11-bit prefix/suffix = affix row + 1; rare: names (8+8 bits), up to 3 prefixes + 3 suffixes;
- runewords: 16-bit id = string-table index of the runeword name key, runes socketed as children;
- ethereal (not on set items): stored defense x3/2, max durability /2 + 1;
- armor with Enhanced Defense: base defense = max + 1;
- stats are saved sorted by (stat, param), same stat+param from several mods are added together.
"""
import os
import random
import re
import struct

import stat_ids as S
from mxl_format import STAT_GROUPS, SET_UNIQUE_ID_BITS
from mxl_format_fix import body_kind
from mxl_lang import T
from mxl_rules import (mod_ranges, mods_stats, item_type_codes, is_innate_damage, runeword_row,
                       stat_range, unique_name, rare_name)

QUALITY_ID = {'low': 1, 'normal': 2, 'superior': 3, 'magic': 4, 'set': 5, 'rare': 6, 'unique': 7, 'crafted': 8, 'tempered': 9}
QUALITY_NAME = {v: k for k, v in QUALITY_ID.items()}
ITEM_VERSION = 101
FLAG_IDENTIFIED = 1 << 4
FLAG_SOCKETED = 1 << 11
FLAG_SIMPLE = 1 << 21
FLAG_ETHEREAL = 1 << 22
FLAG_ALWAYS = 1 << 23
FLAG_PERSONALIZED = 1 << 24
FLAG_RUNEWORD = 1 << 26
LOC_STORED, LOC_SOCKETED = 0, 6
PANEL_STASH = 5
MAX_ILVL = 127  # 7 bits in the save format
CRAFTED_AFFIXES = 4  # crafted items: 4 rare affixes (1-3 prefixes, 1-3 suffixes), as the game's crafted items
FOLLOWERS = {s for lead, grp in STAT_GROUPS.items() for s in grp}


class CreateError(Exception):
    """Item that cannot be created with legit values (message for the user)."""


class StoreError(CreateError):
    """A value the save format cannot hold (too many bits)."""


class Problem(str):
    """A legit-check message (in the chosen language) that remembers its stat id."""

    def __new__(cls, text, sid=None):
        obj = str.__new__(cls, text)
        obj.sid = sid
        return obj


# --------------------------------------------------------------------------------------------- bit writer

class BitWriter:
    def __init__(self):
        self.value = 0
        self.pos = 0

    def write(self, n, v):
        if n <= 0:
            return
        if v < 0 or v >= (1 << n):
            raise CreateError(f'value {v} does not fit in {n} bits')
        self.value |= v << self.pos
        self.pos += n

    def bytes(self):
        return self.value.to_bytes((self.pos + 7) // 8, 'little') if self.pos else b''


# --------------------------------------------------------------------------------------------- raw tables

def _rows(data, name):
    with open(f'{data.folder}/{name}.bin', 'rb') as f:
        b = f.read()
    n = struct.unpack_from('<I', b)[0]
    rs = (len(b) - 4) // n if n else 0
    return [b[4 + i * rs:4 + (i + 1) * rs] for i in range(n)]


class Tables:
    """Fields of the game tables that mxl_data.Data does not load (offsets of the 1.13c structures,
    checked against the extracted tables)."""

    def __init__(self, data):
        self.data = data
        self.item = {}            # code -> dict(compact, spawnable, dur, nodur, qlvl, rarity)
        for kind in ('weapons', 'armor', 'misc'):
            for r in _rows(data, kind):
                code = r[128:132].decode('latin1')
                if code in self.item:
                    continue
                self.item[code] = dict(compact=r[0x143], spawnable=r[0x133], dur=r[0x112], nodur=r[0x113],
                                       qlvl=r[0xFD], magic_lvl=r[0x140], minac=struct.unpack_from('<i', r, 0xCC)[0],
                                       maxac=struct.unpack_from('<i', r, 0xD0)[0])
        self.itype = []           # index -> dict(magic_only, rare, normal_only, maxsock(1, 25, 40))
        for r in _rows(data, 'itemtypes'):
            self.itype.append(dict(magic_only=r[0x14], rare=r[0x15], normal_only=r[0x16],
                                   maxsock=(r[0x1A], r[0x1B], r[0x1C]), gfx=r[0x23]))
        self.affix_extra = {}     # kind -> [dict(rare, cls, freq)]
        for kind in ('magicprefix', 'magicsuffix', 'automagic'):
            self.affix_extra[kind] = [dict(rare=r[0x64], cls=r[0x67], freq=r[0x82]) for r in _rows(data, kind)]
        self.rare_names = {}
        for kind in ('rareprefix', 'raresuffix'):
            self.rare_names[kind] = [dict(itypes={t for t in struct.unpack_from('<7h', r, 16) if t > 0},
                                          etypes={t for t in struct.unpack_from('<4h', r, 30) if t > 0},
                                          key=r[38:64].split(b'\0')[0].decode('latin1')) for r in _rows(data, kind)]
        self.superior = []        # qualityitems rows: (weapon ok, armor ok)
        for r in _rows(data, 'qualityitems'):
            self.superior.append(dict(weapon=bool(r[1] or r[3] or r[6]), armor=bool(r[0] or r[2] or r[8] or r[9])))
        self.rw_complete = [r[128] for r in _rows(data, 'runes')]


# --------------------------------------------------------------------------------------------- encoding

def _write_stats(bw, stats, data):
    """Writes a stat list [{id, param, value}] (already merged and sorted) followed by 0x1FF."""
    by_id = {}
    for s in stats:
        by_id.setdefault(s['id'], s)
    leads = {s['id'] for s in stats if s['id'] in STAT_GROUPS}
    for s in stats:
        sid = s['id']
        if sid in FOLLOWERS and any(sid in STAT_GROUPS[l] for l in leads):
            continue                        # written with its group lead
        bw.write(9, sid)
        for x in [sid] + STAT_GROUPS.get(sid, []):
            c = data.isc[x]
            st = s if x == sid else by_id.get(x, {'id': x, 'param': None, 'value': 0})
            if c['param']:
                bw.write(c['param'], st.get('param') or 0)
            raw = st['value'] + c['add']
            if not 0 <= raw < (1 << c['bits']):
                lo, hi = -c['add'], (1 << c['bits']) - 1 - c['add']
                raise StoreError(T('cr.err.store', 'stat {stat}: {value} cannot be stored ({lo} to {hi})',
                                   stat=x, value=st['value'], lo=lo, hi=hi))
            bw.write(c['bits'], raw)
    bw.write(9, 0x1FF)


def encode_item(rec, data):
    """Bytes ('JM' + bits) of one item record. rec keys: the fields of mxl_format.Item plus
    flags (raw 32-bit header flags), runeword_id (16 bits), tome_bits, realm_bit, set_mask; stats lists are
    lists of {id, param, value}. Socketed children are not included (see build_blob)."""
    bw = BitWriter()
    flags = rec['flags']
    bw.write(32, flags)
    bw.write(10, rec.get('version', ITEM_VERSION))
    bw.write(3, rec.get('location', LOC_STORED))
    bw.write(4, rec.get('equipped', 0))
    bw.write(4, rec.get('x', 0))
    bw.write(4, rec.get('y', 0))
    bw.write(3, rec.get('panel', PANEL_STASH))
    code = rec['code']
    for ch in code:
        bw.write(8, ord(ch))
    if flags & FLAG_SIMPLE:
        bw.write(1, rec.get('socketed_count', 0))
        return b'JM' + bw.bytes()
    bw.write(3, rec.get('socketed_count', 0))
    bw.write(32, rec['id'])
    bw.write(7, rec['ilvl'])
    q = rec['quality']
    bw.write(4, QUALITY_ID[q] if isinstance(q, str) else q)
    q = QUALITY_NAME.get(q, q) if not isinstance(q, str) else q
    if rec.get('image') is not None:
        bw.write(1, 1)
        bw.write(3, rec['image'])
    else:
        bw.write(1, 0)
    if rec.get('auto_affix') is not None:
        bw.write(1, 1)
        bw.write(11, rec['auto_affix'])
    else:
        bw.write(1, 0)
    if q in ('low', 'superior'):
        bw.write(3, rec.get('subquality', 0))
    elif q == 'magic':
        bw.write(11, rec.get('prefix') or 0)
        bw.write(11, rec.get('suffix') or 0)
    elif q in ('set', 'unique'):
        bw.write(SET_UNIQUE_ID_BITS, rec['set_unique_id'])
    elif q == 'tempered':                       # Sigma quality 9: the name only, affix stats in the stat list
        a, b = rec['rare_names']
        bw.write(8, a)
        bw.write(8, b)
    elif q in ('rare', 'crafted'):
        a, b = rec['rare_names']
        bw.write(8, a)
        bw.write(8, b)
        for af in rec['rare_affixes']:
            if af:
                bw.write(1, 1)
                bw.write(11, af)
            else:
                bw.write(1, 0)
    if flags & FLAG_RUNEWORD:
        bw.write(16, rec['runeword_id'])
    if flags & FLAG_PERSONALIZED:
        for ch in rec.get('personalized_name', ''):
            bw.write(7, ord(ch))
        bw.write(7, 0)
    if code in ('tbk ', 'ibk '):
        bw.write(5, rec.get('tome_bits', 0))
    bw.write(1, rec.get('realm_bit', 0))
    base = data.base(code)
    kind = body_kind(data, code)
    if kind == 'armor':
        c = data.isc[S.DEFENSE]
        bw.write(c['bits'], rec['defense'] + c['add'])
    if kind in ('armor', 'weapons'):
        cm = data.isc[S.MAX_DURABILITY]
        maxd = rec['max_durability']          # raw value, as read by parse_item
        bw.write(cm['bits'], maxd)
        if maxd != cm['add']:
            bw.write(data.isc[S.DURABILITY]['bits'], rec['durability'])
    if base.stack_max is not None:
        bw.write(9, rec.get('quantity', 1))
    if flags & FLAG_SOCKETED:
        bw.write(4, rec.get('sockets', 0))
    set_mask = rec.get('set_mask', 0)
    if q == 'set':
        bw.write(5, set_mask)
    _write_stats(bw, rec.get('stats', []), data)
    for i in range(5):
        if set_mask >> i & 1:
            _write_stats(bw, rec.get(f'set_stats_{i}', []), data)
    if flags & FLAG_RUNEWORD:
        _write_stats(bw, rec.get('stats_runeword', []), data)
    return b'JM' + bw.bytes()


NUM_SOCKETS = 194          # item_numsockets: an affix's "sock" property ("Innate" of Angelic gear: +6) is turned into
                           # real sockets by the game (capped at the item's maximum), never kept as a stat


def build_blob(rec, children, data):
    """Item bytes followed by its socketed items (format of a stash entry)."""
    for k in ('stats', 'stats_runeword'):
        if rec.get(k):
            rec[k] = [s for s in rec[k] if s['id'] != NUM_SOCKETS]
    out = encode_item(rec, data)
    for i, ch in enumerate(children):
        ch = dict(ch, location=LOC_SOCKETED, equipped=0, x=i, y=0, panel=0)
        out += encode_item(ch, data)
    return out


# --------------------------------------------------------------------------------------------- decoding (lossless)

class _BR:
    def __init__(self, b):
        self.v = int.from_bytes(b, 'little')
        self.pos = 0
        self.n = len(b) * 8

    def read(self, n):
        r = (self.v >> self.pos) & ((1 << n) - 1)
        self.pos += n
        return r


def _read_stats(br, data):
    out = []
    while True:
        sid = br.read(9)
        if sid == 0x1FF:
            return out
        if br.pos > br.n:
            raise CreateError('stat list without end marker')
        for x in [sid] + STAT_GROUPS.get(sid, []):
            c = data.isc[x]
            p = br.read(c['param']) if c['param'] else None
            out.append(dict(id=x, param=p, value=br.read(c['bits']) - c['add']))


def decode_item(blob, data):
    """Inverse of encode_item for one item (blob starts at 'JM'): (record, size in bytes)."""
    if blob[:2] != b'JM':
        raise CreateError('not an item')
    br = _BR(bytes(blob[2:]))
    r = {}
    r['flags'] = flags = br.read(32)
    r['version'] = br.read(10)
    r['location'] = br.read(3)
    r['equipped'] = br.read(4)
    r['x'] = br.read(4)
    r['y'] = br.read(4)
    r['panel'] = br.read(3)
    r['code'] = code = ''.join(chr(br.read(8)) for _ in range(4))
    if flags & FLAG_SIMPLE:
        r['socketed_count'] = br.read(1)
        return r, 2 + (br.pos + 7) // 8
    r['socketed_count'] = br.read(3)
    r['id'] = br.read(32)
    r['ilvl'] = br.read(7)
    q = r['quality'] = QUALITY_NAME.get(br.read(4), 'unknown')
    r['image'] = br.read(3) if br.read(1) else None
    r['auto_affix'] = br.read(11) if br.read(1) else None
    if q in ('low', 'superior'):
        r['subquality'] = br.read(3)
    elif q == 'magic':
        r['prefix'] = br.read(11)
        r['suffix'] = br.read(11)
    elif q in ('set', 'unique'):
        r['set_unique_id'] = br.read(SET_UNIQUE_ID_BITS)
    elif q == 'tempered':
        r['rare_names'] = (br.read(8), br.read(8))
    elif q in ('rare', 'crafted'):
        r['rare_names'] = (br.read(8), br.read(8))
        r['rare_affixes'] = [br.read(11) if br.read(1) else None for _ in range(6)]
    if flags & FLAG_RUNEWORD:
        r['runeword_id'] = br.read(16)
    if flags & FLAG_PERSONALIZED:
        s = ''
        while True:
            ch = br.read(7)
            if not ch:
                break
            s += chr(ch)
        r['personalized_name'] = s
    if code in ('tbk ', 'ibk '):
        r['tome_bits'] = br.read(5)
    r['realm_bit'] = br.read(1)
    base = data.base(code)
    kind = body_kind(data, code)
    if kind == 'armor':
        c = data.isc[S.DEFENSE]
        r['defense'] = br.read(c['bits']) - c['add']
    if kind in ('armor', 'weapons'):
        cm = data.isc[S.MAX_DURABILITY]
        r['max_durability'] = br.read(cm['bits'])
        if r['max_durability'] != cm['add']:
            r['durability'] = br.read(data.isc[S.DURABILITY]['bits'])
    if base.stack_max is not None:
        r['quantity'] = br.read(9)
    if flags & FLAG_SOCKETED:
        r['sockets'] = br.read(4)
    r['set_mask'] = br.read(5) if q == 'set' else 0
    r['stats'] = _read_stats(br, data)
    for i in range(5):
        if r['set_mask'] >> i & 1:
            r[f'set_stats_{i}'] = _read_stats(br, data)
    if flags & FLAG_RUNEWORD:
        r['stats_runeword'] = _read_stats(br, data)
    return r, 2 + (br.pos + 7) // 8


def decode_blob(blob, data):
    """Item + socketed children of a stash entry: (record, [child records])."""
    rec, n = decode_item(blob, data)
    kids = []
    p = n
    for _ in range(rec.get('socketed_count', 0) if rec['flags'] & FLAG_SOCKETED else 0):
        k, m = decode_item(blob[p:], data)
        kids.append(k)
        p += m
    return rec, kids


# --------------------------------------------------------------------------------------------- stats from mods

def merge_stats(stats):
    """Same (stat, param) added together, then sorted as the game saves them."""
    acc = {}
    for s in stats:
        k = (s['id'], s.get('param'))
        acc[k] = acc.get(k, 0) + s['value']
    return [dict(id=k[0], param=k[1], value=v) for k, v in sorted(acc.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0))]


class Mod:
    """One mod (property, param, min, max) of an item and its rolled value."""

    def __init__(self, source, prop, param, lo, hi, data, label=''):
        self.source, self.prop, self.param = source, prop, param
        self.min, self.max = min(lo, hi), max(lo, hi)
        self.raw = (prop, param, lo, hi)
        self.label = label
        funcs = data.prop_funcs[prop]
        fixed_kind = (any(f in (15, 16, 17) for f in funcs) or data.prop_func[prop] in (11, 19)
                      or any(is_innate_damage(s, data) for s in data.props[prop] if s >= 0))
        self.variable = (self.min != self.max) and not fixed_kind
        self.value = self.max if self.variable else None

    def stats(self, data):
        prop, param, lo, hi = self.raw
        funcs = data.prop_funcs[prop]
        if any(f in (15, 16, 17) for f in funcs):
            # min / max / param given as such to each stat (no min-max swap: e.g. cold length + affix count)
            out = {}
            for sid, f in zip(data.props[prop], funcs):
                if sid < 0:
                    continue
                v = lo if f == 15 else hi if f == 16 else param if f == 17 else lo
                out[sid] = v
            for one, others in ((S.MIN_DAMAGE, (S.MIN_DAMAGE_2, S.THROW_MIN_DAMAGE)),
                                (S.MAX_DAMAGE, (S.MAX_DAMAGE_2, S.THROW_MAX_DAMAGE))):
                if one in out:
                    for o in others:
                        out.setdefault(o, out[one])
            return [dict(id=sid, param=param if data.isc[sid]['param'] else None, value=v) for sid, v in out.items()]
        if not self.variable:
            return mods_stats([(prop, param, lo, hi)], data, 'min')
        v = max(self.min, min(self.max, self.value))
        return mods_stats([(prop, param, v, v)], data, 'min')

    def stat_ids(self, data):
        return [s['id'] for s in mods_stats([self.raw], data, 'min')]


# --------------------------------------------------------------------------------------------- creator

class Creator:
    """Catalogs and legit generation of items, on top of mxl_data.Data."""

    def __init__(self, data):
        self.data = data
        self.t = Tables(data)
        data.key('x')                                      # builds data._bykey
        self.rw_list = [i for i, (n, runes, ok, ex) in enumerate(data.runewords)
                        if runes and self.t.rw_complete[i] and n in data._bykey]

    # ---- catalogs
    def base_name(self, code):
        b = self.data.base(code)
        return b.name

    def bases(self):
        """Base items that can be created (weapons, armor, misc with a name and an icon), sorted by name."""
        out = []
        for code, b in self.data.bases.items():
            if not code.strip() or not b.name or b.quest:
                continue
            out.append(b)
        return sorted(out, key=lambda b: (b.kind, b.name.lower()))

    def uniques(self):
        out = []
        for i, u in enumerate(self.data.uniques):
            if u['code'].strip() and u['code'] in self.data.bases and u['name'] in self.data._bykey:
                out.append((i, self.data.key(u['name']).replace('\n', ' '), u))
        return sorted(out, key=lambda x: x[1].lower())

    def set_items(self):
        out = []
        for i, u in enumerate(self.data.set_items):
            if u['code'].strip() and u['code'] in self.data.bases and u['name'] in self.data._bykey:
                out.append((i, self.data.key(u['name']).replace('\n', ' '), u))
        return sorted(out, key=lambda x: x[1].lower())

    def runewords(self):
        out = []
        for i in self.rw_list:
            n, runes, ok, ex = self.data.runewords[i]
            out.append((i, self.data.key(n), [self.data.item_index[r] for r in runes]))
        return sorted(out, key=lambda x: x[1].lower())

    def types_of(self, code):
        return self.data.type_ancestors(self.data.base(code).type) | self.data.type_ancestors(self.data.base(code).type2)

    def type_codes(self, code):
        return {self.data.itypes[t][0] for t in self.types_of(code)}

    def runeword_bases(self, rw):
        n, runes, ok, ex = self.data.runewords[rw]
        out = []
        for b in self.bases():
            if b.kind == 'misc':
                continue
            anc = self.types_of(b.code)
            if anc & set(ok) and not anc & set(ex) and self.max_sockets(b.code, MAX_ILVL) >= len(runes):
                out.append(b)
        return out

    def qlvl(self, code):
        return self.t.item.get(code, {}).get('qlvl', 1)

    def max_sockets(self, code, ilvl):
        b = self.data.base(code)
        if not b.max_sockets or b.type < 0 or b.type >= len(self.t.itype):
            return 0
        ms = self.t.itype[b.type]['maxsock']
        cap = ms[0] if ilvl <= 25 else ms[1] if ilvl <= 40 else ms[2]
        return min(b.max_sockets, cap)

    def image_count(self, code):
        b = self.data.base(code)
        return len(self.data.itype_gfx[b.type]) if 0 <= b.type < len(self.data.itype_gfx) else 0

    def can_have_quality(self, code, q):
        b = self.data.base(code)
        it = self.t.itype[b.type] if 0 <= b.type < len(self.t.itype) else {}
        if q in ('normal', 'superior'):
            return not it.get('magic_only') and b.kind in ('weapons', 'armor') or (q == 'normal')
        if q == 'magic':
            return not it.get('normal_only')
        if q == 'rare':
            return not it.get('normal_only') and bool(it.get('rare'))
        if q == 'crafted':
            return bool(self.craft_rows(code))
        return True

    def can_roll_affixes(self, code, q):
        """Whether the base can carry the affixes a magic (one or more) / rare (a prefix, a suffix, 3 in all) item
        needs, at the highest item level (quest items, relics... cannot: no affix spawns on them)."""
        cache = self.__dict__.setdefault('_roll_ok', {})
        key = (code, q)
        if key not in cache:
            rare = q == 'rare'
            pre = self.affix_choices('magicprefix', code, MAX_ILVL, rare=rare)
            suf = self.affix_choices('magicsuffix', code, MAX_ILVL, rare=rare)
            if rare:
                gp = {a['group'] for _, _, a in pre}
                gs = {a['group'] for _, _, a in suf}
                cache[key] = bool(gp) and bool(gs) and min(len(gp), 3) + min(len(gs), 3) >= 3
            else:
                cache[key] = bool(pre or suf)
        return cache[key]

    def _affix_ok(self, a, extra, anc, ilvl, rare=False):
        if not a['spawnable'] or a['level'] > ilvl or (a['maxlevel'] and ilvl > a['maxlevel']):
            return False
        if rare and not extra['rare']:
            return False
        return bool(a['itypes'] & anc) and not (a['etypes'] & anc)

    def affix_choices(self, kind, code, ilvl, rare=False):
        """[(save id, label, affix)] of magic prefixes / suffixes that can spawn on this base at this ilvl."""
        anc = self.types_of(code)
        out = []
        for i, a in enumerate(self.data.affixes[kind]):
            if self._affix_ok(a, self.t.affix_extra[kind][i], anc, ilvl, rare):
                out.append((i + 1, self.affix_label(kind, i + 1), a))
        return out

    def mods_text(self, mods, code=None):
        """Tooltip lines of mods [(prop, param, min, max)] as the game writes them, ranges merged:
        "+(31 to 40) to all Attributes", "+(180 to 200)% Enhanced Damage" (affix / recipe / corruption labels)."""
        d = self.data
        lo_d, hi_d = {}, {}
        for p, pa, a, b in mods:
            m = Mod('x', p, pa, a, b, d)
            try:
                if m.variable:
                    m.value = m.min
                    sa = m.stats(d)
                    m.value = m.max
                    sb = m.stats(d)
                else:
                    sa = sb = m.stats(d)
            except Exception:
                continue
            for src, dst in ((sa, lo_d), (sb, hi_d)):
                for x in src:
                    if x['id'] in (S.PREFIX_COUNT, S.SUFFIX_COUNT):
                        continue
                    k = (x['id'], x.get('param'))
                    dst[k] = dst.get(k, 0) + x['value']
        keys = sorted(set(lo_d) | set(hi_d), key=lambda k: (k[0], k[1] or 0))
        lo = [dict(id=k[0], param=k[1], value=lo_d.get(k, 0)) for k in keys]
        hi = [dict(id=k[0], param=k[1], value=hi_d.get(k, 0)) for k in keys]

        class _Ctx:
            pass
        ctx = _Ctx()
        ctx.data, ctx.lo, ctx.hi, ctx.mods = d, lo, hi, list(mods)
        b = d.base(code) if code and code in d.bases else None

        class _B:
            two_handed = bool(getattr(b, 'two_handed', False))
        ctx.base = _B()
        ctx.total = lambda sid: tuple(sum(x['value'] for x in st if x['id'] == sid) for st in (lo, hi))
        try:
            from mxl_catalog_tooltip import catalog_affixes
            lines = [str(t).strip() for line in catalog_affixes(ctx) for _c, t in line]
            return [t for t in lines if t]
        except Exception:
            pass
        try:                                   # fallback: the tooltip at the maximum
            from mxl_tooltip import stat_lines
            return [str(x[1]).strip() for x in stat_lines(hi, d, None) if str(x[1]).strip()]
        except Exception:
            return []

    def affix_label(self, kind, save_id):
        cache = self.__dict__.setdefault('_affix_labels', {})
        if (kind, save_id) not in cache:
            a = self.data.affixes[kind][save_id - 1]
            lines = self.mods_text([(m['prop'], m['param'], m['min'], m['max']) for m in a['mods']])
            cache[(kind, save_id)] = f"{'; '.join(lines) or T('cr.hidden', '(hidden)')}   [L{a['level']}, #{save_id}]"
        return cache[(kind, save_id)]

    def auto_choices(self, code, ilvl):
        b = self.data.base(code)
        if not b.auto_group:
            return []
        anc = self.types_of(code)
        out = []
        for i, a in enumerate(self.data.affixes['automagic']):
            if a['group'] == b.auto_group and self._affix_ok(a, self.t.affix_extra['automagic'][i], anc, ilvl):
                out.append((i + 1, self.affix_label('automagic', i + 1), a))
        return out

    def superior_choices(self, code):
        k = self.data.base(code).kind
        return [i for i, r in enumerate(self.t.superior) if (k == 'weapons' and r['weapon']) or (k == 'armor' and r['armor'])]

    def rare_name_choices(self, code):
        anc = self.types_of(code)
        res = {}
        for kind in ('rareprefix', 'raresuffix'):
            # the item stores a rare prefix as (its index + number of rare suffixes) in 8 bits: Sigma's last
            # rare prefixes (index + 183 > 255) cannot be saved, so the game cannot give them
            lim = 255 - (len(self.data.rare_suffix) if kind == 'rareprefix' else 0)
            res[kind] = [(i, self.data.key(n['key'])) for i, n in enumerate(self.t.rare_names[kind])
                         if i <= lim and (not n['itypes'] or n['itypes'] & anc) and not (n['etypes'] & anc)]
        return res

    # ---- crafted items (Median XL Sigma: rare / crafted gear + shrine vessel (+ oil), amulets + alchemy oils...)
    def _craft_table(self):
        """Cube recipes whose output is a crafted item: [dict(row, itype, item, inputs, mods)], one per distinct
        (base input, other inputs, mods); a rare or a crafted item as input gives the same recipe."""
        if not hasattr(self, '_crafts'):
            d = self.data
            out, seen = [], set()
            for i, r in enumerate(_rows(d, 'cubemain')):
                if len(r) < 0x4C + 0x54 or not r[0] or r[0x4C + 6] != QUALITY_ID['crafted']:
                    continue
                n_in = r[16]
                ins = [struct.unpack_from('<HHHBB', r, 0x14 + 8 * k) for k in range(min(n_in, 7))]
                ins = [x for x in ins if x[0]]
                if not ins:
                    continue
                fl, item, _iid, _q, _qty = ins[0]
                itype = item if fl & 2 else None
                code = d.item_index[item] if not fl & 2 and 0 <= item < len(d.item_index) else None
                others = []
                for fl2, it2, _a, _b, qty in ins[1:]:
                    if fl2 & 2:
                        others.append(f'type:{it2}')
                    elif 0 <= it2 < len(d.item_index):
                        others.append((d.item_index[it2], max(1, qty)))
                mods = []
                for j in range(5):
                    p, pa, lo, hi, _ch = struct.unpack_from('<iHhhH', r, 0x4C + 24 + 12 * j)
                    if 0 <= p < len(d.props):
                        mods.append((p, pa, lo, hi))
                key = (itype, code, tuple(map(str, others)), tuple(mods))
                if not mods or key in seen:
                    continue
                seen.add(key)
                out.append(dict(row=i, itype=itype, item=code, inputs=others, mods=mods))
            self._crafts = out
            self._craft_by_row = {c['row']: c for c in out}
        return self._crafts

    def craft_recipe(self, row):
        self._craft_table()
        return self._craft_by_row.get(row)

    def craft_label(self, rc):
        d = self.data
        names = []
        for x in rc['inputs']:
            if isinstance(x, tuple):
                code, qty = x
                nm = self.display_name(code).split(' — ')[0] if code in d.bases else code
                names.append(f'{qty} × {nm}' if qty > 1 else nm)
            else:
                t = int(x.split(':')[1])
                names.append(d.itypes[t][0] if 0 <= t < len(d.itypes) else x)
        parts = self.mods_text(rc['mods'])
        return ' + '.join(names or ['(nothing)']) + (': ' + '; '.join(parts) if parts else '')

    def craft_rows(self, code):
        """Rows of the crafting recipes that take this base (cached)."""
        if not code or code not in self.data.bases:
            return []
        cache = self.__dict__.setdefault('_craft_rows', {})
        if code not in cache:
            anc = self.types_of(code)
            cache[code] = [rc['row'] for rc in self._craft_table()
                           if (rc['itype'] is not None and rc['itype'] in anc) or rc['item'] == code]
        return cache[code]

    def craft_choices(self, code):
        """[(recipe row, label)] of the crafting recipes that take this base."""
        labels = self.__dict__.setdefault('_craft_labels', {})
        out = []
        for row in self.craft_rows(code):
            if row not in labels:
                labels[row] = self.craft_label(self.craft_recipe(row))
            out.append((row, labels[row]))
        return out

    # ---- corruption (Median XL Sigma: Sacred / Angelic gear and jewelry + Corrupted Crystal in the cube; a
    # "not revealed yet" corruption is cubed with an Oil of Craft, its hidden roll 1-10000 picks the outcome)
    def _cube_input(self, r, k):
        fl, item, _iid, q, qty = struct.unpack_from('<HHHBB', r, 0x14 + 8 * k)
        return fl, item, q

    def _cube_match(self, r, code, quality):
        """True if the cube row's first input is this base (type or item code, quality)."""
        d = self.data
        fl, item, q = self._cube_input(r, 0)
        if q and QUALITY_ID.get(quality) != q:
            return False
        if fl & 2:
            return item in self.types_of(code)
        return 0 <= item < len(d.item_index) and d.item_index[item] == code

    def _cube_mods(self, r):
        out = []
        for j in range(5):
            p, pa, lo, hi, _ch = struct.unpack_from('<iHhhH', r, 0x4C + 24 + 12 * j)
            if 0 <= p < len(self.data.props):
                out.append((p, pa, lo, hi))
        return out

    def _corrupt_tables(self):
        """(step rows, reveal rows): cube rows whose 2nd input is the Corrupted Crystal / the Oil of Craft alone."""
        if not hasattr(self, '_corrupt_t'):
            d = self.data
            step, reveal = [], []
            for i, r in enumerate(_rows(d, 'cubemain')):
                if len(r) < 0x4C + 0x54 or not r[0] or r[16] != 2:
                    continue
                fl2, it2, _q2 = self._cube_input(r, 1)
                if fl2 & 2 or not 0 <= it2 < len(d.item_index):
                    continue
                c2 = d.item_index[it2]
                if c2 == 'scum':
                    step.append(i)
                elif c2 == '7o[ ':
                    reveal.append(i)
            self._corrupt_t = (step, reveal)
        return self._corrupt_t

    CORRUPT_CONTROL = (363, 7, 413, 410, 412, 5)     # props: hidden flags / roll / "Corrupted" (added on their own)

    def corrupt_options(self, code, quality, stats=None):
        """[(row, kind, probability, label)] of the corruptions the game can give this item; kind: 'step' (the
        crystal's outcome: no effect, Required Level...), 'pending' (not revealed yet), 'reveal' (an Oil of Craft
        outcome; probability within the reveal). stats: {stat id: value} of the item (conditions of the recipes)."""
        if not code or code not in self.data.bases or quality in ('tempered', 'runeword'):
            return []
        stats = stats or {}
        key = (code, quality, tuple(sorted((k, v) for k, v in stats.items() if k in (372, 92, 494, 496, 497))))
        cache = self.__dict__.setdefault('_corrupt_opts', {})
        if key in cache:
            return cache[key]
        rows = _rows(self.data, 'cubemain')
        step, reveal = self._corrupt_tables()
        out, left, pending = [], 1.0, False
        for i in step:
            r = rows[i]
            if not self._cube_match(r, code, quality):
                continue
            op = r[4]
            param, value = struct.unpack_from('<ii', r, 8)
            mods = self._cube_mods(r)
            if op == 37:                                     # random: value % of what is left
                pct = max(0, min(100, value)) / 100.0
                if pct <= 0:
                    continue
                pr = left * pct
                left -= pr
                kind = 'pending' if any(m[0] == 410 for m in mods) else 'step'
                pending = pending or kind == 'pending'
                out.append((i, kind, pr, mods))
                if left <= 1e-9:
                    break
                continue
            if op == 18:
                cond = stats.get(param, 0) == value
            elif op == 15:
                cond = stats.get(param, 0) > value
            elif op == 0:
                cond = True
            else:
                cond = False
            if cond and not mods:
                break                                        # the game refuses this item
        if pending:
            last = 0
            for i in reveal:
                r = rows[i]
                if r[4] != 16 or not self._cube_match(r, code, quality):
                    continue
                param, value = struct.unpack_from('<ii', r, 8)
                if param != 498 or value <= last or value > 10000:
                    continue
                out.append((i, 'reveal', (value - last) / 10000.0, self._cube_mods(r)))
                last = value
        res = []
        for i, kind, pr, mods in out:
            shown = [m for m in mods if m[0] not in self.CORRUPT_CONTROL]
            txt = '; '.join(self.mods_text(shown)) if shown else ''
            pct = f'{pr * 100:.4g}%'
            if kind == 'pending':
                lab = T('cr.cor.pending', 'Corrupted, not revealed yet (cube it with an Oil of Craft) ({p})', p=pct)
            elif kind == 'reveal':
                lab = T('cr.cor.reveal', 'Corrupted: {text} ({p} of the reveals)', text=txt or '?', p=pct)
            elif any(m[0] == 5 for m in mods):
                lab = (T('cr.cor.step', 'Corrupted: {text} ({p})', text=txt, p=pct) if txt
                       else T('cr.cor.plain', 'Corrupted, no effect ({p})', p=pct))
            else:
                lab = T('cr.cor.failed', 'Not corrupted: {text} ({p})', text=txt or '?', p=pct)
            res.append((i, kind, pr, lab))
        cache[key] = res
        return res

    def corrupt_choices(self, spec):
        """corrupt_options of the spec's item (conditions checked on its stats without corruption)."""
        q = spec.get('quality')
        if q == 'crafted' or q == 'rare' or q in QUALITY_ID:
            qq = q
        else:
            return []
        stats = {}
        for m in self.mods(dict(spec, corrupt=None)):
            try:
                for x in m.stats(self.data):
                    stats[x['id']] = stats.get(x['id'], 0) + x['value']
            except Exception:
                pass
        for x in spec.get('extra') or []:
            stats[x['id']] = stats.get(x['id'], 0) + x['value']
        return self.corrupt_options(spec.get('code'), qq, stats)

    def corrupt_mods(self, spec):
        """[(prop, param, min, max)] the corruption of the spec adds (the "Corrupted" mark included)."""
        row = spec.get('corrupt')
        if row is None:
            return []
        rows = _rows(self.data, 'cubemain')
        if not 0 <= row < len(rows):
            return []
        mods = self._cube_mods(rows[row])
        mark = any(m[0] in (5, 412) for m in mods) or row in self._corrupt_tables()[1]
        out = [(5, 0, 1, 1)] if mark else []
        if any(m[0] == 410 for m in mods):                  # not revealed: the flag and the hidden roll
            roll = spec.get('corrupt_roll') or 1
            out += [(413, 0, 1, 1), (410, 1, roll, roll)]
        out += [m for m in mods if m[0] not in self.CORRUPT_CONTROL]
        return out

    def _corrupt_stats(self, spec):
        d = self.data
        out = {}
        for p, pa, lo, hi in self.corrupt_mods(spec):
            for x in Mod('c', p, pa, lo, hi, d).stats(d):
                k = (x['id'], x.get('param'))
                out[k] = out.get(k, 0) + x['value']
        return out

    def _check_rare_affixes(self, code, ilvl, pre, suf):
        d = self.data
        groups = set()
        for kind, lst in (('magicprefix', pre), ('magicsuffix', suf)):
            ok = {i for i, _, _ in self.affix_choices(kind, code, ilvl, rare=True)}
            for sid in lst:
                if sid not in ok:
                    raise CreateError(T('cr.err.affix_level', 'an affix cannot spawn on this item at this item level'))
                g = d.affixes[kind][sid - 1]['group']
                if g and (kind, g) in groups:
                    raise CreateError(T('cr.err.same_group', 'two affixes of the same group'))
                groups.add((kind, g))

    def _pick_affixes(self, kind, code, ilvl, n, taken=()):
        """n random rare affixes of one kind (weighted by the game's frequency, one per group)."""
        d = self.data
        groups = {d.affixes[kind][x - 1]['group'] for x in taken}
        out = list(taken)
        pool = [(i, a) for i, _, a in self.affix_choices(kind, code, ilvl, rare=True)]
        while len(out) < n:
            cand = [(i, a) for i, a in pool if i not in out and (not a['group'] or a['group'] not in groups)]
            if not cand:
                break
            w = [max(1, self.t.affix_extra[kind][i - 1]['freq']) for i, _ in cand]
            i, a = random.choices(cand, weights=w)[0]
            out.append(i)
            groups.add(a['group'])
        return out

    def reroll_crafted(self, spec):
        """A new random crafted item on the same base and recipe: name, 4 affixes, every value (a roll whose
        values the save cannot hold, e.g. Enhanced Damage over its limit, is rolled again)."""
        for _ in range(40):
            self._reroll_crafted(spec)
            try:
                self.build(spec)
                return spec
            except CreateError as e:
                if not isinstance(e, StoreError):
                    return spec
        return spec

    def _reroll_crafted(self, spec):
        code, ilvl = spec['code'], spec['ilvl']
        names = self.rare_name_choices(code)
        spec['rare_names'] = (random.choice(names['rareprefix'])[0] if names['rareprefix'] else 0,
                              random.choice(names['raresuffix'])[0] if names['raresuffix'] else 0)
        n_pre = random.randint(max(1, CRAFTED_AFFIXES - 3), min(3, CRAFTED_AFFIXES - 1))
        spec['rare_prefixes'] = self._pick_affixes('magicprefix', code, ilvl, n_pre)
        spec['rare_suffixes'] = self._pick_affixes('magicsuffix', code, ilvl, CRAFTED_AFFIXES - len(spec['rare_prefixes']))
        if len(spec['rare_suffixes']) < CRAFTED_AFFIXES - len(spec['rare_prefixes']):   # few suffixes: more prefixes
            spec['rare_prefixes'] = self._pick_affixes('magicprefix', code, ilvl,
                                                       CRAFTED_AFFIXES - len(spec['rare_suffixes']), spec['rare_prefixes'])
        spec['rolls'] = {}
        self.roll(spec, 'random')
        return spec

    def _visible(self, st):
        """True if the stat shows a tooltip line on its own."""
        try:
            from mxl_tooltip import stat_lines
            return any(str(x[1] if isinstance(x, (tuple, list)) else x).strip()
                       for x in stat_lines([st], self.data, None))
        except Exception:
            return True

    def _infer_affixes(self, it, code, ilvl, leftover):
        """Rare affixes that explain the stats of an item saved without its affix ids (e.g. an imported copy):
        greedy, the affix whose shown stats are all on the item within its range, most stats first; the
        "Prefixes: n / Suffixes: n" counters of the item give how many of each."""
        d = self.data
        have = {}
        for x in leftover:
            k = (x['id'], x.get('param'))
            have[k] = have.get(k, 0) + x['value']
        want = {'magicprefix': have.get((S.PREFIX_COUNT, None)), 'magicsuffix': have.get((S.SUFFIX_COUNT, None))}
        if want['magicprefix'] is None and want['magicsuffix'] is None:
            want = {'magicprefix': 3, 'magicsuffix': 3}
        want = {k: min(3, v or 0) for k, v in want.items()}
        cache = {}

        def ranges(kind, a):
            key = (kind, id(a))
            if key not in cache:
                try:
                    mods = [(m['prop'], m['param'], m['min'], m['max']) for m in a['mods']]
                    lo = {(x['id'], x.get('param')): x['value'] for x in mods_stats(mods, d, 'min')}
                    hi = {(x['id'], x.get('param')): x['value'] for x in mods_stats(mods, d, 'max')}
                    vis = {k for k in set(lo) | set(hi) if k[0] not in (S.PREFIX_COUNT, S.SUFFIX_COUNT)
                           and self._visible(dict(id=k[0], param=k[1], value=hi.get(k, lo.get(k, 1)) or 1))}
                    cache[key] = (lo, hi, vis)
                except Exception:
                    cache[key] = None
            return cache[key]

        chosen = {'magicprefix': [], 'magicsuffix': []}
        groups = set()
        while sum(len(v) for v in chosen.values()) < CRAFTED_AFFIXES:
            best = None
            for kind in ('magicprefix', 'magicsuffix'):
                if len(chosen[kind]) >= want[kind]:
                    continue
                for i, _, a in self.affix_choices(kind, code, ilvl, rare=True):
                    if a['group'] and (kind, a['group']) in groups:
                        continue
                    r = ranges(kind, a)
                    if not r or not r[2]:
                        continue
                    lo, hi, vis = r
                    ok, score = True, 0.0
                    for k in vis:
                        v = have.get(k)
                        x, y = sorted((lo.get(k, 0), hi.get(k, 0)))
                        if v is None or (v < x if x >= 0 else v > y) or (v > 0 > x):
                            ok = False
                            break
                        score += 3 + (1 if x <= v <= y else 0)
                    if ok and (best is None or score > best[0]):
                        best = (score, kind, i, a, lo, hi, vis)
            if best is None:
                break
            _sc, kind, i, a, lo, hi, vis = best
            chosen[kind].append(i)
            groups.add((kind, a['group']))
            for k in vis:
                x, y = sorted((lo.get(k, 0), hi.get(k, 0)))
                take = max(x, min(y, have[k]))
                have[k] -= take
                if not have[k]:
                    del have[k]
        return chosen['magicprefix'], chosen['magicsuffix']

    def sockets_fillers(self):
        """Gems and runes (simple socketables with gem bonuses), as (code, name)."""
        out = []
        for code in self.data.gems:
            b = self.data.base(code)
            if b.name and self.t.item.get(code, {}).get('compact'):
                out.append((code, b.name))
        return sorted(out, key=lambda x: x[1].lower())

    # ---- building
    def new_spec(self, quality, code=None, row=None):
        """Default spec. quality: normal, superior, magic, rare, set, unique, runeword."""
        spec = dict(quality=quality, code=code, row=row, ilvl=None, ethereal=False, sockets=0,
                    socketed=[], image=None, auto=None, superior=None, prefix=None, suffix=None,
                    rare_names=None, rare_prefixes=[], rare_suffixes=[], quantity=None, defense=None,
                    rolls={}, craft=None, extra=[], corrupt=None, corrupt_roll=None, max_sockets=False)
        if quality == 'unique':
            spec['code'] = self.data.uniques[row]['code']
        elif quality == 'set':
            spec['code'] = self.data.set_items[row]['code']
        spec['ilvl'] = self.min_ilvl(spec)
        return spec

    def min_ilvl(self, spec):
        lv = max(1, self.qlvl(spec['code']) if spec['code'] else 1)
        if spec['quality'] == 'unique':
            lv = max(lv, self.data.uniques[spec['row']]['level'])
        elif spec['quality'] == 'set':
            lv = max(lv, self.data.set_items[spec['row']]['level'])
        return min(lv, MAX_ILVL)

    def mods(self, spec):
        """[Mod] of the spec, in display order; rolled values taken from spec['rolls'] (key -> value)."""
        d = self.data
        out = []
        q = spec['quality']
        if spec.get('auto'):
            for k, m in enumerate(d.affixes['automagic'][spec['auto'] - 1]['mods']):
                out.append(Mod(f'auto:{spec["auto"]}:{k}', m['prop'], m['param'], m['min'], m['max'], d, 'Innate'))
        if q in ('superior', 'runeword') and spec.get('superior') is not None:
            for k, m in enumerate(d.quality_mods[spec['superior']]):
                out.append(Mod(f'sup:{k}', m['prop'], m['param'], m['min'], m['max'], d, 'Superior'))
        if q == 'magic':
            for kind, key in (('magicprefix', 'prefix'), ('magicsuffix', 'suffix')):
                if spec.get(key):
                    for k, m in enumerate(d.affixes[kind][spec[key] - 1]['mods']):
                        out.append(Mod(f'{key}:{spec[key]}:{k}', m['prop'], m['param'], m['min'], m['max'], d,
                                       'Prefix' if key == 'prefix' else 'Suffix'))
        if q in ('rare', 'crafted'):
            for kind, key in (('magicprefix', 'rare_prefixes'), ('magicsuffix', 'rare_suffixes')):
                for n, sid in enumerate(spec.get(key) or []):
                    for k, m in enumerate(d.affixes[kind][sid - 1]['mods']):
                        out.append(Mod(f'{key}:{n}:{sid}:{k}', m['prop'], m['param'], m['min'], m['max'], d,
                                       'Prefix' if kind == 'magicprefix' else 'Suffix'))
        if q in ('unique', 'set'):
            row = (d.uniques if q == 'unique' else d.set_items)[spec['row']]
            for k, m in enumerate(row['mods']):
                out.append(Mod(f'{q}:{spec["row"]}:{k}', m['prop'], m['param'], m['min'], m['max'], d))
        if q == 'crafted' and spec.get('craft') is not None:
            rc = self.craft_recipe(spec['craft'])
            if rc:
                for k, (p, pa, lo, hi) in enumerate(rc['mods']):
                    out.append(Mod(f'craft:{spec["craft"]}:{k}', p, pa, lo, hi, d, 'Crafting'))
        if q == 'runeword':
            for k, m in enumerate(d.runeword_mods[spec['row']]):
                out.append(Mod(f'rw:{spec["row"]}:{k}', m['prop'], m['param'], m['min'], m['max'], d, 'Runeword'))
        for k, (p, pa, lo, hi) in enumerate(self.corrupt_mods(spec)):
            m = Mod(f'corrupt:{spec["corrupt"]}:{k}', p, pa, lo, hi, d, 'Corruption')
            m.variable, m.value = False, None                 # fixed values (the hidden roll included)
            out.append(m)
        self._fit_storage(out)
        for m in out:
            if m.variable and m.source in spec['rolls']:
                m.value = spec['rolls'][m.source]
        return out

    def _fit_storage(self, mods):
        """Narrows mod ranges to what the item can store: a stat is saved in a fixed number of bits
        (ItemStatCost save bits / add), and a value outside cannot exist on a game item (Sigma rows that ask more:
        Alnair / Thelema "to Summon Attack Rating" up to 200 / 180 of 155, Spire of Kehjan 250 Damage Reflected of
        155, a Halberd suffix +33 to a skill of 30, Ring of Woe's corruption roll 1 + 10000 of 10000). Mods adding
        to the same stat share the room (the runeword's own stat list is separate)."""
        d = self.data
        info, fixed = [], {}
        for m in mods:
            prop, param, lo, hi = m.raw
            if any(f in (15, 16, 17) for f in d.prop_funcs[prop]):
                info.append(None)                            # given as such: a fixed share of the room
                try:
                    for x in m.stats(d):
                        key = (m.source.startswith('rw:'), (x['id'], x.get('param')))
                        fixed[key] = fixed.get(key, 0) + x['value']
                except Exception:
                    pass
                continue
            try:
                a = {(x['id'], x.get('param')): x['value'] for x in mods_stats([(prop, param, m.min, m.min)], d, 'min')}
                b = {(x['id'], x.get('param')): x['value'] for x in mods_stats([(prop, param, m.max, m.max)], d, 'min')}
            except Exception:
                info.append(None)
                continue
            # stats whose value is the mod's value itself (others: per-level, damage pairs...: left alone)
            info.append([k for k in a if k in b and a[k] == m.min and b[k] == m.max])
        groups = {}
        for m, ks in zip(mods, info):
            for k in ks or []:
                groups.setdefault((m.source.startswith('rw:'), k), []).append(m)
        for (_rw, k), ms in groups.items():
            ic = d.isc[k[0]]
            if not ic['bits']:
                continue
            lo_s, hi_s = -ic['add'], (1 << ic['bits']) - 1 - ic['add']
            f = fixed.get((_rw, k), 0)
            lo_s, hi_s = lo_s - f, hi_s - f
            tot_lo = sum(m.min for m in ms)
            tot_hi = sum(m.max for m in ms)
            if lo_s <= tot_lo and tot_hi <= hi_s:
                continue
            for m in ms:
                others_lo = tot_lo - m.min
                others_hi = tot_hi - m.max
                nmax = min(m.max, hi_s - others_lo)
                nmin = max(m.min, lo_s - others_hi)
                nmin = min(nmin, nmax)
                if (nmin, nmax) == (m.min, m.max):
                    continue
                tot_lo += nmin - m.min
                tot_hi += nmax - m.max
                m.min, m.max = nmin, nmax
                prop, param, lo, hi = m.raw
                m.raw = (prop, param, nmin, nmax) if lo <= hi else (prop, param, nmax, nmin)
                m.variable = m.variable and nmin != nmax
                if m.value is not None:
                    m.value = max(nmin, min(nmax, m.value))
                if not m.variable:
                    m.value = None

    def roll(self, spec, how='random'):
        """Fills spec['rolls'] for every variable mod: 'random', 'max', 'min' or 'mid'."""
        for m in self.mods(spec):
            if m.variable:
                spec['rolls'][m.source] = (random.randint(m.min, m.max) if how == 'random' else
                                           m.max if how == 'max' else m.min if how == 'min' else (m.min + m.max) // 2)

    def defense_range(self, spec):
        """(lo, hi) of the base defense, or (v, v) when fixed (Enhanced Defense: max + 1), before ethereal."""
        t = self.t.item.get(spec['code'], {})
        lo, hi = t.get('minac', 0), t.get('maxac', 0)
        if any(s['id'] == S.ENH_DEFENSE for m in self.mods(spec) for s in m.stats(self.data)):
            return hi + 1, hi + 1
        return lo, hi

    def innate_sockets(self, spec):
        """Sockets the item's affixes give ("sock" property, e.g. the Angelic innate +6), as the game makes them:
        capped at the base's maximum for the item level."""
        try:
            n = sum(s['value'] for m in self.mods(spec) for s in m.stats(self.data) if s['id'] == NUM_SOCKETS)
        except Exception:
            return 0
        return min(n, self.max_sockets(spec['code'], spec['ilvl'])) if n > 0 else 0

    def validate(self, spec):
        """Raises CreateError if the spec is not something the game could generate. An item opened with Edit item
        that breaks a rule (hacked / outside values, Sigma upgrades the rules don't know) is allowed: the problem is
        kept in spec['_warning'] instead."""
        spec.pop('_warning', None)
        if spec.get('code') in self.data.bases and spec.get('ilvl') and spec.get('quality') != 'runeword':
            n = self.innate_sockets(spec)
            if n > (spec.get('sockets') or 0):
                spec['sockets'] = n               # the game gives them: the item always has at least these
        if spec.get('_from_item') and spec.get('code') in self.data.bases:
            try:
                self._validate(spec)
            except CreateError as e:
                spec['_warning'] = str(e)
            return
        self._validate(spec)

    def _validate(self, spec):
        d, q, code = self.data, spec['quality'], spec['code']
        if not code or code not in d.bases:
            raise CreateError(T('cr.err.base', 'choose a base item'))
        b = d.base(code)
        ilvl = spec['ilvl']
        if not self.min_ilvl(spec) <= ilvl <= MAX_ILVL:
            raise CreateError(T('cr.err.ilvl', 'item level must be {lo} to {hi}', lo=self.min_ilvl(spec), hi=MAX_ILVL))
        if spec['ethereal'] and (q == 'set' or b.kind not in ('weapons', 'armor')):
            raise CreateError(T('cr.err.eth', 'only non-set weapons and armor can be ethereal'))
        qq = 'normal' if q == 'runeword' else q
        if qq in ('normal', 'superior', 'magic', 'rare') and not self.can_have_quality(code, qq):
            raise CreateError(T('cr.err.quality', '{base} cannot be {quality}', base=b.name, quality=T(f'cr.quality.{qq}', qq)))
        ms = self.max_sockets(code, ilvl)
        if spec['sockets'] > ms:
            raise CreateError(T('cr.err.max_sockets', '{base} can have at most {n} socket(s) at item level {ilvl}', base=b.name, n=ms, ilvl=ilvl))
        if len(spec['socketed']) > spec['sockets']:
            raise CreateError(T('cr.err.too_many_socketed', 'more socketed items than sockets'))
        autos = [a for a, _, _ in self.auto_choices(code, ilvl)]
        # Median XL Sigma: uniques and set items get their base's innate (automagic) affix too, like the game's
        # own (e.g. Azurewrath's Innate Cold Damage on its Crystal Sword)
        if autos and spec.get('auto') not in autos and not (spec.get('extra') and not spec.get('auto')):
            raise CreateError(T('cr.err.innate', 'choose the innate affix of this base'))
        if q == 'superior' and spec.get('superior') not in self.superior_choices(code):
            raise CreateError(T('cr.err.superior', 'choose the Superior bonus'))
        if q == 'magic':
            if not spec.get('prefix') and not spec.get('suffix'):
                raise CreateError(T('cr.err.magic', 'a magic item needs a prefix or a suffix'))
            for kind, key in (('magicprefix', 'prefix'), ('magicsuffix', 'suffix')):
                if spec.get(key) and spec[key] not in {i for i, _, _ in self.affix_choices(kind, code, ilvl)}:
                    raise CreateError(T('cr.err.magic_affix', '{affix} cannot spawn on this item at item level {ilvl}',
                                        affix=T(f'cr.{key}', key.capitalize()), ilvl=ilvl))
        if q == 'rare':
            pre, suf = spec.get('rare_prefixes') or [], spec.get('rare_suffixes') or []
            if not 1 <= len(pre) <= 3 or not 1 <= len(suf) <= 3 or len(pre) + len(suf) < 3:
                raise CreateError(T('cr.err.rare_count', 'a rare item has 1-3 prefixes and 1-3 suffixes (3 to 6 affixes)'))
            groups = set()
            for kind, lst in (('magicprefix', pre), ('magicsuffix', suf)):
                ok = {i for i, _, _ in self.affix_choices(kind, code, ilvl, rare=True)}
                for sid in lst:
                    if sid not in ok:
                        raise CreateError(T('cr.err.rare_affix', 'an affix cannot spawn on this rare at this item level'))
                    g = d.affixes[kind][sid - 1]['group']
                    if g and (kind, g) in groups:
                        raise CreateError(T('cr.err.same_group', 'two affixes of the same group'))
                    groups.add((kind, g))
            names = self.rare_name_choices(code)
            rn = spec.get('rare_names')
            if not rn or rn[0] not in {i for i, _ in names['rareprefix']} or rn[1] not in {i for i, _ in names['raresuffix']}:
                raise CreateError(T('cr.err.rare_name', 'choose the rare name'))
        if q == 'crafted':
            if spec.get('craft') not in self.craft_rows(code) and not (spec.get('extra') and spec.get('craft') is None):
                raise CreateError(T('cr.err.recipe', 'choose the shrine / crafting recipe'))
            pre, suf = spec.get('rare_prefixes') or [], spec.get('rare_suffixes') or []
            if spec.get('extra'):
                # an edited item whose affixes could not all be found: the rest of its stats are kept as they are
                if len(pre) > 3 or len(suf) > 3 or len(pre) + len(suf) > CRAFTED_AFFIXES:
                    raise CreateError(T('cr.err.crafted_max', 'a crafted item has at most {n} affixes (3 prefixes, 3 suffixes)', n=CRAFTED_AFFIXES))
            elif not 1 <= len(pre) <= 3 or not 1 <= len(suf) <= 3 or len(pre) + len(suf) != CRAFTED_AFFIXES:
                raise CreateError(T('cr.err.crafted_count', 'a crafted item has {n} affixes: 1-3 prefixes and 1-3 suffixes', n=CRAFTED_AFFIXES))
            self._check_rare_affixes(code, ilvl, pre, suf)
            names = self.rare_name_choices(code)
            rn = spec.get('rare_names')
            if not rn or rn[0] not in {i for i, _ in names['rareprefix']} or rn[1] not in {i for i, _ in names['raresuffix']}:
                raise CreateError(T('cr.err.name', 'choose the name'))
        if q == 'runeword':
            n, runes, ok, ex = d.runewords[spec['row']]
            anc = self.types_of(code)
            if not (anc & set(ok)) or anc & set(ex):
                raise CreateError(T('cr.err.rw_base', 'this runeword cannot be made in this base'))
            if spec['sockets'] != len(runes):
                raise CreateError(T('cr.err.rw_sockets', 'the base needs exactly {n} sockets', n=len(runes)))
            if spec.get('superior') is not None and spec['superior'] not in self.superior_choices(code):
                raise CreateError(T('cr.err.bad_superior', 'invalid Superior bonus'))
        if b.stack_max is not None:
            qn = spec.get('quantity') or 1
            if not 1 <= qn <= min(b.stack_max, 511):
                raise CreateError(T('cr.err.quantity', 'quantity 1 to {n}', n=b.stack_max))
        n_img = self.image_count(code)
        if spec.get('image') is not None and not 0 <= spec['image'] < max(n_img, 1):
            raise CreateError(T('cr.err.picture', 'invalid picture'))
        if spec.get('corrupt') is not None and spec['corrupt'] not in {r for r, *_ in self.corrupt_choices(spec)}:
            raise CreateError(T('cr.err.corrupt', 'this item cannot be corrupted that way'))
        for m in self.mods(spec):
            if m.variable and not m.min <= m.value <= m.max:
                raise CreateError(T('cr.err.range', 'value {v} out of range {lo}-{hi}', v=m.value, lo=m.min, hi=m.max))

    def build(self, spec):
        """(blob, record) of a validated spec: bytes of the item and its socketed items."""
        self.validate(spec)
        d, q, code = self.data, spec['quality'], spec['code']
        b = d.base(code)
        t = self.t.item.get(code, {})
        simple = bool(t.get('compact')) and q == 'normal' and b.kind == 'misc'
        flags = FLAG_IDENTIFIED | FLAG_ALWAYS
        if simple:
            return encode_item(dict(flags=flags | FLAG_SIMPLE, code=code), d), None
        if spec['ethereal']:
            flags |= FLAG_ETHEREAL
        if spec['sockets'] or spec.get('_socket_flag'):
            flags |= FLAG_SOCKETED
        if q == 'runeword':
            flags |= FLAG_RUNEWORD
        rq = 'superior' if (q == 'runeword' and spec.get('superior') is not None) else ('normal' if q == 'runeword' else q)
        rec = dict(flags=flags, code=code, id=random.getrandbits(32), ilvl=spec['ilvl'], quality=rq,
                   socketed_count=len(spec['socketed']))
        n_img = self.image_count(code)
        if n_img:
            rec['image'] = spec['image'] if spec.get('image') is not None else random.randrange(n_img)
        if spec.get('auto'):
            rec['auto_affix'] = spec['auto']
        if rq == 'superior':
            rec['subquality'] = spec['superior']
        elif q == 'magic':
            rec['prefix'], rec['suffix'] = spec.get('prefix') or 0, spec.get('suffix') or 0
        elif q in ('set', 'unique'):
            rec['set_unique_id'] = spec['row']
        elif q == 'tempered':
            a, c = spec['rare_names']
            rec['rare_names'] = (a + len(d.rare_suffix), c)
        elif q in ('rare', 'crafted'):
            a, c = spec['rare_names']
            rec['rare_names'] = (a + len(d.rare_suffix), c)
            pre, suf = list(spec['rare_prefixes']), list(spec['rare_suffixes'])
            slots = []
            for k in range(3):
                slots += [pre[k] if k < len(pre) else None, suf[k] if k < len(suf) else None]
            rec['rare_affixes'] = slots
        if q == 'runeword':
            key = d.runewords[spec['row']][0]
            idx = d._bykey.get(key)
            if idx is None:
                raise CreateError(T('cr.err.rw_name', 'runeword name not found in the string tables'))
            rec['runeword_id'] = idx
        mods = self.mods(spec)
        own = [s for m in mods if m.source.split(':')[0] != 'rw' for s in m.stats(d)]
        rw = [s for m in mods if m.source.split(':')[0] == 'rw' for s in m.stats(d)]
        own += [dict(x) for x in spec.get('extra') or []]              # kept as they were on an edited item
        own = [s for s in merge_stats(own) if d.isc[s['id']]['bits']]
        if '_keep_zero' in spec:              # edited item: a stat its mods give but the item had not cancels out
            own = [s for s in own if s['value'] or (s['id'], s.get('param')) in spec['_keep_zero']]
        rec['stats'] = own
        if q == 'runeword':
            rw += [dict(x) for x in spec.get('extra_rw') or []]
            rec['stats_runeword'] = [s for s in merge_stats(rw) if d.isc[s['id']]['bits']]
            if '_keep_zero_rw' in spec:
                rec['stats_runeword'] = [s for s in rec['stats_runeword']
                                         if s['value'] or (s['id'], s.get('param')) in spec['_keep_zero_rw']]
        if body_kind(d, code) == 'armor':
            lo, hi = self.defense_range(spec)
            v = spec.get('defense')
            raw = spec.get('_defense_raw')
            if raw and v == raw[0]:
                v = raw[1]                                        # edited item, defense untouched: as it was
            else:
                v = hi if v is None else max(lo, min(hi, v))
                if spec['ethereal']:
                    v = v * 3 // 2
            rec['defense'] = v
        if body_kind(d, code) in ('armor', 'weapons'):
            cm, cd = d.isc[S.MAX_DURABILITY], d.isc[S.DURABILITY]
            dur = 0 if t.get('nodur') else t.get('dur', 0)
            if dur:
                if q == 'set':
                    dur = min(dur * 2, 255)
                elif q == 'unique':
                    dur = min(dur * 3, 255)
                if spec['ethereal']:
                    dur = dur // 2 + 1
            rec['max_durability'] = dur + cm['add']
            rec['durability'] = dur + cd['add']
        if b.stack_max is not None:
            rec['quantity'] = spec.get('quantity') or 1
        if spec['sockets'] or spec.get('_socket_flag'):
            rec['sockets'] = spec['sockets']
        children = []
        if q == 'runeword':
            runes = [d.item_index[r] for r in d.runewords[spec['row']][1]]
            fill = runes
        else:
            fill = spec['socketed']
        for c in fill:
            children.append(dict(flags=FLAG_IDENTIFIED | FLAG_ALWAYS | FLAG_SIMPLE, code=c))
        rec['socketed_count'] = len(children)
        return build_blob(rec, children, d), rec

    # ---- editing an existing item
    def spec_from_item(self, it):
        """Spec of an item read by mxl_format (quality, base, affixes, rolled values), to edit it in the creator.
        Values that several mods add together cannot be split back: those mods get their maximum. A corrupted
        item: the corruption outcome that explains its stats best is found and taken out of them first."""
        have = {}
        for x in it.get('stats') or []:
            k = (x['id'], x.get('param'))
            have[k] = have.get(k, 0) + x['value']
        if not any(k[0] in (496, 497, 498) for k in have):
            spec = self._spec_from_item(it)
            self._mark_max_sockets(spec)
            return spec
        plain = self._spec_from_item(it)

        def score(sp):
            ex = [x for x in sp.get('extra') or [] if x['value']]
            return (len([x for x in ex if self._visible(x)]), len(ex))
        best = (score(plain), 0, plain)
        plain_ex = {}
        for x in plain.get('extra') or []:
            k = (x['id'], x.get('param'))
            plain_ex[k] = plain_ex.get(k, 0) + x['value']
        q = plain['quality']
        for n, (row, kind, _p, _l) in enumerate(self.corrupt_options(plain['code'], q)):
            roll = have.get((498, None))
            if kind == 'pending' and roll is None:
                continue
            cs = self._corrupt_stats(dict(corrupt=row, corrupt_roll=roll))
            if not cs or any(k not in have for k in cs):
                continue
            if any(v > 0 and plain_ex.get(k, 0) < v or v < 0 and plain_ex.get(k, 0) > v for k, v in cs.items()):
                continue                       # the outcome must be what the item has beyond its own mods
            stats = []
            for k, v in have.items():
                v2 = v - cs.get(k, 0)
                if v2 or k not in cs:
                    stats.append(dict(id=k[0], param=k[1], value=v2))
            try:
                sp = self._spec_from_item(dict(it, stats=stats))
            except CreateError:
                continue
            sc = score(sp)
            if (sc, n + 1) < best[:2]:
                sp['corrupt'], sp['corrupt_roll'] = row, roll if kind == 'pending' else None
                best = (sc, n + 1, sp)
        spec = best[2]
        self._mark_max_sockets(spec)
        return spec

    def _mark_max_sockets(self, spec):
        ms = self.max_sockets(spec['code'], spec['ilvl']) if spec.get('code') in self.data.bases else 0
        spec['max_sockets'] = bool(ms) and spec.get('sockets') == ms and spec.get('quality') != 'runeword'

    def _spec_from_item(self, it):
        d = self.data
        q = it.get('quality')
        if it.get('runeword') is not None or 'stats_runeword' in it:
            row = runeword_row(it, d)
            if row is None:
                raise CreateError(T('cr.err.rw_unknown', 'unknown runeword'))
            spec = self.new_spec('runeword', it['code'], row)
            spec['superior'] = it.get('subquality') if q == 'superior' else None
        elif q in ('unique', 'set'):
            spec = self.new_spec(q, it['code'], it.get('set_unique_id'))
        elif q in ('normal', 'superior', 'magic', 'rare'):
            spec = self.new_spec(q, it['code'])
            if q == 'superior':
                spec['superior'] = it.get('subquality')
            if q == 'magic':
                spec['prefix'], spec['suffix'] = it.get('prefix') or None, it.get('suffix') or None
            if q == 'rare':
                a, b = it['rare_names']
                spec['rare_names'] = (a - len(d.rare_suffix), b)
                ra = it.get('rare_affixes') or []
                spec['rare_prefixes'] = [x for x in ra[0::2] if x]
                spec['rare_suffixes'] = [x for x in ra[1::2] if x]
        elif q == 'tempered':                 # Sigma quality 9: name only, every stat kept as it is
            spec = self.new_spec('tempered', it['code'])
            a, b = it.get('rare_names') or (len(d.rare_suffix), 0)
            spec['rare_names'] = (a - len(d.rare_suffix), b)
        elif q == 'crafted':
            spec = self.new_spec('crafted', it['code'])
            a, b = it.get('rare_names') or (len(d.rare_suffix), 0)
            spec['rare_names'] = (a - len(d.rare_suffix), b)
            ra = it.get('rare_affixes') or []
            spec['rare_prefixes'] = [x for x in ra[0::2] if x]
            spec['rare_suffixes'] = [x for x in ra[1::2] if x]
            spec['ilvl'] = it.get('ilvl') or spec['ilvl']
            spec['craft'] = self._find_craft(it, spec)
            if not spec['rare_prefixes'] and not spec['rare_suffixes']:
                left = list(it.get('stats') or [])
                if spec['craft'] is not None:
                    rc_ids = {(st['id'], st.get('param')) for m in self.mods(dict(spec, rare_prefixes=[], rare_suffixes=[]))
                              for st in m.stats(d)}
                    left = [x for x in left if (x['id'], x.get('param')) not in rc_ids]
                spec['rare_prefixes'], spec['rare_suffixes'] = self._infer_affixes(it, it['code'], spec['ilvl'], left)
        elif q is None:
            raise CreateError(T('cr.err.simple', 'simple items (runes, gems, keys...) have nothing to edit'))
        else:
            raise CreateError(T('cr.err.not_editable_q', '{quality} items cannot be edited here', quality=T(f'cr.quality.{q}', str(q))))
        spec['ilvl'] = it.get('ilvl') or spec['ilvl']
        spec['ethereal'] = bool(it.get('ethereal'))
        spec['sockets'] = it.get('sockets') or 0
        if spec['quality'] != 'runeword':
            spec['socketed'] = [s['code'] for s in it.get('socketed', []) if d.base(s['code']).kind == 'misc'
                                and self.t.item.get(s['code'], {}).get('compact')]
        spec['image'] = it.get('image')
        spec['auto'] = it.get('auto_affix')
        if 'quantity' in it:
            spec['quantity'] = it['quantity']
        if 'defense' in it:
            v = it['defense']
            spec['defense'] = v * 2 // 3 if spec['ethereal'] else v
            spec['_defense_raw'] = (spec['defense'], v)        # kept exactly while the slider isn't moved
        spec['_socket_flag'] = bool(it.get('has_sockets'))
        counts = {}
        mods = self.mods(spec)
        for m in mods:
            for sid in m.stat_ids(d):
                counts[sid] = counts.get(sid, 0) + 1
        have = {}
        for lst in ('stats', 'stats_runeword'):
            for s in it.get(lst) or []:
                have[(lst, s['id'])] = s['value']
        for m in mods:
            if not m.variable:
                continue
            lst = 'stats_runeword' if m.source.startswith('rw:') else 'stats'
            ids = m.stat_ids(d)
            v = next((have[(lst, sid)] for sid in ids if (lst, sid) in have and counts.get(sid) == 1), None)
            spec['rolls'][m.source] = v if v is not None and m.min <= v <= m.max else m.max
        used = set()
        for m in mods:                       # random skill / monster mods: the rolled value is the stat's param
            if m.variable and d.prop_func[m.prop] == 12:
                ids = set(m.stat_ids(d))
                for s in it.get('stats') or []:
                    p = s.get('param')
                    if s['id'] in ids and p is not None and m.min <= p <= m.max and (s['id'], p) not in used:
                        used.add((s['id'], p))
                        spec['rolls'][m.source] = p
                        break
        # what the item's mods do not give, or give outside their range (other upgrades, corruption, an
        # imported copy, a hacked value...): kept on the item exactly as it is (the user's rule: such items
        # are allowed; the status line warns about them)
        made = {}
        for m in self.mods(spec):
            if m.source.startswith('rw:'):
                continue                      # runeword mods go to the runeword list
            for x in m.stats(d):
                k = (x['id'], x.get('param'))
                made[k] = made.get(k, 0) + x['value']
        have = {}
        for x in it.get('stats') or []:
            k = (x['id'], x.get('param'))
            have[k] = have.get(k, 0) + x['value']
        extra = []
        for k in list(have) + [k for k in made if k not in have]:
            dv = have.get(k, 0) - made.get(k, 0)
            if dv or (k not in made and have.get(k) == 0):
                extra.append(dict(id=k[0], param=k[1], value=dv))
        # runeword list: same rule
        made_rw = {}
        for m in self.mods(spec):
            if m.source.startswith('rw:'):
                for x in m.stats(d):
                    k = (x['id'], x.get('param'))
                    made_rw[k] = made_rw.get(k, 0) + x['value']
        have_rw = {}
        for x in it.get('stats_runeword') or []:
            k = (x['id'], x.get('param'))
            have_rw[k] = have_rw.get(k, 0) + x['value']
        spec['extra_rw'] = [dict(id=k[0], param=k[1], value=have_rw.get(k, 0) - made_rw.get(k, 0))
                            for k in list(have_rw) + [k for k in made_rw if k not in have_rw]
                            if have_rw.get(k, 0) != made_rw.get(k, 0)]
        spec['_keep_zero_rw'] = {k for k, v in have_rw.items() if v == 0}
        spec['extra'] = extra
        spec['_keep_zero'] = {k for k, v in have.items() if v == 0}
        spec['_from_item'] = True
        return spec

    def _find_craft(self, it, spec):
        """Recipe of a crafted item: the one whose stats are on the item with values in its ranges, most of
        them (several shrine sizes share the same ranges: any of them gives the same item)."""
        d = self.data
        have = {}
        for x in it.get('stats') or []:
            have[(x['id'], x.get('param'))] = x['value']
        ids = {k[0] for k in have}
        best = None
        for row in self.craft_rows(it['code']):
            score, n = 0, 0
            for m in self.mods(dict(spec, craft=row, rare_prefixes=[], rare_suffixes=[], auto=None)):
                try:
                    lo = mods_stats([(m.prop, m.param, m.min, m.min)], d, 'min') if m.variable else m.stats(d)
                    hi = mods_stats([(m.prop, m.param, m.max, m.max)], d, 'min') if m.variable else m.stats(d)
                except Exception:
                    continue
                la = {(x['id'], x.get('param')): x['value'] for x in lo}
                lb = {(x['id'], x.get('param')): x['value'] for x in hi}
                for k in set(la) | set(lb):
                    n += 1
                    if k[0] not in ids:
                        score -= 10
                        continue
                    x, y = sorted((la.get(k, 0), lb.get(k, 0)))
                    v = have.get(k)
                    if d.prop_func[m.prop] == 12:
                        score += 2
                    elif v is not None and x <= v:
                        score += 3 if v <= y else 2
            if not n:
                continue
            known = bool(spec.get('rare_prefixes') or spec.get('rare_suffixes'))
            probs = len(self._crafted_problems(it, dict(spec, craft=row))) if known else 0
            key = (-probs, score, n)
            if best is None or key > best[0]:
                best = (key, row)
        if best is None or (best[0][0] < 0 or not known) and best[0][1] <= 0:
            ch = self.craft_rows(it['code'])
            return ch[0] if ch else None
        return best[1]

    def check_legit(self, it):
        """Problems [text] of an item read by mxl_format, checked with the collector's own range rules."""
        d = self.data
        out = []
        if it.get('quality') == 'crafted':
            return self._check_crafted(it)
        rand_skill = {x for p, f in enumerate(d.prop_func) if f == 12 for x in d.props[p] if x >= 0}
        pair_stats = {x for p, fs in enumerate(d.prop_funcs) if any(f in (15, 16, 17) for f in fs)
                      for x in d.props[p] if x >= 0}
        for lst in ('stats', 'stats_runeword'):
            for s in it.get(lst) or []:
                if lst == 'stats_runeword' or s['id'] in pair_stats:
                    continue                    # several mods add up / min-max pairs: checked by construction
                if s['id'] in (S.PREFIX_COUNT, S.SUFFIX_COUNT) or s['id'] in rand_skill or is_innate_damage(s['id'], d):
                    continue
                rng = stat_range(it, s['id'], d, lst, s.get('param'))
                if rng is not None and not rng[0] <= s['value'] <= rng[1]:
                    out.append(Problem(T('cr.prob.outside_list', '{where} stat {stat} = {value} outside {lo}-{hi}',
                                         where=T('cr.list.' + lst, lst), stat=s['id'], value=s['value'], lo=rng[0], hi=rng[1]), s['id']))
        return out

    def _check_crafted(self, it):
        """Crafted items: each stat within the sum of the ranges of its affixes and of the crafting recipe."""
        try:
            spec = self.spec_from_item(it)
        except CreateError as e:
            return [str(e)]
        return self._crafted_problems(it, spec)

    def _crafted_problems(self, it, spec):
        d = self.data
        rng = {}
        for m in self.mods(spec):
            if d.prop_func[m.prop] == 12 and m.max - m.min <= 4096:      # random skill / monster: the param
                for st in m.stats(d):
                    for p in range(m.min, m.max + 1):
                        k = (st['id'], p)
                        r = rng.get(k, (0, 0))
                        rng[k] = (r[0], r[1] + st['value'])
                continue
            try:
                if not m.variable:                                    # fixed (incl. skill on event: chance / level)
                    lo = hi = m.stats(d)
                else:
                    lo = mods_stats([(m.prop, m.param, m.min, m.min)], d, 'min')
                    hi = mods_stats([(m.prop, m.param, m.max, m.max)], d, 'min')
            except Exception:
                continue
            la = {(a['id'], a.get('param')): a['value'] for a in lo}
            lb = {(b['id'], b.get('param')): b['value'] for b in hi}
            for k in set(la) | set(lb):
                x, y = sorted((la.get(k, 0), lb.get(k, 0)))
                r = rng.get(k, (0, 0))
                rng[k] = (r[0] + x, r[1] + y)
        out = []
        for s in it.get('stats') or []:
            k = (s['id'], s.get('param'))
            if s['id'] in FOLLOWERS and not s['value'] and k not in rng:
                continue                      # saved with its group (e.g. cold length after cold damage)
            if k not in rng:
                out.append(Problem(T('cr.prob.no_source', 'stat {stat} = {value} comes from no affix or recipe', stat=s['id'], value=s['value']), s['id']))
            elif not rng[k][0] <= s['value'] <= rng[k][1]:
                out.append(Problem(T('cr.prob.outside', 'stat {stat} = {value} outside {lo}-{hi}', stat=s['id'], value=s['value'],
                                     lo=rng[k][0], hi=rng[k][1]), s['id']))
        return out

    # ---- Angelic items (Median XL Sigma: Library of Fate tier, not in the normal drop pool)
    def angelic_bases(self):
        """Weapons / armor of the Angelic tier (item type ancestors include 'aaaa'): Full Helm (Angelic) ...
        They are not spawnable as plain bases; they come as rares with their own Angelic-only affixes."""
        d = self.data
        tag = next((i for i, t in enumerate(d.itypes) if t[0] == 'aaaa'), None)
        if tag is None:
            return []
        out = [b for b in d.bases.values() if b.kind in ('weapons', 'armor') and b.name and tag in self.types_of(b.code)]
        return sorted(out, key=lambda b: (b.kind, b.name.lower()))

    def is_angelic(self, code):
        return any(b.code == code for b in self.angelic_bases())

    # ---- display names and mystic orbs
    def display_name(self, code):
        """Name as the game shows it: multi-line names are drawn bottom line on top, so "Mystic Orb\nKara's Trinket"
        reads "Kara's Trinket — Mystic Orb"."""
        b = self.data.base(code)
        raw = (b.name_raw or b.name or '').replace('\\n', '\n')
        lines = []
        for line in raw.split('\n'):
            while 'ÿc' in line:
                i = line.index('ÿc')
                line = line[:i] + line[i + 3:]
            line = line.strip()
            if line:
                lines.append(line)
        if not lines:
            return b.name or code
        if len(lines) == 1:
            return lines[0]
        return lines[-1] + ' — ' + ' / '.join(reversed(lines[:-1]))

    def _orb_table(self):
        """Median XL Sigma mysticorbs.bin: one 143-byte row per mystic orb, in the order of the 'myst' items of
        misc.txt. Row: name (32 bytes), max orbs of this kind per item (u8 @43), then 6 property blocks of 16 bytes
        from @45: u16 property, u16 -, i32 param, i32 min, i32 max (property 0xFFFF = none)."""
        if hasattr(self, '_orbs'):
            return self._orbs
        self._orbs = {}
        d = self.data
        try:
            with open(os.path.join(d.folder, 'mysticorbs.bin'), 'rb') as f:
                raw = f.read()
            n = struct.unpack_from('<I', raw, 0)[0]
            rs = (len(raw) - 4) // n if n else 0
            codes = [b.code for b in d.bases.values()
                     if b.kind == 'misc' and 0 <= b.type < len(d.itypes) and d.itypes[b.type][0] == 'myst']
            if n and rs >= 61 and len(codes) == n:
                for i, code in enumerate(codes):
                    r = raw[4 + rs * i:4 + rs * (i + 1)]
                    mods = []
                    for k in range(6):
                        o = 45 + 16 * k
                        if o + 16 > len(r):
                            break
                        prop = struct.unpack_from('<H', r, o)[0]
                        if prop == 0xFFFF or prop >= len(d.props):
                            continue
                        param, lo, hi = struct.unpack_from('<iii', r, o + 4)
                        mods.append((prop, param, lo, hi))
                    self._orbs[code] = dict(name=r[:32].split(b'\0')[0].decode('latin1'), max=r[43], mods=mods)
        except (OSError, struct.error, ZeroDivisionError, AttributeError):
            self._orbs = {}
        return self._orbs

    def item_desc(self, code):
        """Lines of the item's in-game description (misc.txt spelldescstr, u16 string index at 0xB6 of the 1.13c
        misc record): the colour of a Mystic Dye, the bonus of a cycle or enchantment scroll, ... or []."""
        if not hasattr(self, '_desc_idx'):
            self._desc_idx = {}
            for r in _rows(self.data, 'misc'):
                c = r[128:132].decode('latin1')
                if c not in self._desc_idx and len(r) > 0xB7:
                    self._desc_idx[c] = struct.unpack_from('<H', r, 0xB6)[0]
        i = self._desc_idx.get(code)
        if not i:
            return []
        ent = self.data.strings.get(i)
        if not ent or ent[0] == 'dummy':
            return []
        out = []
        for line in str(ent[1]).replace('\\n', '\n').split('\n'):
            while 'ÿc' in line:
                k = line.index('ÿc')
                line = line[:k] + line[k + 3:]
            line = line.strip()
            if line and not line.startswith('%'):
                out.append(line)
        return out

    def desc_summary(self, code, limit=90):
        """Short text telling items of the same name apart: the description lines before the usage hint."""
        keep = []
        for line in self.item_desc(code):
            low = line.lower()
            if low.startswith(('right-click', 'right click', 'cube', '- transmute', 'transmute')):
                break
            keep.append(line)
        text = '; '.join(keep or self.item_desc(code)[:1])
        return text if len(text) <= limit else text[:limit - 1] + '…'

    def orb_effects(self, code):
        """(lines of text, max per item) of a mystic orb, or None for other items."""
        o = self._orb_table().get(code)
        if o is None:
            return None
        from mxl_tooltip import stat_lines
        lines = []
        for prop, param, lo, hi in o['mods']:
            try:
                st = Mod('orb', prop, param, lo, hi, self.data).stats(self.data)
                for ln in stat_lines(st, self.data, None):
                    text = ln[1] if isinstance(ln, (tuple, list)) else str(ln)
                    if text.strip():
                        lines.append(text.strip())
            except Exception:
                continue
        return lines, o['max']

    def describe(self, spec):
        d, q = self.data, spec['quality']
        if q == 'unique':
            return d.key(d.uniques[spec['row']]['name'])
        if q == 'set':
            return d.key(d.set_items[spec['row']]['name'])
        if q == 'runeword':
            return d.key(d.runewords[spec['row']][0])
        return self.display_name(spec['code']) if spec.get('code') else '?'
