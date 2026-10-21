"""Two item layouts of Median XL Sigma that the collector's reader (mxl_format.parse_item) did not know, which made it
lose its place in a stash ("FormatError: 'JM' expected at byte ..."):

1. Quality 9 ("tempered": e.g. Shrine Blessed gear). After the item header it holds only the two rare-name bytes
   (8 + 8 bits); there are no affix slots, the affixes' stats are in the stat list.
2. Weapons / armor whose item type is not under 'weap' / 'armo' (Staff of Herding, wd02). The game reads defense
   and durability only for items of those types (D2Common: item type 0x2d weap / 0x32 armo checks); for any other
   type these fields are not in the save.

install() replaces mxl_format.parse_item with the same reader plus these two cases (read_item_list and every
caller go through mxl_format.parse_item) and adds 'tempered' to the quality and name-colour tables."""
import mxl_format
from mxl_format import BitReader, Item, expect, read_stats, SET_UNIQUE_ID_BITS
import stat_ids as S

TEMPERED = 'tempered'
WEAP, ARMO = 'weap', 'armo'


def _tag_index(data, tag):
    cache = data.__dict__.setdefault('_fix_tags', {})
    if tag not in cache:
        cache[tag] = next((i for i, t in enumerate(data.itypes) if t[0] == tag), None)
    return cache[tag]


def body_kind(data, code):
    """'armor' / 'weapons' only when the item type is really under 'armo' / 'weap' (what the game checks to read
    defense and durability), else 'misc'."""
    cache = data.__dict__.setdefault('_fix_kinds', {})
    if code in cache:
        return cache[code]
    base = data.base(code)
    kind = base.kind
    if kind in ('armor', 'weapons'):
        try:
            anc = data.type_ancestors(base.type) | data.type_ancestors(base.type2)
            tag = _tag_index(data, ARMO if kind == 'armor' else WEAP)
            if tag is not None and tag not in anc:
                kind = 'misc'
        except Exception:
            pass
    cache[code] = kind
    return kind


def parse_item(blob, data):
    expect(blob, 0, b'JM')
    br = BitReader(blob[2:])
    it = Item()
    flags = br.read(32)
    it['identified'] = bool(flags >> 4 & 1)
    it['has_sockets'] = bool(flags >> 11 & 1)
    simple = bool(flags >> 21 & 1)
    it['ethereal'] = bool(flags >> 22 & 1)
    personalized = flags >> 24 & 1
    runeword = flags >> 26 & 1
    it['version'] = br.read(10)
    it['location'] = br.read(3)
    it['equipped'] = br.read(4)
    it['_pos_bit'] = br.pos
    it['x'] = br.read(4)
    it['y'] = br.read(4)
    it['panel'] = br.read(3)
    code = ''.join(chr(br.read(8)) for _ in range(4))
    it['code'] = code
    base = data.base(code)
    kind, name = body_kind(data, code), base.name
    it['name'] = name
    it['stats'] = []
    if simple:
        it['socketed_count'] = br.read(1)
        return it, br
    it['socketed_count'] = br.read(3)
    it['id'] = br.read(32)
    it['ilvl'] = br.read(7)
    q = mxl_format.QUALITY.get(br.read(4), 'unknown')
    it['quality'] = q
    if br.read(1):
        it['image'] = br.read(3)
    if br.read(1):
        it['_auto_bit'] = br.pos
        it['auto_affix'] = br.read(11)
    if q in ('low', 'superior'):
        it['subquality'] = br.read(3)
    elif q == 'magic':
        it['prefix'] = br.read(11)
        it['suffix'] = br.read(11)
    elif q in ('set', 'unique'):
        it['set_unique_id'] = br.read(SET_UNIQUE_ID_BITS)
    elif q in ('rare', 'crafted'):
        it['rare_names'] = (br.read(8), br.read(8))
        it['rare_affixes'] = [br.read(11) if br.read(1) else None for _ in range(6)]
    elif q == TEMPERED:
        it['rare_names'] = (br.read(8), br.read(8))
    if runeword:
        it['runeword'] = br.read(12)
        br.read(4)
    if personalized:
        s = ''
        while True:
            c = br.read(7)
            if not c:
                break
            s += chr(c)
        it['personalized_name'] = s
    if code in ('tbk ', 'ibk '):
        br.read(5)
    br.read(1)
    if kind == 'armor':
        it['_def_bit'] = br.pos
        c = data.isc[S.DEFENSE]
        it['defense'] = br.read(c['bits']) - c['add']
    if kind in ('armor', 'weapons'):
        maxd = br.read(data.isc[S.MAX_DURABILITY]['bits'])
        it['max_durability'] = maxd
        if maxd != data.isc[S.MAX_DURABILITY]['add']:
            it['durability'] = br.read(data.isc[S.DURABILITY]['bits'])
    if data.base(code).stack_max is not None:
        it['quantity'] = br.read(9)
    if it['has_sockets']:
        it['sockets'] = br.read(4)
    set_lists = br.read(5) if q == 'set' else 0
    it['stats'] = read_stats(br, data)
    for i in range(5):
        if set_lists >> i & 1:
            it['set_stats_%d' % i] = read_stats(br, data)
    if runeword:
        it['stats_runeword'] = read_stats(br, data)
    return it, br


def install():
    if getattr(mxl_format, '_sigma_fix', False):
        return
    mxl_format.QUALITY.setdefault(9, TEMPERED)
    mxl_format.parse_item = parse_item
    try:
        import mxl_tooltip
        nc = getattr(mxl_tooltip, 'NAME_COLOR', None)
        if isinstance(nc, dict) and 'crafted' in nc:
            nc.setdefault(TEMPERED, nc['crafted'])
    except Exception:
        pass
    mxl_format._sigma_fix = True
