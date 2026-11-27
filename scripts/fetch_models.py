# -*- coding: utf-8 -*-
"""
PaddleOCR 모델을 프로그램 폴더(app/paddlex_home)로 미리 받아 두기 — 이후에는 인터넷 없이 동작.

    python scripts/fetch_models.py [저장폴더]      (기본: app/paddlex_home)

받는 모델
  · PP-OCRv5_server_det         글자 위치 찾기(정확, 기본)
  · PP-OCRv5_mobile_det         글자 위치 찾기(경량, '빠르게 변환')
  · korean_PP-OCRv5_mobile_rec  한국어 글자 판독
"""
import os, sys

for _s in (sys.stdout, sys.stderr):          # 콘솔 인코딩(cp949·cp1252)에 없는 글자 때문에 멈추지 않게
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
dest = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "app", "paddlex_home"))
os.makedirs(dest, exist_ok=True)
os.environ["PADDLE_PDX_CACHE_HOME"] = dest

from paddleocr import TextDetection, TextRecognition   # noqa: E402

for name in ("PP-OCRv5_server_det", "PP-OCRv5_mobile_det"):
    print("●", name); TextDetection(model_name=name)
print("● korean_PP-OCRv5_mobile_rec"); TextRecognition(model_name="korean_PP-OCRv5_mobile_rec")

om = os.path.join(dest, "official_models")
print("\n받은 모델:", ", ".join(sorted(os.listdir(om))) if os.path.isdir(om) else "(없음)")
print("저장 위치:", dest)
