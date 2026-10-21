"""Character save (.d2s, version 96 / Median XL) reader and writer: level, quests, waypoints, equipment,
inventory and mercenary items.

Layout (1.13c): header with quests ('Woo!' @0x14F), waypoints ('WS' @0x279), NPC intros ('w4' @0x2C9), then the
stats ('gf', 9-bit ids, CSvParam/CSvBits widths of itemstatcost; Median adds stat 88 with a 16-bit param), the
skills ('if', kept as is), the character's items ('JM' + count), the corpse ('JM' + count), the mercenary ('jf' +
items when hired) and the golem ('kf'). File size @0x08 and checksum @0x0C are recomputed on writing.
"""
import os
import random
import shutil
import struct

from mxl_format import read_item_list, FormatError
from mxl_creator import decode_item, encode_item, CreateError
from mxl_lang import T

MAGIC = 0xAA55AA55
QUEST_OFF, WP_OFF, NPC_OFF = 0x14F, 0x279, 0x2C9
QUEST_DATA = QUEST_OFF + 10           # 'Woo!' + version u32 + size u16
WP_DATA = WP_OFF + 8                   # 'WS' + 6 bytes
DIFFICULTIES = ('Normal', 'Nightmare', 'Hell')

# quest words per difficulty (48 words): (act, intro word, [(word, string key)], completed word)
ACTS = [
    ('Act I', 0, [(1, 'qstsa1q1'), (2, 'qstsa1q2'), (3, 'qstsa1q3'), (4, 'qstsa1q4'), (5, 'qstsa1q5'), (6, 'qstsa1q6')], 7),
    ('Act II', 8, [(9, 'qstsa2q1'), (10, 'qstsa2q2'), (11, 'qstsa2q3'), (12, 'qstsa2q4'), (13, 'qstsa2q5'), (14, 'qstsa2q6')], 15),
    ('Act III', 16, [(17, 'qstsa3q1'), (18, 'qstsa3q2'), (19, 'qstsa3q3'), (20, 'qstsa3q4'), (21, 'qstsa3q5'), (22, 'qstsa3q6')], 23),
    ('Act IV', 24, [(25, 'qstsa4q1'), (26, 'qstsa4q2'), (27, 'qstsa4q3')], 28),
    ('Act V', 34, [(35, 'qstsa5q1'), (36, 'qstsa5q2'), (37, 'qstsa5q3'), (38, 'qstsa5q4'), (39, 'qstsa5q5'), (40, 'qstsa5q6')], 41),
]
# quest rewards given when a quest is marked done here (per difficulty: 4 skill points, 5 stat points, +20 life)
QUEST_REWARDS = {1: ('skill', 1),      # Den of Evil
                 9: ('skill', 1),      # Radament's Lair
                 25: ('skill', 2),     # The Fallen Angel (Izual)
                 17: ('stats', 5),     # Lam Esen's Tome
                 20: ('life', 20)}     # The Golden Bird
QUEST_DONE = 0x1001                   # completed + closed in the quest log
LAST_QUEST_WORD = 40                  # Eve of Destruction: difficulty finished

# (index in the 'WS' bitfield, in-game name), from levels.txt "Waypoint" and the string tables
WAYPOINTS = [
    ('Act I', [(0, 'Rogue Encampment'), (1, 'Cold Plains'), (2, 'Stony Field'), (3, 'Dark Wood'), (4, 'Black Marsh'),
               (5, 'Outer Cloister'), (6, 'Jail Level 1'), (7, 'Inner Cloister'), (8, 'Catacombs Level 2'),
               (50, 'Duncraig')]),
    ('Act II', [(9, 'Lut Gholein'), (10, 'Sewers Level 2'), (11, 'Dry Hills'), (12, 'Halls of the Dead Level 2'),
                (13, 'Far Oasis'), (14, 'Lost City'), (15, 'Palace Cellar Level 1'), (16, 'Arcane Sanctuary'),
                (17, 'Canyon of the Magi'), (60, 'Tran Athulua'), (61, 'Island of Skartara')]),
    ('Act III', [(18, 'Kurast Docks'), (19, 'Spider Forest'), (20, 'Great Marsh'), (21, 'Flayer Jungle'),
                 (22, 'Lower Kurast'), (23, 'Kurast Bazaar'), (24, 'Upper Kurast'), (25, 'Travincal'),
                 (26, 'Durance of Hate Level 2'), (70, 'Caldeum'), (71, 'Torajan Jungles'), (72, 'Vizjun')]),
    ('Act IV', [(27, 'The Pandemonium Fortress'), (28, 'City of the Damned'), (29, 'River of Flame'),
                (80, 'Silver City')]),
    ('Act V', [(30, 'Harrogath'), (31, 'Frigid Highlands'), (32, 'Arreat Plateau'), (33, 'Crystalline Passage'),
               (34, 'Glacial Trail'), (35, 'Halls of Pain'), (36, 'Frozen Tundra'), (37, "The Ancients' Way"),
               (38, 'Worldstone Keep Level 2'), (90, 'Túr Dúlra')]),
]
# Median XL areas that only exist in Hell (levels.txt: monster level set for Hell only)
HELL_ONLY_WAYPOINTS = frozenset({50, 60, 61, 70, 71, 72, 80, 90})
ALL_WAYPOINTS = [n for _, wps in WAYPOINTS for n, _ in wps]
N_WAYPOINTS = len(ALL_WAYPOINTS)                   # 47


def waypoint_ids(diff):
    """Waypoint indexes that exist in this difficulty (0 Normal, 1 Nightmare, 2 Hell)."""
    return [n for n in ALL_WAYPOINTS if diff == 2 or n not in HELL_ONLY_WAYPOINTS]


# equipment slots (value of the 'equipped' field) and the body location they accept
SLOTS = {1: 'head', 2: 'neck', 3: 'tors', 4: 'rarm', 5: 'larm', 6: 'rrin', 7: 'lrin', 8: 'belt', 9: 'feet',
         10: 'glov', 11: 'rarm', 12: 'larm'}
SLOT_NAMES = {1: 'Helm', 2: 'Amulet', 3: 'Armor', 4: 'Right hand (I)', 5: 'Left hand (I)', 6: 'Right ring',
              7: 'Left ring', 8: 'Belt', 9: 'Boots', 10: 'Gloves', 11: 'Right hand (II)', 12: 'Left hand (II)'}


def slot_name(n, default=''):
    """Name of an equipment slot in the chosen language."""
    return T(f'ch.slot.{n}', SLOT_NAMES[n]) if n in SLOT_NAMES else default


def difficulty_name(k):
    return T(f'ch.diff.{k}', DIFFICULTIES[k])


BODYLOC_CODES = ['', 'head', 'neck', 'tors', 'rarm', 'larm', 'rrin', 'lrin', 'belt', 'feet', 'glov']
MERC_SLOTS = (1, 3, 4, 5)
INV_W, INV_H = 15, 10
LOC_STORED, LOC_EQUIPPED, LOC_BELT = 0, 1, 2
PANEL_INVENTORY, PANEL_CUBE = 1, 4


class CharError(Exception):
    """Character change refused (message for the user); the file is not modified."""


def checksum(buf):
    b = bytearray(buf)
    b[12:16] = b'\0\0\0\0'
    s = 0
    for x in b:
        s = ((s << 1) | (s >> 31)) & 0xFFFFFFFF
        s = (s + x) & 0xFFFFFFFF
    return s


class _Bits:
    def __init__(self, b):
        self.v = int.from_bytes(b, 'little')
        self.pos = 0

    def read(self, n):
        r = (self.v >> self.pos) & ((1 << n) - 1)
        self.pos += n
        return r


class _W:
    def __init__(self):
        self.v = 0
        self.pos = 0

    def write(self, n, x):
        if x < 0 or x >= (1 << n):
            raise CharError(f'value {x} does not fit in {n} bits')
        self.v |= x << self.pos
        self.pos += n

    def bytes(self):
        return self.v.to_bytes((self.pos + 7) // 8, 'little')


def _csv(data):
    """{stat: (param bits, value bits)} of the character stats (itemstatcost CSvParam / CSvBits)."""
    if not hasattr(data, '_csv_widths'):
        out = {}
        for r in data._rows('itemstatcost'):
            sid = struct.unpack_from('<H', r, 0)[0]
            if r[10]:
                out[sid] = (r[11], r[10])
        data._csv_widths = out
    return data._csv_widths


def _exp_table(data):
    if not hasattr(data, '_exp_rows'):
        path = os.path.join(data.folder, 'experience_sp.bin')
        if not os.path.exists(path):
            path = os.path.join(data.folder, 'experience.bin')
        with open(path, 'rb') as f:
            e = f.read()
        n = struct.unpack_from('<I', e)[0]
        data._exp_rows = [struct.unpack_from('<8I', e, 4 + 32 * i) for i in range(n)]
    return data._exp_rows


def _charstats(data):
    if not hasattr(data, '_charstats'):
        rows = []
        for r in data._rows('charstats'):
            rows.append(dict(life=r[0x43], stamina=r[0x44], mana=r[0x45], stats=r[0x50] or 5))
        data._charstats = rows
    return data._charstats


def max_level(data, cls):
    return _exp_table(data)[0][cls]


def exp_for_level(data, cls, level):
    rows = _exp_table(data)
    return rows[level][cls] if 1 <= level < len(rows) else None


def item_bodylocs(data, code):
    """Body locations ('head', 'rarm'...) where an item type can be equipped (item type and its ancestors)."""
    if not hasattr(data, '_bodylocs'):
        out = []
        for r in data._rows('itemtypes'):
            out.append((r[0x0A], r[0x0B]))
        data._bodylocs = out
    b = data.base(code)
    locs = set()
    for t in data.type_ancestors(b.type) | data.type_ancestors(b.type2):
        for x in data._bodylocs[t]:
            if 0 < x < len(BODYLOC_CODES):
                locs.add(BODYLOC_CODES[x])
    return locs


class Entry:
    """A top-level item of the character (with its socketed items): parsed item + raw bytes."""

    def __init__(self, it, blob):
        self.it = it
        self.blob = blob

    def moved(self, data, **fields):
        """Copy with header fields changed (location, equipped, x, y, panel); socketed items unchanged."""
        rec, n = decode_item(self.blob, data)
        rec.update(fields)
        return encode_item(rec, data) + self.blob[n:]


class Character:
    def __init__(self, path, data):
        self.path = path
        self.data = data
        with open(path, 'rb') as f:
            self.raw = f.read()
        self.parse(self.raw)

    # ------------------------------------------------------------------ parse / build
    def parse(self, b):
        d = self.data
        if struct.unpack_from('<I', b, 0)[0] != MAGIC:
            raise FormatError(T('ch.err.not_char', 'not a character file'))
        self.version = struct.unpack_from('<I', b, 4)[0]
        if b[QUEST_OFF:QUEST_OFF + 4] != b'Woo!' or b[WP_OFF:WP_OFF + 2] != b'WS':
            raise FormatError(T('ch.err.layout', 'unexpected character file layout'))
        gf = b.find(b'gf', NPC_OFF)
        if gf < 0:
            raise FormatError(T('ch.err.no_stats', "stats ('gf') not found"))
        self.head = bytearray(b[:gf])
        widths = _csv(d)
        br = _Bits(b[gf + 2:gf + 2 + 2048])
        stats = []
        while True:
            sid = br.read(9)
            if sid == 0x1FF:
                break
            if sid not in widths:
                raise FormatError(T('ch.err.unknown_stat', 'unknown character stat {stat}', stat=sid))
            pb, vb = widths[sid]
            p = br.read(pb) if pb else None
            stats.append([sid, p, br.read(vb)])
        self.stats = stats
        p = gf + 2 + (br.pos + 7) // 8
        if b[p:p + 2] != b'if':
            raise FormatError(T('ch.err.no_skills', "skills ('if') not found after the stats"))
        jm = b.find(b'JM', p)
        self.skills = b[p:jm]
        count = struct.unpack_from('<H', b, jm + 2)[0]
        items, q = read_item_list(b, jm + 4, count, d)
        self.items = [Entry(it, b[it['_offset']:it['_offset'] + self._full_size(it)]) for it in items]
        if b[q:q + 2] != b'JM':
            raise FormatError(T('ch.err.no_corpse', 'corpse list not found'))
        cc = struct.unpack_from('<H', b, q + 2)[0]
        r = q + 4
        if cc:
            r += 12
            if b[r:r + 2] != b'JM':
                raise FormatError(T('ch.err.no_corpse_items', 'corpse items not found'))
            n = struct.unpack_from('<H', b, r + 2)[0]
            _, r = read_item_list(b, r + 4, n, d)
        self.corpse = b[q:r]
        self.has_jf = b[r:r + 2] == b'jf'
        self.merc_items = None
        if self.has_jf:
            r += 2
            if b[r:r + 2] == b'JM':
                n = struct.unpack_from('<H', b, r + 2)[0]
                mitems, r2 = read_item_list(b, r + 4, n, d)
                self.merc_items = [Entry(it, b[it['_offset']:it['_offset'] + self._full_size(it)]) for it in mitems]
                r = r2
        self.tail = b[r:]

    @staticmethod
    def _full_size(it):
        end = it['_offset'] + it['_size']
        for s in it.get('socketed', []):
            end = s['_offset'] + s['_size']
        return end - it['_offset']

    def build(self):
        d = self.data
        out = bytearray(self.head)
        w = _W()
        widths = _csv(d)
        for sid, p, v in self.stats:
            pb, vb = widths[sid]
            w.write(9, sid)
            if pb:
                w.write(pb, p or 0)
            w.write(vb, v)
        w.write(9, 0x1FF)
        out += b'gf' + w.bytes() + self.skills
        out += b'JM' + struct.pack('<H', len(self.items)) + b''.join(e.blob for e in self.items)
        out += self.corpse
        if self.has_jf:
            out += b'jf'
            if self.merc_items is not None:
                out += b'JM' + struct.pack('<H', len(self.merc_items)) + b''.join(e.blob for e in self.merc_items)
        out += self.tail
        struct.pack_into('<I', out, 8, len(out))
        struct.pack_into('<I', out, 12, checksum(out))
        return bytes(out)

    def reload_items(self):
        """Re-reads the item lists from the built file (fresh offsets for the GUI)."""
        self.parse(self.build())

    # ------------------------------------------------------------------ header values
    @property
    def name(self):
        return self.head[0x14:0x24].split(b'\0')[0].decode('latin1')

    @property
    def cls(self):
        return self.head[0x28]

    @property
    def level(self):
        return self.head[0x2B]

    @property
    def progression(self):
        return self.head[0x25]

    @property
    def merc_id(self):
        return struct.unpack_from('<I', self.head, 0xB3)[0]

    @property
    def merc_hired(self):
        return self.merc_items is not None or self.merc_id != 0

    def stat(self, sid):
        return next((v for s, p, v in self.stats if s == sid), 0)

    def set_stat(self, sid, value):
        for st in self.stats:
            if st[0] == sid:
                st[2] = value
                break
        else:
            pb, vb = _csv(self.data)[sid]
            self.stats.append([sid, 0 if pb else None, value])
        if value == 0 and sid not in (12,):
            self.stats = [st for st in self.stats if not (st[0] == sid and st[2] == 0)]
        self.stats.sort(key=lambda st: st[0])

    # ------------------------------------------------------------------ level
    def set_level(self, level):
        """Raises the level like the game does: experience = start of the level; per level gained: stat points,
        1 skill point, life / mana / stamina per level (charstats). Lowering a level is refused (not possible in game)."""
        d = self.data
        cur = self.level
        top = max_level(d, self.cls)
        if not cur <= level <= top:
            raise CharError(T('ch.err.level', 'level must be {lo} to {hi} (a level cannot be lowered)', lo=cur, hi=top))
        gained = level - cur
        if not gained:
            return
        cs = _charstats(d)[self.cls]
        self.head[0x2B] = level
        self.set_stat(12, level)
        exp = exp_for_level(d, self.cls, level)
        if exp is not None and exp > self.stat(13):
            self.set_stat(13, exp)
        self.set_stat(4, self.stat(4) + cs['stats'] * gained)
        self.set_stat(5, self.stat(5) + gained)
        for cur_id, max_id, per in ((6, 7, cs['life']), (8, 9, cs['mana']), (10, 11, cs['stamina'])):
            if per:
                new_max = self.stat(max_id) + (per << 6) * gained
                self.set_stat(max_id, new_max)
                self.set_stat(cur_id, new_max)

    # ------------------------------------------------------------------ quests / waypoints
    def quest_word(self, diff, word):
        return struct.unpack_from('<H', self.head, QUEST_DATA + 96 * diff + 2 * word)[0]

    def set_quest_word(self, diff, word, value):
        struct.pack_into('<H', self.head, QUEST_DATA + 96 * diff + 2 * word, value)

    def quest_done(self, diff, word):
        return bool(self.quest_word(diff, word) & 1)

    def set_quests(self, diff, done_words):
        """done_words: set of quest words to mark completed in this difficulty (others are left as they are when
        already done in game, or cleared when they were only marked). Act introductions / travel words follow."""
        for act, intro, quests, completed in ACTS:
            any_done = False
            for word, _ in quests:
                if word in done_words:
                    if not self.quest_done(diff, word):
                        self.set_quest_word(diff, word, QUEST_DONE)
                        self.quest_reward(word, +1)
                    any_done = True
                elif self.quest_word(diff, word) == QUEST_DONE:
                    self.set_quest_word(diff, word, 0)
                    self.quest_reward(word, -1)
            all_done = all(w in done_words for w, _ in quests)
            if any_done and not self.quest_word(diff, intro):
                self.set_quest_word(diff, intro, 1)
            if all_done and not self.quest_word(diff, completed):
                self.set_quest_word(diff, completed, 1)
        prog = 0
        for k in range(3):
            if self.quest_done(k, LAST_QUEST_WORD):
                prog = 5 * (k + 1)
        if prog > self.head[0x25]:
            self.head[0x25] = prog

    def quest_reward(self, word, sign):
        """Gives (sign +1) or takes back (sign -1, when a quest marked here is unmarked) the quest's reward."""
        r = QUEST_REWARDS.get(word)
        if r is None:
            return
        kind, n = r
        log = self.__dict__.setdefault('reward_log', {})
        log[kind] = log.get(kind, 0) + sign * n
        if kind == 'skill':
            self.set_stat(5, max(0, self.stat(5) + sign * n))
        elif kind == 'stats':
            self.set_stat(4, max(0, self.stat(4) + sign * n))
        else:                                   # life: stats 6 / 7 are stored as 1/256 points
            for sid in (7, 6):
                self.set_stat(sid, max(1 << 8, self.stat(sid) + sign * (n << 8)))

    def waypoint(self, diff, n):
        return bool(self.head[WP_DATA + 24 * diff + 2 + n // 8] >> (n % 8) & 1)

    def set_waypoints(self, diff, active):
        base = WP_DATA + 24 * diff
        self.head[base] = 2
        self.head[base + 1] = 1
        for n in waypoint_ids(diff):
            i = base + 2 + n // 8
            if n in active or n == 0:
                self.head[i] |= 1 << (n % 8)
            else:
                self.head[i] &= ~(1 << (n % 8)) & 0xFF

    # ------------------------------------------------------------------ items
    def entries(self, merc=False):
        return (self.merc_items if self.merc_items is not None else []) if merc else self.items

    def equipped(self, merc=False):
        return {e.it['equipped']: e for e in self.entries(merc) if e.it['location'] == LOC_EQUIPPED}

    def inventory(self):
        return [e for e in self.items if e.it['location'] == LOC_STORED and e.it['panel'] == PANEL_INVENTORY]

    def slot_error(self, it, slot, merc=False, ignore=None):
        """None if the item can go in this equipment slot, else the reason."""
        d = self.data
        if merc and slot not in MERC_SLOTS:
            return T('ch.err.merc_slots', 'mercenaries only use helm, armor and weapons')
        locs = item_bodylocs(d, it['code'])
        if SLOTS[slot] not in locs:
            return T('ch.err.cannot_equip', '{item} cannot be equipped as {slot}', item=d.base(it['code']).name, slot=slot_name(slot))
        eq = self.equipped(merc)
        cur = eq.get(slot)
        if cur is not None and cur is not ignore:
            return T('ch.err.slot_full', '{slot} is not empty', slot=slot_name(slot))
        pair = {4: 5, 5: 4, 11: 12, 12: 11}.get(slot)
        if pair:
            other = eq.get(pair)
            if other is not None and other is not ignore:
                if d.base(it['code']).two_handed or d.base(other.it['code']).two_handed:
                    return T('ch.err.two_hand', 'a two-handed weapon needs the other hand empty')
        return None

    def inventory_error(self, it, x, y, ignore=None):
        w, h = self.data.base(it['code']).size
        if not (0 <= x and 0 <= y and x + w <= INV_W and y + h <= INV_H):
            return T('ch.err.outside_inv', 'outside the inventory')
        for e in self.inventory():
            if e is ignore:
                continue
            ow, oh = self.data.base(e.it['code']).size
            ex, ey = e.it['x'], e.it['y']
            if x < ex + ow and ex < x + w and y < ey + oh and ey < y + h:
                return T('ch.err.overlaps', 'overlaps {item}', item=e.it['name'])
        return None

    def free_inventory_spot(self, it):
        w, h = self.data.base(it['code']).size
        for x in range(INV_W - w + 1):
            for y in range(INV_H - h + 1):
                if self.inventory_error(it, x, y) is None:
                    return x, y
        return None

    def place(self, blob, where, merc=False):
        """Adds an item (bytes incl. socketed items, from a stash or the creator) at where = ('slot', n) or
        ('inv', x, y). Returns the new Entry."""
        d = self.data
        it = blob_item(blob, d)
        if where[0] == 'slot':
            err = self.slot_error(it, where[1], merc)
            if err:
                raise CharError(err)
            new = Entry(None, Entry(it, blob).moved(d, location=LOC_EQUIPPED, equipped=where[1], x=where[1], y=0, panel=0))
        else:
            if merc:
                raise CharError(T('ch.err.merc_no_inv', 'the mercenary has no inventory'))
            err = self.inventory_error(it, where[1], where[2])
            if err:
                raise CharError(err)
            new = Entry(None, Entry(it, blob).moved(d, location=LOC_STORED, equipped=0, x=where[1], y=where[2],
                                                    panel=PANEL_INVENTORY))
        new.it = blob_item(new.blob, d)
        lst = self.entries(merc)
        if merc and self.merc_items is None:
            raise CharError(T('ch.err.no_merc', 'no mercenary hired'))
        lst.append(new)
        return new

    def remove(self, entry, merc=False):
        lst = self.entries(merc)
        lst.remove(entry)

    def move(self, entry, where, merc=False):
        """Moves an item inside the character (slot <-> inventory)."""
        d = self.data
        it = entry.it
        if where[0] == 'slot':
            err = self.slot_error(it, where[1], merc, ignore=entry)
            if err:
                raise CharError(err)
            entry.blob = entry.moved(d, location=LOC_EQUIPPED, equipped=where[1], x=where[1], y=0, panel=0)
        else:
            err = self.inventory_error(it, where[1], where[2], ignore=entry)
            if err:
                raise CharError(err)
            entry.blob = entry.moved(d, location=LOC_STORED, equipped=0, x=where[1], y=where[2], panel=PANEL_INVENTORY)
        entry.it = blob_item(entry.blob, d)

    def stash_blob(self, entry):
        """Bytes of the item as a stash item (location stored, stash panel); position set by the stash writer."""
        return entry.moved(self.data, location=LOC_STORED, equipped=0, x=0, y=0, panel=5)

    # ------------------------------------------------------------------ saving
    def save(self, backup=True):
        """Writes the character (collector's safe writer: game closed, verification, backup). Returns the backup."""
        from mxl_save import commit_file, load_character, EditError
        content = self.build()
        n_items = len(self.items)
        n_merc = None if self.merc_items is None else len(self.merc_items)

        def verify(tmp):
            try:
                c = Character(tmp, self.data)
                load_character(tmp, self.data)
            except Exception as e:
                raise EditError(T('ch.err.verify', 'check after writing failed: {error}', error=e))
            if len(c.items) != n_items or (None if c.merc_items is None else len(c.merc_items)) != n_merc:
                raise EditError(T('ch.err.verify_count', 'check after writing failed: item count'))
        try:
            with open(self.path, 'rb') as f:
                on_disk = f.read()
        except OSError:
            on_disk = None
        if on_disk is not None and on_disk != self.raw:
            raise CharError(T('ch.err.changed', 'the character file changed on disk since it was read (game or another '
                              'window): it has been reloaded, try again'))
        try:
            bak = commit_file(self.path, content, verify, backup)
        except EditError as e:
            raise CharError(str(e))
        self.raw = content
        self.parse(content)
        return bak


def blob_item(blob, data):
    from mxl_save import blob_item as _bi
    return _bi(blob, data)


def copy_character(path, new_name, data):
    """Copies a character (save, map, key and stash files) under a new name. Returns the new .d2s path."""
    from mxl_save import check_game_closed
    check_game_closed()
    if not (2 <= len(new_name) <= 15) or not all(c.isalpha() or c in '-_' for c in new_name):
        raise CharError(T('ch.err.name', 'name: 2 to 15 letters (- and _ allowed)'))
    folder = os.path.dirname(path)
    old = os.path.splitext(os.path.basename(path))[0]
    dst = os.path.join(folder, new_name + '.d2s')
    if os.path.exists(dst):
        raise CharError(T('ch.err.exists', '{name} already exists', name=new_name))
    c = Character(path, data)
    c.head[0x14:0x24] = new_name.encode('latin1').ljust(16, b'\0')
    content = c.build()
    with open(dst, 'wb') as f:
        f.write(content)
    for fn in os.listdir(folder):
        stem, ext = os.path.splitext(fn)
        if stem == old and ext.lower() in ('.key', '.map', '.stash') or (stem == old and ext.lower().startswith('.ma')):
            shutil.copy2(os.path.join(folder, fn), os.path.join(folder, new_name + ext))
    return dst
