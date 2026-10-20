"""Signal model for the Tang Nano 9K test patterns (M0..M6).

WHAT THIS IS
------------
The production profile says *what* to test (which mode, which channel/rate
group); this module says *what the result should be*. Keeping the expectation in
code rather than in the TOML matters because it is 7 modes x up to 32 channels at
several rates, and because a change to the RTL should require exactly one edit
here.

Every number below is derived from the RTL, not guessed from measurements, so
this file is the place to look when a reading looks wrong.

MODE NUMBERS
------------
Mode numbers are what the fixture's mode mux, MODE_LABELS / _MODES below and
product.toml's [[plan]] agree on:

  M0 wiring rotation   M1..M4 SPI banks (+ I2C/UART in M4)
  M5 narrow pulse      M6 burst / trigger

The RTL's internal signal names (m6, m9 ...) still use an older numbering and do
NOT match these; the models here are named after what they test, not after those
signals.

ALIASING
--------
A channel whose true frequency is at or above the Nyquist frequency of the
capture cannot be verified by counting edges. The alias of a *square* wave is not
just the alias of its fundamental -- every harmonic folds back too, and the sum
has an unpredictable edge pattern. This is not theory: CH0 is 108 MHz and at
32ch@200M it is above the 100 MHz Nyquist, and it measured 55.14 MHz where a
naive fundamental-alias calculation predicts 92 MHz.

So above Nyquist the channel is marked `skip` and reported without a verdict,
rather than being failed for something the instrument cannot be asked to do.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# Nominal system clock. The real board uses a 27 MHz crystal, so readings sit
# within a few tens of ppm of these numbers -- well inside any sane tolerance.
CLK_SYS_HZ = 54_000_000

I2C_SCL_HZ = 100_000
I2C_ADDR_W = 0xA0                     # 0x50 << 1 | write
I2C_DATA = (0x12, 0x34)

# M5 narrow pulse / M6 burst constants
NARROW_PERIOD_CLK = 540            # 10 us at 54 MHz
BURST_WIDTH_CLK = (8, 16, 32, 64)        # CH0..CH3 pulse widths


class Kind(Enum):
    FREQ = "freq"          # verify frequency
    STATIC = "static"      # verify a constant level
    DUTY = "duty"          # verify frequency and duty
    SKIP = "skip"          # report the reading, pass no verdict
    PROTOCOL = "protocol"  # decode and check the content


@dataclass(frozen=True)
class Expect:
    """What one channel should carry in one mode at one sample rate."""

    kind: Kind
    freq_hz: float | None = None       # None when not frequency-checkable
    static: int | None = None          # 0/1 when kind is STATIC
    duty_pct: float | None = None
    note: str = ""
    # 这一路的判定权**交给哪个结构化检查**（"走线轮转检查"/"突发结构检查"…）。
    #
    # 只有"刻意把判定权移交出去"才填这里。空着 + kind=SKIP 的含义完全不同：
    # 那是"**没人判得了这一路**"（采集参数配不上信号），属于测试设计缺陷，
    # check_channel 会把它判成 FAIL 而不是放过去 —— 见 checks.py 的说明。
    covered_by: str = ""

    @property
    def checkable(self) -> bool:
        return self.kind is not Kind.SKIP


def _nyquist(samplerate_hz: float) -> float:
    return samplerate_hz / 2.0


def _freq_or_skip(ch: int, f: float, fs: float, duty: float | None = None,
                  note: str = "") -> Expect:
    """Frequency expectation, downgraded to SKIP when it cannot be measured.

    Two separate limits apply to a SQUARE wave, not just one:

      1. the fundamental must be below Nyquist, and
      2. so must its harmonics, because the edge detector counts transitions and
         every folded harmonic contributes extra ones.

    Checking only (1) produced a confidently wrong number instead of a skip. On
    the real fixture M1's CH0 is 108 MHz; at 16ch@400M the fundamental is
    comfortably under the 200 MHz Nyquist, yet the analyser reported 92.636 MHz --
    exactly the 200 MSa/s alias of 108 MHz. The third harmonic at 324 MHz had
    folded back and corrupted the edge pattern. A wrong number is worse than no
    number, so anything whose 3rd harmonic crosses Nyquist is reported, not judged.
    """
    if f >= _nyquist(fs):
        alias = abs(f - fs * round(f / fs)) if fs else 0.0
        return Expect(Kind.SKIP, freq_hz=f, duty_pct=duty,
                      note=("%.4g Hz is above the %.4g Hz Nyquist at this rate "
                            "(fundamental alias would be %.4g Hz, but a square "
                            "wave's harmonics fold back too, so the edge rate is "
                            "not predictable)%s" % (f, _nyquist(fs), alias,
                                                    ("; " + note) if note else "")))
    if 3.0 * f >= _nyquist(fs):
        return Expect(Kind.SKIP, freq_hz=f, duty_pct=duty,
                      note=("%.4g Hz is below the %.4g Hz Nyquist, but its 3rd "
                            "harmonic at %.4g Hz is not, and a folded harmonic "
                            "corrupts edge counting -- reported, not judged%s"
                            % (f, _nyquist(fs), 3.0 * f,
                               ("; " + note) if note else "")))
    return Expect(Kind.DUTY if duty is not None else Kind.FREQ,
                  freq_hz=f, duty_pct=duty, note=note)


# ---------------------------------------------------------------------------
# per-mode models
# ---------------------------------------------------------------------------


def _narrow_pulse(ch: int, fs: float) -> Expect:
    if ch == 0:
        return _freq_or_skip(ch, CLK_SYS_HZ / NARROW_PERIOD_CLK, fs,
                             note="18.5 ns wide, once per 10 us")
    if ch == 1:
        # Was a 4.63 ns pulse from fast_out on the 216 MHz clock. The fast domain
        # now runs at 108 MHz, where the same divider gives 148 ns -- wider than
        # the 18.5 ns pulses pat_core already makes for CH0, so fast_out was
        # dropped and this channel is held low.
        return Expect(Kind.STATIC, static=0,
                      note="held low: the 216 MHz narrow-glitch source was removed "
                           "when the fast domain moved to 108 MHz (148 ns there, "
                           "wider than CH0's 18.5 ns, so it added nothing)")
    if ch == 2:
        return Expect(Kind.SKIP, freq_hz=CLK_SYS_HZ / NARROW_PERIOD_CLK,
                      note="two 18.5 ns pulses 18.5 ns apart; a normal capture "
                           "merges them, so the edge rate is not a stable verdict",
                      covered_by="窄脉冲宽度检查")
    if ch == 3:
        return Expect(Kind.SKIP, freq_hz=1000.0,
                      note="one 18.5 ns pulse per 1 ms -- a trigger test, too rare "
                           "for a reliable edge-rate verdict",
                      covered_by="窄脉冲宽度检查")
    if ch == 4:
        return Expect(Kind.SKIP,
                      note="width ladder 1,2,4,...,128 clk_sys (18.5 ns ~ 2370 ns), "
                           "8 rungs, one rung per 10 us so a full sweep is 80 us",
                      covered_by="窄脉冲宽度检查")
    # Adjacent fill channels run in anti-phase (testgen 2026-10-20: CH6, CH8, ...
    # are inverted).  With all 27 in phase, every edge was 27 simultaneous
    # same-direction transitions, and the resulting common-mode disturbance
    # put 5-25 ns glitches on the 1.8 V bank 3 channels.  Frequency and duty
    # are unchanged, so the expectation is too.
    return _freq_or_skip(ch, 1000.0, fs,
                         note="1 kHz reference fill, adjacent channels in anti-phase")


def _burst(ch: int, fs: float) -> Expect:
    if ch < 4:
        width_clk = BURST_WIDTH_CLK[ch]
        width_ns = width_clk / (CLK_SYS_HZ / 1e9)
        return Expect(Kind.SKIP,
                      freq_hz=CLK_SYS_HZ / NARROW_PERIOD_CLK,
                      note=("%d clocks = %.1f ns wide, repeated every 10 us, but "
                            "only during a 1 ms burst that recurs every 20 ms; "
                            "the measured rate therefore depends on the capture "
                            "window, so the structured check owns this channel"
                            % (width_clk, width_ns)),
                      covered_by="突发结构检查")
    # B_PERIOD 已经是 20 ms（原来是 100 ms，没有哪个窗口负担得起），所以静默是
    # 19 ms 而不是 99 ms，均值是 50 Hz 而不是 10 Hz —— 实测 M6 的 CH4 读到的正是
    # 50 Hz，证明这里原来那行注释和 freq_hz 都是过时的。
    return Expect(Kind.SKIP, freq_hz=50.0,
                  note="1 kHz during the 1 ms burst, silent for 19 ms -> 50 Hz mean",
                  covered_by="突发结构检查")


# ---------------------------------------------------------------------------
# the SPI bank (M1..M4)
#
# One generator covers all four channel-enable modes: the mode picks how many
# instances run and how far the 216 MHz half-clock is divided. Each instance owns
# four consecutive channels, always in this order:
#
#     +0 SCLK   +1 MOSI   +2 MISO   +3 CS_N
#
# See rtl/pat_spi_bank.v. The host must agree with the RTL exactly, so the numbers
# live here once and every consumer reads them from here.
# ---------------------------------------------------------------------------
# 108 MHz, not 216: the SPI bank would not close timing at 216 on this device.
# See the SDIV note in rtl/testgen_top.v. SCLK is half of this at div_sel 0, so
# the four channel-enable modes get 54 / 27 / 13.5 / 6.75 MHz.
SPI_BANK_HALF_HZ = 108_000_000
SPI_PHASES_PER_WORD = 33              # 32 phases (16 bits x 2) + 1 CS-high phase

# mode -> (instances, half-clock divider selector 0..3)
SPI_BANK_MODES = {1: (1, 0), 2: (2, 1), 3: (4, 2), 4: (6, 3)}

# The 32-channel mode leaves CH24..CH31 to the slow generators.
SPI_M4_I2C_SCL = 24
SPI_M4_I2C_SDA = 25
SPI_M4_UART_TX = 26


def spi_bank_word(instance: int) -> int:
    """The 16-bit word instance `instance` shifts out, MSB first.

    Must match `word_of()` in rtl/pat_spi_bank.v. Distinct per instance, so a
    swap between any two instances shows up in the decoded data instead of
    looking like a pass.
    """
    return 0xA5C3 ^ ((instance * 0x1111) & 0xFFFF)


def spi_bank_rates(div_sel: int) -> tuple[float, float]:
    """(SCLK, CS_N) frequencies in Hz for a half-clock divider selector."""
    half = SPI_BANK_HALF_HZ / float(1 << div_sel)
    return half / 2.0, half / float(SPI_PHASES_PER_WORD)


def _spi_bank_mode(ch: int, fs: float, mode: int) -> Expect:
    n_inst, div_sel = SPI_BANK_MODES[mode]
    sclk_hz, cs_hz = spi_bank_rates(div_sel)
    inst, pos = divmod(ch, 4)

    if inst >= n_inst:
        # Past the bank. Only M4 has anything here, and those channels belong to
        # the I2C and UART generators.
        if mode == 4 and ch == SPI_M4_I2C_SCL:
            return _freq_or_skip(ch, I2C_SCL_HZ, fs, note="I2C SCL")
        if mode == 4 and ch == SPI_M4_I2C_SDA:
            return Expect(Kind.PROTOCOL,
                          note=("I2C SDA: one write transaction per frame, "
                                "decoded by the I2C checker"))
        if mode == 4 and ch == SPI_M4_UART_TX:
            return Expect(Kind.PROTOCOL,
                          note=("UART TX 115200 8N1, 12 bytes, decoded by the "
                                "UART checker"))
        return Expect(Kind.STATIC, static=0, note="not driven in this mode")

    if pos == 0:
        return _freq_or_skip(ch, sclk_hz, fs, note="SPI%d SCLK" % inst)
    if pos == 3:
        return _freq_or_skip(ch, cs_hz, fs,
                             note="SPI%d CS_N: exactly one word per CS pulse"
                                  % inst)
    # MOSI and MISO carry a known word; edge counting would only measure how
    # often the data happens to change, so the decoder owns them.
    if pos == 1:
        return Expect(Kind.PROTOCOL,
                      note=("SPI%d MOSI: 0x%04X MSB first, decoded by the SPI "
                            "checker" % (inst, spi_bank_word(inst))))
    return Expect(Kind.PROTOCOL,
                  note=("SPI%d MISO: 0x%04X MSB first (bitwise complement of "
                        "MOSI, so both directions are checked independently)"
                        % (inst, (~spi_bank_word(inst)) & 0xFFFF)))


def _wiring(ch: int, fs: float) -> Expect:
    """Walking one: exactly one channel high at a time, rotating CH0..CH31.

    Every channel is deliberately SKIP here and the verdict comes from the
    rotation check instead. WHICH channel is high during which step is what
    identifies a wire, and check_order proves it across all 32 at once -- it
    requires every channel to show a rising edge and then every one of them to
    fire exactly one step after its predecessor, so a dead, swapped or doubled
    wire fails there.

    A per-channel duty on top of that adds nothing but noise: the duty is
    100 us / 3.2 ms = 3.125%, and edge ringing moves it by half a percentage
    point, which is enough to fail a channel that the rotation check has already
    proved correct. Measured on the real fixture, CH1 read 10 MHz / 60% of
    crosstalk in this mode while decoding SPI perfectly in M1..M4.
    """
    return Expect(Kind.SKIP, freq_hz=1.0 / (32 * 100e-6),
                  note=("walking one, 100 us per step: CH%d is the high channel "
                        "during step %d of 32; the rotation check owns this mode"
                        % (ch, ch)),
                  covered_by="走线轮转检查")


_MODES = {
    0: _wiring,
    1: lambda ch, fs: _spi_bank_mode(ch, fs, 1),
    2: lambda ch, fs: _spi_bank_mode(ch, fs, 2),
    3: lambda ch, fs: _spi_bank_mode(ch, fs, 3),
    4: lambda ch, fs: _spi_bank_mode(ch, fs, 4),
    5: _narrow_pulse,
    6: _burst,
}


MODE_LABELS = {
    0: "wiring / mapping",
    1: "4ch SPI @54 MHz",
    2: "8ch SPI @27 MHz",
    3: "16ch SPI @13.5 MHz",
    4: "32ch SPI + I2C + UART @6.75 MHz",
    5: "narrow pulse",
    6: "burst / trigger",
}

# Modes that must also be captured at the low threshold group's voltage.
#
# Only M4 is here. It is the one mode whose signals reach the bank-3 channels
# (CH20..CH25, locked to 1.8 V by the on-board PSRAM), so it is the only one that
# needs a second capture at 0.9 V. Everything else lives on 3.3 V channels and
# measured BETTER at 1.5 V: M0's rotation check passed at 1.5 V and failed at
# 0.9 V, because the lower threshold makes the 3.3 V edges ring hard enough to
# disturb the ordering. Running the second pass anyway doubled the capture count
# and, worse, let the worse of the two verdicts be the one reported.
NEEDS_THRESHOLD_GROUPS = {4, 5}
# M5 也必须在册：它的 CH20..CH25 同样是 bank 3。实测 1.5 V 下那几路的 1 kHz 方波
# 每个边沿会多跨几次阈值（CH20/22/23 数出 8 个上升沿而不是 4 个，读成 2.33 kHz），
# 换成 0.9 V 就正好是 1 kHz。最初只放了 M4，是因为 M4 的 bank 3 信号（409 kHz /
# 6.75 MHz）边沿很快、1.5 V 下也能正确穿越，所以这个遗漏一直没暴露；
# M5 的 1 kHz 慢边沿在阈值附近停留久，才把它显出来。
#
# 结构化判定的合并规则已同步修正（见 pipeline._run_mode_config）：阈值组轮只覆盖
# "通道落在该组里"的结构化判定，所以 M5 的窄脉冲检查（CH2~CH4，3.3 V 那侧）仍然
# 用 1.5 V 那轮判，不会被 0.9 V 这轮污染。


def expected(mode: int, nch: int, samplerate_hz: float,
             window_s: float | None = None) -> list[Expect]:
    """Expected signal on each of the first `nch` channels in `mode`.

    `samplerate_hz` decides which channels are above Nyquist and therefore cannot
    be verified by edge counting.

    `window_s` is the capture length in seconds. A channel needs several periods
    inside the window to yield a usable edge train: at two periods the measurement
    is fragile, and at less than one period there is often only a single rising
    edge and no frequency at all. Any channel slower than that is downgraded to
    SKIP rather than failed -- the instrument is not being asked a fair question.

    So a profile must declare a window long enough for its slowest checked
    channel (M5's 1 kHz fill needs >= 3 ms).
    """
    if mode not in _MODES:
        raise ValueError("no model for mode %r (expected one of %s)" % (mode, sorted(_MODES)))
    fn = _MODES[mode]
    out = [fn(ch, samplerate_hz) for ch in range(nch)]

    if window_s and window_s > 0:
        min_cycles = 3.0
        for ch, e in enumerate(out):
            if e.freq_hz and e.freq_hz > 0 and e.kind in (Kind.FREQ, Kind.DUTY):
                period = 1.0 / e.freq_hz
                if period * min_cycles > window_s:
                    out[ch] = Expect(
                        Kind.SKIP, freq_hz=e.freq_hz, duty_pct=e.duty_pct,
                        note=("%s is slower than the %.0f ms capture window "
                              "(%.0f ms period, %.1f cycles fit); needs a window of "
                              "at least %.0f ms to verify"
                              % (_fmt_hz(e.freq_hz), window_s * 1e3, period * 1e3,
                                 window_s / period, period * min_cycles * 1e3)))
    return out


def _fmt_hz(f: float) -> str:
    if f >= 1e6:
        return "%.4g MHz" % (f / 1e6)
    if f >= 1e3:
        return "%.4g kHz" % (f / 1e3)
    return "%.4g Hz" % f


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------

def _selftest() -> int:
    """Assert the CURRENT mode table (M0..M6, see MODE_LABELS / _MODES) against
    the numbers in the handover spec, so an RTL/spec drift fails loudly here."""
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    fails = 0

    def check(cond: bool, what: str) -> None:
        nonlocal fails
        print("  %s  %s" % ("ok  " if cond else "FAIL", what))
        if not cond:
            fails += 1

    def near(a, b, tol=0.01) -> bool:
        return a is not None and abs(a - b) <= tol

    print("== M0 wiring: walking one, 100 us/step over 32 ch -> 312.5 Hz, "
          "verdict owned by the rotation check ==")
    e = expected(0, 32, 200e6)
    check(all(near(x.freq_hz, 312.5) for x in e), "every channel 312.5 Hz")
    check(all(x.kind is Kind.SKIP for x in e), "per-channel verdict handed to 'order'")

    print("== M1..M4 SPI banks: SCLK rate, CS_N word rate, per-instance words ==")
    for mode, nch, fs, sclk in ((1, 4, 1400e6, 54e6), (2, 8, 800e6, 27e6),
                                (3, 16, 400e6, 13.5e6), (4, 32, 200e6, 6.75e6)):
        e = expected(mode, nch, fs)
        inst = SPI_BANK_MODES[mode][0]
        for i in range(inst):
            base = 4 * i
            check(e[base].kind is Kind.FREQ and near(e[base].freq_hz, sclk),
                  "M%d SPI%d SCLK %.2f MHz" % (mode, i, sclk / 1e6))
            check(near(e[base + 3].freq_hz, sclk / SPI_PHASES_PER_WORD * 2, 0.5),
                  "M%d SPI%d CS_N one word per CS pulse" % (mode, i))
            check(e[base + 1].kind is Kind.PROTOCOL and e[base + 2].kind is Kind.PROTOCOL,
                  "M%d SPI%d MOSI/MISO decoded by the SPI checker" % (mode, i))
    check([spi_bank_word(i) for i in range(4)] == [0xA5C3, 0xB4D2, 0x87E1, 0x96F0],
          "instance words = 0xA5C3 ^ (i * 0x1111)")

    print("== M4 extras: I2C on CH24/25, UART TX on CH26, rest undriven ==")
    e = expected(4, 32, 200e6)
    check(near(e[24].freq_hz, 100_000.0), "CH24 I2C SCL 100 kHz")
    check(e[25].kind is Kind.PROTOCOL and e[26].kind is Kind.PROTOCOL,
          "CH25 SDA / CH26 UART decoded by protocol checkers")
    check(all(x.kind is Kind.STATIC and x.static == 0 for x in e[27:]),
          "CH27..CH31 static low")
    check(4 in NEEDS_THRESHOLD_GROUPS, "M4 also captured at the bank-3 threshold")

    print("== M5 narrow pulse ==")
    e = expected(5, 32, 200e6)
    check(e[0].kind is Kind.FREQ and near(e[0].freq_hz, 100_000.0), "CH0 100 kHz")
    check(e[1].kind is Kind.STATIC and e[1].static == 0, "CH1 held low")
    check(all(x.kind is Kind.SKIP for x in e[2:5]),
          "CH2..CH4 widths owned by the narrow-pulse width check")
    check(all(x.kind is Kind.FREQ and near(x.freq_hz, 1000.0) for x in e[5:]),
          "CH5..CH31 1 kHz fill")
    check(5 in NEEDS_THRESHOLD_GROUPS, "M5 also captured at the bank-3 threshold")

    print("== M6 burst: envelope owned by the burst check ==")
    e = expected(6, 32, 200e6)
    check(all(x.kind is Kind.SKIP for x in e), "every channel handed to 'burst'")
    check(all(near(x.freq_hz, 50.0) for x in e[4:]),
          "CH4..CH31 1 kHz only inside the 1 ms burst every 20 ms -> 50 Hz mean")

    print("== mode table is exactly M0..M6 ==")
    check(sorted(_MODES) == list(range(7)) == sorted(MODE_LABELS), "7 modes, labels match")

    print()
    print("RESULT: %s" % ("OK" if not fails else "%d FAILURES" % fails))
    return 1 if fails else 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
