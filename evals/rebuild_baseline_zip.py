# -*- coding: utf-8 -*-
"""从 git 对象重建 d71f8c7 提交的 repo/factcheck/src/yjcheck 并打包 baseline zip。

纯 Python 实现 loose object + pack(object + ofs/ref delta) 读取。
"""
import hashlib
import struct
import zlib
from pathlib import Path

GIT_DIR = Path(r"C:\Users\xiaoliu\Desktop\Local github project\Large-Model-Automatic-Verification-Tool-for-Listed-Firms-Research-Reports-via-Structural-Opt.git\.git")
COMMIT = "d71f8c752138da3898208f82bb6c525a82f7d325"
OUT_ZIP = Path("data/v2/baseline/source-d71f8c7.zip")
_TYPE_NAMES = {1: "commit", 2: "tree", 3: "blob", 4: "tag"}


class GitReader:
    def __init__(self, git_dir: Path):
        self.git_dir = git_dir
        self._pack_cache = {}

    def read_object(self, sha: str) -> bytes | None:
        sha = sha.lower()
        loose = self.git_dir / "objects" / sha[:2] / sha[2:]
        if loose.is_file():
            return zlib.decompress(loose.read_bytes())
        # 在 pack 里找
        for idx_path in sorted((self.git_dir / "objects" / "pack").glob("*.idx")):
            pack_path = idx_path.with_suffix(".pack")
            offset = self._locate(idx_path, sha)
            if offset is not None:
                return self._read_packed(pack_path, offset, set())
        return None

    def _locate(self, idx_path: Path, sha: str) -> int | None:
        idx = idx_path.read_bytes()
        if idx[:4] != b"\xfftOc":
            return None
        want = bytes.fromhex(sha)
        n = struct.unpack(">I", idx[8 + 255 * 4:8 + 256 * 4])[0]
        base = 8 + 256 * 4
        lo, hi = 0, n
        while lo < hi:
            mid = (lo + hi) // 2
            cur = idx[base + mid * 20:base + (mid + 1) * 20]
            if cur == want:
                off_base = base + n * 20 + n * 4
                off = struct.unpack(">I", idx[off_base + mid * 4:off_base + (mid + 1) * 4])[0]
                if off & 0x80000000:
                    return None
                return off
            if cur < want:
                lo = mid + 1
            else:
                hi = mid
        return None

    def _read_packed(self, pack_path: Path, offset: int, stack: set) -> bytes:
        key = (str(pack_path), offset)
        if key in self._pack_cache:
            return self._pack_cache[key]
        data = pack_path.read_bytes()
        typ, size, pos = self._object_header(data, offset)
        if typ in (1, 2, 3, 4):
            d = zlib.decompressobj()
            content = d.decompress(data[pos:])
            result = f"{_TYPE_NAMES[typ]} {size}\0".encode() + content
            self._pack_cache[key] = result
            return result
        elif typ == 6:  # ofs-delta
            b = data[pos]
            pos += 1
            base_off = b & 0x7F
            while b & 0x80:
                b = data[pos]
                pos += 1
                base_off = ((base_off + 1) << 7) | (b & 0x7F)
            base_off = offset - base_off
            d = zlib.decompressobj()
            delta = d.decompress(data[pos:])
            if offset in stack:
                raise RuntimeError("delta 循环")
            base_obj = self._read_packed(pack_path, base_off, stack | {offset})
            base_type, base_content = parse_object(base_obj)
            result, dst_size = self._apply_delta(base_content, delta)
            full = f"{base_type} {dst_size}\0".encode() + result
            self._pack_cache[key] = full
            return full
        elif typ == 7:  # ref-delta
            base_sha = data[pos:pos + 20].hex()
            pos += 20
            d = zlib.decompressobj()
            delta = d.decompress(data[pos:])
            base_obj = self.read_object(base_sha)
            if base_obj is None:
                raise RuntimeError(f"ref-delta base 不可读 {base_sha}")
            base_type, base_content = parse_object(base_obj)
            result, dst_size = self._apply_delta(base_content, delta)
            full = f"{base_type} {dst_size}\0".encode() + result
            self._pack_cache[key] = full
            return full
        raise RuntimeError(f"未知对象类型 {typ}")

    @staticmethod
    def _object_header(data: bytes, off: int):
        b = data[off]
        typ = (b >> 4) & 0x7
        size = b & 0x0F
        shift = 4
        off += 1
        while b & 0x80:
            b = data[off]
            size |= (b & 0x7F) << shift
            shift += 7
            off += 1
        return typ, size, off

    @staticmethod
    def _apply_delta(base: bytes, delta: bytes) -> bytes:
        i = 0
        src_size, i = _read_varint(delta, i)
        dst_size, i = _read_varint(delta, i)
        out = bytearray()
        while i < len(delta):
            cmd = delta[i]
            i += 1
            if cmd & 0x80:  # copy
                cp_off = 0
                cp_size = 0
                if cmd & 0x01: cp_off |= delta[i]; i += 1
                if cmd & 0x02: cp_off |= delta[i] << 8; i += 1
                if cmd & 0x04: cp_off |= delta[i] << 16; i += 1
                if cmd & 0x08: cp_off |= delta[i] << 24; i += 1
                if cmd & 0x10: cp_size |= delta[i]; i += 1
                if cmd & 0x20: cp_size |= delta[i] << 8; i += 1
                if cmd & 0x40: cp_size |= delta[i] << 16; i += 1
                if cp_size == 0:
                    cp_size = 0x10000
                out += base[cp_off:cp_off + cp_size]
            elif cmd:  # insert cmd bytes
                out += delta[i:i + cmd]
                i += cmd
            else:
                raise RuntimeError("delta 指令 0")
        return bytes(out), dst_size


def _read_varint(data: bytes, i: int):
    val = 0
    shift = 0
    while True:
        b = data[i]
        i += 1
        val |= (b & 0x7F) << shift
        shift += 7
        if not (b & 0x80):
            return val, i


def parse_object(data: bytes):
    nul = data.index(b"\0")
    header = data[:nul].decode()
    typ, size = header.split(" ")
    return typ, data[nul + 1:nul + 1 + int(size)]


def main() -> int:
    reader = GitReader(GIT_DIR)
    commit_data = reader.read_object(COMMIT)
    typ, content = parse_object(commit_data)
    print("commit type:", typ)
    tree_sha = next(l[5:] for l in content.decode(errors="replace").splitlines() if l.startswith("tree "))
    print("root tree:", tree_sha)

    files = {}
    stack = [("", tree_sha)]
    while stack:
        prefix, sha = stack.pop()
        data = reader.read_object(sha)
        typ, content = parse_object(data)
        i = 0
        while i < len(content):
            sp = content.index(b" ", i)
            mode = content[i:sp].decode()
            nul = content.index(b"\0", sp)
            name = content[sp + 1:nul].decode()
            csha = content[nul + 1:nul + 21].hex()
            path = f"{prefix}/{name}" if prefix else name
            if mode == "40000":
                stack.append((path, csha))
            else:
                files[path] = (mode, csha)
            i = nul + 21

    target = sorted(p for p in files if p.startswith("repo/factcheck/src/yjcheck/"))
    print(f"repo/factcheck/src/yjcheck 文件数: {len(target)}")
    import zipfile
    OUT_ZIP.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in target:
            mode, sha = files[p]
            data = reader.read_object(sha)
            typ, blob = parse_object(data)
            zf.writestr(p, blob)
    print("写入:", OUT_ZIP, "大小", OUT_ZIP.stat().st_size)
    with zipfile.ZipFile(OUT_ZIP) as zf:
        for name in sorted(zf.namelist()):
            print("  ", name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())