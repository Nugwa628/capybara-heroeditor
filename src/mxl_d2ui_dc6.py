"""DC6 images (Diablo II UI art) -> RGBA pixels, pure Python (no PIL)."""
import struct
import zlib


def dc6_frames(data):
    """[(width, height, offset_x, offset_y, indexes bytearray w*h (None = transparent -> 255 marker))]."""
    ver, flags, enc, term, dirs, fpd = struct.unpack_from('<6I', data, 0)
    n = dirs * fpd
    ptrs = struct.unpack_from(f'<{n}I', data, 24)
    out = []
    for p in ptrs:
        flip, w, h, ox, oy, _u, _nb, ln = struct.unpack_from('<8i', data, p)
        px = bytearray(w * h)
        mask = bytearray(w * h)
        pos = p + 32
        end = pos + ln
        x, y = 0, (h - 1 if not flip else 0)
        dy = -1 if not flip else 1
        while pos < end:
            b = data[pos]
            pos += 1
            if b == 0x80:
                x, y = 0, y + dy
            elif b & 0x80:
                x += b & 0x7F
            else:
                if 0 <= y < h:
                    seg = data[pos:pos + b]
                    i = y * w + x
                    k = min(b, w - x)
                    if k > 0:
                        px[i:i + k] = seg[:k]
                        mask[i:i + k] = b'\x01' * k
                pos += b
                x += b
        out.append((w, h, ox, oy, px, mask))
    return out


def palette(pal_dat):
    """pal.dat: 256 × (B, G, R)."""
    return [(pal_dat[i * 3 + 2], pal_dat[i * 3 + 1], pal_dat[i * 3]) for i in range(256)]


def to_rgba(frame, pal):
    w, h, _ox, _oy, px, mask = frame
    out = bytearray(w * h * 4)
    for i in range(w * h):
        if mask[i]:
            r, g, b = pal[px[i]]
            out[i * 4:i * 4 + 4] = bytes((r, g, b, 255))
    return w, h, out


def compose(frames, pal, cols):
    """Panels are cut in tiles (256 wide): rows of `cols` frames, left to right, top to bottom."""
    rows = [frames[i:i + cols] for i in range(0, len(frames), cols)]
    W = sum(f[0] for f in rows[0])
    H = sum(max(f[1] for f in r) for r in rows)
    img = bytearray(W * H * 4)
    y0 = 0
    for r in rows:
        x0 = 0
        for f in r:
            w, h, rgba = to_rgba(f, pal)
            for y in range(h):
                if y0 + y >= H:
                    break
                n = min(w, W - x0)
                img[((y0 + y) * W + x0) * 4:((y0 + y) * W + x0 + n) * 4] = rgba[y * w * 4:(y * w + n) * 4]
            x0 += w
        y0 += max(f[1] for f in r)
    return W, H, img


def png(w, h, rgba):
    raw = b''.join(b'\x00' + bytes(rgba[y * w * 4:(y + 1) * w * 4]) for y in range(h))

    def chunk(t, d):
        c = struct.pack('>I', len(d)) + t + d
        return c + struct.pack('>I', zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))
