"""Measurement and checks for captured logic-analyser data.

TWO IDEAS MATTER HERE
---------------------
1. Frequency is measured from *kept* rising edges, not from every edge in the
   file. A real capture of a slow square wave carries tens of nanoseconds of
   ringing on each edge, and a naive "count every 0->1 transition" sees that
   ringing as extra cycles -- it made M0's 1 kHz channel read as 2 kHz.

2. The glitch threshold is a fraction of the **median width of the same
   polarity**, not a fixed number of nanoseconds. A fixed threshold is wrong in
   both directions: too high and it swallows M5's genuine 74 ns pulses (the duty
   sweep runs at 421.875 kHz, where a 1/32 duty pulse really is that short); too
   low and it keeps the ringing. Comparing high pulses against the median high
   width -- and low pulses against the median low width -- adapts to any pattern.

This mirrors the algorithm in research/vcdstats.py, which was validated against
the whole 40-cell matrix, but operates on the numpy arrays produced by
waveform.extract_channels() instead of a VCD text file.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .patterns import Expect, Kind
from . import deep_checks


@dataclass
class Measurement:
    """What one channel actually did."""

    channel: int
    freq_hz: float = 0.0
    duty_pct: float | None = None
    static: int | None = None          # set when fewer than 2 rising edges
    rises: int = 0
    glitches: int = 0
    first_rise: int | None = None      # sample index of the first kept rising edge
    period_samples: float | None = None

    @property
    def toggling(self) -> bool:
        return self.rises >= 2


def measure_channel(samples: np.ndarray, channel: int,
                    glitch_frac: float = 0.0) -> Measurement:
    """Measure one channel's 1-D 0/1 array.

    glitch_frac = 0 **默认不滤任何毛刺** —— 产测的职责是把采集到的信号如实读
    出来。任何"把窄脉冲当振铃丢掉"的处理都可能同时丢掉真实的东西：

      * 被测板输出上的窄毛刺（焊接/器件问题）；
      * 固件产生的伪边沿（比如状态机跑飞、时钟域出问题）。

    这两类正是产测要抓的，所以判定层不再替它们做决定。代价是探头振铃也会被
    计入读数（实测飞线台架上 M5 的 CH2 会从 100 kHz 读成 27 MHz）—— 那种情况
    由 evaluate() 标记成"疑似测量问题"，只提示、不改判，让人去查探头。

    需要滤的时候可以显式传 glitch_frac>0（自测就是这么对比两种读法的）。
    """
    if samples.size < 2:
        return Measurement(channel, static=int(samples[0]) if samples.size else 0)

    # indices where the level changes
    idx = np.flatnonzero(samples[1:] != samples[:-1]) + 1
    if idx.size == 0:
        return Measurement(channel, static=int(samples[0]))

    if glitch_frac == 0:
        # Production default.  No segment is shorter than a zero threshold, so
        # the walk below would keep every transition; take them all at once.
        level = int(samples[idx[-1]])
        kept_t = np.concatenate(([0], idx, [samples.size]))
        kept_lvl = np.concatenate(([samples[0]], samples[idx], [level]))
        glitches = 0
    else:
        # segment boundaries, including the implicit one at t = 0
        bnd = np.concatenate(([0], idx))
        lvl = np.concatenate(([samples[0]], samples[idx]))       # level from bnd[i]
        width = np.diff(bnd).astype(np.float64)                  # width of segment i
        seg_lvl = lvl[:-1].astype(np.int8)

        highs = width[seg_lvl == 1]
        lows = width[seg_lvl == 0]
        hi_thr = float(np.median(highs)) * glitch_frac if highs.size else 0.0
        lo_thr = float(np.median(lows)) * glitch_frac if lows.size else 0.0

        # Sequential walk: a segment shorter than its polarity's threshold is treated
        # as ringing and merged into the next one, so it contributes no edge. Doing
        # this in order (rather than vectorised) is what makes the merge correct.
        # Collect the KEPT transitions first; the duty calculation needs to know where
        # the first and last rising edges are, which is only known at the end.
        kept_t = [0]
        kept_lvl = [int(samples[0])]
        glitches = 0
        level = int(samples[0])
        start = 0
        for i in range(idx.size):
            t = int(idx[i])
            new = int(samples[t])
            w = t - start
            thr = hi_thr if level == 1 else lo_thr
            # 采集**起点所在的那一段**是被窗口截断的，真实宽度未知，不能拿毛刺阈值
            # 去判它。否则 M0 会误判：捕获起点必然落在某个通道的电平期间（walking-one
            # 每步 100us，10ms 窗口里几乎必然如此），于是
            #   - 起点时正在高电平的那一路：首个高电平被采短 -> 判毛刺丢掉；
            #   - 起点时是低电平的那一路：首个低电平同样被采短 -> 也丢掉。
            # 这两路的"首次上升沿"于是整体跳到下一轮，check_order 的轮转序列错位、
            # 报 FAIL —— 与板子好坏无关，纯粹是采集相位的抽签。
            # 实测（同一台设备连跑）：修复前 1 通过 / 3 失败，起始通道每次不同
            # （CH17 / CH20），失败详情形如 "rank 27: got CH13 at 28.00 steps,
            # expected CH12 at 27"；直接查采集文件可见 CH12 起始电平=1、首个高电平
            # 只有 976 个采样点（正常 20000）。
            at_capture_start = (i == 0)
            if w < thr and not at_capture_start:
                glitches += 1
                start = t
                continue
            kept_t.append(t)
            kept_lvl.append(new)
            level = new
            start = t
        kept_t.append(int(samples.size))
        kept_lvl.append(level)

    # A rising edge is where the level goes 0 -> 1, so `rise_at` indexes the LOW
    # segment that PRECEDES each rise. The edge itself is at kept_t[i + 1]; using
    # kept_t[i] reports the start of the preceding low period instead, which
    # shifted every channel's first edge back by a whole low period (CH0's first
    # rise came out as sample 0 instead of 40000).
    lv = np.asarray(kept_lvl, dtype=np.int8)
    rise_at = np.flatnonzero((lv[:-1] == 0) & (lv[1:] == 1))

    m = Measurement(channel, rises=int(rise_at.size), glitches=glitches)
    if rise_at.size >= 2:
        i_first = int(rise_at[0]) + 1
        i_last = int(rise_at[-1]) + 1
        t_first = int(kept_t[i_first])
        t_last = int(kept_t[i_last])
        span_t = t_last - t_first
        m.first_rise = t_first

        # Frequency and duty are taken from MEDIANS of the per-period values, not
        # from the first-to-last span.
        #
        # WHY: the span-based form divides by the edge count, so every spurious
        # edge that survives the glitch filter inflates the frequency while barely
        # moving the span. Measured on the real fixture, M5's channels read
        # 1.05-21.4 MHz against a true 421.875 kHz while their duty stayed within
        # a percent of correct -- the signature of a mean over a count that a few
        # outliers had doubled.
        #
        # A median ignores a minority of corrupt periods, and ringing blips are
        # always a minority next to real periods. It also removes the need to
        # trade the glitch threshold against M6's deliberately narrow pulses: a
        # 74 ns pulse is real and survives, while a 2-sample blip moves neither
        # median.
        rise_t = np.asarray(kept_t, dtype=np.int64)[
            np.asarray(rise_at, dtype=np.int64) + 1]
        periods = np.diff(rise_t).astype(np.float64)
        period = _mean_period(periods)
        m.period_samples = period

        # 占空比 = 首末上升沿之间**高电平样本占比**（原始，不取中位数）。
        # 以前取高/低段宽度的中位数 —— 那样少数的异常脉冲会被抹掉，而"少数异常
        # 脉冲"正是产测要抓的东西（板子的窄毛刺、固件的伪边沿）。改成原始占比后
        # 它一定会反映到读数上。
        if span_t > 0:
            m.duty_pct = 100.0 * float(
                np.count_nonzero(samples[t_first:t_last])) / span_t
        else:
            m.duty_pct = None
        # freq is normalised (cycles per sample); callers multiply by the rate.
        m.freq_hz = 1.0 / period if period > 0 else None
    else:
        m.static = level
    return m


def _mean_period(periods: np.ndarray) -> float:
    """所有相邻上升沿间隔的**算术平均**（不做任何离群剔除）。

    这里以前会丢掉偏离中位数 0.5x~2x 之外的周期再平均。那等于把"多出来的边沿"
    从频率里抹掉 —— 而多出来的边沿恰恰可能是被测板的窄毛刺或固件的伪边沿，
    是产测必须看见的东西。所以改成直接平均：

      * 真实的窄脉冲 -> 周期被拉短 -> 频率读数偏高 -> FAIL，看得见；
      * 探头振铃 -> 同样读数偏高 -> FAIL，但 evaluate() 会额外标记"疑似测量
        问题"（只提示、不改判），让人去查探头而不是误判成板子坏。

    代价：频率对毛刺敏感。实测飞线台架上 M5 的 CH2（真值 100 kHz）会读成
    27 MHz —— 该通道本来就是 SKIP，所以不影响判定；换成同轴探头线（补上
    AFE 的 100 Ω+15 pF）振铃会明显减小。
    """
    if periods.size == 0:
        return 0.0
    return float(periods.mean())


def measure_all(channels: np.ndarray, glitch_frac: float = 0.0) -> list[Measurement]:
    """Measure every channel of a (n_channels, n_samples) 0/1 array."""
    return [measure_channel(channels[ch], ch, glitch_frac)
            for ch in range(channels.shape[0])]


# ---------------------------------------------------------------------------
# verdicts
# ---------------------------------------------------------------------------

@dataclass
class Verdict:
    channel: int
    ok: bool | None                    # None = not checkable (skip)
    kind: str = ""
    measured: str = ""
    expected: str = ""
    detail: str = ""
    sub: list["Verdict"] = field(default_factory=list)
    # For verdicts that cover several channels at once -- one SPI master is SCLK,
    # MOSI, MISO and CS_N together, not four separate results, and a mode-level
    # check like the M0 rotation spans all of them. Named so the report can say which pins
    # the result is about; falls back to "CH<n>" when empty.
    channel_text: str = ""
    # The channels that text refers to, so a failed group verdict can be narrowed
    # by the per-channel results sitting alongside it. Measured case: the I2C
    # transaction failed and the summary named "CH24/CH25", which reads as two
    # suspect wires -- but CH24's own 100 kHz check had passed, so only CH25 was
    # bad. Same for an SPI master whose SCLK and CS_N measure perfectly while the
    # data lines do not.
    channels: tuple[int, ...] = ()
    # 机器可读的一句话失败原因（"no_edge" / "order" / …）。界面据此写成人话，
    # 不必去解析 detail 里那段给人看会头大的算法内部对比文本。
    reason: str = ""
    # 只做标记、**不参与判定**：这条失败如果是在"滤掉窄毛刺"的读法下本可通过，
    # 那它可能是探头振铃/接地不良（测量问题）而不是板子的问题。判定层不替人
    # 做这个决定，只把事实写在这里，由界面提示"建议查探头/示波器确认"。
    suspect: str = ""

    @property
    def mark(self) -> str:
        if self.ok is None:
            return "SKIP"
        return "ok" if self.ok else "** FAIL **"


def _fmt_freq(f: float) -> str:
    if f >= 1e6:
        return "%.5g MHz" % (f / 1e6)
    if f >= 1e3:
        return "%.5g kHz" % (f / 1e3)
    return "%.5g Hz" % f


def check_freq(m: Measurement, exp: Expect, tol_pct: float,
               sample_rate_hz: float) -> Verdict:
    want = exp.freq_hz
    got = m.freq_hz * sample_rate_hz
    measured = _fmt_freq(got) if m.toggling else "static %s" % m.static
    expected = _fmt_freq(want) if want is not None else "static"
    if not m.toggling:
        return Verdict(m.channel, False, "freq", measured, expected,
                       "no usable edge train")
    ok = abs(got - want) <= want * tol_pct / 100.0
    return Verdict(m.channel, ok, "freq", measured, expected,
                   "%+.3f%%" % (100.0 * (got - want) / want) if want else "")


def check_static(m: Measurement, exp: Expect) -> Verdict:
    want = exp.static
    if m.toggling:
        return Verdict(m.channel, False, "static",
                       _fmt_freq(m.freq_hz), "static %s" % want,
                       "channel is toggling")
    ok = (m.static == want)
    return Verdict(m.channel, ok, "static", "static %s" % m.static,
                   "static %s" % want)


def check_duty(m: Measurement, exp: Expect, tol_pp: float) -> Verdict:
    want = exp.duty_pct
    if m.duty_pct is None:
        return Verdict(m.channel, False, "duty", "n/a", "%.2f%%" % want,
                       "no usable edge train")
    ok = abs(m.duty_pct - want) <= tol_pp
    return Verdict(m.channel, ok, "duty", "%.2f%%" % m.duty_pct,
                   "%.2f%%" % want, "%+.2f pp" % (m.duty_pct - want))


def check_order(measurements: list[Measurement], samples_per_step: float,
                tol_steps: float = 0.5, period: int | None = None) -> Verdict:
    """M3: the one-hot pattern must visit CH0, CH1, ... in order.

    Every channel's first rising edge should be one step later than the previous
    channel's. A swapped pair of probe wires shows up here even though every
    channel still reads the correct 312.5 Hz.

    结论尽量做成"哪几路不对"而不是"排在第几位对不上"：后者是算法内部视角，
    操作员要的是去查哪根线。所以把**没边沿的**和**顺序不对的**通道放进
    `channels`，并用 `reason` 给出机器可读的一句话原因，界面据此写人话。

    `period` is the length of the fixture's rotation (32).  It only matters
    when fewer channels are captured: a 16-channel analyser sees CH15 fire and
    then 17 silent steps before CH0.  Defaults to the captured channel count,
    which is what the 32-channel capture always was.
    """
    n = len(measurements)
    period = max(period or n, n)
    missing = [m.channel for m in measurements if m.first_rise is None]
    seen = [(m.channel, m.first_rise) for m in measurements
            if m.first_rise is not None]
    if len(seen) < 2:
        return Verdict(-1, False, "order", "n/a", "one rising edge per channel",
                       "not enough channels with a rising edge",
                       channels=tuple(missing), reason="no_edge")
    base = min(t for _c, t in seen)
    offsets = [(c, (t - base) / samples_per_step) for c, t in seen]
    # rotate so the channel that fires first is index 0, then require the rest to
    # ascend by one step each
    offsets.sort(key=lambda cs: cs[1])
    start_ch = offsets[0][0]
    # the captured channels in firing order, each with the step it fires on
    # relative to start_ch (one step apart when period == n)
    expect = [(c, k) for k in range(period)
              for c in [(start_ch + k) % period] if c < n]
    disordered = []
    first_bad = ""
    for rank, (ch, off) in enumerate(offsets):
        want_ch, want_off = expect[rank] if rank < len(expect) else (-1, -1)
        if ch != want_ch or abs(off - want_off) > tol_steps:
            disordered.append(ch)
            if not first_bad:
                first_bad = ("rank %d: got CH%d at %.2f steps, expected CH%d at %d"
                             % (rank, ch, off, want_ch, want_off))
    offenders = tuple(sorted(set(missing) | set(disordered)))
    bits = []
    if missing:
        bits.append("%d channels have no rising edge at all" % len(missing))
    if disordered:
        # 缺通道会让后面的通道"看起来"提前了，所以先把"完全没信号的"说清楚，
        # 顺序错位只作为附带说明 —— 否则操作员会去查一堆其实没问题的线。
        bits.append("order mismatch from " + first_bad)
    return Verdict(-1, not offenders, "order",
                   "%d/%d channels have edges, starts at CH%d"
                   % (len(seen), n, start_ch),
                   "all %d channels, one step apart, ascending" % n,
                   "; ".join(bits),
                   # channels 只放"完全没信号"的那几路（真正要去查的线）；
                   # 没有缺通道时才退回顺序错位的那几路。
                   channels=tuple(missing) if missing else tuple(disordered),
                   reason=("no_edge" if missing else "order"))


def check_channel(m: Measurement, exp: Expect, tol_pct: float, tol_pp: float,
                  sample_rate_hz: float) -> Verdict:
    """Run whichever check `exp.kind` calls for.

    **判定只有"通过 / 不通过"两种结果。** "测不了"不是一个中性答案，它必须
    分清是谁的责任：

      * 判定权**刻意移交**给了结构化检查（`exp.covered_by` 非空）-> `ok=None`、
        `kind="covered"`，界面显示成"由 XX 检查覆盖" —— 它其实被测到了，只是
        判定形态不同（比如 M0 的走线顺序、M1~M4 的 SPI 内容）；
      * **没人判得了这一路**（采样率/窗口配不上信号）-> **`ok=False`**、
        `kind="unjudged"`。以前这种会变成 SKIP，等于让"我们自己把采集参数配错了"
        悄悄放行；现在它直接判红，逼人去修档案而不是让它沉默。
    """
    if exp.kind is Kind.SKIP:
        got = _fmt_freq(m.freq_hz * sample_rate_hz) if m.toggling \
            else "static %s" % m.static
        want = _fmt_freq(exp.freq_hz) if exp.freq_hz else ""
        if exp.covered_by:
            return Verdict(m.channel, None, "covered", got, want,
                           "由「%s」覆盖，本路不用边沿率判" % exp.covered_by)
        return Verdict(m.channel, False, "unjudged", got, want,
                       "测试点未被判定：%s" % exp.note,
                       reason="unjudged")

    if exp.kind is Kind.STATIC:
        v = check_static(m, exp)
    elif exp.kind is Kind.DUTY:
        fv = check_freq(m, exp, tol_pct, sample_rate_hz)
        dv = check_duty(m, exp, tol_pp)
        v = Verdict(m.channel, fv.ok and dv.ok, "duty",
                    "%s / %s" % (fv.measured, dv.measured),
                    "%s / %s" % (fv.expected, dv.expected),
                    "%s; %s" % (fv.detail, dv.detail), sub=[fv, dv])
    elif exp.kind is Kind.FREQ:
        v = check_freq(m, exp, tol_pct, sample_rate_hz)
    else:
        # PROTOCOL：逐通道只报读数，内容判定归各自的解码器 —— 所以
        # 这是"覆盖"，不是"没判"。
        v = Verdict(m.channel, None, "covered",
                    _fmt_freq(m.freq_hz * sample_rate_hz) if m.toggling else "static",
                    "", "由内容解码器覆盖（%s）" % exp.note)
    return v


# ---------------------------------------------------------------------------
# deep content checks (M1..M4 protocols, M5 narrow pulses, M6 burst)
# ---------------------------------------------------------------------------


def _spi_bank_verdicts(channels: np.ndarray, sample_rate_hz: float,
                       mode: int) -> list[Verdict]:
    """M1..M4: decode every SPI master and compare against the known words.

    This is the whole point of the redesign. The old fixture judged a channel by
    its edge rate, which a floating input can imitate and a marginal edge can
    corrupt into a plausible-looking number. A decoded word is either the word
    that was sent or it is not, so the check cannot be fooled.

    Verdicts are per instance rather than per channel, because SCLK, MOSI, MISO
    and CS_N are one measurement, not four.
    """
    from . import patterns as P

    n_inst, div_sel = P.SPI_BANK_MODES[mode]
    r = deep_checks.decode_spi_bank(channels, n_inst, div_sel=div_sel,
                                    sample_rate_hz=sample_rate_hz)

    out: list[Verdict] = []
    for inst in r.instances:
        c = 4 * inst.instance
        got = ("MOSI %s | MISO %s"
               % (" ".join("%04X" % w for w in inst.mosi_words[:2]) or "-",
                  " ".join("%04X" % w for w in inst.miso_words[:2]) or "-"))
        want = ("MOSI 0x%04X | MISO 0x%04X on every complete word"
                % (inst.expect_mosi, inst.expect_miso))
        detail = ("SCLK %.4g MHz on CH%d; %d complete MOSI / %d complete MISO "
                  "words checked (first and last are capture fragments)"
                  % (inst.sclk_hz / 1e6, c, len(inst.interior_mosi),
                     len(inst.interior_miso)))
        if not inst.interior_mosi:
            detail = ("no complete MOSI word on CH%d..CH%d (SCLK %.4g MHz on CH%d) "
                      "- the capture may be too short to hold a whole word, or "
                      "SCLK/CS_N are not reaching the analyser"
                      % (c, c + 3, inst.sclk_hz / 1e6, c))
        # The channel is reported as -1 so the per-channel merge keeps this
        # verdict instead of letting a SKIP placeholder overwrite it. The
        # channels involved are named in the text.
        out.append(Verdict(-1, inst.ok, "spi%d" % inst.instance,
                           got, want, detail,
                           channel_text="CH%d..CH%d" % (c, c + 3),
                           channels=tuple(range(c, c + 4))))
    return out


def _protocol_verdicts(channels: np.ndarray, sample_rate_hz: float,
                       mode: int = 4) -> list[Verdict]:
    """M4: the I2C and UART generators alongside the SPI bank."""
    out: list[Verdict] = []
    n = channels.shape[0]

    def ch(i):
        return channels[i] if i < n else None

    # UART TX on CH26, repeating "SLogic32U3\r\n" at 115200
    uart_ch = 26
    s = ch(uart_ch)
    if s is not None:
        got = deep_checks.decode_uart(s, sample_rate_hz, 115_200)
        want = b"SLogic32U3\r\n"
        # The capture starts wherever it starts, so the first decoded byte is
        # normally a fragment and the last is usually cut off. The transmitter
        # repeats, so "does the text appear" is the meaningful test.
        ok = want in got or want in got + got[:len(want)]
        div = int(54e6 / 115_200)       # integer division, as the RTL does it
        out.append(Verdict(-1, ok, "uart",
                           repr(got[:16]), repr(want),
                           "CH%d; 115200 baud; RTL divider %d -> %.1f Hz (%+.2f%%)"
                           % (uart_ch, div, 54e6 / div,
                              100.0 * (54e6 / div - 115_200) / 115_200),
                           channel_text="CH%d" % uart_ch,
                           channels=(uart_ch,)))

    # I2C: one write transaction per frame, SCL on CH24 and SDA on CH25
    scl, sda = ch(24), ch(25)
    if scl is not None and sda is not None:
        txs = deep_checks.decode_i2c(scl, sda)
        want = list(deep_checks_patterns_i2c())
        # Look INSIDE each transaction rather than scanning the concatenation of
        # all of them.
        #
        # Concatenating makes the result depend on how many transactions the
        # decoder happened to find and in what order: one spurious empty
        # transaction between two real ones breaks a sequence that is present in
        # full. That is exactly what happened with the fixture's duplicated STOP
        # (four per frame, now fixed in pat_proto.v) -- the verdict alternated
        # between PASS and FAIL on identical hardware because the spurious
        # transactions landed differently relative to the capture start.
        #
        # Per transaction, a spurious one contributes nothing and a real one is
        # judged on its own bytes.
        hit = None
        for tx in txs:
            b = list(tx.bytes_)
            if any(b[k:k + len(want)] == want for k in range(len(b))):
                hit = b
                break
        allb = [x for tx in txs for x in tx.bytes_]
        out.append(Verdict(-1, hit is not None, "i2c",
                           "%d tx, %s" % (len(txs),
                                          " ".join("%02X" % x for x in allb[:6])),
                           "a transaction containing %s"
                           % " ".join("%02X" % x for x in want),
                           ("CH24 SCL / CH25 SDA; 100 kHz; found in one "
                            "transaction" if hit is not None else
                            "CH24 SCL / CH25 SDA; 100 kHz; no single transaction "
                            "contained the expected sequence"),
                           channel_text="CH24/CH25",
                           channels=(24, 25)))
    return out


def deep_checks_patterns_i2c() -> tuple[int, ...]:
    from .patterns import I2C_ADDR_W, I2C_DATA
    return (I2C_ADDR_W,) + I2C_DATA


def _high_widths(s: np.ndarray) -> tuple[list[int], list[int]]:
    """(每个高电平段的宽度, 相邻两高段之间的低电平宽度)，单位采样点。"""
    d = np.diff(np.concatenate(([0], s, [0])))
    starts = np.flatnonzero(d == 1)
    ends = np.flatnonzero(d == -1)
    highs = (ends - starts).astype(int).tolist()
    k = min(len(ends), len(starts))
    g = starts[1:k] - ends[:k - 1] if k > 1 else starts[:0]
    return highs, g[g > 0].astype(int).tolist()


def _narrow_pulse_verdicts(channels: np.ndarray, sample_rate_hz: float) -> list[Verdict]:
    """M5 的 CH2/CH3/CH4：判**脉冲宽度**，不判边沿率。

    这三路的信号形态让"频率"这个判据失效（档案里原本就标成 SKIP），但它们的
    真实意图是**窄脉冲捕获能力**，完全可以用宽度来判：

      CH2  两个 18.5 ns 的脉冲，**相隔 18.5 ns**（能不能分辨开）
      CH3  每 1 ms 一个 18.5 ns 的脉冲（稀疏窄脉冲）
      CH4  宽度在 80 us 内从 1 扫到 128 个时钟（18.5 ns ~ 2.37 us）

    所以这里给它们一个**真实的判定**（合成一条跨通道判定，channel=-1，避免被
    pipeline 按通道号去重丢掉）。容差放宽到 ±2 个采样点：18.5 ns 在 200 MSa/s
    下只有 3.7 个采样点，量化本身就有 ±5 ns。
    """
    out: list[Verdict] = []
    n = channels.shape[0]
    if n < 5:
        return out
    dt_ns = 1e9 / sample_rate_hz
    clk_ns = 1e9 / 54e6                     # 一个 54 MHz 时钟 = 18.5 ns
    tol_ns = 2.0 * dt_ns                    # ±2 个采样点
    got_l: list[str] = []
    bad_l: list[str] = []
    for ch in (2, 3, 4):
        highs, _gaps = _high_widths(channels[ch])
        if not highs:
            got_l.append("CH%d 没抓到脉冲" % ch)
            bad_l.append("CH%d 完全没有脉冲（期望 %s）"
                         % (ch, "两个 18.5 ns 脉冲" if ch == 2 else
                          "每 1 ms 一个 18.5 ns 脉冲" if ch == 3 else
                          "18.5 ns~2.37 us 宽度扫描"))
            continue
        med_ns = float(np.median(highs)) * dt_ns
        if ch == 4:
            lo_ns = min(highs) * dt_ns
            hi_ns = max(highs) * dt_ns
            got_l.append("CH%d %d 个脉冲，宽 %.1f~%.1f ns" % (ch, len(highs), lo_ns, hi_ns))
            # 规格（rtl/pat_core.v:270 + docs/patterns.md）写的是一条**幂次阶梯
            # 1,2,4,...,128 clk_sys**，共 8 档，所以最窄 18.5 ns、最宽 2370 ns。
            #
            # 判据按**规格**写，不按实测写。2026-10-15 实测只到 595 ns（32 clk），
            # 查 RTL 发现 `(6'd1 << wsel)` 是 6 位表达式：wsel=6/7 时 64/128 被截成
            # 0，于是最上面两档根本不发脉冲。按实测放宽判据会把夹具的 bug 放过去，
            # 所以这里要求阶梯必须真的扫到 128 clk（留 10% 余量）。
            if lo_ns > 4 * clk_ns or hi_ns < 115 * clk_ns:
                bad_l.append("CH%d 宽度阶梯没扫到顶（实测最宽 %.1f ns，规格要求 "
                             "128 clk = %.1f ns；最窄 %.1f ns，规格 1 clk = %.1f ns）"
                             % (ch, hi_ns, 128 * clk_ns, lo_ns, clk_ns))
        else:
            got_l.append("CH%d %d 个脉冲，中位宽 %.1f ns" % (ch, len(highs), med_ns))
            if abs(med_ns - clk_ns) > tol_ns:
                bad_l.append("CH%d 中位脉宽 %.1f ns，期望约 %.1f ns（±%.1f ns）"
                             % (ch, med_ns, clk_ns, tol_ns))
    out.append(Verdict(-1, not bad_l, "narrow-pulse",
                       "; ".join(got_l),
                       "CH2/CH3 = 18.5 ns 脉冲；CH4 = 18.5 ns~2.37 us 扫描",
                       "; ".join(bad_l),
                       channel_text="CH2~CH4", channels=(2, 3, 4),
                       reason="" if not bad_l else "narrow_pulse"))
    return out


def _burst_verdicts(channels: np.ndarray, sample_rate_hz: float) -> list[Verdict]:
    """M6: measure the burst envelope and the pulse widths inside it."""
    out: list[Verdict] = []
    n = channels.shape[0]
    if n < 1:
        return out
    r = deep_checks.check_burst(channels[0], sample_rate_hz)
    period_ms = (r.period / sample_rate_hz * 1e3) if r.period else 0.0
    ok = r.ok and r.period is not None and abs(period_ms - 20.0) < 2.0
    out.append(Verdict(-1, ok, "burst",
                       "%d pulses, period %.1f ms" % (len(r.widths), period_ms),
                       "1 ms burst every 20 ms",
                       "envelope measured from the silent gap",
                       channel_text="CH0", channels=(0,)))
    # 四个窄脉冲通道的宽度是刻意做成阶梯的，但**必须合并成一条跨通道判定**。
    #
    # 以前这里一条通道一条判定、而且带的是正数通道号，于是被 pipeline 的
    # `verdicts_by_channel.setdefault(v.channel, v)` 静默丢掉 —— 通道 0..3 早被
    # 逐通道判定占住了。结果 M6 只剩 32 条全 SKIP 的逐通道判定，报
    # "0 ok / 0 fail / 32 skip" 看着像通过，其实 burst 判定从来没跑过。
    # 结构化判定一律用 channel<0 + channel_text，才会按 kind 进 extra 保留下来。
    widths_clk = (8, 16, 32, 64)
    clk_ns = 1e9 / 54e6
    got_l, want_l, bad_l = [], [], []
    for c in range(min(4, n)):
        b = deep_checks.check_burst(channels[c], sample_rate_hz)
        want_ns = widths_clk[c] * clk_ns
        want_l.append("%.0f ns" % want_ns)
        if not b.widths:
            got_l.append("CH%d 没测到脉冲" % c)
            bad_l.append("CH%d 完全没有脉冲（期望 %.0f ns 宽）" % (c, want_ns))
            continue
        med = sorted(b.widths)[len(b.widths) // 2]
        got_ns = med / sample_rate_hz * 1e9
        got_l.append("CH%d %.0f ns" % (c, got_ns))
        if abs(got_ns - want_ns) > max(want_ns * 0.25, 2 * 1e9 / sample_rate_hz):
            bad_l.append("CH%d 实测 %.0f ns，期望 %.0f ns" % (c, got_ns, want_ns))
    out.append(Verdict(-1, not bad_l, "burst-width",
                       "; ".join(got_l), "; ".join(want_l),
                       "; ".join(bad_l),
                       channel_text="CH0~CH3",
                       channels=tuple(range(min(4, n)))))
    return out


def evaluate(mode: int, channels: np.ndarray, expects: list[Expect],
             sample_rate_hz: float, tol_pct: float = 5.0,
             tol_pp: float = 5.0, glitch_frac: float = 0.0) -> list[Verdict]:
    """Measure a capture and check it against the model.

    Structured checks are added on top of the per-channel ones. Modes 1..4 are the
    SPI bank: the per-channel model covers SCLK and CS_N by edge rate, and the
    decoder supplies the verdict that actually matters -- whether the known word
    came through. M0 adds a rotation check, because "which channel is high" is the
    whole point of a wiring capture and no single channel can prove it.

    glitch_frac 默认 0：判定基于**原始边沿**，不做任何毛刺滤波（理由见
    measure_channel 的说明）。下面那段"疑似测量问题"的标注只写 verdict.suspect，
    **不改变任何 ok 值**。
    """
    ms = measure_all(channels, glitch_frac)
    verdicts = [check_channel(m, e, tol_pct, tol_pp, sample_rate_hz)
                for m, e in zip(ms, expects)]
    if mode == 0:
        from .patterns import WIRING_STEP_S, WIRING_STEPS
        verdicts.append(check_order(ms, samples_per_step=WIRING_STEP_S * sample_rate_hz,
                                    period=WIRING_STEPS))
    elif mode in (1, 2, 3, 4):
        verdicts.extend(_spi_bank_verdicts(channels, sample_rate_hz, mode))
        if mode == 4:
            verdicts.extend(_protocol_verdicts(channels, sample_rate_hz, mode))
    elif mode == 5:
        # CH2/CH3/CH4 判脉冲宽度（它们用边沿率判不了），CH0/CH1/CH5+ 仍走逐通道频率
        verdicts.extend(_narrow_pulse_verdicts(channels, sample_rate_hz))
    elif mode == 6:
        verdicts.extend(_burst_verdicts(channels, sample_rate_hz))
        # CH4 and up are judged one by one by the fill check, replacing their
        # "covered" placeholder -- per channel, so a threshold-group pass takes
        # over exactly its own channels
        complete, every = deep_checks.burst_windows(channels[0], sample_rate_hz)
        for c in range(4, min(len(verdicts), channels.shape[0])):
            if verdicts[c].channel != c:
                continue
            r = deep_checks.check_burst_fill(channels[c], sample_rate_hz,
                                             complete, every)
            verdicts[c] = Verdict(c, r.ok, "burst-fill", r.measured,
                                  "1 kHz for 0.5 ms of each 1 ms burst, low between",
                                  r.detail, reason="" if r.ok else "burst_fill")

    # 只标注、不改判：某条逐通道失败如果换成"滤掉窄毛刺"的读法本可通过，那它
    # **可能**是探头振铃/接地不良（测量问题）而不是板子的问题。判定层不替人下
    # 结论，只把这个事实写进 suspect，让界面提示去查探头/用示波器确认。
    if glitch_frac <= 0.0 and any(v.ok is False and v.channel >= 0 for v in verdicts):
        try:
            ms_f = measure_all(channels, 0.1)
            for v in verdicts:
                if v.ok is not False or v.channel < 0 or v.channel >= len(ms_f):
                    continue
                if v.channel >= len(expects):
                    continue
                if check_channel(ms_f[v.channel], expects[v.channel],
                                 tol_pct, tol_pp, sample_rate_hz).ok:
                    v.suspect = (
                        "这条失败是窄毛刺造成的（把窄毛刺滤掉后本可通过）—— "
                        "可能是探头振铃/接地不良，也可能是板子或固件真的产生了"
                        "窄脉冲。判定不做让步，建议换标配同轴探头线或用示波器确认")
        except Exception:                                     # noqa: BLE001
            pass
    return verdicts


def summarise(verdicts: list[Verdict]) -> tuple[bool, int, int, int]:
    """(ok, passed, failed, skipped)."""
    p = sum(1 for v in verdicts if v.ok is True)
    f = sum(1 for v in verdicts if v.ok is False)
    s = sum(1 for v in verdicts if v.ok is None)
    return (f == 0, p, f, s)


# ---------------------------------------------------------------------------
# self-test: synthesise captures and check the checkers
# ---------------------------------------------------------------------------

def _selftest() -> int:
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    fails = 0

    # NOTE on window length: a capture must span enough periods of the SLOWEST
    # signal being checked. An early version of this self-test used a 2 ms window
    # for a 1 kHz signal, which contains exactly two periods and -- when the phase
    # lands so that the last rising edge falls outside -- only ONE rising edge, so
    # no frequency can be computed at all. The same trap applies to real captures:
    # M0's 1 kHz channel needs a window of at least a few milliseconds. This is why
    # the profile carries a per-mode window rather than one global value.

    def make(freq, n, fs, duty=0.5, phase=0.0):
        t = np.arange(n)
        period = fs / freq
        ph = np.mod(t / period + phase, 1.0)
        return (ph < duty).astype(np.uint8)

    print("== clean signals ==")
    cases = [(964320.0, 200_000_000, 400_000, 0.5),
             (421875.0, 200_000_000, 400_000, 1 / 32),
             (250000.0, 200_000_000, 400_000, 0.5),
             (100000.0, 200_000_000, 400_000, 0.01),
             (1000.0, 2_000_000, 40_000, 0.5)]       # 20 ms -> 20 periods
    for freq, fs, n, duty in cases:
        s = make(freq, n, fs, duty)
        m = measure_channel(s, 0)
        got = m.freq_hz * fs
        err = 100.0 * (got - freq) / freq
        dok = m.duty_pct is not None and abs(m.duty_pct - duty * 100) < 1.0
        ok = abs(err) < 0.5
        print("  %9.1f Hz duty %5.2f%% -> %10.1f Hz duty %5.2f%%  %s"
              % (freq, duty * 100, got, m.duty_pct if m.duty_pct is not None else -1,
                 "ok" if (ok and dok) else "FAIL"))
        fails += 0 if (ok and dok) else 1

    print("== the M5 trap: 74 ns real pulses must NOT be filtered ==")
    m = measure_channel(make(421875.0, 400_000, 200_000_000, 1 / 32), 0)
    print("  glitches filtered: %d (must be 0)" % m.glitches)
    if m.glitches != 0:
        print("  FAIL: genuine narrow pulses were treated as ringing"); fails += 1

    print("== the M0 trap: 25 ns of ringing must be filtered ==")
    # 100 kHz at 200 MSa/s over 2 ms -> 200 periods, and 5 samples = 25 ns
    s = make(100000.0, 400_000, 200_000_000)
    rises = np.flatnonzero((s[:-1] == 0) & (s[1:] == 1))
    for k in rises:                       # a short notch + spike after each edge
        if k + 10 < s.size:
            s[k + 2:k + 5] = 0
            s[k + 5] = 1
    m_raw = measure_channel(s, 0, glitch_frac=0.0)
    m = measure_channel(s, 0, glitch_frac=0.1)
    print("  %d rising edges, 25 ns ringing injected on each" % rises.size)
    print("  no filtering  : %12.1f Hz" % (m_raw.freq_hz * 200_000_000))
    print("  with filtering: %12.1f Hz  (expect 100000)" % (m.freq_hz * 200_000_000))
    if abs(m.freq_hz * 200_000_000 - 100000.0) > 200.0:
        print("  FAIL: ringing not rejected"); fails += 1

    print("== static detection ==")
    for lvl in (0, 1):
        m = measure_channel(np.full(1000, lvl, dtype=np.uint8), 0)
        print("  constant %d -> static=%s toggling=%s" % (lvl, m.static, m.toggling))
        if m.static != lvl or m.toggling:
            print("  FAIL"); fails += 1

    print("== M3 rotation order ==")
    # 4 ms window so a whole 3.2 ms rotation fits; a 2 ms window holds less than
    # one cycle, and then every channel shows a single pulse whose "frequency" is
    # just an artefact of where the window happened to start.
    nch, n, fs = 8, 800_000, 200_000_000
    t = np.arange(n)
    step = int(100e-6 * fs)
    chans = np.zeros((nch, n), dtype=np.uint8)
    for ch in range(nch):
        ph = (t - (ch + 2) * step) % (nch * step)   # start at CH2
        chans[ch] = (ph < step).astype(np.uint8)
    ms = measure_all(chans)
    v = check_order(ms, samples_per_step=step)
    print("  %s  %s  (each channel reads %.2f Hz)"
          % (v.mark, v.measured, ms[0].freq_hz * fs))
    if not v.ok:
        print("  FAIL: correct rotation rejected"); fails += 1

    print("== M3 swapped pair must be caught ==")
    chans2 = chans.copy()
    chans2[[3, 4]] = chans2[[4, 3]]
    v2 = check_order(measure_all(chans2), samples_per_step=step)
    print("  %s  %s" % (v2.mark, (v2.detail or "")[:100]))
    if v2.ok:
        print("  FAIL: swap not detected"); fails += 1

    print("== a channel that never fires must not silently pass order ==")
    chans3 = chans.copy()
    chans3[5] = 0
    v3 = check_order(measure_all(chans3), samples_per_step=step)
    print("  %s  (%d channels seen)" % (v3.mark, 7))
    if v3.ok:
        print("  FAIL: a dead channel was accepted"); fails += 1

    print("== 16 of a 32-step rotation (16U3 in front of the fixture) ==")
    # 10 ms, as the M0 plan captures: a channel needs two rising edges, and the
    # 32-step rotation takes 3.2 ms
    nch16, per, n16 = 16, 32, 2_000_000
    t16 = np.arange(n16)
    chans16 = np.zeros((nch16, n16), dtype=np.uint8)
    for ch in range(nch16):
        ph = (t16 - (ch - 10) * step - step // 2) % (per * step)   # CH10 fires first
        chans16[ch] = (ph < step).astype(np.uint8)
    v4 = check_order(measure_all(chans16), samples_per_step=step, period=per)
    print("  %s  %s" % (v4.mark, v4.measured))
    if not v4.ok:
        print("  FAIL: partial rotation rejected"); fails += 1
    if check_order(measure_all(chans16), samples_per_step=step).ok:
        print("  FAIL: without period the 17-step wrap should not pass"); fails += 1
    chans16b = chans16.copy()
    chans16b[[3, 4]] = chans16b[[4, 3]]
    if check_order(measure_all(chans16b), samples_per_step=step, period=per).ok:
        print("  FAIL: swap in a partial rotation not detected"); fails += 1

    print()
    print("RESULT: %s" % ("OK" if not fails else "%d FAILURES" % fails))
    return 1 if fails else 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
