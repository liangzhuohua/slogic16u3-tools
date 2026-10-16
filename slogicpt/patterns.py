"""Signal model for the Tang Nano 9K test patterns (M0..M9).

WHAT THIS IS
------------
The production profile says *what* to test (which mode, which channel/rate
group); this module says *what the result should be*. Keeping the expectation in
code rather than in the TOML matters because the full matrix is 10 modes x 4 rate
groups x 32 channels = 1280 numbers, and because a change to the RTL should
require exactly one edit here.

Every number below is derived from the RTL, not guessed from measurements, so
this file is the place to look when a reading looks wrong.

RTL COMMENT ERRORS FOUND WHILE WRITING THIS (code is right, comments are wrong)
------------------------------------------------------------------------------
* `pat_ladder.v` says CH11 is 50 kHz. The divisor is 25, so it is **40 kHz**
  (1 MHz / 25). The 1-2-5 sequence would need 20. Measurement agrees with the
  code: 40001 Hz.
* `pat_core.v` says M6's narrow pulse is on CH0. `fast_out.v` drives it on
  **fast[1], i.e. CH1**; CH0 in M6 is an 18.5 ns pulse from the 54 MHz domain.
* `pat_proto.v` says the SPI word rate is 62.5 kHz. `sp_bit` counts 0..16, so a
  word is 17 bit clocks: **58.82 kHz**. Measurement: 58825 Hz.
* `pat_core.v`'s M9 header gives pulse widths of 0.1/0.3/0.6/1.2 us. The widths
  are 8/16/32/64 clocks of 54 MHz: **148/296/593/1185 ns**.
* The M5 "108 MHz" comment was already corrected in the RTL; the counter runs on
  the 54 MHz core clock, giving 421.875 kHz.

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
CLK_FAST_HZ = 216_000_000            # 432 MHz / 2, PLL ODIV cancels

UART_BAUD_A = 115_200                 # actual divider 468 -> 115384.6 (+0.16%)
UART_BAUD_B = 9_600                   # exact
SPI_CLK_HZ = 1_000_000
SPI_WORD = 0xA5C3
SPI_CLKS_PER_WORD = 17                # 16 data bits + 1 idle bit
I2C_SCL_HZ = 100_000
I2C_ADDR_W = 0xA0                     # 0x50 << 1 | write
I2C_DATA = (0x12, 0x34)
I2S_BCLK_HZ = 1_000_000
I2S_WS_HZ = 31_250                 # BCLK / 32: a stereo frame is 32 bit clocks
#   (the RTL comment says BCLK/64; the code toggles WS every 16 BCLK, so a
#    full WS period is 32 BCLK. Measurement agrees: 31.251 kHz.)

# pat_ladder.v's divisor table, indexed by ladder position (CH7 + i).
# 1 MHz / DIV[i], NOT the 1-2-5 sequence the header comment claims: index 4 is
# 25 (40 kHz), not 20 (50 kHz).
LADDER_DIV = (1, 2, 5, 10, 25, 50, 100, 200, 500, 1000,
              2000, 5000, 10_000, 20_000, 50_000, 100_000, 200_000, 500_000,
              1_000_000, 2_000_000, 5_000_000, 10_000_000, 20_000_000,
              50_000_000, 100_000_000)

# M6 / M9 constants
M6_GLITCH_PERIOD_CLK = 540            # 10 us at 54 MHz
M9_BURST_PERIOD_CLK = 1_080_000       # 20 ms (was 100 ms; see pat_core.v B_PERIOD)
M9_BURST_ACTIVE_CLK = 54_000          # 1 ms
M9_WIDTH_CLK = (8, 16, 32, 64)        # CH0..CH3 pulse widths


class Kind(Enum):
    FREQ = "freq"          # verify frequency
    STATIC = "static"      # verify a constant level
    DUTY = "duty"          # verify frequency and duty
    SKIP = "skip"          # report the reading, pass no verdict
    GOLDEN = "golden"      # compare against a generated reference sequence
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

def _m0(ch: int, fs: float) -> Expect:
    if ch == 0:
        # toggle every C1M_MAX+1 = 28 cycles of 54 MHz
        return _freq_or_skip(ch, CLK_SYS_HZ / 56.0, fs,
                             note="doubles as the time-base check")
    if ch == 1:
        return _freq_or_skip(ch, CLK_SYS_HZ / 54_000.0, fs)
    return Expect(Kind.STATIC, static=(0 if ch % 2 else 1))


def _m1(ch: int, fs: float) -> Expect:
    if ch <= 6:
        return _freq_or_skip(ch, CLK_FAST_HZ / float(1 << (ch + 1)), fs,
                             note="fast domain, from fast_out.v")
    div = LADDER_DIV[ch - 7]
    return _freq_or_skip(ch, 1_000_000.0 / div, fs,
                         note="ladder DIV=%d" % div)


def _m2(ch: int, fs: float) -> Expect:
    return _freq_or_skip(ch, 1_000_000.0 / float(1 << (ch + 1)), fs)


def _m3(ch: int, fs: float) -> Expect:
    # one channel high for 100 us out of a 3.2 ms rotation
    return _freq_or_skip(ch, 1.0 / 3.2e-3, fs, duty=100.0 / 3200.0 * 100.0)


def _m4(ch: int, fs: float) -> Expect:
    # channel N emits N+1 one-microsecond pulses in the 96 us window at the start
    # of each 1 ms frame. Averaging rising-edge intervals over whole frames gives
    # approximately (N+1) kHz, but the inter-frame gap biases it upward at the top
    # of the range, so this is reported rather than failed.
    return Expect(Kind.SKIP, freq_hz=(ch + 1) * 1000.0,
                  note="%d pulses per 1 ms frame, 1 us wide, 3 us slots" % (ch + 1))


def _m5(ch: int, fs: float) -> Expect:
    # 128 clocks of 54 MHz per frame, channel n high for (n+1) of 32 slots.
    #
    # CH31 is high for all 32 slots, so it is a CONSTANT, not a signal. Modelling
    # it as "421.875 kHz at 100% duty" made the checker demand an edge train that
    # cannot exist and fail a perfectly good channel.
    if ch == 31:
        return Expect(Kind.STATIC, static=1,
                      note="100% duty -- always high, no edges by construction")
    return _freq_or_skip(ch, CLK_SYS_HZ / 128.0, fs,
                         duty=100.0 * (ch + 1) / 32.0)


def _m6(ch: int, fs: float) -> Expect:
    if ch == 0:
        return _freq_or_skip(ch, CLK_SYS_HZ / M6_GLITCH_PERIOD_CLK, fs,
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
        return Expect(Kind.SKIP, freq_hz=CLK_SYS_HZ / M6_GLITCH_PERIOD_CLK,
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
    return _freq_or_skip(ch, 1000.0, fs, note="1 kHz reference fill")


def _m7(ch: int, fs: float) -> Expect:
    # A maximal-length 32-bit LFSR clocked at 1 MHz: each bit is pseudorandom, so
    # it changes on half the clocks -> 500k transitions/s -> 250k rising edges/s.
    # The exact sequence is available from golden_prbs() for a bit-for-bit check.
    return Expect(Kind.GOLDEN, freq_hz=250_000.0,
                  note="LFSR x^32+x^22+x^2+x+1 at 1 MHz; ~250 kHz mean edge rate")


def _m8(ch: int, fs: float) -> Expect:
    if ch == 0:
        return Expect(Kind.PROTOCOL, note="UART %d 8N1 -> 'SLogic32U3\\r\\n'"
                      % UART_BAUD_A)
    if ch == 1:
        return Expect(Kind.PROTOCOL, note="UART %d 8N1 -> 'SLogic32U3\\r\\n'"
                      % UART_BAUD_B)
    if ch == 2:
        return _freq_or_skip(ch, SPI_CLK_HZ, fs, note="SPI clock")
    if ch == 3:
        return Expect(Kind.PROTOCOL, note="SPI MOSI, MSB first, word 0x%04X" % SPI_WORD)
    if ch == 4:
        return _freq_or_skip(ch, SPI_CLK_HZ / SPI_CLKS_PER_WORD, fs,
                             note="SPI CS_N, one low pulse per %d-bit word"
                                  % (SPI_CLKS_PER_WORD - 1))
    if ch == 5:
        return _freq_or_skip(ch, I2C_SCL_HZ, fs, note="I2C clock")
    if ch == 6:
        return Expect(Kind.PROTOCOL,
                      note="I2C SDA: START, 0x%02X, 0x%02X, 0x%02X, STOP"
                           % (I2C_ADDR_W, I2C_DATA[0], I2C_DATA[1]))
    if ch == 7:
        return _freq_or_skip(ch, I2S_WS_HZ, fs, note="I2S word select")
    if ch == 8:
        return Expect(Kind.PROTOCOL,
                      note="I2S SD: left = frame counter, right = its complement")
    if ch == 9:
        return _freq_or_skip(ch, I2S_BCLK_HZ, fs, note="I2S bit clock")
    # CH10..CH31: the top 22 bits of the 1 MHz counter
    return _freq_or_skip(ch, 1_000_000.0 / float(1 << (ch + 1)), fs,
                         note="counter fill bit %d" % ch)


def _m9(ch: int, fs: float) -> Expect:
    if ch < 4:
        width_clk = M9_WIDTH_CLK[ch]
        width_ns = width_clk / (CLK_SYS_HZ / 1e9)
        return Expect(Kind.SKIP,
                      freq_hz=CLK_SYS_HZ / M6_GLITCH_PERIOD_CLK,
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


def _new_m0(ch: int, fs: float) -> Expect:
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
    0: _new_m0,
    1: lambda ch, fs: _spi_bank_mode(ch, fs, 1),
    2: lambda ch, fs: _spi_bank_mode(ch, fs, 2),
    3: lambda ch, fs: _spi_bank_mode(ch, fs, 3),
    4: lambda ch, fs: _spi_bank_mode(ch, fs, 4),
    5: _m6,          # narrow pulse, unchanged from the previous design
    6: _m9,          # burst / trigger, unchanged
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

    This matters because the ladder and the counter both run down to 0.01 Hz. Those
    rungs are real and correct, but verifying them would need a capture of minutes,
    so a production profile is expected to declare a window and accept SKIPs below
    it.
    """
    if mode not in _MODES:
        raise ValueError("no model for mode %r (expected 0..9)" % (mode,))
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
# golden reference sequences
# ---------------------------------------------------------------------------

def golden_prbs(n_samples: int) -> list[int]:
    """The first `n_samples` states of M7's LFSR, oldest first.

    Mirrors pat_core.v: seed 0xDEADBEEF, feedback from bits 31,21,1,0, shifted
    left into bit 0, advancing at 1 MHz. Each returned value is the 32-bit state,
    which is exactly what the 32 channels present at that instant (CH0 = bit 0).
    """
    lfsr = 0xDEADBEEF
    out = []
    for _ in range(n_samples):
        fb = ((lfsr >> 31) ^ (lfsr >> 21) ^ (lfsr >> 1) ^ lfsr) & 1
        lfsr = ((lfsr << 1) | fb) & 0xFFFFFFFF
        out.append(lfsr)
    return out


def golden_uart(text: bytes, baud: int, clk_hz: int = CLK_SYS_HZ) -> list[int]:
    """Sample-by-sample UART line level at `clk_hz`, 8N1, idle high.

    The divider is rounded the way the RTL does it, so the +0.16% baud error on
    the 115200 transmitter shows up here too instead of silently disagreeing with
    the hardware.
    """
    div = max(1, round(clk_hz / baud))
    bits: list[int] = []
    for byte in text:
        bits.append(0)                              # start
        for i in range(8):
            bits.append((byte >> i) & 1)            # LSB first
        bits.append(1)                              # stop
    out = []
    for b in bits:
        out.extend([b] * div)
    return out


def golden_i2c_bytes() -> tuple[int, ...]:
    return (I2C_ADDR_W,) + I2C_DATA


def expected_spi_word() -> int:
    return SPI_WORD


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------

def _selftest() -> int:
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    fails = 0

    print("== M0 (32ch @200M) ==")
    e = expected(0, 32, 200e6)
    print("  CH0 %.1f Hz (expect 964285.7)" % e[0].freq_hz)
    print("  CH1 %.1f Hz (expect 1000)" % e[1].freq_hz)
    for ch in (2, 3, 4):
        print("  CH%d static %s (expect %d)" % (ch, e[ch].static, 0 if ch % 2 else 1))
    if abs(e[0].freq_hz - 964285.714) > 0.01 or e[1].freq_hz != 1000.0:
        print("  FAIL"); fails += 1

    print("== M1 ladder, CH7..CH31 ==")
    e = expected(1, 32, 200e6)
    print("  CH7  %10.1f Hz" % e[7].freq_hz)
    print("  CH11 %10.1f Hz  <- must be 40000, not the 50000 the RTL comment says"
          % e[11].freq_hz)
    print("  CH16 %10.1f Hz" % e[16].freq_hz)
    print("  CH19 %10.1f Hz" % e[19].freq_hz)
    print("  CH0  kind=%s (108 MHz > 100 MHz Nyquist)"
          % e[0].kind.value)
    if abs(e[11].freq_hz - 40000.0) > 0.1:
        print("  FAIL: CH11 model"); fails += 1
    if e[0].kind is not Kind.SKIP:
        print("  FAIL: CH0 at 32ch@200M should be SKIP (above Nyquist)"); fails += 1
    e400 = expected(1, 32, 400e6)
    if e400[0].kind is Kind.SKIP:
        print("  FAIL: CH0 at 16ch@400M is 108 MHz < 200 MHz Nyquist, should check")
        fails += 1

    print("== M5 duty sweep ==")
    e = expected(5, 32, 200e6)
    print("  CH0..CH30 %.3f Hz; duty CH0 %.2f%% CH30 %.2f%%"
          % (e[0].freq_hz, e[0].duty_pct, e[30].duty_pct))
    print("  CH31 kind=%s static=%s  (%s)" % (e[31].kind.value, e[31].static, e[31].note))
    if abs(e[0].freq_hz - 421875.0) > 0.1 or abs(e[30].duty_pct - 96.875) > 0.01:
        print("  FAIL"); fails += 1
    if e[31].kind is not Kind.STATIC or e[31].static != 1:
        print("  FAIL: CH31 must be static high, it has a 100% duty cycle"); fails += 1

    print("== window awareness ==")
    e20 = expected(1, 32, 200e6, window_s=0.020)
    e200 = expected(1, 32, 200e6, window_s=0.200)
    n20 = sum(1 for x in e20 if x.kind in (Kind.FREQ, Kind.DUTY))
    n200 = sum(1 for x in e200 if x.kind in (Kind.FREQ, Kind.DUTY))
    print("  M1 verifiable channels: %d in a 20 ms window, %d in a 200 ms window"
          % (n20, n200))
    print("  CH19 (100 Hz) at 20 ms -> %s" % e20[19].kind.value)
    print("     %s" % e20[19].note[:96])
    print("  no window given -> %d verifiable (backwards compatible)"
          % sum(1 for x in expected(1, 32, 200e6) if x.kind in (Kind.FREQ, Kind.DUTY)))
    if not (n20 < n200) or e20[19].kind is not Kind.SKIP:
        print("  FAIL: a slow channel in a short window should be SKIP"); fails += 1

    print("== M8 protocol rates ==")
    e = expected(8, 32, 200e6)
    print("  CH2 SPI CLK %.0f Hz" % e[2].freq_hz)
    print("  CH4 CS_N     %.2f Hz (expect 58823.53 = 1MHz/17)" % e[4].freq_hz)
    print("  CH5 I2C SCL  %.0f Hz" % e[5].freq_hz)
    print("  CH7 I2S WS   %.0f Hz" % e[7].freq_hz)
    print("  CH9 I2S BCLK %.0f Hz" % e[9].freq_hz)
    if abs(e[4].freq_hz - 58823.529) > 0.01:
        print("  FAIL: CS_N rate"); fails += 1

    print("== golden PRBS ==")
    g = golden_prbs(4)
    print("  first states: %s" % " ".join("%08X" % v for v in g))
    # a maximal LFSR over 2^32-1 must not return to the seed early
    g2 = golden_prbs(1000)
    if 0xDEADBEEF in g2[1:]:
        print("  FAIL: LFSR repeated early"); fails += 1

    print("== golden UART ==")
    u = golden_uart(b"A", 9600)
    bits = round(len(u) / 10)
    print("  'A' at 9600 -> %d samples, %d samples/bit" % (len(u), bits))
    if bits != round(CLK_SYS_HZ / 9600):
        print("  FAIL: baud divider"); fails += 1

    print()
    print("RESULT: %s" % ("OK" if not fails else "%d FAILURES" % fails))
    return 1 if fails else 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
