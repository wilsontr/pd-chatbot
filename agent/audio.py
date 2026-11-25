"""Measurements of a rendered WAV, for checking a patch against what was asked."""

from __future__ import annotations

import io
import math
import wave
from typing import Any

import numpy as np

SILENCE_PEAK = 1e-4  # about -80 dBFS
_SEGMENTS = 8


def _db(value: float) -> float | None:
    return round(20 * math.log10(value), 1) if value > 1e-9 else None


def read_wav(data: bytes) -> tuple[np.ndarray, int]:
    """Samples as floats, shape (frames, channels), and the sample rate."""
    with wave.open(io.BytesIO(data)) as w:
        raw = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        return raw.reshape(-1, w.getnchannels()).astype(np.float64) / 32767, w.getframerate()


def _peaks(mono: np.ndarray, rate: int, count: int) -> list[tuple[float, float]]:
    """Strongest spectral peaks as (Hz, dB below the strongest)."""
    n = len(mono)
    if n < 64:
        return []
    x = mono - mono.mean()
    size = max(n, 1 << 15)  # zero-pad short clips so peaks are not bin-quantised
    mag = np.abs(np.fft.rfft(x * np.hanning(n), size))
    if mag.max() < 1e-6 * n:
        return []
    inner = mag[1:-1]
    index = np.flatnonzero((inner > mag[:-2]) & (inner >= mag[2:]) & (inner > mag.max() * 0.01)) + 1
    index = index[index * rate / size >= 15]
    found: list[tuple[float, float]] = []
    min_gap = max(10.0, 4 * rate / n)  # one entry per main lobe
    for i in index[np.argsort(mag[index])[::-1]]:
        a, b, c = np.log(mag[i - 1 : i + 2] + 1e-12)
        bottom = a - 2 * b + c
        offset = 0.5 * (a - c) / bottom if abs(bottom) > 1e-12 else 0.0
        hz = (i + offset) * rate / size
        if all(abs(hz - other) >= min_gap for other, _ in found):
            found.append((round(float(hz), 1), _db(mag[i] / mag.max()) or 0.0))
            if len(found) == count:
                break
    return found


def analyze(wav: bytes) -> dict[str, Any]:
    samples, rate = read_wav(wav)
    if not len(samples):
        return {"duration_seconds": 0.0, "silent": True}
    peak = float(np.abs(samples).max())
    levels = np.sqrt((samples**2).mean(axis=0))
    result: dict[str, Any] = {
        "duration_seconds": round(len(samples) / rate, 3),
        "silent": peak < SILENCE_PEAK,
        "peak": round(peak, 4),
        "rms": round(float(np.sqrt((samples**2).mean())), 4),
        "rms_db": _db(float(np.sqrt((samples**2).mean()))),
        "channel_rms": [round(float(v), 4) for v in levels],
        "clipped_fraction": round(float((np.abs(samples) >= 0.9999).mean()), 5),
        "dc_offset": round(float(samples.mean()), 4),
    }
    if result["silent"]:
        return result
    # The louder channel, because summing channels can cancel them.
    mono = samples[:, int(levels.argmax())]
    peaks = _peaks(mono, rate, 6)
    power = np.abs(np.fft.rfft(mono - mono.mean())) ** 2
    freqs = np.fft.rfftfreq(len(mono), 1 / rate)
    total = float(power.sum())
    result["dominant_hz"] = peaks[0][0] if peaks else None
    result["spectral_peaks"] = [{"hz": hz, "db": db} for hz, db in peaks]
    result["spectral_centroid_hz"] = round(float((freqs * power).sum() / total), 1) if total else None
    result["segments"] = [
        {
            "start": round(i * len(mono) / _SEGMENTS / rate, 3),
            "rms": round(float(np.sqrt((part**2).mean())), 4),
            "dominant_hz": (_peaks(part, rate, 1) or [(None, 0)])[0][0],
        }
        for i, part in enumerate(np.array_split(mono, _SEGMENTS))
        if len(part)
    ]
    return result
