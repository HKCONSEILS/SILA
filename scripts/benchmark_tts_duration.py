"""Benchmark TTS duration control: CosyVoice vs Qwen3-TTS."""
import json, sys, time, torch, gc, soundfile as sf
from pathlib import Path

def main():
    project_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/opt/sila/projects/test_002_final")
    translations_path = project_dir / "asr" / "translations_en.json"
    ref_path = project_dir / "voice_refs" / "voice_ref.wav"
    with open(translations_path) as f:
        translations = json.load(f)
    print(f"Benchmarking {len(translations)} segments from {project_dir.name}")

    from qwen_tts import Qwen3TTSModel
    model = Qwen3TTSModel.from_pretrained("Qwen/Qwen3-TTS-12Hz-1.7B-Base")

    results = []
    for t in translations:
        sid, text, budget = t["segment_id"], t["translated_text"], t["timing_budget_ms"]
        t0 = time.time()
        try:
            audio, sr = model.generate_voice_clone(text=text, language="english", ref_audio=str(ref_path), x_vector_only_mode=True, non_streaming_mode=True)
            dur_ms = int(len(audio[0]) / sr * 1000)
            elapsed = time.time() - t0
        except Exception as e:
            print(f"  {sid}: ERROR {e}"); dur_ms = 0; elapsed = 0
        delta_pct = (dur_ms - budget) / budget * 100 if budget > 0 else 0
        status = "PASS" if abs(delta_pct) <= 15 else "FAIL"
        results.append({"sid": sid, "budget": budget, "qwen3": dur_ms, "delta": delta_pct, "status": status, "time": elapsed})
        print(f"  {sid}: Qwen3={dur_ms:>6}ms / budget={budget:>6}ms ({delta_pct:+.0f}%) {status} [{elapsed:.1f}s]")

    del model; torch.cuda.empty_cache(); gc.collect()
    total = len(results); passed = sum(1 for r in results if r["status"] == "PASS")
    print(f"\nQwen3-TTS QC: {passed}/{total} = {passed/total*100:.0f}%")
    print(f"Avg gen time: {sum(r['time'] for r in results)/total:.1f}s/seg")

if __name__ == "__main__":
    main()
