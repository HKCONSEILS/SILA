"""MOSS-TTS benchmark vs CosyVoice on SILA test segments."""
import torch, torchaudio, time, json, os, sys, argparse
from transformers import AutoModel, AutoProcessor
torch.backends.cuda.enable_cudnn_sdp(False)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--test-dir', required=True)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--report', required=True)
    args = parser.parse_args()

    device = 'cuda'
    os.makedirs(os.path.join(args.output_dir, 'mode_a'), exist_ok=True)
    os.makedirs(os.path.join(args.output_dir, 'mode_b'), exist_ok=True)

    # Load translations
    trans_path = os.path.join(args.test_dir, 'asr', 'translations_en.json')
    translations = json.load(open(trans_path))

    # Load CosyVoice TTS reference
    tts_path = os.path.join(args.test_dir, 'tts', 'en', 'tts_manifest.json')
    cv_data = {}
    if os.path.exists(tts_path):
        for t in json.load(open(tts_path)):
            cv_data[t['segment_id']] = t

    # Voice reference
    vr = os.path.join(args.test_dir, 'voice_refs', 'spk_0_multi_ref.wav')
    has_voice_ref = os.path.exists(vr)

    print("Loading MOSS-TTS Local 1.7B...")
    processor = AutoProcessor.from_pretrained(
        'OpenMOSS-Team/MOSS-TTS-Local-Transformer', trust_remote_code=True)
    model = AutoModel.from_pretrained(
        'OpenMOSS-Team/MOSS-TTS-Local-Transformer',
        trust_remote_code=True, dtype=torch.float16,
        device_map='auto', low_cpu_mem_usage=True)
    model.eval()
    print(f"Model loaded. VRAM: {torch.cuda.memory_allocated() / 1e9:.2f} Go")

    results = []
    for i, trans in enumerate(translations):
        sid = trans['segment_id']
        text = trans['translated_text']
        budget_ms = trans['timing_budget_ms']
        target_tokens = round(budget_ms / 80)
        cv = cv_data.get(sid, {})
        cv_dur = cv.get('duration_ms', 0)

        # Mode A: free (no tokens)
        convs_a = [[processor.build_user_message(text=text)]]
        if has_voice_ref:
            convs_a = [[processor.build_user_message(text=text, reference=[vr])]]
        batch_a = processor(convs_a, mode='generation')
        t0 = time.time()
        with torch.no_grad():
            out_a = model.generate(input_ids=batch_a['input_ids'].to(device),
                                   attention_mask=batch_a['attention_mask'].to(device),
                                   max_new_tokens=4096)
        ta = time.time() - t0
        dur_a = 0
        for msg in processor.decode(out_a):
            audio = msg.audio_codes_list[0]
            dur_a = int(audio.shape[-1] / processor.model_config.sampling_rate * 1000)
            torchaudio.save(os.path.join(args.output_dir, 'mode_a', f'{sid}.wav'),
                            audio.unsqueeze(0), processor.model_config.sampling_rate)

        # Mode B: constrained (tokens=N)
        convs_b = [[processor.build_user_message(text=text, tokens=target_tokens)]]
        if has_voice_ref:
            convs_b = [[processor.build_user_message(text=text, tokens=target_tokens, reference=[vr])]]
        batch_b = processor(convs_b, mode='generation')
        t0 = time.time()
        with torch.no_grad():
            out_b = model.generate(input_ids=batch_b['input_ids'].to(device),
                                   attention_mask=batch_b['attention_mask'].to(device),
                                   max_new_tokens=4096)
        tb = time.time() - t0
        dur_b = 0
        for msg in processor.decode(out_b):
            audio = msg.audio_codes_list[0]
            dur_b = int(audio.shape[-1] / processor.model_config.sampling_rate * 1000)
            torchaudio.save(os.path.join(args.output_dir, 'mode_b', f'{sid}.wav'),
                            audio.unsqueeze(0), processor.model_config.sampling_rate)

        delta_a = abs(dur_a - budget_ms) / budget_ms * 100 if budget_ms else 0
        delta_b = abs(dur_b - budget_ms) / budget_ms * 100 if budget_ms else 0
        cv_delta = abs(cv_dur - budget_ms) / budget_ms * 100 if budget_ms and cv_dur else 0

        seg_result = {
            'segment_id': sid, 'budget_ms': budget_ms,
            'mode_a': {'dur_ms': dur_a, 'delta_pct': round(delta_a, 1), 'pass': delta_a <= 15, 'inference_s': round(ta, 2)},
            'mode_b': {'dur_ms': dur_b, 'delta_pct': round(delta_b, 1), 'pass': delta_b <= 15, 'inference_s': round(tb, 2)},
            'cosyvoice': {'dur_ms': cv_dur, 'delta_pct': round(cv_delta, 1), 'pass': cv_delta <= 15},
        }
        results.append(seg_result)
        status_a = 'PASS' if delta_a <= 15 else 'FAIL'
        status_b = 'PASS' if delta_b <= 15 else 'FAIL'
        print(f'  {sid} budget={budget_ms}ms | A={dur_a}ms({delta_a:.0f}%){status_a} | B={dur_b}ms({delta_b:.0f}%){status_b} | CV={cv_dur}ms({cv_delta:.0f}%)')

    # Summary
    n = len(results)
    a_pass = sum(1 for r in results if r['mode_a']['pass'])
    b_pass = sum(1 for r in results if r['mode_b']['pass'])
    cv_pass = sum(1 for r in results if r['cosyvoice']['pass'])
    a_delta = sum(r['mode_a']['delta_pct'] for r in results) / n if n else 0
    b_delta = sum(r['mode_b']['delta_pct'] for r in results) / n if n else 0
    a_inf = sum(r['mode_a']['inference_s'] for r in results) / n if n else 0
    b_inf = sum(r['mode_b']['inference_s'] for r in results) / n if n else 0

    report = {
        'test': os.path.basename(args.test_dir),
        'model': 'MossTTSLocal-1.7B',
        'segments': results,
        'summary': {
            'mode_a': {'qc_pass_pct': round(a_pass/n*100, 1), 'mean_delta_pct': round(a_delta, 1), 'mean_inference_s': round(a_inf, 2), 'total_inference_s': round(sum(r['mode_a']['inference_s'] for r in results), 1)},
            'mode_b': {'qc_pass_pct': round(b_pass/n*100, 1), 'mean_delta_pct': round(b_delta, 1), 'mean_inference_s': round(b_inf, 2), 'total_inference_s': round(sum(r['mode_b']['inference_s'] for r in results), 1)},
            'cosyvoice_ref': {'qc_pass_pct': round(cv_pass/n*100, 1)},
            'vram_peak_gb': round(torch.cuda.max_memory_allocated() / 1e9, 2),
            'sample_rate_hz': processor.model_config.sampling_rate,
        }
    }
    with open(args.report, 'w') as f:
        json.dump(report, f, indent=2)

    print(f'\n=== BENCHMARK {os.path.basename(args.test_dir)} ===')
    print(f'Segments: {n}')
    print(f'Mode A (free):       QC {a_pass}/{n} ({a_pass/n*100:.0f}%), delta {a_delta:.1f}%, inf {a_inf:.1f}s/seg')
    print(f'Mode B (constrained): QC {b_pass}/{n} ({b_pass/n*100:.0f}%), delta {b_delta:.1f}%, inf {b_inf:.1f}s/seg')
    print(f'CosyVoice (ref):     QC {cv_pass}/{n} ({cv_pass/n*100:.0f}%)')
    print(f'VRAM peak: {torch.cuda.max_memory_allocated() / 1e9:.2f} Go')

if __name__ == '__main__':
    main()
