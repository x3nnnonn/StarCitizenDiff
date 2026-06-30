#!/usr/bin/env python3
"""Create CryEngine-compatible stub textures for missing planet assets."""

from __future__ import annotations

import argparse
import re
import struct
import sys
from pathlib import Path

# Planet hardware texture arrays expect full-size mips, not 4x4 placeholders.
STUB_SIZE = 512

DDSD_CAPS = 0x1
DDSD_HEIGHT = 0x2
DDSD_WIDTH = 0x4
DDSD_PIXELFORMAT = 0x1000
DDSD_MIPMAPCOUNT = 0x20000

DDSCAPS_TEXTURE = 0x1000
DDSCAPS_COMPLEX = 0x8
DDSCAPS_MIPMAP = 0x400000

DXGI_BC1_UNORM = 71
DXGI_BC3_UNORM = 77
DXGI_BC4_UNORM = 80
DXGI_BC5_UNORM = 83
DXGI_R8G8B8A8_UNORM = 28


def blocks(w: int, h: int) -> int:
    return max(1, (w + 3) // 4) * max(1, (h + 3) // 4)


def bc1_block_flat() -> bytes:
    # mid-gray DXT1 block
    return struct.pack("<HHI", 0x39E7, 0x39E7, 0)


def bc4_block_flat() -> bytes:
    # constant ~0.5 single channel
    return struct.pack("<BB6s", 0x00, 0xFF, b"\x00" * 6)


def bc5_block_flat_normal() -> bytes:
    # flat tangent-space normal (RG ~0.5)
    return struct.pack("<BB6sBB6s", 0x00, 0xFF, b"\x00" * 6, 0x00, 0xFF, b"\x00" * 6)


def mip_chain_sizes(width: int, height: int, block_size: int, mip_count: int) -> list[int]:
    sizes = []
    w, h = width, height
    for _ in range(mip_count):
        sizes.append(blocks(w, h) * block_size)
        w = max(1, w // 2)
        h = max(1, h // 2)
    return sizes


def cry_reserved1(width: int) -> list[int]:
    """CryEngine sideband fields (see shipped textures like stanton1_clouds_global.dds)."""
    return [
        0,
        width << 10,  # 0x200000 for 2048, 0x80000 for 512, etc.
        0,
        0,
        0,
        0,
        0,
        0x3F800000,
        0x3F800000,
        0x3F800000,
        0x3F800000,
    ]


def cry_dds_header(
    width: int,
    height: int,
    *,
    dxgi_format: int,
    mip_count: int,
) -> bytes:
    flags = (
        DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PIXELFORMAT | DDSD_MIPMAPCOUNT
    )
    header = bytearray(128)
    struct.pack_into("<4s", header, 0, b"DDS ")
    struct.pack_into("<I", header, 4, 124)
    struct.pack_into("<I", header, 8, flags)
    struct.pack_into("<I", header, 12, height)
    struct.pack_into("<I", header, 16, width)
    struct.pack_into("<I", header, 20, 0)  # pitch / linear size — 0 for CryEngine split DDS
    struct.pack_into("<I", header, 24, 0)  # depth
    struct.pack_into("<I", header, 28, mip_count)
    for i, val in enumerate(cry_reserved1(width)):
        struct.pack_into("<I", header, 32 + i * 4, val)
    struct.pack_into("<I", header, 76, 32)
    struct.pack_into("<I", header, 80, 0x4)
    struct.pack_into("<4s", header, 84, b"DX10")
    struct.pack_into(
        "<I",
        header,
        108,
        DDSCAPS_TEXTURE | DDSCAPS_COMPLEX | DDSCAPS_MIPMAP,
    )

    dx10 = struct.pack("<IIIII", dxgi_format, 3, 0, 1, 0)
    return bytes(header) + dx10


def fill_blocks(width: int, height: int, block_size: int, block: bytes) -> bytes:
    return block * blocks(width, height)


def make_split_cry_dds(
    width: int,
    height: int,
    *,
    dxgi_format: int,
    block_size: int,
    block: bytes,
    mip_count: int,
) -> tuple[bytes, dict[str, bytes]]:
    """CryEngine split layout: .1..N = early mips, base .dds = trailing small mips."""
    siblings: dict[str, bytes] = {}
    base_payload = bytearray()
    w, h = width, height

    for level in range(mip_count):
        mip_data = fill_blocks(w, h, block_size, block)
        if level < 8:
            siblings[f".{level + 1}"] = mip_data
        else:
            base_payload.extend(mip_data)
        w = max(1, w // 2)
        h = max(1, h // 2)

    if mip_count == 1:
        siblings.clear()
        base_payload = bytearray(fill_blocks(width, height, block_size, block))

    header = cry_dds_header(
        width,
        height,
        dxgi_format=dxgi_format,
        mip_count=mip_count,
    )
    return header + bytes(base_payload), siblings


def make_ddna_stub(size: int = STUB_SIZE) -> tuple[bytes, dict[str, bytes]]:
    mip_count = 10
    dds, sibs = make_split_cry_dds(
        size,
        size,
        dxgi_format=DXGI_BC5_UNORM,
        block_size=16,
        block=bc5_block_flat_normal(),
        mip_count=mip_count,
    )
    w, h = size, size
    for level in range(mip_count):
        mip_data = fill_blocks(w, h, 8, bc4_block_flat())
        if level == 0:
            sibs[".1a"] = mip_data
        elif level < 8:
            sibs[f".{level}a"] = mip_data
        else:
            sibs.setdefault(".a", bytearray())
            if isinstance(sibs[".a"], bytearray):
                sibs[".a"].extend(mip_data)
            else:
                sibs[".a"] = mip_data
        w = max(1, w // 2)
        h = max(1, h // 2)
    if ".a" in sibs and isinstance(sibs[".a"], bytearray):
        sibs[".a"] = bytes(sibs[".a"])
    return dds, sibs


def make_diff_stub(size: int = STUB_SIZE) -> tuple[bytes, dict[str, bytes]]:
    dds, sibs = make_split_cry_dds(
        size,
        size,
        dxgi_format=DXGI_BC1_UNORM,
        block_size=8,
        block=bc1_block_flat(),
        mip_count=10,
    )
    return dds, sibs


def make_displ_stub(size: int = STUB_SIZE) -> tuple[bytes, dict[str, bytes]]:
    dds, sibs = make_split_cry_dds(
        size,
        size,
        dxgi_format=DXGI_BC4_UNORM,
        block_size=8,
        block=bc4_block_flat(),
        mip_count=10,
    )
    return dds, sibs


def make_data_stub(size: int = STUB_SIZE) -> tuple[bytes, dict[str, bytes]]:
    pixel = struct.pack("<BBBB", 128, 128, 128, 255)
    payload = pixel * (size * size)
    header = cry_dds_header(
        size,
        size,
        dxgi_format=DXGI_R8G8B8A8_UNORM,
        mip_count=1,
    )
    return header + payload, {}


def pick_stub(path: str) -> tuple[bytes, dict[str, bytes]]:
    name = path.lower()
    if "_ddna" in name or name.endswith("_ddn.dds"):
        return make_ddna_stub()
    if "_displ" in name:
        return make_displ_stub()
    if (
        "_data" in name
        or "soil_amount" in name
        or "geo_soil" in name
        or "_color" in name
        or "_grad" in name
    ):
        return make_data_stub()
    if "_diff" in name:
        return make_diff_stub()
    return make_diff_stub()


def make_flat_tiff(width: int = STUB_SIZE, height: int = STUB_SIZE) -> bytes:
    """Minimal grayscale TIFF (elevation stub)."""
    row_bytes = width
    img = bytes([128] * (width * height))
    # IFD: width, height, 8-bit gray, single strip
    ifd = bytearray()
    entries = [
        (256, 3, 1, width),
        (257, 3, 1, height),
        (258, 3, 1, 8),
        (259, 3, 1, 1),  # no compression
        (262, 3, 1, 1),  # min-is-black
        (273, 4, 1, 0),  # strip offset patched below
        (278, 3, 1, height),
        (279, 4, 1, len(img)),
        (284, 3, 1, 1),
    ]
    header = b"II\x2a\x00" + struct.pack("<I", 8)
    ifd_offset = len(header)
    strip_offset = ifd_offset + 2 + len(entries) * 12 + 4
    for tag, typ, count, val in entries:
        if tag == 273:
            val = strip_offset
        ifd.extend(struct.pack("<HHII", tag, typ, count, val))
    ifd.extend(b"\x00\x00\x00\x00")
    return header + ifd + img


def parse_texture_paths(log_text: str) -> tuple[list[str], list[str]]:
    dds: set[str] = set()
    tif: set[str] = set()
    for m in re.finditer(r"File=([^\]]+\.(?:dds|tif))\]", log_text, re.I):
        p = m.group(1).replace("\\", "/").lower()
        if p.endswith(".dds"):
            dds.add(p)
        else:
            tif.add(p)
    return sorted(dds), sorted(tif)


def write_texture(out_root: Path, rel: str, force: bool) -> bool:
    dest = out_root / rel.replace("/", "\\")
    if dest.exists() and not force:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)

    if rel.endswith(".tif"):
        dest.write_bytes(make_flat_tiff())
        return True

    dds, siblings = pick_stub(rel)
    dest.write_bytes(dds)
    for suffix, data in siblings.items():
        (out_root / f"{rel}{suffix}".replace("/", "\\")).write_bytes(data)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path, help="Game.log (or excerpt) to parse")
    parser.add_argument(
        "out_root",
        nargs="?",
        type=Path,
        default=Path(r"D:\RSI\StarCitizen\PTU\Data"),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing stub files",
    )
    args = parser.parse_args()

    text = args.log.read_text(encoding="utf-8", errors="replace")
    dds_paths, tif_paths = parse_texture_paths(text)
    if not dds_paths and not tif_paths:
        print("no texture paths found in log", file=sys.stderr)
        return 1

    created = 0
    skipped = 0
    for rel in dds_paths + tif_paths:
        dest = args.out_root / rel.replace("/", "\\")
        if dest.exists() and not args.force:
            skipped += 1
            continue
        write_texture(args.out_root, rel, force=True)
        created += 1

    print(
        f"wrote {created} texture(s), skipped {skipped} existing "
        f"({len(dds_paths)} dds, {len(tif_paths)} tif in log)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
