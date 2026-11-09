import sys, types, numpy as np, scipy.io as sio
from pathlib import Path
sys.path.insert(0, ".")
from x410_2x2_5g import load_nr_dsp
import x410_pss_timed_tx as base
a = types.SimpleNamespace(nr_freq=3541.44e6, nr_rate=15.36e6, nr_gain=32, nr_chan=2, nr_block_ms=20.0, force_nid2=0,
                          force_ibar_ssb=0, force_ncellid=None, no_cfo_correction=True, cfo_warmup_iters=30)
nr_args, analyze, _, _ = load_nr_dsp(Path("/home/nextnet/syncSense/modulos_rx_tx"), a)
m = sio.loadmat(sys.argv[1]); w = m["wave"].ravel().astype(np.complex64); sfns = m["sfns"].ravel()
rng = np.random.default_rng(3)
N = 307200; per = 307200
for snr_db, cfo in ((10, 0.0), (10, 300.0), (6, 0.0), (3, 0.0)):
    ok = 0; tot = 0; got = []
    for i in range(len(sfns) - 1):
        start = i * per + int(rng.integers(5000, 200000))      # SSB somewhere in the block
        blk = w[start:start + N].copy()
        if len(blk) < N: break
        p = np.mean(np.abs(w[w != 0]) ** 2)
        nz = np.sqrt(p * 10 ** (-snr_db / 10) / 2) * (rng.standard_normal(N) + 1j * rng.standard_normal(N))
        blk = (blk + nz) * np.exp(2j * np.pi * cfo * np.arange(N) / 15.36e6)
        blk = blk.astype(np.complex64)
        try:
            res = analyze(blk, nr_args, 0.0, 15.36e6, None, 0)
        except Exception as e:
            got.append(f"err {str(e)[:40]}"); tot += 1; continue
        r = base.pbch_decode(res)
        tot += 1
        exp = int(sfns[i + 1]) if start >= (i + 1) * per - 0 else int(sfns[i + 1])
        # the SSB contained in the block is the one of period i+1 (block starts after SSB i)
        got.append((r.get("sfn"), int(sfns[i + 1]), r.get("pbch_evm_db")))
        ok += r.get("pbch_ok", False) and r.get("sfn") == int(sfns[i + 1])
    print(f"SNR {snr_db:+} dB (per sample), CFO {cfo:.0f} Hz: SFN correct {ok}/{tot} | {got[:4]}")
