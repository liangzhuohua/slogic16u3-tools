"""Waveform unpacking, generic over channel count.

Data format: sigrok-cli `-O binary` output.  Each sample is `unitsize`
bytes little-endian, where unitsize = ceil(device_total_channels / 8) --
the device's native width, regardless of how many channels were enabled
for the capture (verified empirically on SLogic16U3: 8-channel capture
still yields 2 bytes/sample).  The stream may carry textual
`FRAME-BEGIN\\n` / `FRAME-END\\n` markers which must be stripped.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

FRAME_MARKERS = (b"FRAME-BEGIN\n", b"FRAME-END\n")


def strip_frame_markers(data: bytes) -> bytes:
    for marker in FRAME_MARKERS:
        if marker in data:
            data = data.replace(marker, b"")
    return data


def extract_channels(data: bytes, num_channels: int,
                     unitsize: int | None = None) -> np.ndarray:
    """Unpack packed samples into per-channel bit arrays.

    Returns array of shape (num_channels, n_samples), values 0/1.
    `unitsize` defaults to ceil(num_channels/8); pass the device unitsize
    explicitly when verifying a channel subset (e.g. 8 of 16 channels).
    """
    if unitsize is None:
        unitsize = (num_channels + 7) // 8
    if num_channels > unitsize * 8:
        raise ValueError(f"num_channels={num_channels} 超出 unitsize={unitsize} 容量")
    n = len(data) // unitsize
    if n == 0:
        raise ValueError("数据为空或不足一个样本")
    arr = np.frombuffer(data, dtype=np.uint8, count=n * unitsize).reshape(n, unitsize)
    # One channel at a time (little-endian bit/byte order): unpacking every
    # bit and transposing would allocate two n*unitsize*8 temporaries -- over
    # 500 MB for a long 32-channel capture.
    out = np.empty((num_channels, n), dtype=np.uint8)
    for ch in range(num_channels):
        np.bitwise_and(np.right_shift(arr[:, ch >> 3], ch & 7), 1, out=out[ch])
    return out


def load_capture_file(path: Path, num_channels: int, unitsize: int) -> np.ndarray:
    return extract_channels(strip_frame_markers(Path(path).read_bytes()),
                            num_channels, unitsize)


def apply_channel_order(channels: np.ndarray,
                        order: tuple[int, ...] | list[int]) -> np.ndarray:
    """Return the capture with the wiring permutation undone.

    `order[g]` is the analyser input that generator channel g reaches, so the
    corrected array is `captured[order[g]]` at row g.

    This exists because the harness is hand-wired and the two 16-channel probe
    groups can end up crossed -- measured on the real fixture as generator
    CH0..CH7 arriving at analyser D16..D23 and CH16..CH23 at D0..D7. The
    alternative is rewiring, which is the right long-term fix; until the cable can
    be replaced, this keeps the test meaningful.

    It is deliberately data, in the product profile, and not a constant in code:
    a permutation that silently disagrees with the harness turns a wiring fault
    into a PASS, so it has to be visible where the rest of the product is
    described. Nothing here validates it -- M0 and M4 do, because the rotation
    order and the per-instance SPI words are both wrong under a bad permutation.
    """
    order = list(order)
    if len(order) < channels.shape[0]:
        return channels
    return channels[np.asarray(order[:channels.shape[0]], dtype=int)]


def _selftest() -> int:
    print("== waveform 自测：合成方波打包后逐通道拆回 ==")
    n = 10_000
    rng = np.random.default_rng(1)
    failures = 0
    for num_ch, unitsize in ((4, 1), (8, 1), (16, 2), (32, 4)):
        bits = rng.integers(0, 2, size=(num_ch, n), dtype=np.uint8)
        words = np.zeros(n, dtype=np.uint64)
        for ch in range(num_ch):
            words |= bits[ch].astype(np.uint64) << np.uint64(ch)
        packed = words.astype(f"<u{unitsize}").tobytes()
        ok = np.array_equal(extract_channels(packed, num_ch, unitsize), bits)
        print(f"  {num_ch:>2}ch unitsize={unitsize}: {'PASS' if ok else 'FAIL'}")
        failures += 0 if ok else 1
        # an 8-channel capture of a 32-channel device still has unitsize 4
        if unitsize == 4:
            ok = np.array_equal(extract_channels(packed, 8, 4), bits[:8])
            print(f"   8ch of unitsize=4: {'PASS' if ok else 'FAIL'}")
            failures += 0 if ok else 1
    stripped = strip_frame_markers(b"FRAME-BEGIN\n" + b"\x01\x02" + b"FRAME-END\n")
    ok = stripped == b"\x01\x02"
    print(f"  FRAME 标记剥离: {'PASS' if ok else 'FAIL'}")
    failures += 0 if ok else 1
    chans = np.arange(4)[:, None] * np.ones((1, 3), dtype=int)
    ok = apply_channel_order(chans, [2, 0, 3, 1])[:, 0].tolist() == [2, 0, 3, 1]
    print(f"  通道置换: {'PASS' if ok else 'FAIL'}")
    failures += 0 if ok else 1
    return failures


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
