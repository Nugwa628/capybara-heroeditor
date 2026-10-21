"""Rebuild a PyInstaller (6.x, onedir) exe: same bootloader, same CArchive entries, PYZ with extra modules,
entry script replaced. Usage: repack.py in.exe out.exe [--identity]"""
import struct, zlib, marshal, sys
sys.path.insert(0, '/root/work/tools')
import carch

def build_pyz(z, extra):
    toff = struct.unpack('!i', z[8:12])[0]
    toc = marshal.loads(z[toff:])
    blobs = []
    for name, (typ, off, ln) in toc:
        if name in extra:
            continue
        blobs.append((name, typ, z[off:off + ln]))
    for name, code in extra.items():
        blobs.append((name, 0, zlib.compress(marshal.dumps(code), 9)))
    out = bytearray(z[:8]) + b'\0\0\0\0'
    newtoc = []
    for name, typ, data in blobs:
        newtoc.append((name, (typ, len(out), len(data))))
        out += data
    struct.pack_into('!i', out, 8, len(out))
    out += marshal.dumps(newtoc)
    return bytes(out)

def build_exe(d, start, ents, info, replace):
    pkg = bytearray()
    toc = bytearray()
    for e in ents:
        data = replace.get(e['name'], None)
        if data is None:
            data, ulen, cflag = e['data'], e['ulen'], e['cflag']
        else:
            data, ulen, cflag = data
        off = len(pkg)
        pkg += data
        name = e['name'].encode() + b'\0'
        elen = 18 + len(name)
        pad = (16 - elen % 16) % 16
        toc += struct.pack('!IIIIBc', elen + pad, off, len(data), ulen, cflag, e['typ'].encode()) + name + b'\0' * pad
    tocoff = len(pkg)
    pkg += toc
    pyver, pylib = info
    pkglen = len(pkg) + 88
    pkg += struct.pack('!8sIIii64s', carch.COOKIE, pkglen, tocoff, len(toc), pyver, pylib)
    return d[:start] + bytes(pkg)

if __name__ == '__main__':
    src, dst = sys.argv[1], sys.argv[2]
    d, start, ents, info = carch.read(src)
    if '--identity' in sys.argv:
        out = build_exe(d, start, ents, info, {})
        print('identical:', out == d, len(out), len(d))
        sys.exit()
    plugin = '/root/work/plugin'
    extra = {}
    for mod in ('mxl_creator', 'mxl_creator_gui', 'mxl_char', 'mxl_char_gui', 'mxl_inv_gui', 'mxl_he_art', 'mxl_app_icon', 'mxl_statparse', 'mxl_import', 'mxl_format_fix', 'mxl_lang', 'mxl_d2ui_dc6', 'mxl_d2look_art', 'mxl_d2look', 'mxl_splash', 'mxl_log'):
        extra[mod] = compile(open(f'{plugin}/{mod}.py', encoding='utf-8').read(), f'{mod}.py', 'exec')
    script = [e for e in ents if e['typ'] == 's' and e['name'] == 'mxl_gui'][0]
    extra['mxl_gui'] = marshal.loads(zlib.decompress(script['data']))      # original GUI, now importable
    z = [e for e in ents if e['typ'] == 'z'][0]
    pyz = build_pyz(z['data'], extra)
    launcher = compile("import sys\nimport mxl_splash\nif mxl_splash.FLAG_ARG in sys.argv:\n    mxl_splash.main()\nelse:\n"
                       "    mxl_splash.start()\n    import mxl_creator_gui\n    mxl_creator_gui.run()\n", 'mxl_gui.py', 'exec')
    ld = marshal.dumps(launcher)
    replace = {'PYZ.pyz': (pyz, len(pyz), 0), 'mxl_gui': (zlib.compress(ld, 9), len(ld), 1)}
    out = build_exe(d, start, ents, info, replace)
    ico = next((a.split('=', 1)[1] for a in sys.argv if a.startswith('--icon=')), None)
    if ico:                                    # exe file icon: new resource section, overlay (archive) kept as is
        import rsrc
        out = rsrc.replace_icons(out, open(ico, 'rb').read(), start)
    open(dst, 'wb').write(out)
    print('written', dst, len(out))
