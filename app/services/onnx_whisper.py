"""CPU Whisper via ONNX Runtime — used when PyTorch/c10.dll cannot load.

faster-whisper needs ctranslate2, which imports torch. On this Windows install
that import raises WinError 1114 and the native Whisper() constructor then
access-violates. ORT is already working in-process for LaMa, so we decode
with the Hugging Face whisper-base ONNX export instead.
"""
from __future__ import annotations

import logging
import os
import wave
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_HF_REPO = "onnx-community/whisper-base"
_ENCODER_FILE = "onnx/encoder_model_quantized.onnx"
_DECODER_FILE = "onnx/decoder_model_quantized.onnx"
_TOKENIZER_FILE = "tokenizer.json"
_SAMPLE_RATE = 16000
_N_MELS = 80
_HOP = 160
_N_FFT = 400
_CHUNK = 30
_MAX_FRAMES = (_CHUNK * _SAMPLE_RATE) // _HOP
_MAX_TOKENS = 224

_SESSIONS: Dict[str, Any] = {"encoder": None, "decoder": None, "tokenizer": None, "root": None}


def torch_import_broken() -> bool:
    try:
        from app.core.native_dll_guard import is_torch_available

        return not is_torch_available()
    except Exception:
        pass
    import sys

    cached = sys.modules.get("torch")
    if cached is not None and not getattr(cached, "__version__", None):
        return True
    try:
        import torch

        return not bool(getattr(torch, "__version__", None))
    except Exception:
        return True


def ort_import_broken() -> bool:
    try:
        from app.core.native_dll_guard import is_onnx_available

        return not is_onnx_available()
    except Exception:
        pass
    import sys

    cached = sys.modules.get("onnxruntime")
    if cached is not None and not getattr(cached, "SessionOptions", None):
        return True
    try:
        import onnxruntime  # noqa: F401

        return not bool(getattr(onnxruntime, "SessionOptions", None))
    except Exception:
        return True


def _models_root() -> str:
    try:
        from app.config import settings

        root = settings.MODELS_DIR
        if not os.path.isabs(root):
            root = os.path.join(str(settings.BASE_DIR), root)
        path = os.path.join(os.path.abspath(root), "whisper-onnx-base")
    except Exception:
        path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "data", "models", "whisper-onnx-base")
        )
    os.makedirs(path, exist_ok=True)
    return path


def _download(name: str, dest_dir: str) -> str:
    from huggingface_hub import hf_hub_download

    return hf_hub_download(repo_id=_HF_REPO, filename=name, local_dir=dest_dir)


def _mel_filters() -> np.ndarray:
    n_mels = _N_MELS
    sr = _SAMPLE_RATE
    n_fft = _N_FFT
    fftfreqs = np.fft.rfftfreq(n=n_fft, d=1.0 / sr)
    min_mel = 0.0
    max_mel = 45.245640471924965
    mels = np.linspace(min_mel, max_mel, n_mels + 2)
    f_min = 0.0
    f_sp = 200.0 / 3
    freqs = f_min + f_sp * mels
    min_log_hz = 1000.0
    min_log_mel = (min_log_hz - f_min) / f_sp
    logstep = np.log(6.4) / 27.0
    log_t = mels >= min_log_mel
    freqs[log_t] = min_log_hz * np.exp(logstep * (mels[log_t] - min_log_mel))
    fdiff = np.diff(freqs)
    ramps = freqs.reshape(-1, 1) - fftfreqs.reshape(1, -1)
    lower = -ramps[:-2] / np.expand_dims(fdiff[:-1], axis=1)
    upper = ramps[2:] / np.expand_dims(fdiff[1:], axis=1)
    weights = np.maximum(np.zeros_like(lower), np.minimum(lower, upper))
    enorm = 2.0 / (freqs[2 : n_mels + 2] - freqs[:n_mels])
    weights *= np.expand_dims(enorm, axis=1)
    return weights.astype("float32")


_MEL_FILTERS = None


def log_mel_spectrogram(waveform: np.ndarray) -> np.ndarray:
    global _MEL_FILTERS
    if _MEL_FILTERS is None:
        _MEL_FILTERS = _mel_filters()
    if waveform.dtype != np.float32:
        waveform = waveform.astype(np.float32)
    waveform = np.pad(waveform, (0, 160))
    window = np.hanning(_N_FFT + 1)[:-1].astype("float32")
    padded = np.pad(waveform, (_N_FFT // 2, _N_FFT // 2), mode="reflect")
    n_frames = 1 + (len(padded) - _N_FFT) // _HOP
    frames = np.lib.stride_tricks.as_strided(
        padded,
        (n_frames, _N_FFT),
        (padded.strides[0] * _HOP, padded.strides[0]),
    ).copy()
    frames *= window
    stft = np.fft.rfft(frames, n=_N_FFT, axis=-1)
    magnitudes = np.abs(stft.T) ** 2
    mel_spec = _MEL_FILTERS @ magnitudes
    log_spec = np.log10(np.clip(mel_spec, a_min=1e-10, a_max=None))
    log_spec = np.maximum(log_spec, log_spec.max() - 8.0)
    return (log_spec + 4.0) / 4.0


def _load_wav(path: str) -> np.ndarray:
    with wave.open(path, "rb") as handle:
        rate = handle.getframerate()
        channels = handle.getnchannels()
        frames = handle.readframes(handle.getnframes())
        width = handle.getsampwidth()
    if width == 2:
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    else:
        audio = np.frombuffer(frames, dtype=np.uint8).astype(np.float32) / 128.0 - 1.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != _SAMPLE_RATE and rate > 0:
        duration = audio.shape[0] / float(rate)
        target = int(duration * _SAMPLE_RATE)
        x_old = np.linspace(0.0, 1.0, num=audio.shape[0], endpoint=False)
        x_new = np.linspace(0.0, 1.0, num=max(1, target), endpoint=False)
        audio = np.interp(x_new, x_old, audio).astype(np.float32)
    return audio


def _ensure_sessions(on_status: Optional[Callable[[str], None]] = None) -> None:
    if _SESSIONS["encoder"] is not None and _SESSIONS["decoder"] is not None:
        return
    from app.services.activity import emit_status

    root = _models_root()
    emit_status(on_status, "🎧 Đang nạp Whisper ONNX (không dùng torch)...")
    enc_path = os.path.join(root, os.path.basename(_ENCODER_FILE))
    dec_path = os.path.join(root, os.path.basename(_DECODER_FILE))
    tok_path = os.path.join(root, os.path.basename(_TOKENIZER_FILE))
    nested_enc = os.path.join(root, _ENCODER_FILE.replace("/", os.sep))
    nested_dec = os.path.join(root, _DECODER_FILE.replace("/", os.sep))
    nested_tok = os.path.join(root, _TOKENIZER_FILE)
    if os.path.isfile(nested_enc):
        enc_path = nested_enc
    if os.path.isfile(nested_dec):
        dec_path = nested_dec
    if os.path.isfile(nested_tok):
        tok_path = nested_tok
    if not os.path.isfile(enc_path) or not os.path.isfile(dec_path) or not os.path.isfile(tok_path):
        emit_status(on_status, "🎧 Đang tải model Whisper ONNX (~80MB, một lần)...")
        enc_src = _download(_ENCODER_FILE, root)
        dec_src = _download(_DECODER_FILE, root)
        tok_src = _download(_TOKENIZER_FILE, root)
        import shutil

        for src, dest in ((enc_src, enc_path), (dec_src, dec_path), (tok_src, tok_path)):
            if os.path.abspath(src) != os.path.abspath(dest):
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copy2(src, dest)
    import onnxruntime as ort
    from tokenizers import Tokenizer

    so = ort.SessionOptions()
    try:
        from app.config import settings

        so.intra_op_num_threads = max(1, int(settings.STT_CPU_THREADS or 1))
    except Exception:
        so.intra_op_num_threads = 2
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    providers = ["CPUExecutionProvider"]
    _SESSIONS["encoder"] = ort.InferenceSession(enc_path, sess_options=so, providers=providers)
    _SESSIONS["decoder"] = ort.InferenceSession(dec_path, sess_options=so, providers=providers)
    _SESSIONS["tokenizer"] = Tokenizer.from_file(tok_path)
    _SESSIONS["root"] = root
    logger.info("Loaded ONNX whisper encoder/decoder from %s", root)


def _tid(tokenizer, token: str) -> int:
    value = tokenizer.token_to_id(token)
    if value is None:
        raise RuntimeError(f"Whisper tokenizer missing {token}")
    return int(value)


def _run_named(session, **tensors: np.ndarray) -> List[np.ndarray]:
    feed = {}
    available = {name: arr for name, arr in tensors.items()}
    for item in session.get_inputs():
        name = item.name
        if name in available:
            feed[name] = available[name]
            continue
        if name in ("input_features", "mel") and "mel" in available:
            feed[name] = available["mel"]
        elif name in ("input_ids", "decoder_input_ids") and "input_ids" in available:
            feed[name] = available["input_ids"]
        elif name in ("encoder_hidden_states", "last_hidden_state") and "hidden" in available:
            feed[name] = available["hidden"]
        elif name == "attention_mask" and "input_ids" in available:
            feed[name] = np.ones_like(available["input_ids"])
        else:
            shape = []
            for dim in item.shape:
                if isinstance(dim, int) and dim > 0:
                    shape.append(dim)
                else:
                    shape.append(1)
            dtype = np.int64 if "int" in str(item.type or "") else np.float32
            feed[name] = np.zeros(tuple(shape) or (1,), dtype=dtype)
    return session.run(None, feed)


def _pad_or_trim_mel(mel: np.ndarray) -> np.ndarray:
    if mel.shape[-1] >= _MAX_FRAMES:
        return mel[..., :_MAX_FRAMES]
    pad = _MAX_FRAMES - mel.shape[-1]
    return np.pad(mel, ((0, 0), (0, pad)))


def _decode_chunk(
    hidden: np.ndarray,
    tokenizer,
    language: str,
) -> List[int]:
    sot = _tid(tokenizer, "<|startoftranscript|>")
    transcribe = _tid(tokenizer, "<|transcribe|>")
    eot = _tid(tokenizer, "<|endoftext|>")
    lang_id = tokenizer.token_to_id(f"<|{language}|>")
    tokens = [sot]
    if lang_id is not None:
        tokens.append(int(lang_id))
    tokens.append(transcribe)
    decoder = _SESSIONS["decoder"]
    for _ in range(_MAX_TOKENS):
        ids = np.array([tokens], dtype=np.int64)
        outputs = _run_named(decoder, input_ids=ids, hidden=hidden)
        logits = outputs[0]
        next_id = int(np.argmax(logits[0, -1]))
        if next_id == eot:
            break
        tokens.append(next_id)
        if len(tokens) > 8 and tokens[-1] == tokens[-2] == tokens[-3] == tokens[-4]:
            break
    return tokens


def _tokens_to_cues(tokens: List[int], tokenizer, chunk_start: float) -> List[Dict[str, Any]]:
    eot = _tid(tokenizer, "<|endoftext|>")
    no_ts = tokenizer.token_to_id("<|notimestamps|>")
    ts_begin = (int(no_ts) + 1) if no_ts is not None else 50364
    text_tokens: List[int] = []
    stamps: List[Tuple[int, float]] = []
    for tok in tokens:
        if tok >= ts_begin:
            stamps.append((len(text_tokens), chunk_start + (tok - ts_begin) * 0.02))
        elif tok < eot:
            text_tokens.append(tok)
    cues: List[Dict[str, Any]] = []
    if not stamps:
        text = tokenizer.decode(text_tokens).strip()
        if text:
            cues.append({
                "start_time": chunk_start,
                "end_time": chunk_start + 2.0,
                "duration": 2.0,
                "text": text,
            })
        return cues
    last_pos = 0
    last_t = stamps[0][1]
    for pos, ts in stamps[1:]:
        piece = tokenizer.decode(text_tokens[last_pos:pos]).strip()
        if piece:
            end = max(last_t + 0.35, ts)
            cues.append({
                "start_time": last_t,
                "end_time": end,
                "duration": end - last_t,
                "text": piece,
            })
        last_pos = pos
        last_t = ts
    piece = tokenizer.decode(text_tokens[last_pos:]).strip()
    if piece:
        end = last_t + max(0.6, 0.08 * max(1, len(piece)))
        cues.append({
            "start_time": last_t,
            "end_time": end,
            "duration": end - last_t,
            "text": piece,
        })
    return cues


def transcribe_wav(
    wav_path: str,
    *,
    language: Optional[str] = "zh",
    on_status: Optional[Callable[[str], None]] = None,
) -> Tuple[List[Dict[str, Any]], str]:
    from app.services.activity import emit_status

    _ensure_sessions(on_status)
    audio = _load_wav(wav_path)
    if audio.size < 1600:
        return [], language or "zh"
    lang = (language or "zh").split("-")[0]
    tokenizer = _SESSIONS["tokenizer"]
    encoder = _SESSIONS["encoder"]
    hop_sec = _HOP / float(_SAMPLE_RATE)
    cues: List[Dict[str, Any]] = []
    window = _CHUNK * _SAMPLE_RATE
    total = audio.shape[0]
    offset = 0
    chunk_i = 0
    while offset < total:
        chunk_i += 1
        piece = audio[offset : offset + window]
        if piece.shape[0] < _SAMPLE_RATE:
            break
        emit_status(
            on_status,
            f"🎧 Whisper ONNX đang nhận {offset / _SAMPLE_RATE:.0f}s/{total / _SAMPLE_RATE:.0f}s...",
        )
        mel = log_mel_spectrogram(piece)
        mel = _pad_or_trim_mel(mel)
        mel_in = np.expand_dims(mel, 0).astype(np.float32)
        hidden = _run_named(encoder, mel=mel_in)[0]
        tokens = _decode_chunk(hidden, tokenizer, lang)
        chunk_start = offset / float(_SAMPLE_RATE)
        cues.extend(_tokens_to_cues(tokens, tokenizer, chunk_start))
        offset += window
        del hop_sec
    for index, cue in enumerate(cues, start=1):
        cue["index"] = index
    return cues, lang
