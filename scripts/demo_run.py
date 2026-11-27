# -*- coding: utf-8 -*-
"""
가상 예시 문서(samples/*.pdf)로 실제 변환·비식별을 돌려 결과를 docs/demo 에 모은다(README 그림·영상 재료).
GitHub Actions 'demo' 워크플로가 실행한다.

    python scripts/demo_run.py [출력폴더]
"""
import os, sys, shutil, glob, time, pickle, io, contextlib

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "docs", "demo"))
sys.path.insert(0, os.path.join(ROOT, "app"))
os.makedirs(OUT, exist_ok=True)

import pdf2hwpx as P        # noqa: E402
import pii_redact as R      # noqa: E402


class Tee(io.TextIOBase):
    def __init__(self, *s): self.s = s
    def write(self, x):
        for s in self.s: s.write(x)
        return len(x)
    def flush(self):
        for s in self.s: s.flush()


pdfs = sorted(glob.glob(os.path.join(ROOT, "samples", "*.pdf")))
for f in pdfs:
    shutil.copy(f, OUT)
pdfs = [os.path.join(OUT, os.path.basename(f)) for f in pdfs]

# ① 한글 변환 — 콘솔 출력 그대로 기록(영상용)
log = io.StringIO()
with contextlib.redirect_stdout(Tee(sys.stdout, log)):
    t0 = time.time()
    P.main(pdfs + ["--redo"])
    print(f"(총 {time.time() - t0:.0f}초)")
open(os.path.join(OUT, "convert_console.txt"), "w", encoding="utf-8").write(log.getvalue())

# ② 비식별 — 판독 결과(pkl)도 남겨 화면 캡처에 쓴다
eng = P.make_engine("paddle")
log = io.StringIO()
with contextlib.redirect_stdout(Tee(sys.stdout, log)):
    for f in pdfs:
        print(f"\n[비식별] {os.path.basename(f)}")
        doc = R.analyze(f, eng, 300)
        with open(os.path.splitext(f)[0] + ".analysis.pkl", "wb") as fo:
            pickle.dump(doc, fo)
        occ = R.find_name_occurrences(doc)
        print("  이름 후보:", sorted({o["name"] for o in occ}))
        out, n, logp = R.finish_file(doc, occ, set(), True)
        print(f"  → {os.path.basename(out)} (지운 곳 {n}건)")
open(os.path.join(OUT, "redact_console.txt"), "w", encoding="utf-8").write(log.getvalue())
print("\n● 결과:", OUT)
for f in sorted(os.listdir(OUT)):
    print("  ", f)
