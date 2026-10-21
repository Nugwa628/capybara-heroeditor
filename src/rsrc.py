"""Minimal PE resource tree read / write (enough to replace the icons of a PyInstaller bootloader exe)."""
import struct

def sections(d):
    pe = struct.unpack_from('<I', d, 0x3c)[0]
    nsec = struct.unpack_from('<H', d, pe + 6)[0]
    optsz = struct.unpack_from('<H', d, pe + 20)[0]
    opt = pe + 24
    out = []
    for i in range(nsec):
        o = opt + optsz + 40 * i
        name = d[o:o + 8]
        vs, va, rs, ra = struct.unpack_from('<IIII', d, o + 8)
        out.append(dict(hdr=o, name=name, vs=vs, va=va, rs=rs, ra=ra))
    return pe, opt, optsz, out

def read_tree(d):
    """{type: {name: {lang: (data, codepage)}}} with ints or strings as names."""
    pe, opt, optsz, secs = sections(d)
    rva, size = struct.unpack_from('<II', d, opt + 112 + 16)
    def off(r):
        for s in secs:
            if s['va'] <= r < s['va'] + max(s['vs'], s['rs']):
                return r - s['va'] + s['ra']
        raise ValueError(hex(r))
    base = off(rva)
    def name_of(n):
        if n & 0x80000000:
            o = base + (n & 0x7fffffff)
            ln = struct.unpack_from('<H', d, o)[0]
            return d[o + 2:o + 2 + 2 * ln].decode('utf-16-le')
        return n
    def level(o):
        nn, ni = struct.unpack_from('<HH', d, o + 12)
        for i in range(nn + ni):
            n, t = struct.unpack_from('<II', d, o + 16 + 8 * i)
            yield name_of(n), t
    tree = {}
    for t, tt in level(base):
        tree[t] = {}
        for n, nt in level(base + (tt & 0x7fffffff)):
            tree[t][n] = {}
            for l, lt in level(base + (nt & 0x7fffffff)):
                r, sz, cp, _ = struct.unpack_from('<IIII', d, base + lt)
                tree[t][n][l] = (d[off(r):off(r) + sz], cp)
    return tree

def build_tree(tree, rva):
    """Serialises the tree for a section mapped at rva."""
    def key(k):
        return (0, k) if isinstance(k, str) else (1, k)
    dirs, strings, entries, datas = bytearray(), bytearray(), [], bytearray()
    # layout: all directories, then data entries, then strings, then data
    nodes = []
    def walk(node, depth):
        items = sorted(node.items(), key=lambda kv: key(kv[0]))
        rec = dict(items=items, depth=depth)
        nodes.append(rec)
        if depth < 2:
            rec['children'] = [walk(v, depth + 1) for _, v in items]
        return rec
    root = walk(tree, 0)
    pos = 0
    for n in nodes:
        n['off'] = pos
        pos += 16 + 8 * len(n['items'])
    dir_end = pos
    leaves = []
    for n in nodes:
        if n['depth'] == 2:
            for _, v in n['items']:
                leaves.append(v)
    leaf_off = dir_end
    str_off = leaf_off + 16 * len(leaves)
    names = {}
    sbuf = bytearray()
    for n in nodes:
        for k, _ in n['items']:
            if isinstance(k, str) and k not in names:
                names[k] = str_off + len(sbuf)
                sbuf += struct.pack('<H', len(k)) + k.encode('utf-16-le')
    data_off = (str_off + len(sbuf) + 15) & ~15
    out = bytearray(data_off)
    out[str_off:str_off + len(sbuf)] = sbuf
    li = 0
    leaf_pos = {}
    for n in nodes:
        if n['depth'] == 2:
            for k, v in n['items']:
                data, cp = v
                while len(out) % 8:
                    out.append(0)
                drva = rva + len(out)
                out += data
                struct.pack_into('<IIII', out, leaf_off + 16 * li, drva, len(data), cp, 0)
                leaf_pos[id(v)] = leaf_off + 16 * li
                li += 1
    for n in nodes:
        named = sum(1 for k, _ in n['items'] if isinstance(k, str))
        struct.pack_into('<IIHHHH', out, n['off'], 0, 0, 0, 0, named, len(n['items']) - named)
        for i, (k, v) in enumerate(n['items']):
            nm = (names[k] | 0x80000000) if isinstance(k, str) else k
            if n['depth'] < 2:
                tgt = n['children'][i]['off'] | 0x80000000
            else:
                tgt = leaf_pos[id(v)]
            struct.pack_into('<II', out, n['off'] + 16 + 8 * i, nm, tgt)
    return bytes(out)

def replace_icons(d, ico_bytes, overlay_start):
    """New exe bytes: icon group(s) replaced by the .ico, resources moved to a new last section, overlay kept."""
    tree = read_tree(d)
    pe, opt, optsz, secs = sections(d)
    falign, salign = struct.unpack_from('<II', d, opt + 36), None
    salign, falign = struct.unpack_from('<II', d, opt + 32)
    # .ico -> RT_ICON entries + RT_GROUP_ICON
    n = struct.unpack_from('<H', ico_bytes, 4)[0]
    lang = next(iter(next(iter(tree[14].values())).keys())) if 14 in tree else 0
    group_name = next(iter(tree[14].keys())) if 14 in tree else 1
    icons = {}
    grp = bytearray(struct.pack('<HHH', 0, 1, n))
    for i in range(n):
        w, h, cc, r, pl, bc, sz, o = struct.unpack_from('<BBBBHHII', ico_bytes, 6 + 16 * i)
        icons[i + 1] = {lang: (ico_bytes[o:o + sz], 0)}
        grp += struct.pack('<BBBBHHIH', w, h, cc, r, pl, bc, sz, i + 1)
    tree[3] = icons
    tree[14] = {group_name: {lang: (bytes(grp), 0)}}
    old = next((sec for sec in secs if sec['name'].rstrip(b'\0') == b'.rsrc'), None)
    body = build_tree(tree, old['va']) if old is not None else b''
    if old is not None and len(body) <= min(old['rs'], old['vs']):
        out = bytearray(d[:overlay_start])            # in place: same section, same layout, only the content changes
        out[old['ra']:old['ra'] + old['rs']] = body + bytes(old['rs'] - len(body))
        struct.pack_into('<II', out, opt + 112 + 16, old['va'], len(body))
        struct.pack_into('<I', out, opt + 64, 0)
        return bytes(out) + d[overlay_start:]
    last = max(secs, key=lambda s: s['va'])
    new_va = (last['va'] + max(last['vs'], last['rs']) + salign - 1) // salign * salign
    body = build_tree(tree, new_va)
    raw_size = (len(body) + falign - 1) // falign * falign
    pe_end = max(s['ra'] + s['rs'] for s in secs)
    new_ra = (pe_end + falign - 1) // falign * falign
    hdr = opt + optsz + 40 * len(secs)
    first_raw = min(s['ra'] for s in secs if s['rs'])
    if hdr + 40 > first_raw:
        raise ValueError('no room for a new section header')
    out = bytearray(d[:overlay_start])
    out = out[:pe_end] + bytes(new_ra - pe_end)
    out += body + bytes(raw_size - len(body))
    for sec in secs:                          # the old section loses its name, the new one is '.rsrc'
        if sec['name'].rstrip(b'\0') == b'.rsrc':
            out[sec['hdr']:sec['hdr'] + 8] = b'.rsrcold'
    out[hdr:hdr + 40] = struct.pack('<8sIIIIIIHHI', b'.rsrc\0\0\0', len(body), new_va, raw_size, new_ra, 0, 0, 0, 0,
                                    0x40000040)
    struct.pack_into('<H', out, pe + 6, len(secs) + 1)
    struct.pack_into('<I', out, opt + 56, (new_va + len(body) + salign - 1) // salign * salign)   # SizeOfImage
    struct.pack_into('<II', out, opt + 112 + 16, new_va, len(body))                               # resource dir
    struct.pack_into('<I', out, opt + 64, 0)                                                       # CheckSum
    return bytes(out) + d[overlay_start:]
