"""The kiosk's screen as a PNG, without X: cog draws straight to KMS, into a buffer in the VC4's
"T" tiling (modifier 0x0700000000000001, see DRM_FORMAT_MOD_BROADCOM_VC4_T_TILED in the kernel's
drm_fourcc.h). ffmpeg's kmsgrab reads that buffer as it is; this puts the pixels back in rows.
Runs ON the Pi (stdlib only):

    ffmpeg -device /dev/dri/card0 -f kmsgrab -i - -vf hwdownload,format=bgr0 -frames:v 1 \
        -f rawvideo raw.bgr0 && python3 kms_untile.py raw.bgr0 1024 600 out.png

The layout, 32 bits a pixel: 64-byte utiles of 4x4 pixels; 1KB subtiles of 4x4 utiles; 4KB tiles
of 2x2 subtiles, in the order (BL, TL, TR, BR) on even tile rows and (TR, BR, BL, TL) on odd ones,
"bottom" being the start of memory; tile rows left to right when even, right to left when odd.
"""
import struct
import sys
import zlib

UTILE, SUB, TILE = 4, 16, 32  # edge lengths in pixels
# Subtile order in memory per tile-row parity, as (x, y) in subtiles; y 0 is "bottom" = the
# start of memory, which on a scanout buffer is the top of the screen.
ORDER = {0: ((0, 0), (0, 1), (1, 1), (1, 0)), 1: ((1, 1), (1, 0), (0, 0), (0, 1))}


def untile(raw, width, height):
    tiles_x = (width + TILE - 1) // TILE
    out = bytearray(width * height * 4)
    offset = 0
    tile_row = 0
    while offset < len(raw):
        columns = range(tiles_x) if tile_row % 2 == 0 else range(tiles_x - 1, -1, -1)
        for tile_col in columns:
            for sx, sy in ORDER[tile_row % 2]:
                for uy in range(4):
                    for ux in range(4):
                        utile = raw[offset:offset + 64]
                        offset += 64
                        if len(utile) < 64:
                            return out
                        x0 = tile_col * TILE + sx * SUB + ux * UTILE
                        y0 = tile_row * TILE + sy * SUB + uy * UTILE
                        for row in range(4):
                            y = y0 + row
                            if y < height and x0 < width:
                                n = min(4, width - x0) * 4
                                start = (y * width + x0) * 4
                                out[start:start + n] = utile[row * 16:row * 16 + n]
        tile_row += 1
    return out


def png(path, bgrx, width, height):
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        line = bgrx[y * width * 4:(y + 1) * width * 4]
        rgb = bytearray(width * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = line[2::4], line[1::4], line[0::4]
        rows += rgb

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(bytes(rows), 6)) + chunk(b"IEND", b""))


if __name__ == "__main__":
    raw_path, width, height, out_path = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
    with open(raw_path, "rb") as f:
        raw = f.read()
    pixels = untile(raw, width, height)
    png(out_path, pixels, width, height)
    mean = sum(pixels[0::97]) / max(1, len(pixels[0::97])) / 255
    print(f"brightness {mean:.5f}")
