# -*- coding: utf-8 -*-
"""
PDF → 편집 가능한 한글(HWPX) 변환기 (PaddleOCR 기반, 오프라인)
────────────────────────────────────────────────────────────────────
· 내용을 '그대로' 옮기는 데 집중(판단·통합·비식별화 없음, 형식은 달라질 수 있음).
· OCR 엔진: PaddleOCR(한국어) 우선, 없으면 Tesseract 로 자동 폴백.
· 외부 네트워크/클라우드 불필요(모델·바이너리를 paddlex_home·vendor 에 두면 완전 오프라인).

지원:
  · 문자 기반 PDF(디지털)  → 글자·표를 그대로 추출 (정확)
  · 스캔 이미지 + 표        → 격자 감지 + 셀 OCR (검은 비식별박스 자동 제거)
  · 스캔 이미지 + 줄글      → 전체 OCR → 문단 (원본 여백·글자크기·줄간격 재현)
  · 여러 페이지 자동 처리

사용:  python pdf2hwpx.py 파일.pdf|폴더 ... [--engine paddle|tesseract|auto] [--redo] [--dump]
       인자가 없으면 '변환_폴더' 안 PDF 전부를 차례로 자동 변환(이미 변환한 것은 건너뜀)
       빠른 모드: 환경변수 PDF2HWPX_DET_MODEL=PP-OCRv5_mobile_det

의존:  poppler(pdftoppm), opencv, numpy, pdfplumber, pdf2image, python-hwpx
       + (권장) paddleocr·paddlepaddle   또는  (폴백) tesseract-ocr(+kor)·pytesseract
"""
import sys, os, re, argparse

import cv2

for _s in (sys.stdout, sys.stderr):          # 콘솔 인코딩(cp949·cp1252)에 없는 글자 때문에 멈추지 않게
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass
import numpy as np

__version__ = "1.0.0"

# ──────────────────────────────────────────────────────────────────
# 0. 경로 자동 탐지 (vendor → 환경변수 → 시스템)
# ──────────────────────────────────────────────────────────────────
def _base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

BASE = _base_dir()
VENDOR = os.path.join(BASE, "vendor")
# 작업 폴더(변환_폴더·비식별_대상)를 둘 위치. 배포판은 런처가 프로그램 최상위 폴더로 지정한다.
WORK_ROOT = os.environ.get("PDF2HWPX_WORKDIR") or BASE

def _find_poppler():
    if os.environ.get("POPPLER_PATH"):
        return os.environ["POPPLER_PATH"]
    root = os.path.join(VENDOR, "poppler")
    if os.path.isdir(root):
        for dp, _d, files in os.walk(root):
            if any(f.lower().startswith("pdftoppm") for f in files):
                return dp
    return ""

POPPLER_PATH = _find_poppler()

# PaddleOCR 모델 위치: 프로그램 폴더 안 paddlex_home 이 있으면 그것을 사용(완전 오프라인)
PX_HOME = os.environ.get("PDF2HWPX_MODELS") or os.path.join(BASE, "paddlex_home")
if os.path.isdir(PX_HOME):
    os.environ.setdefault("PADDLE_PDX_CACHE_HOME", PX_HOME)
# 오프라인 환경에서 모델 사이트 접속 확인(타임아웃 대기)을 하지 않도록
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

def _rasterize(pdf_path, dpi=300, page=None):
    """page(0부터)가 주어지면 그 쪽만 그림으로 변환(긴 문서도 메모리 일정)"""
    from pdf2image import convert_from_path
    kw = {"dpi": dpi}
    if POPPLER_PATH:
        kw["poppler_path"] = POPPLER_PATH
    if page is not None:
        kw["first_page"] = kw["last_page"] = page + 1
    return convert_from_path(pdf_path, **kw)

def _page_gray(pdf_path, i):
    img = _rasterize(pdf_path, dpi=RENDER_DPI, page=i)[0]
    return cv2.cvtColor(np.array(img)[:, :, ::-1].copy(), cv2.COLOR_BGR2GRAY)


# ══════════════════════════════════════════════════════════════════
#  OCR 엔진 추상화 (PaddleOCR 우선, Tesseract 폴백)
# ══════════════════════════════════════════════════════════════════
RENDER_DPI = 300   # 그림 변환 해상도(글자 높이→pt 환산에도 사용)

class OcrResult:
    """한 줄 인식 결과: 위쪽 y, 왼쪽 x, 중심 cy/cx, 높이 h, 글자"""
    __slots__ = ("y", "x", "cy", "cx", "h", "x1", "text")
    def __init__(self, y, x, text, cy=None, cx=None, h=0.0, x1=None):
        self.y, self.x, self.text = y, x, text
        self.cy = y if cy is None else cy
        self.cx = x if cx is None else cx
        self.h = h
        self.x1 = (2 * self.cx - x) if x1 is None else x1


def _env_int(name, default=0):
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default

class PaddleEngine:
    name = "paddle"
    page_level_tables = True   # 표 전체를 한 번에 인식 후 좌표로 칸 배정
    def __init__(self):
        from paddleocr import PaddleOCR
        det = os.path.join(VENDOR, "paddle_models", "det")
        rec = os.path.join(VENDOR, "paddle_models", "rec")
        threads = _env_int("PDF2HWPX_THREADS", 0)            # 0 = PaddleOCR 기본값(10)
        self.rec_onednn = os.environ.get("PDF2HWPX_REC_ONEDNN", "1") != "0"
        det_name = os.environ.get("PDF2HWPX_DET_MODEL", "PP-OCRv5_server_det")
        kw = dict(lang="korean",
                  use_doc_orientation_classify=False,
                  use_doc_unwarping=False,
                  use_textline_orientation=False,
                  # PaddlePaddle 3.3 CPU 가속(oneDNN)이 PP-OCRv5 탐지모델과 충돌 → 탐지는 끔
                  # (인식 모델만 아래에서 oneDNN 으로 따로 켬: 결과 동일·속도↑)
                  enable_mkldnn=False,
                  # 탐지 단계만 긴 변 960px로 축소(메모리 절약). 글자 판독은 원본 해상도로 함
                  text_det_limit_type="max",
                  text_det_limit_side_len=_env_int("PDF2HWPX_DET_SIDE", 960))
        if threads > 0:
            kw["cpu_threads"] = threads
        px_models = os.path.join(PX_HOME, "official_models")   # 받아둔 모델 폴더
        if os.path.isdir(det):
            kw["text_detection_model_dir"] = det
        elif det_name != "PP-OCRv5_server_det":
            # 탐지모델 교체(경량 등): 이름만 주면 PaddleOCR이 인식모델까지 기본값으로
            # 다시 골라 인터넷에서 받으려 함(오프라인이면 실패) → 탐지·인식 모두
            # 받아둔 로컬 폴더로 '경로까지' 못박아 다운로드 시도를 원천 차단.
            md = os.path.join(px_models, det_name)
            kw["text_detection_model_name"] = det_name
            if os.path.isdir(md):
                kw["text_detection_model_dir"] = md
            if not os.path.isdir(rec):
                rec_local = os.path.join(px_models, "korean_PP-OCRv5_mobile_rec")
                if os.path.isdir(rec_local):
                    kw["text_recognition_model_name"] = "korean_PP-OCRv5_mobile_rec"
                    kw["text_recognition_model_dir"] = rec_local
        if os.path.isdir(rec):
            kw["text_recognition_model_dir"] = rec
        self.ocr = PaddleOCR(**kw)
        self._rec_orig = None
        self.desc = f"탐지={det_name if not os.path.isdir(det) else 'vendor'}, " \
                    f"스레드={threads or '기본(10)'}"
        if self.rec_onednn:
            try:
                self._swap_rec_onednn(threads, rec if os.path.isdir(rec) else None)
                self.desc += ", 인식 가속(oneDNN)=켬"
            except Exception as e:
                self.desc += f", 인식 가속=불가({type(e).__name__})"
        else:
            self.desc += ", 인식 가속=끔"

    def _swap_rec_onednn(self, threads, rec_dir):
        """인식(글자 판독) 모델만 oneDNN 가속판으로 교체. 판독 결과는 같고 속도만 빨라진다."""
        from paddleocr import TextRecognition
        ip = self.ocr.paddlex_pipeline._pipeline
        old = ip.text_rec_model
        rk = dict(model_name="korean_PP-OCRv5_mobile_rec", enable_mkldnn=True)
        if rec_dir:
            rk["model_dir"] = rec_dir
        else:
            rec_local = os.path.join(PX_HOME, "official_models", "korean_PP-OCRv5_mobile_rec")
            if os.path.isdir(rec_local):
                rk["model_dir"] = rec_local
        if threads > 0:
            rk["cpu_threads"] = threads
        new = TextRecognition(**rk).paddlex_predictor
        new.batch_sampler.batch_size = old.batch_sampler.batch_size
        ip.text_rec_model = new
        self._rec_orig = old

    def _predict(self, img):
        try:
            return self.ocr.predict(img)
        except Exception as e:
            if self._rec_orig is None:
                raise
            # 이 PC에서 oneDNN 인식이 안 되면 원래 방식으로 되돌려 계속 진행
            print(f"  (안내) 인식 가속을 끄고 계속합니다: {type(e).__name__}")
            self.ocr.paddlex_pipeline._pipeline.text_rec_model = self._rec_orig
            self._rec_orig = None
            self.desc = self.desc.replace("인식 가속(oneDNN)=켬", "인식 가속=자동해제")
            return self.ocr.predict(img)

    def _run(self, img):
        """반환: list[OcrResult]  (PaddleOCR는 3채널 컬러 이미지만 받음)"""
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        out = []
        res = self._predict(img)               # PaddleOCR 3.x
        if not res:
            return out
        r = res[0]
        def _get(k):
            try:
                return r[k]
            except Exception:
                return r.get(k) if hasattr(r, "get") else None
        texts = _get("rec_texts")
        polys = None
        for k in ("rec_polys", "dt_polys", "rec_boxes"):
            polys = _get(k)
            if polys is not None and len(polys):
                break
        if texts is None:
            return out
        for i, t in enumerate(texts):
            t = str(t).strip()
            if not t:
                continue
            if polys is not None and i < len(polys):
                pts = np.array(polys[i]).reshape(-1, 2).astype(float)
                out.append(OcrResult(pts[:, 1].min(), pts[:, 0].min(), t,
                                     cy=pts[:, 1].mean(), cx=pts[:, 0].mean(),
                                     h=float(pts[:, 1].max() - pts[:, 1].min()),
                                     x1=float(pts[:, 0].max())))
            else:
                out.append(OcrResult(float(i), 0.0, t))
        return out

    def cell_text(self, crop):
        lines = _lines_from_results(self._run(crop))
        return "\n".join(lines)

    def page_results(self, img):
        return self._run(img)


class TesseractEngine:
    name = "tesseract"
    def __init__(self):
        import pytesseract
        self.pt = pytesseract
        if os.environ.get("TESSERACT_CMD"):
            pytesseract.pytesseract.tesseract_cmd = os.environ["TESSERACT_CMD"]
        else:
            for n in ("tesseract.exe", "tesseract"):
                p = os.path.join(VENDOR, "tesseract", n)
                if os.path.exists(p):
                    pytesseract.pytesseract.tesseract_cmd = p
        self.tessdata = ""
        for p in (os.path.join(VENDOR, "tessdata"), os.path.join(VENDOR, "tesseract", "tessdata")):
            if os.path.isdir(p):
                self.tessdata = p; break

    def _cfg(self, base):
        return base + (f' --tessdata-dir "{self.tessdata}"' if self.tessdata else "")

    def cell_text(self, crop, numeric=False):
        if crop.size == 0 or crop.shape[0] < 5 or crop.shape[1] < 5:
            return ""
        g = crop if crop.ndim == 2 else cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        g = cv2.resize(g, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        _, g = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        cfg = self._cfg("--psm 6")
        if numeric:
            cfg += " -c tessedit_char_whitelist=0123456789,.: "
        return self.pt.image_to_string(g, lang="kor+eng", config=cfg).strip()

    def page_results(self, img):
        g = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        g = cv2.resize(g, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
        _, g = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        data = self.pt.image_to_data(g, lang="kor+eng", config=self._cfg("--psm 3"),
                                     output_type=self.pt.Output.DICT)
        out = []
        for i, t in enumerate(data["text"]):
            if str(t).strip():
                out.append(OcrResult(data["top"][i]/1.5, data["left"][i]/1.5, str(t).strip(),
                                     cy=(data["top"][i]+data["height"][i]/2)/1.5,
                                     cx=(data["left"][i]+data["width"][i]/2)/1.5,
                                     h=data["height"][i]/1.5,
                                     x1=(data["left"][i]+data["width"][i])/1.5))
        return out


def _group_lines(results, y_tol=12):
    """OcrResult 들을 y로 줄 묶음. 반환: [[OcrResult,...], ...](줄별)"""
    if not results:
        return []
    # 중심선(cy) 기준으로 묶는다: 좌·우로 떨어진 같은 줄(예: 하단 문서번호 좌/우)도 한 줄로
    hs = sorted(r.h for r in results if r.h > 0)
    tol = max(y_tol, 0.6 * hs[len(hs)//2]) if hs else y_tol
    rs = sorted(results, key=lambda r: (r.cy, r.x))
    lines, cur, cur_y = [], [], None
    for r in rs:
        if cur_y is None or abs(r.cy - cur_y) <= tol:
            cur.append(r)
            cur_y = sum(z.cy for z in cur) / len(cur)
        else:
            lines.append(cur); cur, cur_y = [r], r.cy
    if cur:
        lines.append(cur)
    return lines

def _lines_from_results(results, y_tol=12):
    """줄 문자열 리스트."""
    out = []
    for ln in _group_lines(results, y_tol):
        s = " ".join(x.text for x in sorted(ln, key=lambda z: z.x))
        if s.strip():
            out.append(s)
    return out

def _px_to_pt(h_px):
    """글자 높이(px, 300dpi) → 대략의 글자크기(pt). 표준값에 가깝게 정리."""
    pt = h_px * 72.0 / RENDER_DPI
    pt = max(7.0, min(40.0, pt))
    return round(pt * 2) / 2.0            # 0.5pt 단위

def _median_size(results):
    hs = sorted(x.h for x in results if x.h > 0)
    return _px_to_pt(hs[len(hs)//2]) if hs else None


def make_engine(pref="auto"):
    """pref: paddle|tesseract|auto. 반환: 엔진 인스턴스"""
    paddle_err = None
    if pref in ("auto", "paddle"):
        try:
            eng = PaddleEngine()
            print(f"● OCR 엔진: PaddleOCR(korean) — {eng.desc}")
            return eng
        except Exception as e:
            paddle_err = f"{type(e).__name__}: {e}"
            print(f"[경고] PaddleOCR 사용 불가 → {paddle_err}")
    try:
        eng = TesseractEngine()
    except Exception as e:
        raise RuntimeError(f"OCR 엔진을 쓸 수 없습니다. PaddleOCR 오류: {paddle_err} / "
                           f"Tesseract 오류: {type(e).__name__}: {e}")
    print("● OCR 엔진: Tesseract(kor+eng)")
    return eng


# ══════════════════════════════════════════════════════════════════
#  A. 스캔 페이지: 검은박스 제거 → 격자 → 표 OR 줄글
# ══════════════════════════════════════════════════════════════════
def whiten_black_boxes(gray):
    """속이 꽉 찬 큰 검은 박스(비식별 처리)만 흰색으로 지움"""
    dark = (gray < 80).astype("uint8") * 255
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25))
    solid = cv2.dilate(cv2.erode(dark, k), k)
    out = gray.copy()
    out[solid > 0] = 255
    return out

def remove_footer_marks(gray, bands=((0.0, 0.09), (0.88, 1.0))):
    """상·하단 여백의 도장·바코드 같은 '덩어리 마크'만 흰색으로 지운다.
    - 글자(작음)·표선(가늘음)은 건드리지 않는다.
    - 기준은 페이지 크기 대비 상대값이라 해상도(dpi)와 무관하게 동작한다."""
    H, W = gray.shape
    out = gray.copy()
    dark = (gray < 150).astype("uint8")
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    merged = cv2.dilate(dark, k)               # 점점이 흩어진 도장을 한 덩어리로
    n, _lab, stats, _c = cv2.connectedComponentsWithStats(merged, 8)
    # 바코드·QR·도장은 쪽 높이의 약 6% — 큰 제목 글자(약 2~3%)는 지우지 않도록 4% 이상만
    minw, minh = max(20, int(W * 0.04)), max(20, int(H * 0.04))
    for i in range(1, n):
        x, y, w, h, _area = stats[i]
        cy = y + h / 2
        if any(b0 * H <= cy <= b1 * H for b0, b1 in bands) and w >= minw and h >= minh:
            out[y:y+h, x:x+w] = 255
    return out

def detect_grid(gray):
    h, w = gray.shape
    bw = cv2.adaptiveThreshold(~gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 15, -2)
    def ext(mask, ksize, vert):
        st = cv2.getStructuringElement(cv2.MORPH_RECT, (1, ksize) if vert else (ksize, 1))
        return cv2.dilate(cv2.erode(mask, st), st)
    horiz = ext(bw, max(10, w // 20), False)
    vert  = ext(bw, max(10, h // 20), True)
    def pos(mask, axis):
        proj = (mask > 0).sum(axis=axis)
        if proj.max() == 0: return []
        idx = np.where(proj > proj.max() * 0.3)[0]
        g, start, prev = [], idx[0], idx[0]
        for v in idx[1:]:
            if v - prev > 10:
                g.append(int((start + prev)//2)); start = v
            prev = v
        g.append(int((start + prev)//2))
        return g
    return pos(horiz, 1), pos(vert, 0), horiz, vert

def _line_present(mask, a0, a1, fixed, horizontal, thr=0.5):
    b = 6
    seg = mask[max(0,fixed-b):fixed+b, a0:a1] if horizontal else mask[a0:a1, max(0,fixed-b):fixed+b]
    if seg.size == 0: return False
    return (seg > 0).any(axis=0 if horizontal else 1).mean() >= thr

def build_span_map(ys, xs, hm, vm):
    nR, nC = len(ys)-1, len(xs)-1
    hb = lambda r,c: _line_present(hm, xs[c]+8, xs[c+1]-8, ys[r+1], True)
    rb = lambda r,c: _line_present(vm, ys[r]+8, ys[r+1]-8, xs[c+1], False)
    absorbed = [[False]*nC for _ in range(nR)]
    span = {}
    for r in range(nR):
        for c in range(nC):
            if absorbed[r][c]: continue
            cs = 1
            while c+cs < nC and not rb(r, c+cs-1): cs += 1
            rs = 1
            while r+rs < nR and not hb(r+rs-1, c): rs += 1
            for dr in range(rs):
                for dc in range(cs):
                    if dr or dc: absorbed[r+dr][c+dc] = True
            span[(r,c)] = (rs, cs)
    return span

def longest_regular_run(ys):
    """표 본체만 남김. 정상 표(행 높이가 다소 달라도)는 통째로 두고,
    비식별 박스·여백 때문에 생긴 '극단적으로 벌어진 유령행'만 잘라낸다.
    판단 기준은 절대값이 아니라 '중앙 행높이의 3배'(상대값)."""
    if len(ys) < 3: return ys
    gaps = sorted(ys[i+1]-ys[i] for i in range(len(ys)-1))
    med = gaps[len(gaps)//2]
    hi = max(3*med, 40)
    best, cur = [], [ys[0]]
    for i in range(1, len(ys)):
        if ys[i]-ys[i-1] <= hi: cur.append(ys[i])
        else:
            if len(cur) > len(best): best = cur
            cur = [ys[i]]
    if len(cur) > len(best): best = cur
    return best if len(best) >= 3 else ys

def _stroke(gray, x0, y0, x1, y1):
    """획 두께 지표: 글자 잉크 중 3x3 침식 후에도 남는 비율(굵은 글씨일수록 큼)."""
    try:
        crop = gray[max(0, int(y0)):int(y1), max(0, int(x0)):int(x1)]
        if crop.size < 50:
            return 0.0
        ink = (crop < 140).astype("uint8")
        n = int(ink.sum())
        if n < 30:
            return 0.0
        er = cv2.erode(ink, np.ones((3, 3), np.uint8))
        return float(er.sum()) / n
    except Exception:
        return 0.0

def _table_from_band(engine, clean, results, dump=False):
    """clean 에서 표 본체(band)를 찾아 ((table블록),(y0,y1)) 반환. 없으면 (None,None).
    - 전체 페이지 OCR 결과(results)를 각 칸에 배정한다(OCR 1회).
    - 마크/여백 때문에 생긴 '내용 없는 가짜 격자'는 표로 인정하지 않는다."""
    ys0, xs0, _hm, _vm = detect_grid(clean)
    if len(ys0) < 3 or len(xs0) < 3:
        return None, None
    band = longest_regular_run(ys0)
    if len(band) < 3:
        return None, None
    y0, y1 = band[0], band[-1]
    off = max(0, y0 - 3)
    sub = clean[off:y1+3, :]
    ys, xs, hm2, vm2 = detect_grid(sub)
    if len(ys) < 3 or len(xs) < 3:
        return None, None
    span = build_span_map(ys, xs, hm2, vm2)
    nR, nC = len(ys)-1, len(xs)-1
    bucket = {k: [] for k in span}
    for res in results:                       # 표 band 안의 글자만 칸에 배정
        scy = res.cy - off
        if 0 <= scy < sub.shape[0]:
            for (r,c),(rs,cs) in span.items():
                if ys[r] <= scy < ys[r+rs] and xs[c] <= res.cx < xs[c+cs]:
                    bucket[(r,c)].append(res); break
    nonempty = sum(1 for v in bucket.values() if v)
    if nonempty < max(4, nR):                 # 진짜 표인지 검증(가짜 격자 거름)
        return None, None
    tmed = _median_size(results)
    cells = {}
    for (r,c),(rs,cs) in span.items():
        t = "\n".join(_lines_from_results(bucket[(r,c)]))
        hs = sorted(x.h for x in bucket[(r,c)] if x.h > 0)
        # 셀 배경(음영): 글자는 소수이므로 80백분위 밝기 = 바탕색
        cy0, cy1 = ys[r] + 6, ys[r+rs] - 6
        cx0, cx1 = xs[c] + 6, xs[c+cs] - 6
        bg = None
        if cy1 > cy0 and cx1 > cx0:
            v = float(np.percentile(sub[cy0:cy1, cx0:cx1], 80))
            if v < 238:
                g = int(max(180, min(250, v)))
                bg = "#%02X%02X%02X" % (g, g, g)
        align = None
        if bucket[(r,c)]:
            tx0 = min(x.x for x in bucket[(r,c)]); tx1 = max(x.x1 for x in bucket[(r,c)])
            lg, rg, cw = tx0 - xs[c], xs[c+cs] - tx1, max(1, xs[c+cs] - xs[c])
            align = "CENTER" if abs(lg - rg) <= 0.12 * cw else ("LEFT" if lg < rg else "RIGHT")
        cells[(r,c)] = {"text": t, "rowspan": rs, "colspan": cs,
                        "size": _median_size(bucket[(r,c)]) or tmed,
                        "th": hs[len(hs)//2] if hs else 0.0, "bg": bg, "align": align}
    # 원본 비율대로: 열 너비(xs 간격) · 행 높이(ys 간격)
    colw = [max(1, xs[c+1]-xs[c]) for c in range(nC)]
    rowh = [max(1, ys[r+1]-ys[r]) for r in range(nR)]
    geom = (xs[0], xs[-1], off + ys[0], off + ys[-1])   # 페이지 위 표 위치(px)
    if dump: print(f"    [표] {nR}x{nC} (내용칸 {nonempty})")
    return ("table", nR, nC, cells, colw, rowh, geom), (off, y1 + 3)

# 쪽 맨 아래 꼬리말 중 '쪽 표시'(2 / 5, - 3 -, 3쪽)만 남기고 문서번호·출력일 등은 지운다
PAGE_NO_RE = re.compile(r"^[\s\-–—(]*\d{1,3}\s*(?:/\s*\d{1,3}|쪽|페이지)?[\s\-–—)]*$")
FOOTER_BAND = 0.94          # 쪽 높이 대비 이 아래를 꼬리말 영역으로 본다

def _split_page_no(text):
    """'출력본 생성일: 2026-10-07   2 / 5' 처럼 한 조각에 붙은 경우 끝의 쪽 표시만 떼어낸다."""
    m = re.search(r"(\d{1,3}\s*/\s*\d{1,3})\s*$", text)
    return m.group(1) if m else None

def drop_footer_text(results, H, dump=False):
    """꼬리말 영역의 OCR 조각 중 쪽 표시만 남기고 버린다(문서번호, 출력본 생성일 등)."""
    out, dropped = [], []
    for r in results:
        if r.cy < FOOTER_BAND * H:
            out.append(r); continue
        t = r.text.strip()
        if PAGE_NO_RE.match(t):
            out.append(r); continue
        pn = _split_page_no(t)
        if pn:
            # 조각 오른쪽 끝에 붙은 쪽 표시: 글자 비율로 x 위치를 잘라 쪽 표시만 남김
            frac = len(pn) / max(1, len(t))
            w = (r.x1 - r.x)
            nx = r.x1 - w * frac
            out.append(OcrResult(r.y, nx, pn, cy=r.cy, cx=(nx + r.x1) / 2, h=r.h, x1=r.x1))
            dropped.append(t[: len(t) - len(pn)].strip())
            continue
        dropped.append(t)
    if dump and dropped:
        print("    [꼬리말 삭제]", dropped)
    return out

def process_scanned_page(engine, gray, dump=False):
    clean = whiten_black_boxes(gray)
    clean = remove_footer_marks(clean)
    results = engine.page_results(clean)      # 전체 페이지 OCR 1회(표·줄글 공용)
    results = drop_footer_text(results, clean.shape[0], dump)
    table_block, band = _table_from_band(engine, clean, results, dump)
    H, W = clean.shape[:2]
    elems = []

    def add_lines(rs):
        for ln in _group_lines(rs):
            ln = sorted(ln, key=lambda z: z.x)
            # 같은 줄 안에서 멀리 떨어진 조각(칸 구분)은 그 간격만큼 공백으로 재현
            hh = sorted(z.h for z in ln if z.h > 0)
            em_px = (hh[len(hh)//2] if hh else 40.0) * 0.95
            parts = [ln[0].text]
            for a, b in zip(ln, ln[1:]):
                gap = b.x - a.x1
                n = int(round(gap / (0.40 * em_px))) if gap > 1.5 * em_px else 1
                parts.append(" " * max(1, min(60, n)) + b.text)
            t = "".join(parts)
            if not t.strip():
                continue
            hs = sorted(z.h for z in ln if z.h > 0)
            x0, x1 = min(z.x for z in ln), max(z.x1 for z in ln)
            y0, y1 = min(z.y for z in ln), max(z.y + z.h for z in ln)
            elems.append({"kind": "line", "t": t, "x0": x0, "x1": x1, "y0": y0, "y1": y1,
                          "h": hs[len(hs)//2] if hs else 0.0,
                          "stroke": _stroke(clean, x0, y0, x1, y1)})

    if table_block is not None:
        y0, y1 = band
        # ★ 표만 남기지 말고, 표 위/아래의 줄글도 그대로 보존(위치·크기 포함)
        add_lines([r for r in results if r.cy < y0])
        _, nR, nC, cells, colw, rowh, geom = table_block
        elems.append({"kind": "table", "nR": nR, "nC": nC, "cells": cells,
                      "colw": colw, "rowh": rowh,
                      "x0": geom[0], "x1": geom[1], "y0": geom[2], "y1": geom[3]})
        add_lines([r for r in results if r.cy > y1])
    else:
        add_lines(results)                 # 표가 없거나 가짜면 전체를 줄글로
        if dump: print(f"    [줄글] 줄 {len(elems)}개")
    elems.sort(key=lambda e: e["y0"])
    return [("page", W, H, elems)] if elems else []


# ══════════════════════════════════════════════════════════════════
#  B. 디지털 페이지: pdfplumber
# ══════════════════════════════════════════════════════════════════
def process_digital_page(page, dump=False):
    blocks, items = [], []
    try: tables = page.find_tables()
    except Exception: tables = []
    tb = []
    for t in tables:
        try: tb.append(t.bbox)
        except Exception: pass
    def in_tbl(x, top):
        return any(x0-1<=x<=x1+1 and y0-1<=top<=y1+1 for (x0,y0,x1,y1) in tb)
    for t in tables:
        try: data = t.extract()
        except Exception: data = None
        if not data: continue
        nR = len(data); nC = max(len(r) for r in data)
        cells = {}
        for r in range(nR):
            for c in range(nC):
                v = data[r][c] if c < len(data[r]) else None
                v = "" if v is None else re.sub(r"[ \t]+"," ",str(v)).strip()
                cells[(r,c)] = {"text": v, "rowspan":1, "colspan":1}
        items.append((t.bbox[1], "table", (nR,nC,cells)))
    try: words = page.extract_words(use_text_flow=True, keep_blank_chars=False)
    except Exception: words = []
    lm = {}
    for w in words:
        if in_tbl(w["x0"], w["top"]): continue
        lm.setdefault(round(w["top"]/3.0), []).append((w["x0"], w["text"], w["top"]))
    lines = []
    for k in sorted(lm):
        parts = sorted(lm[k], key=lambda p:p[0])
        lines.append((min(p[2] for p in parts), " ".join(p[1] for p in parts)))
    if lines:
        gaps = [lines[i+1][0]-lines[i][0] for i in range(len(lines)-1)]
        med = sorted(gaps)[len(gaps)//2] if gaps else 0
        pg = med*1.6 if med else 1e9
        cur, ct = [lines[0][1]], lines[0][0]
        for i in range(1, len(lines)):
            if lines[i][0]-lines[i-1][0] > pg:
                items.append((ct, "para", "\n".join(cur))); cur, ct = [], lines[i][0]
            cur.append(lines[i][1])
        if cur: items.append((ct, "para", "\n".join(cur)))
    for _y, kind, payload in sorted(items, key=lambda it: it[0]):
        blocks.append(("para", payload) if kind=="para" else ("table",)+payload)
    if dump: print(f"    [디지털] 표 {len(tables)} 블록 {len(blocks)}")
    return blocks


# ══════════════════════════════════════════════════════════════════
#  C. 파이프라인
# ══════════════════════════════════════════════════════════════════
RASTER_CHUNK = max(1, _env_int("PDF2HWPX_RASTER_CHUNK", 4))   # 몇 쪽씩 묶어 그림으로 바꿀지

class _PageImages:
    """스캔 쪽 그림을 몇 쪽씩 묶어 미리 만들어 둔다(Poppler 호출 횟수↓). 쓴 쪽은 바로 버려 메모리 일정."""
    def __init__(self, pdf_path, n):
        self.pdf, self.n, self.cache = pdf_path, n, {}
    def gray(self, i):
        if i not in self.cache:
            last = min(self.n - 1, i + RASTER_CHUNK - 1)
            kw = {"dpi": RENDER_DPI, "first_page": i + 1, "last_page": last + 1}
            if POPPLER_PATH:
                kw["poppler_path"] = POPPLER_PATH
            from pdf2image import convert_from_path
            for k, img in enumerate(convert_from_path(self.pdf, **kw)):
                self.cache[i + k] = cv2.cvtColor(np.array(img)[:, :, ::-1].copy(), cv2.COLOR_BGR2GRAY)
        return self.cache.pop(i)
    def drop(self, i):
        self.cache.pop(i, None)

def convert(pdf_path, engine, dump=False):
    import pdfplumber
    blocks = []
    with pdfplumber.open(pdf_path) as pdf:
        n = len(pdf.pages)
        pim = _PageImages(pdf_path, n)
        for i, page in enumerate(pdf.pages):
            try: txt = (page.extract_text() or "").strip()
            except Exception: txt = ""
            digital = len(txt) >= 30
            if dump: print(f"  · {i+1}/{n}쪽 → {'디지털' if digital else '스캔'}")
            if digital:
                pim.drop(i)
                pb = process_digital_page(page, dump)
                if not pb:
                    pb = process_scanned_page(engine, _page_gray(pdf_path, i), dump)
            else:
                pb = process_scanned_page(engine, pim.gray(i), dump)
            print(f"  · {i+1}/{n}쪽 처리 완료")
            blocks.extend(pb)
            if i < n-1: blocks.append(("pagebreak",))
    return blocks


# ══════════════════════════════════════════════════════════════════
#  D. HWPX 생성
# ══════════════════════════════════════════════════════════════════
def fix_ns(path):
    import zipfile
    NS = {"http://www.hancom.co.kr/hwpml/2011/head":"hh",
          "http://www.hancom.co.kr/hwpml/2011/core":"hc",
          "http://www.hancom.co.kr/hwpml/2011/paragraph":"hp",
          "http://www.hancom.co.kr/hwpml/2011/section":"hs"}
    tmp = path + ".nst"
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp,"w",zipfile.ZIP_DEFLATED) as zout:
        for it in zin.infolist():
            d = zin.read(it.filename)
            if it.filename.startswith("Contents/") and it.filename.endswith(".xml"):
                t = d.decode("utf-8"); al = {}
                for m in re.finditer(r'xmlns:(ns\d+)="([^"]+)"', t):
                    if m.group(2) in NS: al[m.group(1)] = NS[m.group(2)]
                for o,nn in al.items():
                    t = t.replace(f'xmlns:{o}=',f'xmlns:{nn}=').replace(f'<{o}:',f'<{nn}:').replace(f'</{o}:',f'</{nn}:')
                d = t.encode("utf-8")
            zout.writestr(it, d)
    os.replace(tmp, path)

def _save_doc(doc, path):
    """python-hwpx 7.x(doc.save(path)) / 6.x(doc.save_to_path(path)) 모두 지원"""
    if getattr(type(doc), "save", None) is not None:
        try:
            doc.save(path)
            if os.path.exists(path):
                return
        except (TypeError, AttributeError):
            pass
    doc.save_to_path(path)


def _skeleton_bytes():
    """python-hwpx 기본 서식(Skeleton.hwpx)이 설치본에서 빠진 경우 대비: 프로그램 폴더·휠에서 찾는다."""
    import glob, zipfile
    p = os.path.join(BASE, "Skeleton.hwpx")
    if os.path.isfile(p):
        return open(p, "rb").read()
    for whl in glob.glob(os.path.join(BASE, "wheels", "python_hwpx-*.whl")):
        try:
            with zipfile.ZipFile(whl) as z:
                return z.read("hwpx/data/Skeleton.hwpx")
        except Exception:
            pass
    raise FileNotFoundError("Skeleton.hwpx 를 찾을 수 없습니다(python-hwpx 를 다시 설치하세요).")

def _new_doc():
    from hwpx import HwpxDocument
    try:
        try:
            return HwpxDocument.new(inherit_style=False)
        except TypeError:
            return HwpxDocument.new()
    except FileNotFoundError:
        print("  (안내) 한글 기본서식 파일이 설치본에 없어 예비 사본을 사용합니다.")
        import hwpx.templates as T
        data = _skeleton_bytes()
        T.blank_document_bytes = lambda: data
        f = getattr(T, "_blank_document_members", None)
        if f is not None and hasattr(f, "cache_clear"):
            f.cache_clear()
        try:
            try:
                return HwpxDocument.new(inherit_style=False)
            except TypeError:
                return HwpxDocument.new()
        except FileNotFoundError:
            return HwpxDocument.open(data)

FONT = "휴먼명조"
BODY_PT = 12.0            # 글자크기 추정 실패 시 본문 기본값
LETTER = -5               # 기본 자간(%)

def _num_like(s):
    return bool(s) and bool(re.fullmatch(r"[\d,\.\-:()/ ~]+", s.strip()))

def _fmt_runs(p, pt):
    for run in p.runs:
        try: run.set_font(FONT)
        except Exception:
            try: run.font = FONT
            except Exception: pass
        if pt:
            try: run.size = float(pt)
            except Exception: pass

def _fmt_par(p, pt, align="JUSTIFY"):
    try: p.alignment = align
    except Exception: pass
    _fmt_runs(p, pt or BODY_PT)

def _fmt_cell(table, r, c, cell, header=False):
    try: cl = table.cell(r, c)
    except Exception: return
    align = "CENTER" if (header or not _num_like(cell.get("text",""))) else "RIGHT"
    pt = cell.get("size") or BODY_PT
    try:
        for p in cl.paragraphs:
            try: p.alignment = align
            except Exception: pass
            for run in p.runs:
                try: run.set_font(FONT)
                except Exception: pass
                try: run.size = float(pt)
                except Exception: pass
                if header:
                    try: run.bold = True
                    except Exception: pass
    except Exception:
        pass

def _median(xs):
    xs = sorted(x for x in xs if x)
    return xs[len(xs)//2] if xs else None

def _normalize_sizes(blocks):
    """스캔 OCR 높이는 줄마다 흔들리므로, 원본처럼 '적은 수의 크기'로 정리한다.
    · 본문: 문서 전체 중앙값 1개로 통일(11~13pt 범위로 보정)
    · 제목: '짧고(≤25자) 본문보다 확실히 큰' 줄만 비례 확대
    · 표: 표마다 글자 크기를 하나로 통일(9~12pt)"""
    raw = [pt for b in blocks if b[0] == "para" and not isinstance(b[1], str)
           for _t, pt in b[1] if pt]
    med = _median(raw) or BODY_PT
    body = min(13.0, max(11.0, med))
    out = []
    for b in blocks:
        if b[0] == "para" and not isinstance(b[1], str):
            items = []
            for t, pt in b[1]:
                r = (pt / med) if pt else 1.0
                s = t.strip()
                ends_sentence = bool(re.search(r"(다|요|음|함|임)\s*[.。]?\s*$", s)) and len(s) > 6
                if r >= 1.18 and len(s) <= 25 and not ends_sentence:   # 제목(문단 끝줄 제외)
                    v = round(min(30.0, body * r) * 2) / 2.0
                elif r <= 0.80 and len(t.strip()) <= 25:        # 작은 글씨
                    v = round(max(8.0, body * r) * 2) / 2.0
                else:
                    v = body
                items.append((t, v))
            out.append(("para", items))
        elif b[0] == "table":
            cells = b[3]
            tmed = _median([c.get("size") for c in cells.values()]) or body
            tsz = min(12.0, max(9.0, tmed * (body / med) if med else tmed))
            tsz = round(tsz * 2) / 2.0
            for c in cells.values():
                c["size"] = tsz
            out.append(b)
        else:
            out.append(b)
    return out

# ══════════════════════════════════════════════════════════════════
#  D-2. 원본 외형 재현(스캔 페이지): 여백·글자크기·줄간격·들여쓰기·정렬·표
# ══════════════════════════════════════════════════════════════════
MM = 283.465   # HWPUNIT per mm

def _em(t):
    """줄 문자열의 대략적 폭(em). 한글·한자·원문자=1, 숫자≈0.52, 영문≈0.55, 공백·기호≈0.4"""
    n = 0.0
    for ch in t:
        o = ord(ch)
        if ch == " ":
            n += 0.40
        elif (0xAC00 <= o <= 0xD7A3 or 0x3130 <= o <= 0x318F or 0x4E00 <= o <= 0x9FFF
              or 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF
              or 0x2460 <= o <= 0x24FF or 0x2160 <= o <= 0x217F):
            n += 1.0
        elif ch.isdigit():
            n += 0.52
        elif ch.isalpha():
            n += 0.55
        else:
            n += 0.40
    return max(n, 0.5)

def _q(xs, f):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    return xs[min(len(xs) - 1, max(0, int(round((len(xs) - 1) * f))))]

def _plan_geometry(pages):
    """원본 스캔들에서 여백·본문크기·줄간격 등 문서 기하정보를 추정한다."""
    W0, H0 = pages[0][0], pages[0][1]
    pw, ph = (297.0, 210.0) if W0 > H0 else (210.0, 297.0)
    lefts, rights, tops, bots, pitches, lhs, ptw, strokes = [], [], [], [], [], [], [], []
    for W, H, els in pages:
        lines = [e for e in els if e["kind"] == "line"]
        longl = [e for e in lines if len(e["t"].strip()) >= 12]
        if longl:
            lefts.append(_q([e["x0"] / W for e in longl], 0.15))
            rights.append(_q([e["x1"] / W for e in longl], 0.85))
        if els:
            tops.append(min(e["y0"] for e in els) / H)
            bots.append(max(e["y1"] for e in els) / H)
        cys = [((e["y0"] + e["y1"]) / 2) / H for e in lines]
        pitches += [b - a for a, b in zip(cys, cys[1:]) if b > a]
        lhs += [e["h"] / H for e in lines if e["h"] > 0]
        strokes += [e.get("stroke", 0) for e in lines if e.get("stroke", 0) > 0.1]
    left = _q(lefts, 0.5) or 0.12
    right = _q(rights, 0.5) or 0.90
    Lmm = max(8.0, min(45.0, left * pw))
    Rmm = max(5.0, min(45.0, (1 - right) * pw))
    avail = pw - Lmm - Rmm
    # 본문 크기: 오른쪽 끝까지 찬 줄(원본에서 폭이 확정된 줄)로부터 역산
    for W, H, els in pages:
        for e in els:
            if e["kind"] == "line" and len(e["t"].strip()) >= 12 and e["x1"] / W >= right - 0.02:
                wmm = (e["x1"] - e["x0"]) / W * pw
                ptw.append(wmm / _em(e["t"]) / 0.3528)
    lh_med = _q(lhs, 0.5)
    body_h = (lh_med * ph / 0.3528) if lh_med else None
    body = _q(ptw, 0.3) if ptw else body_h
    body = round(max(8.0, min(16.0, (body or BODY_PT) * 0.98)) * 2) / 2.0
    # 줄 간격(문단 안 줄 사이 거리)
    base = _q(pitches, 0.25)
    typical = _q([p for p in pitches if p <= base * 1.5], 0.5) if base else None
    pitch_mm = (typical * ph) if typical else body * 0.3528 * 1.6
    spacing = int(round(max(100, min(300, pitch_mm / 0.3528 / body * 100))))
    top = _q(tops, 0.5) or 0.08
    bot = _q(bots, 1.0) or 0.92          # 가장 아래까지 찬 쪽 기준(덜 찬 쪽 때문에 여백이 커지지 않게)
    Tmm = max(5.0, min(60.0, top * ph - 1.0))
    Bmm = max(5.0, min(40.0, (1 - bot) * ph - 8.0))
    return {"pw": pw, "ph": ph, "L": Lmm, "R": Rmm, "T": Tmm, "B": Bmm,
            "avail": avail, "body": body, "spacing": spacing, "pitch_mm": pitch_mm,
            "lh_med": lh_med, "landscape": W0 > H0,
            "stroke_med": _q(strokes, 0.5) or 0.25}

def _fixed_ls(mm):
    """고정 줄간격(mm) → LineSpacing(FIXED, HWPUNIT). 실패 시 None"""
    try:
        import hwpx.objects as O
        return O.LineSpacing(type=O.LineSpacingType.FIXED, value=int(round(mm / 0.3528 * 100)))
    except Exception:
        return None

def _spacer(doc, mm, pagebreak=False):
    """세로 빈 공간(mm)을 '고정 높이의 빈 줄'로 넣는다(문단 위 간격보다 프로그램 간 호환성↑)."""
    p = doc.add_paragraph("")
    ls = _fixed_ls(max(0.5, mm))
    _set_par(p, align="LEFT", indent=0.0, spacing=ls, pagebreak=pagebreak)
    _set_runs(p, 1.0)
    return p

def _set_par(p, align=None, indent=None, before=None, spacing=None, pagebreak=False):
    if align:
        try: p.alignment = align
        except Exception: pass
    if indent is not None:
        try: p.indent_left_mm = round(max(0.0, indent), 1)
        except Exception: pass
    try: p.spacing_before_pt = round(before, 1) if before else 0.0
    except Exception: pass
    if spacing:
        try: p.line_spacing = spacing
        except Exception: pass
    if pagebreak:
        try: p.page_break_before = True
        except Exception: pass

def _set_runs(p, size, bold=False, letter=None):
    for run in p.runs:
        try: run.set_font(FONT)
        except Exception: pass
        try: run.size = float(size)
        except Exception: pass
        if bold:
            try: run.bold = True
            except Exception: pass
        lt = LETTER if letter is None else min(int(letter), LETTER)
        if lt:
            try: run.letter_spacing = int(lt)
            except Exception: pass


def _trim_empty_rows(e):
    """표 위/아래 가장자리의 '내용 없는 행'(격자 오검출) 제거. e 를 직접 수정."""
    nR, nC, cells, rowh = e["nR"], e["nC"], e["cells"], e["rowh"]
    def empty(r):
        return all(not cells[(r, c)].get("text", "").strip()
                   for c in range(nC) if (r, c) in cells)
    top, bot = 0, nR - 1
    while top < bot and empty(top): top += 1
    while bot > top and empty(bot): bot -= 1
    if top == 0 and bot == nR - 1:
        return
    new = {}
    for (r, c), cell in cells.items():
        if top <= r <= bot:
            nc = dict(cell); nc["rowspan"] = min(cell.get("rowspan", 1), bot - r + 1)
            new[(r - top, c)] = nc
    e["y0"] += sum(rowh[:top]); e["y1"] -= sum(rowh[bot + 1:])
    e["nR"], e["cells"], e["rowh"] = bot - top + 1, new, rowh[top:bot + 1]

def _table_font(e, W, H, G):
    """표 글자크기: 각 칸의 가장 긴 줄이 열 너비에 '한 줄로' 들어가는 크기(+행높이 상한)."""
    pw, ph = G["pw"], G["ph"]
    colw = e["colw"]; tot = float(sum(colw)) or 1.0
    tw = min(G["avail"], (e["x1"] - e["x0"]) / W * pw)
    col_mm = [w / tot * tw for w in colw]
    fits = []
    for (r, c), cell in e["cells"].items():
        txt = cell.get("text", "")
        if not txt.strip():
            continue
        cs = cell.get("colspan", 1)
        wmm = sum(col_mm[c:c + cs]) - 1.6
        longest = max(txt.split("\n"), key=_em)
        if wmm > 1:
            fits.append(wmm / _em(longest) / 0.3528)
    rows_mm = [h / H * ph for h in e["rowh"]]
    cap = (_q(rows_mm, 0.25) or 6.0) * 0.62 / 0.3528
    f = _q(fits, 0.15)
    tpt = min(G["body"], cap, (f * 0.98) if f else G["body"] * 0.8)
    return round(max(6.0, tpt) * 2) / 2.0

def _emit_table(doc, e, W, H, G, before, pagebreak):
    pw, ph, L, avail = G["pw"], G["ph"], G["L"], G["avail"]
    nR, nC, cells = e["nR"], e["nC"], e["cells"]
    table = doc.add_table(nR, nC)
    for (r, c), cell in cells.items():
        table.set_cell_text(r, c, cell.get("text", ""), split_paragraphs=True)
    for (r, c), cell in cells.items():
        rs, cs = cell.get("rowspan", 1), cell.get("colspan", 1)
        if rs > 1 or cs > 1:
            try: table.merge_cells(r, c, r + rs - 1, c + cs - 1)
            except Exception: pass
    # 원본 표 너비·열 비율·행 높이
    tw = min(avail, (e["x1"] - e["x0"]) / W * pw)
    try: table.set_column_widths([float(w) for w in e["colw"]])
    except Exception: pass
    try: table.set_width(int(tw * MM))
    except Exception: pass
    rows_mm = [max(3.0, h / H * ph) for h in e["rowh"]]
    try: table.set_row_heights([int(r * MM) for r in rows_mm])
    except Exception: pass
    try: table.set_in_margins(left=int(0.6 * MM), right=int(0.6 * MM),
                              top=int(0.1 * MM), bottom=int(0.1 * MM))
    except Exception: pass
    try: table.set_out_margins(left=0, right=0, top=0, bottom=0)   # 표 바깥 여백 0(위치 오차 방지)
    except Exception: pass
    # 표 글자 크기: 본문 대비 글자높이 비율, 행 높이 안에 들어가게
    tpt = _table_font(e, W, H, G)
    for (r, c), cell in cells.items():
        try:
            cl = table.cell(r, c)
            align = cell.get("align") or ("RIGHT" if (r > 0 and _num_like(cell.get("text", ""))) else "CENTER")
            for p in cl.paragraphs:
                _set_par(p, align=align, spacing=100)
                _set_runs(p, tpt)
        except Exception:
            pass
        if cell.get("bg"):
            try: table.set_cell_shading(r, c, cell["bg"])
            except Exception: pass
    # 표의 가로 위치(가운데/왼쪽) · 위 간격 · 쪽 나눔
    x0mm, x1mm = e["x0"] / W * pw, e["x1"] / W * pw
    centered = abs((x0mm + x1mm) / 2 - (L + avail / 2)) < 5.0
    try:
        ap = table.paragraph
        _set_par(ap, align="CENTER" if centered else "LEFT",
                 indent=0.0 if centered else max(0.0, x0mm - L),
                 before=before, spacing=100, pagebreak=pagebreak)
    except Exception:
        pass
    return table

def _emit_page(doc, W, H, elems, G, pagebreak, last_page=False):
    pw, ph, L, R, avail, body = G["pw"], G["ph"], G["L"], G["R"], G["avail"], G["body"]
    pitch = G["pitch_mm"]
    lh_med = G["lh_med"] or 0
    right_edge, center_x = pw - R, L + avail / 2
    cur = G["T"]          # 지금까지 채운 세로 위치(mm, 쪽 위 기준)
    nt = nl = 0
    for idx, e in enumerate(elems):
        y0mm, y1mm = e["y0"] / H * ph, e["y1"] / H * ph
        if e["kind"] == "table":
            _trim_empty_rows(e)
            y0mm, y1mm = e["y0"] / H * ph, e["y1"] / H * ph
            gap = y0mm - cur
            if gap > 0.8:
                _spacer(doc, gap, pagebreak); pagebreak = False; cur += gap
            _emit_table(doc, e, W, H, G, None, pagebreak)
            pagebreak = False
            cur += sum(max(3.0, h / H * ph) for h in e["rowh"])
            nt += 1
            continue
        t = e["t"]; s = t.strip()
        x0mm, x1mm = e["x0"] / W * pw, e["x1"] / W * pw
        ratio = (e["h"] / H) / lh_med if (lh_med and e["h"]) else 1.0
        ends = bool(re.search(r"(다|요|음|함|임)\s*[.。]?\s*$", s)) and len(s) > 6
        heading = ratio >= 1.18 and len(s) <= 25 and not ends
        bold = e.get("stroke", 0) >= max(0.36, 1.45 * G["stroke_med"])
        big_title = heading and ratio >= 1.5 and (
            abs((x0mm + x1mm) / 2 - center_x) < 8.0 or y0mm < ph * 0.2)
        if big_title:                               # 가운데/쪽 상단의 확실히 큰 제목만 확대
            size = round(min(30.0, body * min(ratio, 2.0)) * 2) / 2.0
        elif heading:                               # 일반 제목: 본문 크기
            size = body
        elif ratio <= 0.75:                       # 원본에서 확실히 작은 글씨
            size = round(max(6.0, body * ratio) * 2) / 2.0
        else:
            size = body
        full = x1mm >= right_edge - 2.0 and len(s) >= 8
        centered = ((not full) and (x1mm - x0mm) < avail * 0.6
                    and abs((x0mm + x1mm) / 2 - center_x) < 6.0 and (x0mm - L) > 8.0)
        indent = 0.0 if centered else max(0.0, x0mm - L)
        if indent < 1.2:
            indent = 0.0
        # 오른쪽 끝에 붙은 짧은 줄(쪽 표시 2 / 5, 날짜·서명 등): 오른쪽 맞춤
        right_short = (not full) and (not centered) and x1mm >= right_edge - 3.0 \
            and (x1mm - x0mm) < avail * 0.4
        if right_short:
            indent = 0.0
        if centered:
            align = "CENTER"
        elif right_short:
            align = "RIGHT"
        elif full:
            align = "DISTRIBUTE_SPACE" if t.count(" ") >= max(3, len(s) / 10) else "DISTRIBUTE"
        else:
            align = "LEFT"
        # 줄 높이는 '고정값'으로: 원본의 '이 줄 → 다음 줄' 실제 간격을 그대로 재현
        nxt = elems[idx + 1] if idx + 1 < len(elems) else None
        if nxt is None:
            lh = size * 0.3528 * 1.25
        else:
            local = nxt["y0"] / H * ph - y0mm
            if nxt["kind"] == "line" and 0 < local <= pitch * 1.5:
                lh = local
            else:
                lh = pitch
            lh = max(lh, size * 0.3528 * 1.1)
        lsp = _fixed_ls(lh) or int(round(max(100, min(300, lh / 0.3528 / size * 100))))
        # 한글은 줄간격 여분을 글자 '아래'에 둔다 → 글자 윗변을 원본 위치(y0)에 맞춤
        gap = y0mm - cur
        # 공백 때문에 쪽 아래로 넘치지 않게(쪽 수 = 원본 쪽 수 유지)
        limit = ph - G["B"] - 10.0
        if idx == len(elems) - 1 and last_page:   # 문서 끝: 일부 프로그램이 빈 줄을 덧붙이므로 여유 추가
            limit -= lh + 3.0
        if gap > 0:
            gap = min(gap, limit - cur - lh)
        if gap > 0.8:
            _spacer(doc, gap, pagebreak); pagebreak = False
        # 넘침(줄바뀜) 방지: 자연 폭 > 남은 폭이면 자간을 줄인다
        room = avail - indent
        natural = _em(t) * size * 0.3528 + len(t) * size * 0.3528 * LETTER / 100.0
        letter = None
        if natural > room * 0.995 and len(t) > 1:
            need = (natural - room * 0.99) / len(t)
            letter = LETTER - min(30, int(round(need / (size * 0.3528) * 100)) + 1)
        p = doc.add_paragraph(t)
        _set_par(p, align=align, indent=indent, before=None,
                 spacing=lsp, pagebreak=pagebreak)
        _set_runs(p, size, bold=bold, letter=letter)
        pagebreak = False
        cur += (gap if gap > 0.8 else 0.0) + lh
        nl += 1
    return nt, nl

def build_hwpx(blocks, out):
    import shutil
    pages = [(b[1], b[2], b[3]) for b in blocks if b[0] == "page"]
    if pages:
        return _build_faithful(blocks, pages, out)
    blocks = _normalize_sizes(blocks)
    doc = _new_doc()
    try:
        doc.page.setup(paper_size="A4", margin_top_mm=15, margin_bottom_mm=15,
                       margin_left_mm=20, margin_right_mm=20)
    except Exception:
        pass
    nt = npa = 0
    for blk in blocks:
        if blk[0] == "para":
            data = blk[1]
            items = [(ln, None) for ln in data.split("\n")] if isinstance(data, str) else data
            for text, pt in items:
                p = doc.add_paragraph(text)
                _fmt_par(p, pt, align="JUSTIFY")
            doc.add_paragraph(""); npa += 1
        elif blk[0] == "pagebreak":
            doc.add_paragraph("")
        elif blk[0] == "table":
            nR, nC, cells = blk[1], blk[2], blk[3]
            colw = blk[4] if len(blk) > 4 else None
            table = doc.add_table(nR, nC)
            for (r,c),cell in cells.items():
                table.set_cell_text(r, c, cell.get("text",""), split_paragraphs=True)
            for (r,c),cell in cells.items():
                rs, cs = cell.get("rowspan",1), cell.get("colspan",1)
                if rs>1 or cs>1:
                    try: table.merge_cells(r, c, r+rs-1, c+cs-1)
                    except Exception: pass
            for (r,c),cell in cells.items():
                _fmt_cell(table, r, c, cell, header=(r == 0))
            if colw:
                tot = float(sum(colw)) or 1.0
                rel = [max(1, int(round(w / tot * 1000))) for w in colw]
                try: table.set_column_widths(rel)
                except Exception: pass
            nt += 1
    if nt == 0 and npa == 0:
        doc.add_paragraph("(추출된 내용이 없습니다. 스캔 품질을 확인하세요.)")
    tmp = out + ".t.hwpx"
    _save_doc(doc, tmp); fix_ns(tmp); shutil.move(tmp, out)
    return nt, npa


def _build_faithful(blocks, pages, out):
    """스캔 페이지의 기하정보로 원본 외형(여백·크기·줄간격·쪽)을 최대한 재현."""
    import shutil
    G = _plan_geometry(pages)
    doc = _new_doc()
    try:
        doc.page.setup(paper_size="A4",
                       orientation="landscape" if G["landscape"] else "portrait",
                       margin_top_mm=G["T"], margin_bottom_mm=G["B"],
                       margin_left_mm=G["L"], margin_right_mm=G["R"],
                       header_margin_mm=0, footer_margin_mm=0)
    except Exception:
        pass
    nt = nl = 0
    started = False
    pending = False
    last_idx = max(i for i, b in enumerate(blocks) if b[0] == "page")
    for bi, b in enumerate(blocks):
        if b[0] == "page":
            t, l = _emit_page(doc, b[1], b[2], b[3], G, pagebreak=started,
                              last_page=(bi == last_idx))
            nt += t; nl += l; started = True; pending = False
        elif b[0] == "pagebreak":
            pending = started
        elif b[0] == "para":           # (디지털 페이지가 섞인 경우) 단순 문단
            data = b[1]
            items = [(ln, None) for ln in data.split("\n")] if isinstance(data, str) else data
            for text, _pt in items:
                p = doc.add_paragraph(text)
                _set_par(p, align="JUSTIFY", spacing=G["spacing"], pagebreak=pending)
                _set_runs(p, G["body"])
                pending = False; nl += 1
            started = True
        elif b[0] == "table":
            nR, nC, cells = b[1], b[2], b[3]
            table = doc.add_table(nR, nC)
            for (r, c), cell in cells.items():
                table.set_cell_text(r, c, cell.get("text", ""), split_paragraphs=True)
            for (r, c), cell in cells.items():
                _fmt_cell(table, r, c, cell, header=(r == 0))
            if pending:
                try: table.paragraph.page_break_before = True
                except Exception: pass
            pending = False; started = True; nt += 1
    if nt == 0 and nl == 0:
        doc.add_paragraph("(추출된 내용이 없습니다. 스캔 품질을 확인하세요.)")
    print(f"  · 외형: 여백 위{G['T']:.0f}/아래{G['B']:.0f}/좌{G['L']:.0f}/우{G['R']:.0f}mm, "
          f"본문 {G['body']}pt, 줄간격 {G['spacing']}%")
    tmp = out + ".t.hwpx"
    _save_doc(doc, tmp); fix_ns(tmp); shutil.move(tmp, out)
    return nt, nl


CONVERT_DIR = "변환_폴더"      # 여기에 PDF를 넣고 실행 → 전부 자동 변환

def _log_line(msg):
    import datetime
    try:
        with open(os.path.join(WORK_ROOT, "pdf2hwpx_log.txt"), "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except Exception:
        pass

def main(argv=None):
    import traceback, time
    ap = argparse.ArgumentParser(description="PDF → HWPX (PaddleOCR 기반, 오프라인)")
    ap.add_argument("inputs", nargs="*", help="PDF 파일·폴더(없으면 '변환_폴더')")
    ap.add_argument("--engine", choices=["auto","paddle","tesseract"], default="auto")
    ap.add_argument("--fast", action="store_true", help="경량 탐지모델(빠름, 정확도 조금 낮음)")
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--redo", action="store_true", help="이미 .hwpx 가 있어도 다시 변환")
    ap.add_argument("--version", action="version", version=f"pdf2hwpx {__version__}")
    a = ap.parse_args(argv)
    if a.fast:
        os.environ["PDF2HWPX_DET_MODEL"] = "PP-OCRv5_mobile_det"
    ins, folder_mode = a.inputs, False
    if not ins:
        d = os.path.join(WORK_ROOT, CONVERT_DIR)
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
            print(f"● '{CONVERT_DIR}' 폴더를 만들었습니다: {d}")
            print("  이 폴더에 변환할 PDF들을 넣고 다시 실행하세요.")
            return
        ins, folder_mode = [d], True
    files = []
    for p in ins:
        if os.path.isdir(p):
            folder_mode = True
            files += [os.path.join(p, f) for f in sorted(os.listdir(p)) if f.lower().endswith(".pdf")]
        elif p.lower().endswith(".pdf") and os.path.exists(p):
            files.append(p)
        else:
            print("[건너뜀] PDF 아님/없음:", p)
    # 폴더에서 가져온 파일은 이미 변환된 것(.hwpx 있음)을 건너뜀
    todo = [f for f in files if a.redo or not (folder_mode and os.path.exists(os.path.splitext(f)[0] + ".hwpx"))]
    print(f"● 대상 PDF {len(files)}개 — 변환 {len(todo)}개, 이미 변환되어 건너뜀 {len(files) - len(todo)}개")
    if not todo:
        return
    engine = make_engine(a.engine)
    ok = bad = 0
    for k, f in enumerate(todo, 1):
        out = os.path.splitext(f)[0] + ".hwpx"
        print(f"\n[{k}/{len(todo)}] {os.path.basename(f)}")
        try:
            t0 = time.time()
            blocks = convert(f, engine, dump=a.dump)
            nt, npa = build_hwpx(blocks, out)
            print(f"  → {os.path.basename(out)}  (표 {nt} · 문단 {npa}, {time.time() - t0:.0f}초)")
            _log_line(f"OK  {f}"); ok += 1
        except Exception:
            tb = traceback.format_exc()
            print("  [오류] 이 파일은 건너뜁니다 — 자세한 내용은 pdf2hwpx_log.txt")
            _log_line(f"ERROR  {f}\n{tb}"); bad += 1
    print(f"\n● 완료 {ok}개" + (f", 실패 {bad}개(pdf2hwpx_log.txt 확인)" if bad else ""))
    print("※ 스캔 문서의 숫자·계좌는 원문과 대조하세요.")
    return bad

if __name__ == "__main__":
    import traceback
    try:
        sys.exit(1 if main() else 0)
    except SystemExit:
        raise
    except Exception:
        tb = traceback.format_exc()
        print(tb)
        _log_line(f"ERROR  {' '.join(sys.argv[1:])}\n{tb}")
        print("[오류] 위 내용이 pdf2hwpx_log.txt 에 기록되었습니다.")
        sys.exit(1)
