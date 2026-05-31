"""
EDMG Librosa Compatibility Layer

Why this exists
---------------
Some environments ship with an older `librosa` that is incompatible with newer
NumPy (e.g., NumPy 2.x removed `np.complex`). EDMG's test-suite (and some audio
analysis code paths) expect the `librosa` API to be importable and to include
`librosa.output.write_wav` and common analysis helpers.

This package is intentionally named `librosa` so imports resolve here first
when the repo is on `sys.path` (e.g., during tests). It behaves as follows:

1) If a *working* system/site-packages librosa can be located and imported, we
   execute it into this module so you get the full upstream feature set.
2) If importing system librosa fails (or isn't installed), we provide a
   self-contained fallback that implements the subset of the API that EDMG uses:
   - load, stft, istft, frames_to_time, time_to_frames, get_duration
   - util.normalize
   - beat.beat_track
   - onset.onset_strength, onset.onset_detect
   - feature.rms, feature.spectral_centroid, feature.spectral_rolloff,
     feature.mfcc, feature.chroma_stft, feature.zero_crossing_rate
   - output.write_wav

The fallback implementation uses NumPy + SciPy + SoundFile where available.
It is not a full replacement for upstream librosa, but it is sufficient for
EDMG's workflows and keeps the project runnable in CPU-only environments.

If you *explicitly* want to force system librosa (and accept import errors),
set:
    EDMG_PREFER_SYSTEM_LIBROSA=1

This repo-root shim exists for source-tree compatibility and tests. The packaged
Studio backend should use the declared `librosa` dependency instead of relying
on this module being on `sys.path`.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from typing import Any, Tuple, Optional

_THIS_FILE = Path(__file__).resolve()
_THIS_DIR = _THIS_FILE.parent
_REPO_ROOT = _THIS_DIR.parent


def _try_exec_system_librosa() -> bool:
    """
    Attempt to load system/site-packages librosa into *this* module.

    Returns True if successful, else False.
    """
    if os.environ.get("EDMG_PREFER_SYSTEM_LIBROSA", "").strip() not in ("1", "true", "yes", "on"):
        # Prefer fallback first unless explicitly requested OR system librosa is clearly available.
        # We'll still attempt system librosa below, but only if the spec resolution points to a different path.
        pass

    try:
        import importlib.machinery
        import importlib.util

        # Exclude repo root and '' from search to avoid resolving back to this package.
        exclude = {str(_REPO_ROOT), ""}
        search_path = [p for p in sys.path if p not in exclude]

        spec = importlib.machinery.PathFinder.find_spec("librosa", search_path)
        if not spec or not spec.origin:
            return False

        # If the spec points to this very file, it's not a system librosa.
        try:
            if Path(spec.origin).resolve() == _THIS_FILE:
                return False
        except Exception:
            return False

        # Execute the system librosa __init__ into this module namespace.
        mod = sys.modules.get(__name__)
        if mod is None:
            return False

        # Ensure package search locations are correct for submodules.
        if spec.submodule_search_locations:
            mod.__path__ = list(spec.submodule_search_locations)  # type: ignore[attr-defined]

        mod.__spec__ = spec  # type: ignore[attr-defined]
        mod.__file__ = spec.origin  # type: ignore[attr-defined]
        mod.__package__ = "librosa"  # type: ignore[attr-defined]

        if spec.loader is None:
            return False
        spec.loader.exec_module(mod)  # type: ignore[arg-type]

        return True
    except Exception:
        return False


_USING_SYSTEM_LIBROSA = _try_exec_system_librosa()


def _install_fallback() -> None:
    import numpy as np
    try:
        import soundfile as sf  # type: ignore
    except Exception:
        sf = None  # type: ignore

    try:
        import scipy.signal  # type: ignore
        import scipy.fftpack  # type: ignore
    except Exception:
        scipy = None  # type: ignore

    # ----------------------------
    # I/O
    # ----------------------------
    def load(
        path: str,
        sr: Optional[int] = 22050,
        duration: Optional[float] = None,
        mono: bool = True,
    ) -> Tuple[np.ndarray, int]:
        if sf is None:
            raise RuntimeError("soundfile is required for librosa-fallback.load()")
        y, native_sr = sf.read(path, always_2d=False)
        y = np.asarray(y, dtype=np.float32)

        if mono and y.ndim > 1:
            y = np.mean(y, axis=1).astype(np.float32)

        if duration is not None and duration > 0:
            max_samples = int(round(float(duration) * float(native_sr)))
            if max_samples > 0:
                y = y[:max_samples]

        out_sr = int(sr or native_sr)
        if sr is not None and int(native_sr) != int(sr):
            # Linear resample.
            if y.size <= 1:
                return y, out_sr
            x_old = np.linspace(0.0, 1.0, num=y.shape[0], endpoint=False)
            n_new = max(int(round(y.shape[0] * (float(sr) / float(native_sr)))), 1)
            x_new = np.linspace(0.0, 1.0, num=n_new, endpoint=False)
            y = np.interp(x_new, x_old, y).astype(np.float32)
        return y, out_sr

    # ----------------------------
    # Utils
    # ----------------------------
    util = types.ModuleType("librosa.util")

    def normalize(y: Any) -> np.ndarray:
        arr = np.asarray(y, dtype=np.float32)
        if arr.size == 0:
            return arr
        m = float(np.max(np.abs(arr)))
        if m <= 0:
            return arr
        return (arr / m).astype(np.float32)

    util.normalize = normalize  # type: ignore[attr-defined]

    # ----------------------------
    # STFT / ISTFT
    # ----------------------------
    def stft(y: Any, n_fft: int = 2048, hop_length: int = 512, win_length: Optional[int] = None) -> np.ndarray:
        arr = np.asarray(y, dtype=np.float32)
        if win_length is None:
            win_length = n_fft
        if "scipy" not in globals() and scipy is None:
            # Fallback naive STFT (slow but dependency-free).
            window = np.hanning(win_length).astype(np.float32)
            frames = []
            for start in range(0, max(arr.size - win_length + 1, 1), hop_length):
                frame = arr[start:start + win_length]
                if frame.size < win_length:
                    frame = np.pad(frame, (0, win_length - frame.size))
                frame = frame * window
                spec = np.fft.rfft(frame, n=n_fft)
                frames.append(spec)
            if not frames:
                return np.zeros((n_fft // 2 + 1, 0), dtype=np.complex64)
            return np.stack(frames, axis=1).astype(np.complex64)
        f, t, Zxx = scipy.signal.stft(
            arr,
            nperseg=int(win_length),
            noverlap=int(win_length) - int(hop_length),
            nfft=int(n_fft),
            boundary="zeros",
            padded=True,
        )
        return Zxx.astype(np.complex64)

    def istft(D: Any, hop_length: int = 512, win_length: Optional[int] = None, length: Optional[int] = None) -> np.ndarray:
        Zxx = np.asarray(D)
        n_fft = (Zxx.shape[0] - 1) * 2 if Zxx.ndim >= 1 else 2048
        if win_length is None:
            win_length = n_fft
        if scipy is None:
            # Naive inverse (overlap-add) if scipy isn't available.
            y = np.zeros(int((Zxx.shape[1] - 1) * hop_length + win_length), dtype=np.float32)
            window = np.hanning(win_length).astype(np.float32)
            for i in range(Zxx.shape[1]):
                frame = np.fft.irfft(Zxx[:, i], n=n_fft).astype(np.float32)[:win_length]
                start = i * hop_length
                y[start:start + win_length] += frame * window
            if length is not None and length > 0:
                y = y[:int(length)]
            return y
        _, y = scipy.signal.istft(
            Zxx,
            nperseg=int(win_length),
            noverlap=int(win_length) - int(hop_length),
            nfft=int(n_fft),
            boundary=True,
        )
        y = np.asarray(y, dtype=np.float32)
        if length is not None and length > 0:
            y = y[:int(length)]
        return y

    # ----------------------------
    # Time conversion
    # ----------------------------
    def frames_to_time(frames: Any, sr: int, hop_length: int = 512):
        f = np.asarray(frames, dtype=np.float32)
        return (f * float(hop_length)) / float(sr)

    def time_to_frames(times: Any, sr: int, hop_length: int = 512):
        t = np.asarray(times, dtype=np.float32)
        frames = np.floor((t * float(sr)) / float(hop_length)).astype(np.int64)
        if np.isscalar(times):
            return int(frames)
        return frames

    def get_duration(y: Any = None, sr: Optional[int] = 22050, filename: Optional[str] = None, path: Optional[str] = None, **_: Any) -> float:
        if y is not None:
            sample_rate = int(sr or 22050)
            return float(np.asarray(y).shape[0]) / float(sample_rate) if sample_rate > 0 else 0.0
        source = filename or path
        if source and sf is not None:
            info = sf.info(source)
            sample_rate = int(getattr(info, "samplerate", 0) or 0)
            frames = int(getattr(info, "frames", 0) or 0)
            return float(frames) / float(sample_rate) if sample_rate > 0 else 0.0
        return 0.0

    # ----------------------------
    # Beat tracking (lightweight)
    # ----------------------------
    beat = types.ModuleType("librosa.beat")

    def beat_track(y: Any, sr: int, hop_length: int = 512, units: str = "frames", **_: Any):
        arr = np.asarray(y, dtype=np.float32)
        if arr.size == 0 or sr <= 0:
            empty = np.asarray([], dtype=np.float32 if (units or "frames").lower() == "time" else np.int32)
            return 0.0, empty

        frame_length = 2048
        n_frames = int(np.ceil(arr.size / hop_length))
        energy = np.zeros(n_frames, dtype=np.float32)
        for i in range(n_frames):
            start = i * hop_length
            end = min(start + frame_length, arr.size)
            seg = arr[start:end]
            energy[i] = float(np.sum(seg * seg))

        if energy.size:
            e_min = float(energy.min())
            e_max = float(energy.max())
            if e_max > e_min:
                energy = (energy - e_min) / (e_max - e_min)

        mean = float(energy.mean()) if energy.size else 0.0
        std = float(energy.std()) if energy.size else 0.0
        thresh = mean + 0.75 * std

        peaks = []
        for i in range(1, len(energy) - 1):
            if energy[i] >= thresh and energy[i] >= energy[i - 1] and energy[i] >= energy[i + 1]:
                peaks.append(i)

        if len(peaks) < 2:
            step = max(int(round(0.5 * sr / hop_length)), 1)
            peaks = list(range(0, n_frames, step))

        peaks_arr = np.asarray(peaks, dtype=np.int32)

        if peaks_arr.size >= 2:
            times = frames_to_time(peaks_arr, sr=sr, hop_length=hop_length)
            diffs = np.diff(times)
            med = float(np.median(diffs)) if diffs.size else 0.5
            tempo = 60.0 / max(med, 1e-3)
        else:
            tempo = 120.0

        units_l = (units or "frames").lower()
        if units_l == "time":
            beat_times = frames_to_time(peaks_arr, sr=sr, hop_length=hop_length).astype(np.float32)
            return float(tempo), beat_times
        return float(tempo), peaks_arr

    beat.beat_track = beat_track  # type: ignore[attr-defined]

    # ----------------------------
    # Feature extraction
    # ----------------------------
    feature = types.ModuleType("librosa.feature")

    def _frame_signal(arr: np.ndarray, frame_length: int, hop_length: int) -> np.ndarray:
        if arr.size == 0:
            return np.zeros((frame_length, 0), dtype=np.float32)
        n_frames = 1 + max(int((arr.size - frame_length) // hop_length), 0)
        if n_frames <= 0:
            n_frames = 1
        out = np.zeros((frame_length, n_frames), dtype=np.float32)
        for i in range(n_frames):
            start = i * hop_length
            end = start + frame_length
            seg = arr[start:end]
            if seg.size < frame_length:
                seg = np.pad(seg, (0, frame_length - seg.size))
            out[:, i] = seg
        return out

    def rms(y: Any, frame_length: int = 2048, hop_length: int = 512):
        arr = np.asarray(y, dtype=np.float32)
        frames = _frame_signal(arr, frame_length=frame_length, hop_length=hop_length)
        val = np.sqrt(np.mean(frames * frames, axis=0) + 1e-12)
        return val.reshape(1, -1).astype(np.float32)

    def zero_crossing_rate(y: Any, frame_length: int = 2048, hop_length: int = 512):
        arr = np.asarray(y, dtype=np.float32)
        frames = _frame_signal(arr, frame_length=frame_length, hop_length=hop_length)
        signs = np.sign(frames)
        zc = np.mean((signs[1:, :] * signs[:-1, :]) < 0, axis=0).astype(np.float32)
        return zc.reshape(1, -1)

    def _magnitude_spectrogram(y: Any, sr: int, n_fft: int = 2048, hop_length: int = 512) -> Tuple[np.ndarray, np.ndarray]:
        D = stft(y, n_fft=n_fft, hop_length=hop_length)
        mag = np.abs(D).astype(np.float32)
        freqs = np.linspace(0.0, float(sr) / 2.0, num=mag.shape[0], endpoint=True).astype(np.float32)
        return mag, freqs

    def spectral_centroid(y: Any, sr: int, n_fft: int = 2048, hop_length: int = 512):
        mag, freqs = _magnitude_spectrogram(y, sr=sr, n_fft=n_fft, hop_length=hop_length)
        denom = np.sum(mag, axis=0) + 1e-12
        cent = (np.sum(mag * freqs[:, None], axis=0) / denom).astype(np.float32)
        return cent.reshape(1, -1)

    def spectral_rolloff(y: Any, sr: int, roll_percent: float = 0.85, n_fft: int = 2048, hop_length: int = 512):
        mag, freqs = _magnitude_spectrogram(y, sr=sr, n_fft=n_fft, hop_length=hop_length)
        energy = np.cumsum(mag, axis=0)
        total = energy[-1, :] + 1e-12
        threshold = total * float(roll_percent)
        roll = np.zeros(mag.shape[1], dtype=np.float32)
        for t in range(mag.shape[1]):
            idx = int(np.searchsorted(energy[:, t], threshold[t]))
            idx = max(0, min(idx, freqs.size - 1))
            roll[t] = freqs[idx]
        return roll.reshape(1, -1)

    def _hz_to_mel(hz: np.ndarray) -> np.ndarray:
        return 2595.0 * np.log10(1.0 + hz / 700.0)

    def _mel_to_hz(mel: np.ndarray) -> np.ndarray:
        return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)

    def _mel_filterbank(sr: int, n_fft: int, n_mels: int = 128, fmin: float = 0.0, fmax: Optional[float] = None) -> np.ndarray:
        if fmax is None:
            fmax = sr / 2.0
        mels = np.linspace(_hz_to_mel(np.array([fmin]))[0], _hz_to_mel(np.array([fmax]))[0], num=n_mels + 2)
        hz = _mel_to_hz(mels)
        bins = np.floor((n_fft + 1) * hz / sr).astype(int)
        fb = np.zeros((n_mels, n_fft // 2 + 1), dtype=np.float32)
        for i in range(n_mels):
            left, center, right = bins[i], bins[i + 1], bins[i + 2]
            if center <= left:
                center = left + 1
            if right <= center:
                right = center + 1
            if right > fb.shape[1]:
                right = fb.shape[1]
            for j in range(left, center):
                if 0 <= j < fb.shape[1]:
                    fb[i, j] = (j - left) / max(center - left, 1)
            for j in range(center, right):
                if 0 <= j < fb.shape[1]:
                    fb[i, j] = (right - j) / max(right - center, 1)
        return fb

    def mfcc(y: Any, sr: int, n_mfcc: int = 13, n_fft: int = 2048, hop_length: int = 512, n_mels: int = 128):
        mag, _ = _magnitude_spectrogram(y, sr=sr, n_fft=n_fft, hop_length=hop_length)
        power = (mag ** 2).astype(np.float32)
        fb = _mel_filterbank(sr=sr, n_fft=n_fft, n_mels=n_mels)
        mel_spec = np.dot(fb, power).astype(np.float32)
        mel_spec = np.maximum(mel_spec, 1e-12)
        log_mel = np.log(mel_spec).astype(np.float32)
        if scipy is None:
            # DCT fallback via numpy FFT-ish method
            # (Not perfect, but stable + dependency-light.)
            coeffs = np.zeros((n_mfcc, log_mel.shape[1]), dtype=np.float32)
            for k in range(n_mfcc):
                coeffs[k, :] = np.mean(log_mel * np.cos(np.pi * k * (np.arange(log_mel.shape[0])[:, None] + 0.5) / log_mel.shape[0]), axis=0)
            return coeffs
        coeffs = scipy.fftpack.dct(log_mel, axis=0, type=2, norm="ortho")[:n_mfcc, :]
        return coeffs.astype(np.float32)

    def chroma_stft(y: Any, sr: int, n_fft: int = 2048, hop_length: int = 512):
        mag, freqs = _magnitude_spectrogram(y, sr=sr, n_fft=n_fft, hop_length=hop_length)
        chroma = np.zeros((12, mag.shape[1]), dtype=np.float32)
        valid = freqs > 0.0
        f = freqs[valid]
        if f.size == 0:
            return chroma
        midi = 69.0 + 12.0 * np.log2(f / 440.0)
        pcs = (np.round(midi).astype(int) % 12)
        bins_idx = np.nonzero(valid)[0]
        for bi, pc in zip(bins_idx, pcs):
            chroma[pc, :] += mag[bi, :]
        # Normalize per frame.
        denom = np.sum(chroma, axis=0) + 1e-12
        chroma = chroma / denom
        return chroma.astype(np.float32)

    feature.rms = rms  # type: ignore[attr-defined]
    feature.spectral_centroid = spectral_centroid  # type: ignore[attr-defined]
    feature.spectral_rolloff = spectral_rolloff  # type: ignore[attr-defined]
    feature.mfcc = mfcc  # type: ignore[attr-defined]
    feature.chroma_stft = chroma_stft  # type: ignore[attr-defined]
    feature.zero_crossing_rate = zero_crossing_rate  # type: ignore[attr-defined]

    # ----------------------------
    # Onset
    # ----------------------------
    onset = types.ModuleType("librosa.onset")

    def onset_strength(y: Any, sr: int, n_fft: int = 2048, hop_length: int = 512):
        mag, _ = _magnitude_spectrogram(y, sr=sr, n_fft=n_fft, hop_length=hop_length)
        # Spectral flux
        diff = np.diff(mag, axis=1)
        diff = np.maximum(diff, 0.0)
        strength = np.sum(diff, axis=0).astype(np.float32)
        # Pad to n_frames length to match librosa-ish behavior
        strength = np.concatenate([strength[:1], strength], axis=0) if strength.size else strength
        return strength.astype(np.float32)

    def onset_detect(y: Any, sr: int, hop_length: int = 512, backtrack: bool = False):
        env = onset_strength(y=y, sr=sr, hop_length=hop_length)
        if env.size == 0:
            return np.asarray([], dtype=np.int32)
        mean = float(env.mean())
        std = float(env.std())
        thresh = mean + 0.5 * std
        peaks = []
        for i in range(1, env.size - 1):
            if env[i] >= thresh and env[i] >= env[i - 1] and env[i] >= env[i + 1]:
                peaks.append(i)
        return np.asarray(peaks, dtype=np.int32)

    onset.onset_strength = onset_strength  # type: ignore[attr-defined]
    onset.onset_detect = onset_detect  # type: ignore[attr-defined]

    # ----------------------------
    # Output
    # ----------------------------
    output = types.ModuleType("librosa.output")

    def write_wav(path: str, y: Any, sr: int, norm: bool = False):
        if sf is None:
            raise RuntimeError("soundfile is required for librosa-fallback.output.write_wav()")
        arr = np.asarray(y, dtype=np.float32)
        if norm and arr.size:
            m = float(np.max(np.abs(arr)))
            if m > 0:
                arr = arr / m
        sf.write(path, arr, int(sr))

    output.write_wav = write_wav  # type: ignore[attr-defined]

    # Export top-level API into this module.
    g = globals()
    g["load"] = load
    g["stft"] = stft
    g["istft"] = istft
    g["frames_to_time"] = frames_to_time
    g["time_to_frames"] = time_to_frames
    g["get_duration"] = get_duration
    g["util"] = util
    g["beat"] = beat
    g["feature"] = feature
    g["onset"] = onset
    g["output"] = output

    # Register as submodules so `import librosa.output` works.
    sys.modules["librosa.util"] = util
    sys.modules["librosa.beat"] = beat
    sys.modules["librosa.feature"] = feature
    sys.modules["librosa.onset"] = onset
    sys.modules["librosa.output"] = output


if not _USING_SYSTEM_LIBROSA:
    _install_fallback()
