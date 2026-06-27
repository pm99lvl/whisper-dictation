#!/usr/bin/env python3
"""Reproduce the silence-hallucination bug and inspect segment metadata."""
import os, tempfile, numpy as np
import scipy.io.wavfile as wf
import mlx_whisper

MODEL = "mlx-community/whisper-large-v3-turbo"
SR = 16000

def run(name, audio):
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False); tmp.close()
    wf.write(tmp.name, SR, (audio * 32767).astype(np.int16))
    r = mlx_whisper.transcribe(tmp.name, path_or_hf_repo=MODEL, language="ru",
                               word_timestamps=False, condition_on_previous_text=False,
                               temperature=0)
    os.unlink(tmp.name)
    text = r["text"].strip()
    print(f"\n=== {name} ===")
    print(f"TEXT: {text!r}")
    for s in r.get("segments", []):
        print(f"  seg no_speech_prob={s.get('no_speech_prob'):.3f} "
              f"avg_logprob={s.get('avg_logprob'):.3f} "
              f"compression_ratio={s.get('compression_ratio'):.2f} text={s.get('text')!r}")
    return r

rng = np.random.default_rng(0)
# Case 1: near-silent like the real bug (peak ~0.0015), 25s long
n = SR * 25
quiet = (rng.standard_normal(n) * 0.0005).astype(np.float32)
run("near-silence 25s (real-bug-like)", quiet)

# Case 2: pure digital silence
run("pure silence 8s", np.zeros(SR * 8, dtype=np.float32))
