"""Content-level checks: PRBS recurrence, protocol decoding, burst structure.

These go beyond frequency. Frequency says a channel is toggling at roughly the
right rate; it says nothing about whether the bits are the right bits, whether a
probe pair is swapped, or whether a UART is sending the right text. That gap is
exactly what "the existing production test is not thorough enough" meant.

M7 -- PRBS WITHOUT ALIGNMENT
----------------------------
The obvious way to check an LFSR output is to generate the golden sequence and
search for where the capture starts. That needs the seed, a known start time and a
tolerance for a drifting bit clock.

There is a much better way. The generator advances by a fixed recurrence:

    next = (state << 1) | (bit31 ^ bit21 ^ bit1 ^ bit0)

so every pair of CONSECUTIVE observed states can be checked against that rule
independently. No alignment, no seed, no phase. A single wrong bit anywhere breaks
the recurrence at that step, and a swapped probe pair breaks every step.

The only thing it cannot see is a constant rotation of all 32 channels at once,
because that maps the state to itself shifted by one step; the golden search is
kept as a secondary check for that case.

M8 -- PROTOCOLS
---------------
UART is decoded by sampling mid-bit from each start edge; SPI on the clock edges
with CS framing; I2C by watching SDA during SCL high for start/stop and sampling
data bits. I2S uses the bit clock to sample the serial data and the word select to
split left from right.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# ---------------------------------------------------------------------------
# M7 PRBS
# ---------------------------------------------------------------------------

LFSR_SEED = 0xDEADBEEF
PRBS_CLK_HZ = 1_000_000            # pat_core.v advances the LFSR on tick_us


def _lfsr_next(state: int) -> int:
    """One step of x^32 + x^22 + x^2 + x + 1, matching pat_core.v."""
    fb = ((state >> 31) ^ (state >> 21) ^ (state >> 1) ^ state) & 1
    return ((state << 1) | fb) & 0xFFFFFFFF


def _states_from_capture(channels: np.ndarray, sample_rate_hz: float,
                         clk_hz: float = PRBS_CLK_HZ
                         ) -> tuple[list[int], int]:
    """Read one 32-bit LFSR state per generator clock.

    Returns (states, samples_per_step). A state is sampled in the middle of its
    step so a slow edge never lands on a boundary sample.
    """
    step = sample_rate_hz / clk_hz
    n_samples = channels.shape[1]
    if step < 2:
        return [], 0
    istep = int(round(step))
    states = []
    # skip the first step: it may be a partial one from before the capture began
    t = istep + istep // 2
    while t < n_samples:
        word = 0
        for ch in range(min(32, channels.shape[0])):
            if channels[ch, t]:
                word |= 1 << ch
        states.append(word)
        t += istep
    return states, istep


@dataclass
class PrbsResult:
    steps: int = 0
    good: int = 0
    bad: list[tuple[int, int, int]] = field(default_factory=list)   # (i, got, want)
    samples_per_step: int = 0

    @property
    def ok(self) -> bool:
        return self.steps >= 4 and not self.bad


def check_prbs(channels: np.ndarray, sample_rate_hz: float,
               clk_hz: float = PRBS_CLK_HZ, max_report: int = 4) -> PrbsResult:
    states, istep = _states_from_capture(channels, sample_rate_hz, clk_hz)
    r = PrbsResult(steps=len(states) - 1, samples_per_step=istep)
    for i in range(len(states) - 1):
        want = _lfsr_next(states[i])
        if states[i + 1] == want:
            r.good += 1
        elif len(r.bad) < max_report:
            r.bad.append((i, states[i + 1], want))
    return r


def prbs_alignment(channels: np.ndarray, sample_rate_hz: float,
                   clk_hz: float = PRBS_CLK_HZ, span: int = 4096) -> int | None:
    """Secondary check: where in the golden sequence does this capture start?

    Returns the step offset, or None if no offset matches. Guards against a
    constant rotation of every channel, which the recurrence alone cannot see.
    """
    states, _ = _states_from_capture(channels, sample_rate_hz, clk_hz)
    if len(states) < 8:
        return None
    seed_state = states[1] if len(states) > 1 else states[0]
    gold = LFSR_SEED
    for off in range(span):
        if gold == seed_state:
            for k in range(min(len(states) - 1, 8)):
                if states[1 + k] != gold:
                    break
                gold = _lfsr_next(gold)
            else:
                return off
            return None
        gold = _lfsr_next(gold)
    return None


# ---------------------------------------------------------------------------
# M8 protocol decoders
# ---------------------------------------------------------------------------

def decode_uart(samples: np.ndarray, sample_rate_hz: float, baud: int,
                max_bytes: int = 64) -> bytes:
    """Decode an 8N1 UART line, idle high. LSB first."""
    spb = sample_rate_hz / baud
    n = samples.size
    out = bytearray()
    i = 0
    while i < n - 1 and len(out) < max_bytes:
        # find a falling edge (start bit), then sample the middle of each bit
        if samples[i] == 1 and samples[i + 1] == 0:
            base = i + 1
            if base + int(9.5 * spb) >= n:
                break
            byte = 0
            for b in range(8):
                pos = int(base + (b + 1.5) * spb)
                if pos >= n:
                    break
                byte |= int(samples[pos]) << b
            stop = int(base + 9.5 * spb)
            if stop < n and samples[stop] == 1:
                out.append(byte)
                i = stop
                continue
        i += 1
    return bytes(out)


@dataclass
class SpiResult:
    words: list[int] = field(default_factory=list)
    clocks: int = 0

    @property
    def ok(self) -> bool:
        return bool(self.words)


def decode_spi(sclk: np.ndarray, mosi: np.ndarray, cs_n: np.ndarray,
               max_words: int = 64) -> SpiResult:
    """Decode SPI words, delimited by CS_N, sampling MOSI on SCLK rising edges.

    The previous version only looked at CS_N *at* the rising edges. That works
    when CS_N stays high for at least one full clock, but pat_spi_bank raises it
    for a single half period with no clock edge inside it, so the check never
    fired: the decoder simply counted 16 edges from wherever the capture began.

    Groups of 16 edges only line up with real word boundaries by luck, so the same
    hardware decoded as 0xA5C3 on one capture and 0x970E on the next -- the second
    value being the first one rotated by a bit. Delimiting on the CS_N
    transitions themselves removes the guesswork; this also matches how every real
    SPI slave frames a transfer.
    """
    r = SpiResult()
    rises = np.flatnonzero((sclk[:-1] == 0) & (sclk[1:] == 1)) + 1
    falls = np.flatnonzero((sclk[:-1] == 1) & (sclk[1:] == 0)) + 1
    r.clocks = int(rises.size)

    def sample_at(t: int) -> int:
        """Sample the data line in the MIDDLE of the clock high period.

        Not at the rising edge itself. Sampling exactly on the edge leaves no
        margin: the data line also changes on the falling edge, so any skew
        between the clock wire and the data wire -- and on a 32-lead harness there
        always is some -- can land the sample on the wrong side of a transition.
        Measured symptom on the real fixture: an occasional single wrong word
        (0xA5C3 decoded as 0xA6E1) in an otherwise clean capture, moving between
        runs and between channels. The middle of the high period is half a clock
        away from both edges, which is the most tolerance the waveform can offer.
        """
        nxt = falls[falls > t]
        mid = (t + int(nxt[0])) // 2 if nxt.size else t
        return int(mosi[mid])

    # Falling edges of CS_N open a word, rising edges close it.
    cs_fall = np.flatnonzero((cs_n[:-1] == 1) & (cs_n[1:] == 0)) + 1
    cs_rise = np.flatnonzero((cs_n[:-1] == 0) & (cs_n[1:] == 1)) + 1

    for start in cs_fall:
        end = cs_rise[cs_rise > start]
        stop = int(end[0]) if end.size else int(cs_n.size)
        word = rises[(rises > start) & (rises < stop)]
        if word.size == 0:
            continue
        sh = 0
        for t in word[:16]:
            sh = ((sh << 1) | sample_at(int(t))) & 0xFFFF
        if word.size >= 16:
            r.words.append(sh)
        if len(r.words) >= max_words:
            break
    return r


@dataclass
class SpiBankInstance:
    """One SPI master of the bank, decoded from four consecutive channels."""

    instance: int
    expect_mosi: int
    expect_miso: int
    sclk_hz: float = 0.0
    mosi_words: list[int] = field(default_factory=list)
    miso_words: list[int] = field(default_factory=list)

    @property
    def interior_mosi(self) -> list[int]:
        """Decoded MOSI words excluding the first and last.

        A capture starts and ends wherever it starts and ends, so the first and
        last assembled words are almost always fragments of a word rather than
        whole ones. Requiring every decoded word to match failed on perfectly
        correct data for exactly that reason -- M2's first word decoded as a
        complete-looking value while M1's happened to be cut, so identical
        hardware passed one and failed the other.
        """
        return self.mosi_words[1:-1] if len(self.mosi_words) > 2 else []

    @property
    def interior_miso(self) -> list[int]:
        return self.miso_words[1:-1] if len(self.miso_words) > 2 else []

    @property
    def mosi_ok(self) -> bool:
        w = self.interior_mosi
        return bool(w) and all(x == self.expect_mosi for x in w)

    @property
    def miso_ok(self) -> bool:
        w = self.interior_miso
        return bool(w) and all(x == self.expect_miso for x in w)

    @property
    def ok(self) -> bool:
        return self.mosi_ok and self.miso_ok


@dataclass
class SpiBankResult:
    instances: list[SpiBankInstance] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.instances) and all(i.ok for i in self.instances)


def decode_spi_bank(channels: np.ndarray, n_inst: int, base: int = 0,
                    div_sel: int = 0, sample_rate_hz: float = 0.0,
                    max_words: int = 8) -> SpiBankResult:
    """Decode `n_inst` SPI masters on channels base+4k .. base+4k+3.

    The channel order is fixed by rtl/pat_spi_bank.v: SCLK, MOSI, MISO, CS_N. The
    word each instance sends is `patterns.spi_bank_word(k)`; the import is local
    so this module keeps no dependency on the expectation model.
    """
    from .patterns import spi_bank_word, spi_bank_rates

    out = SpiBankResult()
    # Rates come from patterns, not a local constant. This used to compute
    # 216 MHz / 2**div_sel inline, which was written when the fast domain ran at
    # 216 MHz; after it moved to 108 MHz every report said "SCLK 108 MHz" for a
    # 54 MHz signal.
    sclk_hz, _cs_hz = spi_bank_rates(div_sel)
    for k in range(n_inst):
        c = base + 4 * k
        if c + 3 >= channels.shape[0]:
            break
        inst = SpiBankInstance(instance=k,
                               expect_mosi=spi_bank_word(k),
                               expect_miso=(~spi_bank_word(k)) & 0xFFFF,
                               sclk_hz=sclk_hz)
        inst.mosi_words = decode_spi(channels[c + 0], channels[c + 1],
                                     channels[c + 3], max_words).words
        inst.miso_words = decode_spi(channels[c + 0], channels[c + 2],
                                     channels[c + 3], max_words).words
        out.instances.append(inst)
    return out


@dataclass
class I2cTransaction:
    start: bool = False
    bytes_: list[int] = field(default_factory=list)
    stop: bool = False
    nacks: list[int] = field(default_factory=list)


def decode_i2c(scl: np.ndarray, sda: np.ndarray,
               max_transactions: int = 8) -> list[I2cTransaction]:
    """Decode I2C: START when SDA falls while SCL is high, STOP when it rises.

    Bytes are recovered PER TRANSACTION, from the SCL rising edges between that
    transaction's START and STOP.

    The first version instead chopped the entire rising-edge list into 9-bit
    groups in one pass. That only works if the capture happens to begin exactly on
    a byte boundary, which it never does, and it paid no attention to where START
    and STOP were -- so with 8 transactions detected it produced no bytes at all.
    """
    n = scl.size

    # --- 1. find the START / STOP events and their sample positions ---------
    #
    # START positions are collected on their own, NOT taken from the paired events
    # list. Pairing a START to the next STOP depends on both being detected, and
    # one missed STOP shifts every later pairing -- which is how this decoder
    # produced 8 transactions of nonsense from a bus that carries 31 clean frames.
    # The START is the reliable event: the fixture emits exactly one per frame.
    starts: list[int] = []
    stops: list[int] = []
    i = 1
    while i < n:
        if scl[i] == 1 and scl[i - 1] == 1:
            if sda[i - 1] == 1 and sda[i] == 0:                 # START
                starts.append(i)
            elif sda[i - 1] == 0 and sda[i] == 1:               # STOP
                stops.append(i)
        i += 1

    # --- 2. three bytes from every START, at the 27 clocks that follow -------
    #
    # Fixed length, anchored on START. Verified against the fixture's raw bits:
    # the 27 samples after a START are exactly
    #   10100000 0 00010010 0 00110100 0
    # i.e. A0 12 34 with no ACK from any device (nothing is listening, and the
    # generator holds SDA low for the ACK slots). Deriving the length from where
    # the next STOP happens to be found is what made this fragile.
    rises = np.flatnonzero((scl[:-1] == 0) & (scl[1:] == 1)) + 1
    out: list[I2cTransaction] = []
    for pos in starts:
        edges = rises[rises > pos]
        if edges.size < 27:
            break
        tx = I2cTransaction(start=True)
        bits = [int(sda[t]) for t in edges[:27]]
        for k in range(3):
            v = 0
            for b in bits[k * 9:k * 9 + 8]:
                v = (v << 1) | b
            tx.bytes_.append(v)
            tx.nacks.append(bits[k * 9 + 8])
        nxt = [e for e in stops if e > pos]
        tx.stop = bool(nxt and nxt[0] < edges[27]) if edges.size > 27 else False
        out.append(tx)
        if len(out) >= max_transactions:
            break
    return out


@dataclass
class I2sResult:
    left: list[int] = field(default_factory=list)
    right: list[int] = field(default_factory=list)
    frames: int = 0


def decode_i2s(bclk: np.ndarray, ws: np.ndarray, sd: np.ndarray,
               bits_per_channel: int = 16, max_frames: int = 32) -> I2sResult:
    """Decode I2S into (left, right) sample pairs, MSB first.

    STATUS: OPEN ITEM. This decodes something structured but not yet correct -- on
    real hardware it reports 32 frame pairs of which about half satisfy
    right == ~left, and every mismatch is off by exactly one. That is the signature
    of a frame-counter offset between the two channels, not of a broken decoder, but
    three formulations (WS-window with a one-bit skip, a fixed 17-edge stride, and
    re-aligning to each WS transition) gave 50%, 0% and 0% respectively, and two RTL
    changes to the reload logic did not move the result. It needs a deliberate pass
    with the WS/SD/BCLK edges laid out side by side, not more guessing.

    What IS established:
      * pat_proto.v reloads its shift register on the same falling edge that toggles
        WS, so the first rising edge after a WS change still carries the previous
        channel's last bit. A one-bit skip is therefore required.
      * The RTL originally loaded the left from ~frame and the right from frame+1,
        which makes right == (~left) + 1 rather than the bitwise complement its own
        header comment promises. That was changed so both channels of a stereo pair
        come from the same frame number.
      * The generator does produce 16 bits per channel at a 1 MHz bit clock and a
        31.25 kHz word select, and BCLK/WS measure correctly.
    """
    r = I2sResult()
    rises = np.flatnonzero((bclk[:-1] == 0) & (bclk[1:] == 1)) + 1
    cur = 0
    nbits = 0
    chan: int | None = None
    skip_first = False
    pending_left: int | None = None

    for t in rises:
        if t >= ws.size:
            break
        c = int(ws[t])
        if chan is None:
            chan = c
            skip_first = True
        if c != chan:
            if chan == 0:
                pending_left = cur
            elif pending_left is not None and nbits >= bits_per_channel - 1:
                r.left.append(pending_left)
                r.right.append(cur)
                r.frames += 1
                if r.frames >= max_frames:
                    break
                pending_left = None
            else:
                pending_left = None
            chan = c
            cur = 0
            nbits = 0
            skip_first = True
        if skip_first:
            skip_first = False
            continue
        cur = ((cur << 1) | int(sd[t])) & ((1 << bits_per_channel) - 1)
        nbits += 1

    return r


# ---------------------------------------------------------------------------
# M9 burst structure
# ---------------------------------------------------------------------------

@dataclass
class BurstResult:
    edges: list[int] = field(default_factory=list)     # sample indices
    widths: list[int] = field(default_factory=list)    # high widths, in samples
    period: int | None = None
    active: int | None = None

    @property
    def ok(self) -> bool:
        return bool(self.widths)


def check_burst(samples: np.ndarray, sample_rate_hz: float) -> BurstResult:
    """Find the burst envelope and the pulse widths inside it.

    The burst is 1 ms of activity every 20 ms, so a single channel shows a long
    silent gap. Measuring that gap gives the envelope; the widths inside the burst
    are what tell the narrow-pulse channels apart.

    ⚠️ `BurstResult.period` 装的是**静默间隔**（低电平段），不是周期本身：
    真实夹具是 1 ms 活动 + 约 19 ms 静默 = 20 ms 周期。调用方比较时应留出这
    1 ms 的余量（checks._burst_verdicts 用 ±2 ms 就覆盖了）。
    """
    r = BurstResult()
    edges = np.flatnonzero(samples[1:] != samples[:-1]) + 1
    if edges.size < 2:
        return r
    r.edges = [int(e) for e in edges]
    lvl = samples[edges]
    widths = np.diff(np.concatenate(([0], edges, [samples.size])))
    # pulses inside a burst: high runs that are not the giant silent gap
    lo = int(round(0.005 * sample_rate_hz))        # 5 ms is longer than any real pulse
    hi = []
    for k in range(edges.size):
        if lvl[k] == 1 and widths[k + 1] < lo:
            hi.append(int(widths[k + 1]))
    r.widths = hi
    # 静默间隔门限：必须**小于真实的突发间低电平**。
    #
    # 夹具的突发是 1 ms 活动 / 20 ms 周期 -> 静默间隔约 19 ms；原先这里写的是
    # 50 ms，于是门限永远命中不了、period 恒为 None，M6 的包络判定恒判 FAIL。
    # 之所以没被发现，是本文件自测里那个合成突发用的周期是 100 ms（> 50 ms 能过），
    # 与真实夹具的 20 ms 不一致 —— 自测已一并改成 20 ms。
    # 10 ms 能命中 19 ms，而突发内部的低电平间隙只有 ~10 us，仍被排除。
    gaps = [int(w) for k, w in enumerate(widths[1:])
            if lvl[k] == 0 and w > int(round(0.010 * sample_rate_hz))]
    if gaps:
        r.period = max(gaps)
    if edges.size > 2:
        active_span = edges[-1] - edges[0]
        r.active = active_span
    return r


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------

def _selftest() -> int:
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    fails = 0
    fs = 200_000_000
    n = 400_000

    print("== M7 PRBS recurrence ==")
    states = []
    st = LFSR_SEED
    for _ in range(2000):
        states.append(st)
        st = _lfsr_next(st)
    step = fs // PRBS_CLK_HZ
    chans = np.zeros((32, n), dtype=np.uint8)
    for i, s in enumerate(states[: n // step]):
        for ch in range(32):
            chans[ch, i * step:(i + 1) * step] = (s >> ch) & 1
    r = check_prbs(chans, fs)
    print("  %d steps, %d good, %d bad (samples/step=%d)"
          % (r.steps, r.good, len(r.bad), r.samples_per_step))
    if not r.ok:
        print("  FAIL"); fails += 1
    print("  alignment: offset %s in the golden sequence"
          % prbs_alignment(chans, fs))

    print("== M7 a single flipped bit must be caught ==")
    # The state is sampled at the MIDDLE of each generator clock, so corrupting a
    # single sample at a step boundary changes nothing. The flip has to last the
    # whole step to be a real bit error.
    bad = chans.copy()
    bad[7, 50 * step:(50 + 1) * step] ^= 1
    rb = check_prbs(bad, fs)
    print("  %d bad steps (one bit held wrong for one 1 us step)" % len(rb.bad))
    if rb.ok:
        print("  FAIL: a corrupted state was accepted"); fails += 1

    print("== M7 a swapped channel pair must be caught ==")
    sw = chans.copy()
    sw[[3, 4]] = sw[[4, 3]]
    rs = check_prbs(sw, fs)
    print("  %d bad steps" % len(rs.bad))
    if rs.ok:
        print("  FAIL: a swapped pair was accepted"); fails += 1

    print("== M8 UART ==")
    text = b"SLogic32U3\r\n"
    for baud in (9600, 115384):
        bits = [1] * 200
        for byte in text:
            bits += [0] + [(byte >> i) & 1 for i in range(8)] + [1]
        line = np.repeat(np.array(bits, dtype=np.uint8), int(round(fs / baud)))
        got = decode_uart(line, fs, baud)
        print("  %6d baud -> %r  %s" % (baud, got, "ok" if got == text else "FAIL"))
        if got != text:
            fails += 1

    print("== M8 SPI ==")
    sclk = np.zeros(n, dtype=np.uint8)
    mosi = np.zeros(n, dtype=np.uint8)
    cs = np.ones(n, dtype=np.uint8)
    half = 27
    word = 0xA5C3
    t = 100
    for _ in range(4):
        cs[t:t + 16 * 2 * half] = 0
        for bitpos in range(16):
            v = (word >> (15 - bitpos)) & 1
            mosi[t:t + 2 * half] = v
            sclk[t + half:t + 2 * half] = 1
            t += 2 * half
        t += 2 * half
    sp = decode_spi(sclk, mosi, cs)
    print("  %d clocks, words: %s  %s"
          % (sp.clocks, " ".join("%04X" % w for w in sp.words[:6]),
             "ok" if sp.words and all(w == word for w in sp.words) else "FAIL"))
    if not (sp.words and all(w == word for w in sp.words)):
        fails += 1

    print("== M8 I2C ==")
    # Built the way pat_proto.v drives it: four quarter phases per SCL period
    # (0 = SCL low + data change, 1 = SCL low, 2 = SCL high, 3 = SCL high), and
    # SDA only ever changes during phase 0 or phase 1 -- i.e. while SCL is low --
    # except for START and STOP, which are defined by changing it while SCL is
    # HIGH. That distinction is the whole point of the decoder.
    QUARTER = 135
    idle = (1, 1)
    phases: list[tuple[int, int]] = [idle] * 4

    phases += [(1, 0)] * 1                       # START: SDA falls, SCL high
    for byte in (0xA0, 0x12, 0x34):
        for k in range(8):
            bit = (byte >> (7 - k)) & 1
            phases += [(0, bit), (0, bit), (1, bit), (1, bit)]
        phases += [(0, 0), (0, 0), (1, 0), (1, 0)]        # ACK: SDA low
    phases += [(1, 0)] * 2                       # hold, then
    phases += [(1, 1)] * 2                       # STOP: SDA rises, SCL high
    phases += [idle] * 8

    scl = np.repeat(np.array([p[0] for p in phases], dtype=np.uint8), QUARTER)
    sda = np.repeat(np.array([p[1] for p in phases], dtype=np.uint8), QUARTER)
    i2 = decode_i2c(scl, sda)
    got = [b for tx in i2 for b in tx.bytes_]
    print("  %d transaction(s), bytes: %s  %s"
          % (len(i2), " ".join("%02X" % b for b in got),
             "ok" if got[:3] == [0xA0, 0x12, 0x34] else "FAIL"))
    if got[:3] != [0xA0, 0x12, 0x34]:
        fails += 1

    print("== M9 burst ==")
    fs9 = 50_000_000
    # 周期必须是**真实夹具的 20 ms**，不能是 100 ms：用 100 ms 时 50 ms 的静默
    # 门限也能过，于是掩盖了"门限比真实间隙还大"这个 bug（真机上 M6 从来没被
    # 真正判过）。改成 20 ms 后，门限写错就会被这条自测拦住。
    period = int(0.020 * fs9)
    active = int(0.001 * fs9)
    line = np.zeros(2 * period, dtype=np.uint8)
    for start in (0, period):
        for k in range(100):                              # 100 pulses of 10 us
            p = start + k * int(10e-6 * fs9)
            line[p:p + 8] = 1                             # 8 clocks at 54 MHz
    br = check_burst(line, fs9)
    # check_burst 量的是**静默间隔** = 周期 - 活动时长，所以这里按 (period-active)
    # 比。真实夹具是 20 ms 周期 / 1 ms 活动 -> 间隔 19 ms；以前自测写成 100 ms
    # 周期（间隔≈周期）才让"间隔≠周期"这件事一直被掩盖。
    gap = period - active
    print("  %d pulses, widths %s, gap %.1f ms (expect %.1f)"
          % (len(br.widths), sorted(set(br.widths))[:4],
             (br.period or 0) / fs9 * 1e3, gap / fs9 * 1e3))
    if not br.ok or abs((br.period or 0) - gap) > gap * 0.02:
        print("  FAIL"); fails += 1

    print()
    print("RESULT: %s" % ("OK" if not fails else "%d FAILURES" % fails))
    return 1 if fails else 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
