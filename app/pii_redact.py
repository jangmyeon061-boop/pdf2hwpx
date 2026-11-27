# -*- coding: utf-8 -*-
"""
PDF 개인정보 비식별화(부분 마스킹)
────────────────────────────────────────────────────────────────────
· 원본 PDF(스캔/이미지) 위에서 '필요한 글자만' 지우고 * (또는 검정 박스)로 표시해 비식별 PDF 생성.
· HWPX 재구성 없이 그 글자 픽셀만 가리므로 자간·줄간격·글자크기를 안 건드린다.

마스킹 규칙
  · 주민등록번호 : 앞 2자리(출생연도)만 남기고 나머지 전부
  · 전화·휴대전화·계좌·카드·사업자번호 : 끝 숫자 2개만
  · 사람이름 : 성(첫 글자)을 뺀 자리 중 '여러 이름이 공유하는(흔한) 글자'를
               지워 희귀한 글자를 남긴다. 딱 1글자.
  · 주소 : 읍·면·동(행정동) 이상은 유지. 그 이하 상세주소의 번지 등 숫자를 지우되,
           '○○동 ○○호'(공동주택 동/호)는 각 숫자의 마지막 1자리만.

사용:  python pii_redact.py [PDF파일|폴더 ...]  (없으면 '비식별_대상' 폴더)
       --keep-names : 이름은 그대로 두고 나머지만(묻지 않고 전부 자동)
       --auto       : 검토 화면 없이 자동 판단한 이름까지 감춤
결과:  같은 폴더에 입력_비식별.pdf, 로그는 _로그 폴더(원문 포함 — 검수 후 삭제 권장)
의존:  pdf2hwpx.py(같은 폴더), paddleocr, opencv, pillow, poppler
"""
import sys, os, re, argparse
from collections import Counter
import cv2

for _s in (sys.stdout, sys.stderr):          # 콘솔 인코딩(cp949·cp1252)에 없는 글자 때문에 멈추지 않게
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pdf2hwpx as P   # 엔진·렌더링·경로 재사용


# ══════════════════════════════════════════════════════════════════
#  1. 숫자 규칙형 개인정보 탐지
# ══════════════════════════════════════════════════════════════════
B0 = r"(?<![0-9A-Za-z])"
B1 = r"(?![0-9A-Za-z])"
BANK_KW = ("은행", "계좌", "농협", "국민", "신한", "하나", "우리", "기업",
           "카카오", "토스", "우체국", "새마을", "수협", "대구", "부산",
           "경남", "광주", "전북", "제주", "산업", "씨티", "케이뱅크")

# 차량번호 가운데 한글(용도 기호) — '월·년·일·만·원' 같은 일반 단위 글자는 제외(날짜·금액 오탐 방지)
_PLATE = "가나다라마거너더러머버서어저고노도로모보소오조구누두루무부수우주아바사자배하허호육공해국합"
PATTERNS = [
    ("주민등록번호",   re.compile(B0 + r"\d{6}\s?[-–]\s?\d{7}" + B1), False),   # 외국인·법인등록번호 동일 형식
    ("운전면허번호",   re.compile(B0 + r"(?:[가-힣]{2}\s?)?\d{2}-\d{2}-\d{6}-\d{2}" + B1), False),
    ("카드번호",       re.compile(B0 + r"\d{4}[- ]\d{4}[- ]\d{4}[- ]\d{4}" + B1), False),
    ("사업자등록번호", re.compile(B0 + r"\d{3}[-]\d{2}[-]\d{5}" + B1), False),
    ("휴대전화",       re.compile(B0 + r"01[016789][-\s]?\d{3,4}[-\s]?\d{4}" + B1), False),
    ("전화번호",       re.compile(B0 + r"0\d{1,2}[-\s]\d{3,4}[-\s]\d{4}" + B1), False),
    ("차량번호",       re.compile(r"(?<![0-9A-Za-z가-힣])(?:[가-힣]{2}\s?)?\d{2,3}\s?[" + _PLATE + r"]\s?\d{4}" + B1), False),
    ("여권번호",       re.compile(B0 + r"[A-Z]{1,2}\d{7,8}" + B1), False),
    ("IP주소",         re.compile(B0 + r"(?:\d{1,3}\.){3}\d{1,3}" + B1), False),
    ("가상자산주소",   re.compile(r"(?<![0-9A-Za-z])(?:0x[0-9a-fA-F]{40}|bc1[0-9a-z]{25,59}|[13][1-9A-HJ-NP-Za-km-z]{25,34}|T[1-9A-HJ-NP-Za-km-z]{33})(?![0-9A-Za-z])"), False),
    ("계좌번호",       re.compile(B0 + r"\d[\d\-]{8,16}\d" + B1), True),
    ("이메일",         re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), False),
]

def _digits(s):
    return sum(c.isdigit() for c in s)

def find_number_pii(text, ctx=""):
    """(유형, s, e) 목록(겹침 제거). ctx: 바로 앞 OCR 조각(표에서 은행명이 옆 칸에 있는 경우)."""
    hits, has_bank = [], any(k in (ctx + " " + text) for k in BANK_KW)
    for name, rgx, need_bank in PATTERNS:
        if need_bank and not has_bank:
            continue
        for m in rgx.finditer(text):
            val = m.group()
            if name == "IP주소" and not all(0 <= int(x) <= 255 for x in val.split(".")):
                continue
            if name == "여권번호" and not re.fullmatch(r"[A-Z]{1,2}\d{7,8}", val):
                continue
            if name == "계좌번호":
                if not (10 <= _digits(val) <= 16):
                    continue
                if re.fullmatch(r"\d{4}[-]\d{1,2}[-]\d{1,2}", val):   # 날짜 오탐 제외
                    continue
            hits.append((name, m.start(), m.end()))
    hits.sort(key=lambda h: (h[1], -(h[2]-h[1])))
    out, last = [], -1
    for name, s, e in hits:
        if s >= last:
            out.append((name, s, e)); last = e
    return out

def number_mask_ranges(name, text, s, e):
    """지울 글자 하위범위 [(a,b)...] 반환(패턴이 숫자로 끝나므로 끝 2글자=끝 2숫자)."""
    if name == "주민등록번호":
        return [(s + 2, e)]           # 앞 2자리(연도)만 남기고 나머지 전부
    if name == "이메일":
        at = text.find("@", s, e)
        return [(max(s, at - 2), at)] if at > s else [(s, e)]   # 아이디 끝 2글자
    # 가상자산(전자지갑) 주소: 끝 문자·숫자 7개
    # 나머지(전화·계좌·카드·사업자·차량·면허·여권·IP): 끝 영숫자 2개
    k = 7 if name == "가상자산주소" else 2
    idx = [i for i in range(s, e) if text[i].isalnum() and not ("가" <= text[i] <= "힣")]
    if len(idx) >= k:
        return [(idx[-k], idx[-1] + 1)]
    return [(e - 2, e)]


# ══════════════════════════════════════════════════════════════════
#  2. 사람 이름 탐지(앵커 + 성씨) 및 마스킹 글자 결정
# ══════════════════════════════════════════════════════════════════
SURNAMES = set("김이박최정강조윤장임한오서신권황안송전홍유고문양손배백허남심노하곽성차주우구"
               "나지엄채원천방공현함변염여추도소석선설마길연위표명기반라왕금옥육인맹제탁국어은편"
               "용예경봉사부가복태석빈선")
# 줄 전체가 이름 한 토큰(이름 칸)일 때만 쓰는 '흔한 성씨' 집합 — '사용중' 같은 오탐 방지
TOP_SURNAMES = set("김이박최정강조윤장임한오서신권황안송전홍유고문양손배백허남심노하곽"
                   "성차주우구나지엄채원천방현함변염여추")
# 흔한 이름 끝 글자(동률일 때 '흔한' 쪽을 지우기 위한 보조 사전)
COMMON_GIVEN = set("수민준영지현성진우연은서호재주아윤예하유도원희경선정미숙자순옥철용환식근범규태혁훈빈찬")

_AFTER = "은는이가을를과와의에도만로으씨군양님"   # 이름 뒤 조사·호칭(여기까진 같은 이름)
_NAME_END = r"(?=[" + _AFTER + r"]|[^가-힣]|$)"
ANCHOR_RE = re.compile(r"(피의자|피해자|참고인|진술인|고소인|고발인|신고자|목격자|명의|성\s*명|이름|"
                       r"피고인|변호인|대리인|대표자|담당자|형사|경위|경사|경장|순경|경감|경정|수사관)"
                       r"\s*[:：)\]]?\s*([가-힣]{2,3})" + _NAME_END)
SUFFIX_RE = re.compile(r"(?<![가-힣])([가-힣]{2,3})\s*(씨|군|양|님|(?:경위|경사|경장|순경|경감|경정|총경|"
                       r"검사|수사관|변호사|판사)(?![가-힣]))")
# 이름 나열(김갑동, 홍길동, 김을식 등은): 한 명이라도 이름으로 확인되면 나머지도 후보로
LIST_RE = re.compile(r"(?<![가-힣])([가-힣]{2,3}(?:\s*[,，·ㆍ]\s*[가-힣]{2,3})+)(?=\s*(?:등|은|는|이|가|과|와|에게|을|를|$))")
_JOSA = "은는이가을를과와의에도만"
NOT_NAME = {"인적사", "진술서", "주장하", "본인이", "명의로", "측에서",
            # 지명(시·도) — 이름 후보에서 뺌
            "서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종", "경기", "강원", "충북", "충남",
            "전북", "전남", "경북", "경남", "제주", "서울시", "부산시", "대구시", "인천시", "광주시", "대전시", "울산시"}

# 이름 바로 앞에 붙어 와도 되는 호칭·지위(한국어 OCR은 띄어쓰기를 자주 빠뜨린다: '피의자홍길동은')
ROLE_WORDS = ("피의자", "피해자", "참고인", "진술인", "고소인", "고발인", "신고자", "목격자", "피고인",
              "변호인", "대리인", "대표자", "담당자", "수사관", "명의", "형사", "경위", "경사", "경장",
              "순경", "경감", "경정")
NAME_START = "(?:(?<![가-힣])|" + "|".join(f"(?<={w})" for w in ROLE_WORDS) + ")"
_GLUED_JOSA = "가은는을를의에도와과"      # '피의자가같은' → '가같' 같은 오탐 방지


NOT_NAME |= set(BANK_KW)                 # '명의국민은행' → '국민' 은 이름이 아님


def _looks_like_name(tok):
    return 2 <= len(tok) <= 3 and tok[0] in SURNAMES and tok not in NOT_NAME

def _clean_name(tok):
    """끝에 붙은 조사 1글자를 떼어 실제 이름만 남긴다(김수는→김수)."""
    tok = tok.strip()
    if len(tok) >= 3 and tok[-1] in _JOSA and _looks_like_name(tok[:-1]):
        return tok[:-1]
    return tok

def collect_name_candidates(all_texts):
    """문서 전체 줄 텍스트에서 이름 후보 집합을 모은다(보수적)."""
    names = set()
    texts = list(all_texts) + [" ".join(all_texts)]   # 줄을 넘는 앵커(…피해자 / 홍길동에게)
    for t in texts:
        for m in ANCHOR_RE.finditer(t):
            nm = _clean_name(m.group(2))
            glued = m.start(2) == m.end(1)              # 호칭과 이름 사이 띄어쓰기 없음
            if glued and nm[:1] in _GLUED_JOSA:
                continue
            if _looks_like_name(nm):
                names.add(nm)
        for m in SUFFIX_RE.finditer(t):
            nm = _clean_name(m.group(1))
            if _looks_like_name(nm):
                names.add(nm)
        s = t.strip()
        if re.fullmatch(r"[가-힣]{3}", s) and s[0] in TOP_SURNAMES:
            names.add(s)              # 줄 전체가 이름 한 토큰(이름 칸/인적사항)
    for t in texts:                   # 나열된 이름(한 명이라도 확인된 목록)
        for m in LIST_RE.finditer(t):
            mem = [_clean_name(x) for x in re.split(r"\s*[,，·ㆍ]\s*", m.group(1))]
            if any(x in names for x in mem):
                names |= {x for x in mem if _looks_like_name(x)}
    return names

def auto_names(doc):
    return collect_name_candidates([L["text"] for lines in doc["pages"] for L in lines])

def decide_name_positions(names):
    """이름별로 '지울 글자 위치' 결정: 여러 이름이 공유하는(흔한) 글자를 지움."""
    cand = {nm: list(range(1, len(nm))) for nm in names if len(nm) >= 2}
    freq = Counter()
    for nm, idxs in cand.items():
        for ch in {nm[i] for i in idxs}:
            freq[ch] += 1
    pos = {}
    for nm, idxs in cand.items():
        if not idxs:
            continue
        if len(idxs) == 1:
            pos[nm] = idxs[0]; continue
        # 점수 높을수록 지움: ①공유빈도 ②흔한글자 ③뒤쪽
        pos[nm] = max(idxs, key=lambda i: (freq[nm[i]],
                                           1 if nm[i] in COMMON_GIVEN else 0, i))
    return pos

def name_mask_ranges(text, name_pos):
    """줄에서 확정 이름들의 '지울 1글자' 하위범위 목록.
    이름 뒤에 조사/호칭이 붙어도 매칭하되, 더 긴 이름의 일부는 아니게 한다."""
    out = []
    for nm, p in name_pos.items():
        pat = NAME_START + re.escape(nm) + r"(?=[" + _AFTER + r"]|[^가-힣]|$)"
        for m in re.finditer(pat, text):
            a = m.start() + p
            out.append((a, a + 1))        # 이름의 p번째 글자 1개
    return out


# ══════════════════════════════════════════════════════════════════
#  3. 주소 탐지 및 상세주소 마스킹
# ══════════════════════════════════════════════════════════════════
ADDR_LABEL = re.compile(r"(?<![가-힣])(주\s*소|주\s*거|등록기준지|본\s*적|거\s*소)(?=\s*[:：]|\s)")

# 주소 탐지(지번·도로명, 라벨 줄·본문 모두)
#   · 읍·면·동·리 / 도로명(○○로·○○길·○○번길)까지는 그대로 두고
#   · 그 뒤 번지·건물번호 숫자는 전부, ○○동 ○○호 의 숫자는 마지막 1자리만 지운다
#   · 층(1층)·날짜·금액 등은 건드리지 않는다
_NUM = r"(\d{1,5}(?:\s?-\s?\d{1,4})?)"
_NOT_UNIT = r"(?!\d)(?!\s?(?:만|원|억|천|명|회|개|년|월|일|시|분|초|세|%|㎡|평|건|번째|차|kg|km|m\b|층|호|동))"
ROAD_RE = re.compile(r"(?<![가-힣A-Za-z0-9])([가-힣A-Za-z][가-힣A-Za-z0-9·]{0,18}?(?:대로|로|길))"
                     r"(?:\s?\d{1,4}\s?(?:번?길|가))?\s*(?:지하\s?)?" + _NUM + _NOT_UNIT)
JIBUN_RE = re.compile(r"(?<![가-힣A-Za-z0-9])([가-힣][가-힣0-9]{0,8}?(?:읍|면|동|리|가))\s*(?:산\s?)?" + _NUM
                      + r"(?:\s?번지)?" + _NOT_UNIT)
UNIT_RE = re.compile(r"(\d{1,5})\s?(동|호)(?!선|실|기|차|봉|수|회)")     # 103동 1503호
ADMIN_CTX = re.compile(r"(서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충청|충북|충남|전라|전북|전남|"
                       r"경상|경북|경남|제주|특별시|광역시|특별자치)|[가-힣]{1,6}(?:시|군|구|읍|면)(?![가-힣])|"
                       r"[가-힣]{1,6}(?:시|군|구)(?=[가-힣0-9]{1,14}(?:대로|로|길|동|읍|면|리))")
BAD_ROAD_END = ("으로", "므로", "대로서", "로부터", "로서", "로써", "에게로", "쪽으로")
DATE_RE = re.compile(r"\d{4}\s*[.\-/년]\s*\d{1,2}\s*[.\-/월]\s*\d{1,2}\s*[.일]?|\d{4}\s*년")

def _is_address(text):
    return bool(address_mask_ranges(text))

def out_spans(out, off):
    return [(a + off, b + off) for a, b in out]

def address_mask_ranges(text, prev=""):
    """주소의 '지울 숫자' 하위범위 목록 [(a,b)]. prev: 같은 쪽 바로 앞 OCR 조각들
    (표에서 '주      거' 라벨이 다른 칸에 있거나, 주소가 줄을 넘어가는 경우의 문맥)."""
    if not re.search(r"\d", text):
        return []
    full = (prev[-60:] + "\n" if prev else "") + text
    off = len(full) - len(text)
    labeled = bool(ADDR_LABEL.search(full))
    dates = [d.span() for d in DATE_RE.finditer(full)]
    out, ends = [], []
    def ok_ctx(start):
        return labeled or bool(ADMIN_CTX.search(full[max(0, start - 40):start]))
    def add(a, b):
        if b > off and not any(x <= a < y for x, y in dates):
            out.append((max(a, off) - off, b - off))
    for rx, kind in ((ROAD_RE, "road"), (JIBUN_RE, "jibun")):
        for m in rx.finditer(full):
            name = m.group(1)
            if kind == "road" and (name.endswith(BAD_ROAD_END) or re.fullmatch(r"[0-9]+.*", name)):
                continue
            if kind == "jibun" and re.fullmatch(r"\d+[동호]", name):
                continue
            if not ok_ctx(m.start(2)):
                continue
            a, b = m.span(2)
            if any(x <= a < y for x, y in out_spans(out, off)):
                continue
            add(a, b); ends.append(b)
    # 건물 번호 뒤(같은 줄, 40자 안)의 ○○동 ○○호 → 숫자 마지막 1자리
    for e in ends:
        seg = full[e:e + 40]
        for u in UNIT_RE.finditer(seg):
            nb = e + u.end(1)
            add(nb - 1, nb)
    # 라벨 줄이면 건물번호 없이 나온 동·호도(예: 주거 : 금호아파트 102동 1503호)
    if labeled:
        for u in UNIT_RE.finditer(full):
            if u.start() >= off and u.group(2) in "동호":
                pre = full[max(0, u.start() - 1):u.start()]
                if re.match(r"[가-힣]", pre or " "):
                    continue
                add(u.end(1) - 1, u.end(1))
    return sorted(set(out))


# ══════════════════════════════════════════════════════════════════
#  4. OCR 줄 박스 + 글자 단위 분할
# ══════════════════════════════════════════════════════════════════
def _cw(ch):
    """글자 상대폭(덩어리 안 비례 배분용)."""
    o = ord(ch)
    if ch == " ":
        return 0.30
    if 0xAC00 <= o <= 0xD7A3 or 0x3130 <= o <= 0x318F or 0x4E00 <= o <= 0x9FFF or o >= 0x2E80:
        return 1.0
    if ch.isdigit() or ch.isalpha():
        return 0.55
    return 0.35

def _spread(text, x0, x1):
    """text 의 각 글자를 [x0,x1] 에 상대폭대로 배분 → [(cx0,cx1)...]"""
    ws = [_cw(c) for c in text]; tot = sum(ws) or 1.0
    out, acc = [], 0.0
    for w in ws:
        out.append((x0 + (x1 - x0) * acc / tot, x0 + (x1 - x0) * (acc + w) / tot))
        acc += w
    return out

def _ink_profile(gray, ly0, ly1, lx0, lx1):
    """줄 띠(ly0~ly1)의 열별 잉크 수. 표의 가로 테두리선(줄 폭 대부분을 덮는 행)은 빼고 센다
    — 그래야 표 칸 안 숫자 사이의 빈틈이 살아난다."""
    band = (gray[ly0:ly1, :] < 140)
    if band.size == 0:
        return None
    a, b = max(0, int(lx0)), max(int(lx0) + 1, int(lx1))
    rr = band[:, a:b].mean(axis=1)
    band = band.copy()
    band[rr > 0.55, :] = False
    return band.sum(axis=0)

def ocr_lines(engine, bgr):
    """반환: [{"text","poly","chars":[(x0,x1)...] }] — 글자마다 x 구간.
    PaddleOCR 의 '덩어리(word) 박스'를 쓰면 한글·숫자가 섞인 줄도 정확하다."""
    img = bgr if bgr.ndim == 3 else cv2.cvtColor(bgr, cv2.COLOR_GRAY2BGR)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    try:
        res = engine.ocr.predict(img, return_word_box=True)
    except TypeError:
        res = engine.ocr.predict(img)
    if not res:
        return []
    r = res[0]
    def g(k):
        try: return r[k]
        except Exception: return r.get(k) if hasattr(r, "get") else None
    texts = g("rec_texts"); polys = None
    for k in ("rec_polys", "dt_polys", "rec_boxes"):
        polys = g(k)
        if polys is not None and len(polys):
            break
    words, wboxes = g("text_word"), g("text_word_region")
    out = []
    if texts is None:
        return out
    for i, t in enumerate(texts):
        t = str(t)
        if not t.strip() or polys is None or i >= len(polys):
            continue
        poly = np.array(polys[i]).reshape(-1, 2).astype(float)
        lx0, lx1 = float(poly[:, 0].min()), float(poly[:, 0].max())
        chars = None
        wspans = []
        try:
            wl, bl = words[i], wboxes[i]
            if wl and bl and "".join(wl) == t and len(wl) == len(bl):
                xs0 = [float(min(p[0] for p in bx)) for bx in bl]
                xs1 = [float(max(p[0] for p in bx)) for bx in bl]
                ly0, ly1 = int(poly[:, 1].min()), int(poly[:, 1].max())
                ink = _ink_profile(gray, ly0, ly1, lx0, lx1) if ly1 > ly0 else None
                n = len(wl)
                chars = []
                wspans = []
                for k, w in enumerate(wl):
                    # 덩어리 구간: 앞/뒤 덩어리와의 중간점까지 → 그 안의 '실제 잉크 시작~끝'에 글자 배분
                    L0 = lx0 if k == 0 else (xs1[k - 1] + xs0[k]) / 2
                    R0 = lx1 if k == n - 1 else (xs1[k] + xs0[k + 1]) / 2
                    lead = len(w) - len(w.lstrip()); core = w.strip(); tail = len(w) - lead - len(core)
                    e0, e1 = xs0[k], xs1[k]
                    if ink is not None and R0 > L0:
                        cols = np.where(ink[int(L0):int(R0) + 1] > 0)[0]
                        if len(cols):
                            e0, e1 = int(L0) + cols[0], int(L0) + cols[-1] + 1
                    if lead:
                        chars += _spread(" " * lead, L0, max(L0 + 1, e0))
                    if core:
                        wspans.append((len(chars), len(chars) + len(core), int(e0), int(e1)))
                        chars += _spread(core, e0, max(e0 + 1, e1))
                    if tail:
                        chars += _spread(" " * tail, e1, max(e1 + 1, R0))
        except Exception:
            chars = None
        if chars is None or len(chars) != len(t):
            chars = _spread(t, lx0, lx1)          # 폴백: 줄 전체에 상대폭 배분
            wspans = []
        out.append({"text": t, "poly": poly, "chars": chars, "wspans": wspans})
    return out

def char_box(line, a, b):
    """글자 [a,b) 를 덮는 픽셀 박스(덩어리 좌표 기반)."""
    poly, chars = line["poly"], line["chars"]
    ly0, ly1 = int(poly[:, 1].min()), int(poly[:, 1].max())
    a = max(0, a); b = min(len(chars), b)
    if b <= a or ly1 <= ly0:
        return None
    x0 = int(round(chars[a][0])); x1 = int(round(chars[b - 1][1]))
    return (x0, ly0, max(x0 + 1, x1), ly1)


# ══════════════════════════════════════════════════════════════════
#  5. 페이지/문서 처리 (이름은 문서 전체를 본 뒤 2패스)
# ══════════════════════════════════════════════════════════════════
def collect_targets(text, name_pos, prev=""):
    """한 줄에서 (유형, 지울범위[(a,b)...], 표시값) 목록. prev = 같은 쪽 바로 앞 OCR 조각(주소 라벨·문맥)."""
    tg = []
    num = find_number_pii(text, prev)
    for name, s, e in num:
        tg.append((name, number_mask_ranges(name, text, s, e), text[s:e]))
    ar = [(a, b) for a, b in address_mask_ranges(text, prev)
          if not any(s <= a < e for _n, s, e in num)]          # 전화·계좌 등과 겹치면 그쪽 규칙 우선
    if ar:
        tg.append(("주소", ar, text))
    nr = name_mask_ranges(text, name_pos)
    if nr:
        tg.append(("이름", nr, text))
    return tg

def _strict_map(line, a, b, ink, h):
    """덩어리(word) 안의 잉크 덩어리 수가 글자 수(공백 제외)와 '정확히' 같을 때만
    글자 ↔ 잉크 덩어리를 1:1 대응(숫자·영문·하이픈 위주 덩어리에서 성립).
    반환: (cmap{글자idx:(x0,x1)}, 왼쪽 이웃 끝, 오른쪽 이웃 시작) 또는 None"""
    text = line["text"]
    for (cs, ce, e0, e1) in line.get("wspans", []):
        if not (cs <= a and b <= ce):
            continue
        idx = [i for i in range(cs, ce) if not text[i].isspace()]
        runs = []
        i, hi = max(0, e0 - 1), min(len(ink), e1 + 2)
        while i < hi:
            if ink[i] > 0:
                j = i
                while j < hi and ink[j] > 0:
                    j += 1
                if int(ink[i:j].sum()) >= max(3, 0.004 * h * h):
                    runs.append((i, j))
                i = j
            else:
                i += 1
        if len(runs) != len(idx) or not runs:
            return None
        m = dict(zip(idx, runs))
        sel = [m[i] for i in range(a, b) if i in m]
        if not sel:
            return None
        k0 = min(i for i in range(a, b) if i in m); k1 = max(i for i in range(a, b) if i in m)
        p = idx.index(k0); q = idx.index(k1)
        left = runs[p - 1][1] if p > 0 else None
        right = runs[q + 1][0] if q + 1 < len(runs) else None
        return m, left, right
    return None

def _run_at(ink, x):
    """x 가 잉크 덩어리 안이면 그 덩어리 (시작,끝), 아니면 None"""
    n = len(ink)
    if not (0 <= x < n) or ink[x] == 0:
        return None
    a = x
    while a > 0 and ink[a - 1] > 0: a -= 1
    b = x
    while b < n and ink[b] > 0: b += 1
    return a, b

def _clean_edges(ink, x0, x1, cw0=None, cw1=None):
    """경계가 글자 획 한가운데를 지나면: 그 덩어리가 범위 안에 절반 이상이면 통째 포함, 아니면 통째 제외.
    단, 덩어리가 글자 하나보다 훨씬 넓으면(인쇄 글자끼리 붙음) 통째 판단을 하지 않는다."""
    r = _run_at(ink, x0)
    if r:
        a, b = r
        if cw0 is None or (b - a) <= 1.3 * cw0:
            x0 = a if (b - x0) >= 0.5 * (b - a) else b
    r = _run_at(ink, x1 - 1)
    if r:
        a, b = r
        if cw1 is None or (b - a) <= 1.3 * cw1:
            x1 = b if (x1 - a) >= 0.5 * (b - a) else a
    return x0, max(x0 + 1, x1)

def _gaps(ink, lo, hi):
    """[lo,hi] 안의 '잉크 없는 열' 구간들 → [(시작, 끝)]"""
    out, i = [], max(0, lo)
    hi = min(len(ink) - 1, hi)
    while i <= hi:
        if ink[i] == 0:
            j = i
            while j + 1 <= hi and ink[j + 1] == 0:
                j += 1
            out.append((i, j)); i = j + 1
        else:
            i += 1
    return out

def _snap_pair(ink, x0, x1, win):
    """왼쪽·오른쪽 경계를 '함께' 빈틈에 맞춘다. 한글 한 음절 안의 틈(미 = ㅁ|ㅣ)에 잘못 붙지 않도록
    두 경계 사이 폭이 추정 글자폭에 가까운 빈틈 쌍을 고른다. 양쪽 모두 빈틈이 없으면 None."""
    L = [(a + b) / 2 for a, b in _gaps(ink, x0 - win, x0 + win)]
    R = [(a + b) / 2 for a, b in _gaps(ink, x1 - win, x1 + win)]
    if not L or not R:
        return None
    w = x1 - x0
    best = min(((abs(l - x0) + abs(r - x1) + 1.5 * abs((r - l) - w), l, r)
                for l in L for r in R if r - l > 2), default=None)
    if not best:
        return None
    return int(round(best[1])), int(round(best[2]))

def _snap(ink, x, win):
    """x 근처(±win)에서 잉크가 없는 '글자 사이 빈틈' 열로 경계를 옮긴다."""
    n = len(ink)
    lo, hi = max(0, x - win), min(n - 1, x + win)
    if hi <= lo:
        return x
    seg = ink[lo:hi + 1]
    m = seg.min()
    if m > 0.5 * max(1, seg.max()):       # 빈틈도, 글자끼리 붙은 '잘록한 이음매'도 없으면 그대로
        return x
    cand = [lo + i for i, v in enumerate(seg) if v <= m]
    return min(cand, key=lambda c: abs(c - x))

def _restore_rules(bgr, gray, X0, Y0, X1, Y1):
    """지운 사각형이 표 테두리(가로·세로선)를 건드렸으면 그 선을 다시 이어 그린다."""
    if gray is None:
        return
    H, W = gray.shape[:2]
    for y in range(max(0, Y0), min(H, Y1 + 1)):
        L = gray[y, max(0, X0 - 60):max(0, X0)]
        R = gray[y, min(W, X1 + 1):min(W, X1 + 61)]
        side = [seg for seg in (L, R) if seg.size >= 20]
        # 선 행: 좌·우 '양쪽 모두' 길게 고르게 어두움 → 그 밝기로 이어 그림
        good = [seg for seg in side if (seg < 215).mean() > 0.95 and float(seg.std()) < 30]
        if side and len(good) == len(side):
            v = int(np.median(np.concatenate(good)))
            bgr[y, max(0, X0):min(W, X1 + 1)] = (v, v, v)
    for x in range(max(0, X0), min(W, X1 + 1)):
        U = gray[max(0, Y0 - 60):max(0, Y0), x]
        D = gray[min(H, Y1 + 1):min(H, Y1 + 61), x]
        side = [seg for seg in (U, D) if seg.size >= 15]
        if len(side) == 2 and all((seg < 215).mean() > 0.9 and float(seg.std()) < 30 for seg in side):
            v = int(np.median(np.concatenate(side)))
            bgr[max(0, Y0):min(H, Y1 + 1), x] = (v, v, v)

STYLE = "star"     # "star": 가린 글자 수만큼 * 표시(기본) / "box": 검정 박스

def _draw_star(img, cx, cy, r, th, color):
    """글꼴 없이 선 3개로 * 를 그린다(어느 PC에서나 동일한 모양)."""
    import math
    for ang in (90, 30, 150):
        dx = r * math.cos(math.radians(ang)); dy = r * math.sin(math.radians(ang))
        cv2.line(img, (int(round(cx - dx)), int(round(cy - dy))),
                 (int(round(cx + dx)), int(round(cy + dy))), color, th, cv2.LINE_AA)

def draw_targets(bgr, line, targets, pad=2, log=None, page=0, gray=None):
    text = line["text"]
    poly = line["poly"]
    ly0, ly1 = int(poly[:, 1].min()), int(poly[:, 1].max())
    ink = None
    gy0, gy1, ink_col, bg_col = ly0, ly1, (40, 40, 40), (255, 255, 255)
    if gray is not None and ly1 > ly0:
        lx0, lx1 = int(poly[:, 0].min()), int(poly[:, 0].max())
        ink = _ink_profile(gray, ly0, ly1, lx0, lx1)
        # 글자 실제 높이(잉크 있는 행 — 표 테두리선 행 제외)와 잉크색·바탕색
        reg = gray[ly0:ly1, max(0, lx0):max(lx0 + 1, lx1)]
        rdark = (reg < 140).mean(axis=1)
        rows = np.where(((reg < 140).sum(axis=1) > 0) & (rdark <= 0.55))[0]
        if len(rows):
            gy0, gy1 = ly0 + int(rows[0]), ly0 + int(rows[-1]) + 1
        dark = reg[reg < 140]
        if dark.size:
            v = int(np.median(dark)); ink_col = (v, v, v)
        light = reg[reg >= 200]
        v = int(np.median(light)) if light.size else int(np.percentile(reg, 90)); bg_col = (v, v, v)
    for name, ranges, shown in targets:
        for (a, b) in ranges:
            box = char_box(line, a, b)
            if not box:
                continue
            x0, y0, x1, y1 = box
            e0, e1 = x0, x1
            pd = pad
            cmap = None
            sm = _strict_map(line, a, b, ink, y1 - y0) if ink is not None else None
            if sm:
                cmap, left, right = sm
                sel = [cmap[i] for i in range(a, b) if i in cmap]
                x0 = min(c[0] for c in sel); x1 = max(c[1] for c in sel)
                e0, e1 = x0, x1
                X0 = x0 - pad if left is None else max(left + 1, x0 - pad)
                X1 = x1 + pad if right is None else min(right - 1, x1 + pad)
                X0 = max(0, X0); X1 = min(bgr.shape[1], max(X0 + 1, X1))
                pd = None
            elif ink is not None:
                win = max(3, int((y1 - y0) * 0.22))
                sp = _snap_pair(ink, x0, x1, win)
                if sp:
                    x0, x1 = sp
                else:
                    x0 = _snap(ink, x0, win)
                    x1 = _snap(ink, x1, win)
                if x1 <= x0:
                    x0, x1 = e0, e1
                cw0 = line["chars"][a][1] - line["chars"][a][0]
                cw1 = line["chars"][b - 1][1] - line["chars"][b - 1][0]
                x0, x1 = _clean_edges(ink, x0, x1, cw0, cw1)
                pd = 1
            if pd is not None:
                X0 = max(0, x0 - pd); X1 = min(bgr.shape[1], x1 + pd)
            # 지우는 높이: 줄 상자 밖으로 살짝 나온 획(받침 등)까지 — 글자 높이의 15%만큼 위아래 확장
            ext = int(round(0.15 * max(6, gy1 - gy0)))
            Y0 = max(0, min(y0, gy0) - pad - ext); Y1 = min(bgr.shape[0], max(y1, gy1) + pad + ext)
            if STYLE == "box":
                cv2.rectangle(bgr, (X0, Y0), (X1, Y1), (0, 0, 0), -1)
                shown_mask = "■" * (b - a)
            else:
                # ① 원래 글자를 바탕색으로 완전히 지움 ② 글자 칸마다 * (구분기호 - 는 다시 그림)
                cv2.rectangle(bgr, (X0, Y0), (X1, Y1), bg_col, -1)
                _restore_rules(bgr, gray, X0, Y0, X1, Y1)
                gh = max(6, gy1 - gy0)
                r = 0.23 * gh; th = max(2, int(round(gh * 0.075)))
                cy = gy0 + 0.42 * gh
                k = (x1 - x0) / max(1e-6, (e1 - e0))
                out = []
                for i in range(a, b):
                    ch = text[i]
                    if cmap and i in cmap:
                        c0, c1 = cmap[i]; cx = (c0 + c1) / 2; kk = 1.0
                    else:
                        c0, c1 = line["chars"][i]
                        cx = x0 + ((c0 + c1) / 2 - e0) * k; kk = k
                    if ch in "-–":
                        hw = max(3, (c1 - c0) * kk * 0.45)
                        cv2.line(bgr, (int(cx - hw), int(gy0 + 0.55 * gh)), (int(cx + hw), int(gy0 + 0.55 * gh)),
                                 ink_col, th, cv2.LINE_AA)
                        out.append(ch)
                    elif ch.isspace():
                        out.append(ch)
                    else:
                        rr = min(r, 0.42 * max((c1 - c0) * kk, 0.45 * gh))   # 좁은 글자칸에선 별을 작게
                        _draw_star(bgr, cx, cy, max(3.0, rr), th, ink_col)
                        out.append("*")
                shown_mask = "".join(out)
            if log is not None:
                log.append((page, name, text[a:b] + " → " + shown_mask, shown, (X0, Y0, X1, Y1)))

# ══════════════════════════════════════════════════════════════════
#  5-2. 문서 판독(패스1) · 이름 후보 목록(3어절) · 비식별 적용(패스2)
# ══════════════════════════════════════════════════════════════════
def _reading_order(lines):
    """같은 높이(행)끼리 묶어 왼쪽→오른쪽, 위→아래 순서로 정렬."""
    if not lines:
        return lines
    cys = [(L["poly"][:, 1].min() + L["poly"][:, 1].max()) / 2 for L in lines]
    hs = sorted(float(L["poly"][:, 1].max() - L["poly"][:, 1].min()) for L in lines)
    tol = 0.5 * (hs[len(hs) // 2] or 20)
    order = sorted(range(len(lines)), key=lambda i: cys[i])
    rows, cur, ref = [], [], None
    for i in order:
        if ref is None or abs(cys[i] - ref) <= tol:
            cur.append(i); ref = cys[i] if ref is None else ref
        else:
            rows.append(cur); cur, ref = [i], cys[i]
    if cur:
        rows.append(cur)
    out = []
    for r in rows:
        out += [lines[i] for i in sorted(r, key=lambda i: lines[i]["poly"][:, 0].min())]
    return out

def analyze(pdf_path, engine=None, dpi=300, progress=print, step=None):
    """패스1: 쪽마다 OCR → {"pdf","dpi","n","pages":[[line...]], "sizes":[(W,H)]}"""
    import pdfplumber
    if engine is None:
        engine = P.make_engine("paddle")
    with pdfplumber.open(pdf_path) as pdf:
        n = len(pdf.pages)
    pages, sizes = [], []
    for i in range(n):
        pil = P._rasterize(pdf_path, dpi=dpi, page=i)[0]
        bgr = np.array(pil)[:, :, ::-1].copy()
        lines = _reading_order(ocr_lines(engine, bgr))
        pages.append(lines); sizes.append((bgr.shape[1], bgr.shape[0]))
        progress(f"  · {i+1}/{n}쪽 판독")
        if step:
            step("read", i + 1, n)
    return {"pdf": pdf_path, "dpi": dpi, "n": n, "pages": pages, "sizes": sizes}

def _is_footer_line(doc, pi, L):
    """쪽 맨 아래 꼬리말(문서번호·출력일 등) 줄인가 — 변환기와 같은 기준(쪽 높이 94% 아래)."""
    H = doc["sizes"][pi][1]
    cy = (L["poly"][:, 1].min() + L["poly"][:, 1].max()) / 2
    return cy >= P.FOOTER_BAND * H

def _norm_name(x):
    return re.sub(r"\s+", "", x or "")

def find_name_occurrences(doc, added=(), skip_footer=True):
    """이름이 나오는 모든 자리(자동 후보 + 사용자 추가).
    띄어쓰기가 잘못 들어간 경우(홍길동 ↔ 홍길 동)도 같은 이름으로 찾는다."""
    auto = {_norm_name(a) for a in auto_names(doc)}
    added = [a for a in dict.fromkeys(_norm_name(x) for x in added) if len(a) >= 2]
    names = sorted(auto | set(added), key=len, reverse=True)   # 긴 이름 우선
    pats = []
    for nm in names:
        core = r"[ \t]?".join(re.escape(c) for c in nm)
        if nm in added:      # 사용자가 추가한 이름: 붙여 쓴 경우(피의자홍길동은)도 찾음
            pats.append((nm, re.compile(core)))
        else:
            pats.append((nm, re.compile(NAME_START + core + r"(?=[" + _AFTER + r"]|[^가-힣]|$)")))
    occ = []
    for pi, lines in enumerate(doc["pages"]):
        for li, L in enumerate(lines):
            if skip_footer and _is_footer_line(doc, pi, L):
                continue
            t, taken = L["text"], []
            for nm, rx in pats:
                for m in rx.finditer(t):
                    if any(m.start() < b and a < m.end() for a, b in taken):
                        continue
                    taken.append((m.start(), m.end()))
                    idx = [k for k in range(m.start(), m.end()) if not t[k].isspace()]
                    occ.append({"key": (pi, li, m.start(), nm), "page": pi, "li": li,
                                "s": m.start(), "e": m.end(), "idx": idx, "name": nm,
                                "src": "추가" if nm in added else "자동"})
    occ.sort(key=lambda o: (o["page"], o["li"], o["s"]))
    return occ

def _occ_parts(doc, o):
    """한 자리의 문맥 3어절(앞 어절 + 이름이 든 어절 + 뒤 어절) → [(글자, 이름여부)]"""
    lines = doc["pages"][o["page"]]
    t = lines[o["li"]]["text"]
    toks = [(m.start(), m.end()) for m in re.finditer(r"\S+", t)]
    a = min((k for k, (x, y) in enumerate(toks) if y > o["s"]), default=0)
    b = max((k for k, (x, y) in enumerate(toks) if x < o["e"]), default=len(toks) - 1)
    ts, te = toks[a][0], toks[b][1]
    if a > 0:
        prev = t[toks[a - 1][0]:toks[a - 1][1]]
    else:
        pl = lines[o["li"] - 1]["text"].split() if o["li"] > 0 else []
        prev = pl[-1] if pl else ""
    if b + 1 < len(toks):
        nxt = t[toks[b + 1][0]:toks[b + 1][1]]
    else:
        nl = lines[o["li"] + 1]["text"].split() if o["li"] + 1 < len(lines) else []
        nxt = nl[0] if nl else ""
    parts = []
    if prev:
        parts.append((prev + " ", False))
    parts += [(t[ts:o["s"]], False), (t[o["s"]:o["e"]], True), (t[o["e"]:te], False)]
    if nxt:
        parts.append((" " + nxt, False))
    return [(x, f) for x, f in parts if x]

def name_items(doc, occ):
    """목록용: 같은 이름(띄어쓰기 차이 포함)은 한 줄로 — 한 번 체크하면 모든 자리에 적용."""
    groups = {}
    for o in occ:
        groups.setdefault(o["name"], []).append(o)
    items = []
    for nm, g in sorted(groups.items(), key=lambda kv: (kv[1][0]["page"], kv[1][0]["li"], kv[1][0]["s"])):
        items.append({"no": len(items) + 1, "name": nm, "occ": g, "page": g[0]["page"],
                      "parts": _occ_parts(doc, g[0]), "count": len(g),
                      "src": "추가" if any(o["src"] == "추가" for o in g) else "자동"})
    return items

def _clear_footer(doc, pi, bgr, gray, log=None):
    """위·아래 여백의 바코드·도장 같은 덩어리 표식 + 꼬리말 문서번호·출력일을 지운다
    (쪽 표시 '2 / 5' 는 남김)."""
    light = gray[gray >= 200]
    bgv = int(np.median(light)) if light.size else 255      # 종이 바탕색(순백이 아닐 수 있음)
    clean = P.remove_footer_marks(gray)
    bgr[clean != gray] = bgv
    for L in doc["pages"][pi]:
        if not _is_footer_line(doc, pi, L):
            continue
        t = L["text"].strip()
        if P.PAGE_NO_RE.match(t):
            continue
        poly = L["poly"]
        y0, y1 = int(poly[:, 1].min()) - 3, int(poly[:, 1].max()) + 3
        x0, x1 = int(poly[:, 0].min()) - 3, int(poly[:, 0].max()) + 3
        pn = P._split_page_no(L["text"])
        if pn:                               # 끝에 붙은 쪽 표시는 남기고 앞부분만
            k = L["text"].rstrip().rfind(pn)
            x1 = int(L["chars"][max(0, k - 1)][1]) + 2 if k > 0 else x0
        if x1 > x0:
            bgr[max(0, y0):y1, max(0, x0):x1] = bgv
        if log is not None:
            log.append((pi + 1, "꼬리말", t, t, (x0, y0, x1, y1)))

def apply_redaction(doc, out_pdf, occ, excluded=frozenset(), clear_footer=True, progress=print,
                    step=None, regions=()):
    """패스2: 재렌더 후 숫자·주소 규칙 + (제외되지 않은) 이름 자리를 비식별해 PDF 저장.
    regions: 사용자가 끌어서 지정한 '선택영역' [(쪽0부터, x0, y0, x1, y1) — 판독 해상도 좌표] → 검정 블럭"""
    from PIL import Image
    pdf_path, dpi = doc["pdf"], doc["dpi"]
    use = [o for o in occ if o["key"] not in excluded]
    name_pos = decide_name_positions({o["name"] for o in use})
    by_line = {}
    for o in use:
        p = name_pos.get(o["name"], len(o["name"]) - 1)
        idx = o.get("idx") or list(range(o["s"], o["e"]))
        k = idx[min(p, len(idx) - 1)]
        by_line.setdefault((o["page"], o["li"]), []).append((k, k + 1))
    pages, log = [], []
    for o in occ:
        if o["key"] in excluded:
            L = doc["pages"][o["page"]][o["li"]]
            log.append((o["page"] + 1, "이름(제외)", o["name"], L["text"], None))
    for i in range(doc["n"]):
        pil = P._rasterize(pdf_path, dpi=dpi, page=i)[0]
        bgr = np.array(pil)[:, :, ::-1].copy()
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        if clear_footer:
            _clear_footer(doc, i, bgr, gray, log)
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        cnt = 0
        for li, line in enumerate(doc["pages"][i]):
            if clear_footer and _is_footer_line(doc, i, line):
                continue
            prev = " ".join(L["text"] for L in doc["pages"][i][max(0, li - 2):li])
            tg = collect_targets(line["text"], {}, prev)     # 숫자·주소
            nr = by_line.get((i, li))
            if nr:
                tg.append(("이름", nr, line["text"]))
            if tg:
                draw_targets(bgr, line, tg, log=log, page=i + 1, gray=gray)
                cnt += sum(len(r) for _, r, _ in tg)
        for (pg, rx0, ry0, rx1, ry1) in regions:          # 선택영역 → 검정 블럭
            if pg == i:
                X0, Y0 = int(max(0, min(rx0, rx1))), int(max(0, min(ry0, ry1)))
                X1 = int(min(bgr.shape[1], max(rx0, rx1))); Y1 = int(min(bgr.shape[0], max(ry0, ry1)))
                if X1 > X0 and Y1 > Y0:
                    cv2.rectangle(bgr, (X0, Y0), (X1, Y1), (0, 0, 0), -1)
                    log.append((i + 1, "선택영역", "검정 블럭", "", (X0, Y0, X1, Y1))); cnt += 1
        pages.append(Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)))
        progress(f"  · {i+1}/{doc['n']}쪽 비식별 {cnt}건")
        if step:
            step("mask", i + 1, doc["n"])
    pages[0].save(out_pdf, "PDF", resolution=dpi, save_all=True, append_images=pages[1:])
    logpath = os.path.splitext(out_pdf)[0] + "_로그.txt"
    with open(logpath, "w", encoding="utf-8") as f:
        f.write("쪽\t유형\t지운글자\t원문맥락\t위치\n")
        for pg, name, masked, shown, box in log:
            f.write(f"{pg}\t{name}\t{masked}\t{shown}\t{box}\n")
    n_masked = sum(1 for r in log if r[1] not in ("이름(제외)", "꼬리말"))
    return n_masked, logpath

def redact_pdf(pdf_path, out_pdf, engine=None, dpi=300, dump=False, clear_footer=True):
    """검토 없이 자동 실행(--auto)."""
    doc = analyze(pdf_path, engine, dpi)
    occ = find_name_occurrences(doc)
    if dump:
        print("    [이름] 후보:", sorted(auto_names(doc)))
    return apply_redaction(doc, out_pdf, occ, clear_footer=clear_footer)


# ══════════════════════════════════════════════════════════════════
#  6. 이름 검토(명령창 버전)
# ══════════════════════════════════════════════════════════════════
def _parse_names(s):
    return [x.strip() for x in re.split(r"[,，、;\n]+", s or "") if len(x.strip()) >= 2]

def review_console(doc, added, ex_names, clear_footer=True):
    """tkinter 가 없는 PC용 간이 검토(명령창). added·ex_names 는 다음 파일에도 이어서 적용."""
    print(f"\n===== {os.path.basename(doc['pdf'])} =====")
    while True:
        occ = find_name_occurrences(doc, added)
        items = name_items(doc, occ)
        print("───── ① 이름 후보 — 이름이 아닌 번호를 고르세요 ─────")
        for it in items:
            ctx = "".join(t for t, _ in it["parts"])
            mark = "[이름아님]" if it["name"] in ex_names else ""
            print(f"  {it['no']:>3}. {it['name']}  ({it['page']+1}쪽, {it['count']}곳)  {ctx} {mark}")
        a = input("\n② 추가할 이름(쉼표로 구분, 없으면 Enter): ").strip()
        if a:
            added += [n for n in (_norm_name(x) for x in _parse_names(a)) if n not in added]; continue
        break
    ex = input("이름이 아닌 번호(예: 2,5,7-9 / 없으면 Enter): ").strip()
    nos = set()
    for part in re.split(r"[,\s]+", ex):
        m = re.fullmatch(r"(\d+)-(\d+)", part)
        if m:
            nos |= set(range(int(m.group(1)), int(m.group(2)) + 1))
        elif part.isdigit():
            nos.add(int(part))
    ex_names |= {it["name"] for it in items if it["no"] in nos}
    excluded = {o["key"] for o in occ if o["name"] in ex_names}
    return finish_file(doc, occ, excluded, clear_footer)

# ── 여러 파일(폴더) 처리 ──
DEFAULT_DIR = "비식별_대상"          # 기본 작업 폴더
OUT_SUFFIX = "_비식별"               # 이름도 감춘 결과
OUT_SUFFIX_KEEP = "_비식별_이름유지"   # 이름은 그대로 둔 결과

def out_path_for(pdf, keep_names=False):
    return os.path.splitext(pdf)[0] + (OUT_SUFFIX_KEEP if keep_names else OUT_SUFFIX) + ".pdf"

def _is_output(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    return stem.endswith(OUT_SUFFIX) or stem.endswith(OUT_SUFFIX_KEEP)

def log_dir_for(pdf):
    return os.path.join(os.path.dirname(os.path.abspath(pdf)), "_로그")

def collect_inputs(paths):
    """인자(파일·폴더) → 처리할 PDF 목록(이미 만든 _비식별.pdf 는 제외)."""
    files = []
    for p in paths:
        if os.path.isdir(p):
            for f in sorted(os.listdir(p)):
                if f.lower().endswith(".pdf") and not _is_output(f):
                    files.append(os.path.join(p, f))
        elif p.lower().endswith(".pdf") and not _is_output(p):
            files.append(p)
    return files

def _cache_path(pdf):
    d = os.path.join(os.path.dirname(os.path.abspath(pdf)), "_판독캐시")
    return d, os.path.join(d, os.path.basename(pdf) + ".pkl")

def _sig(pdf):
    st = os.stat(pdf); return (st.st_size, int(st.st_mtime))

def analyze_cached(pdf, engine, dpi=300, progress=print, step=None):
    """판독 결과를 _판독캐시 에 저장 → 중간에 창을 닫아도 다시 판독하지 않음(완료 후 자동 삭제)."""
    import pickle
    d, cp = _cache_path(pdf)
    try:
        with open(cp, "rb") as f:
            doc = pickle.load(f)
        if doc.get("sig") == _sig(pdf) and doc.get("dpi") == dpi:
            doc["pdf"] = pdf
            progress("  · 이전 판독 결과 사용")
            return doc
    except Exception:
        pass
    doc = analyze(pdf, engine, dpi, progress, step)
    doc["sig"] = _sig(pdf)
    try:
        os.makedirs(d, exist_ok=True)
        with open(cp, "wb") as f:
            pickle.dump(doc, f)
    except Exception:
        pass
    return doc

def drop_cache(pdf):
    d, cp = _cache_path(pdf)
    try:
        os.remove(cp)
        if not os.listdir(d):
            os.rmdir(d)
    except Exception:
        pass

def finish_file(doc, occ, excluded, clear_footer, progress=print, keep_names=False, step=None, regions=()):
    """한 파일 비식별 → 같은 폴더에 _비식별.pdf(이름유지면 _비식별_이름유지.pdf), 로그는 _로그 폴더."""
    pdf = doc["pdf"]
    out = out_path_for(pdf, keep_names)
    nred, logp = apply_redaction(doc, out, occ, excluded, clear_footer=clear_footer, progress=progress,
                                 step=step, regions=regions)
    try:
        ld = log_dir_for(pdf); os.makedirs(ld, exist_ok=True)
        dst = os.path.join(ld, os.path.basename(logp))
        if os.path.exists(dst):
            os.remove(dst)
        os.replace(logp, dst); logp = dst
    except Exception:
        pass
    drop_cache(pdf)
    return out, nred, logp


def _err_log(f):
    import traceback
    with open(os.path.join(P.WORK_ROOT, "pii_redact_log.txt"), "a", encoding="utf-8") as fo:
        fo.write(f"\n[ERROR] {f}\n" + traceback.format_exc())


def main(argv=None):
    ap = argparse.ArgumentParser(description="PDF 개인정보 비식별화(부분 마스킹)")
    ap.add_argument("inputs", nargs="*", help="PDF 파일 또는 폴더(없으면 '비식별_대상' 폴더)")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--style", choices=["star", "box"], default="star",
                    help="star: 가린 글자 수만큼 * 표시(기본) / box: 검정 박스")
    ap.add_argument("--auto", action="store_true", help="이름 검토 없이 바로 실행(자동 판단한 이름도 감춤)")
    ap.add_argument("--keep-names", action="store_true",
                    help="사람이름은 그대로 두고 나머지만 비식별(묻지 않고 폴더 전체 자동 처리)")
    ap.add_argument("--console", action="store_true", help="검토를 화면 대신 명령창에서")
    ap.add_argument("--keep-footer", action="store_true", help="하단 문서번호·바코드 표식을 지우지 않음")
    a = ap.parse_args(argv)
    global STYLE
    STYLE = a.style
    ins = a.inputs
    if not ins:
        d = os.path.join(P.WORK_ROOT, DEFAULT_DIR)
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
            print(f"● '{DEFAULT_DIR}' 폴더를 만들었습니다: {d}")
            print("  이 폴더에 비식별할 PDF들을 넣고 다시 실행하세요.")
            return
        ins = [d]
    files = collect_inputs(ins)
    if not files:
        print("● 처리할 PDF가 없습니다:", ", ".join(ins)); return
    todo = [f for f in files if not os.path.exists(out_path_for(f, a.keep_names))]
    print(f"● 대상 PDF {len(files)}개 (이미 완료 {len(files) - len(todo)}개)")
    for f in files:
        print("   -", os.path.basename(f), "(완료됨)" if f not in todo else "")
    if not todo:
        print("● 새로 처리할 파일이 없습니다(모두 _비식별.pdf 가 있음)."); return
    cf = not a.keep_footer
    gui_ok = not a.console and not a.auto
    if gui_ok:
        try:
            import tkinter  # noqa
            from PIL import ImageTk  # noqa
            import pii_gui  # noqa
        except Exception:
            gui_ok = False
            print("  (화면 표시 모듈이 없어 명령창으로 진행합니다)")
    factory = lambda: P.make_engine("paddle")
    if a.keep_names and gui_ok:
        print("● 사람이름은 그대로 두고 비식별합니다 — 진행 안내 창을 엽니다(묻지 않고 전부 자동 처리).")
        r = pii_gui.keep_names_gui(todo, factory, a.dpi, clear_footer=cf)
        print(f"● 완료 {r.get('ok', 0)}개" + (f", 실패 {r['bad']}개(pii_redact_log.txt)" if r.get("bad") else ""))
        return
    if a.auto or a.keep_names:
        eng = factory()
        if a.keep_names:
            print("● 사람이름은 그대로 두고 비식별합니다(묻지 않고 전부 자동 처리).")
        bad = 0
        for k, f in enumerate(todo, 1):
            print(f"\n[{k}/{len(todo)}] {os.path.basename(f)}")
            try:
                doc = analyze_cached(f, eng, a.dpi)
                occ = [] if a.keep_names else find_name_occurrences(doc)
                if a.dump:
                    print("    [이름] 후보:", sorted(auto_names(doc)))
                out, nred, logp = finish_file(doc, occ, set(), cf, keep_names=a.keep_names)
                print(f"  → {os.path.basename(out)} (지운 곳 {nred}건)")
            except Exception as ex:
                bad += 1
                print(f"  [오류] 이 파일은 건너뜁니다: {ex}")
                _err_log(f)
        print(f"\n● 완료 {len(todo) - bad}개" + (f", 실패 {bad}개(pii_redact_log.txt)" if bad else ""))
        return bad
    if gui_ok:
        print("● 검토 화면을 엽니다. (판독은 뒤에서 파일 순서대로 진행됩니다)")
        res = pii_gui.review_gui(files, factory, a.dpi, clear_footer=cf)
        for src, out, nred, logp in res:
            print(f"  → {os.path.basename(out)} (지운 곳 {nred}건)")
        print(f"● 완료 {len(res)}개. 로그는 각 폴더의 _로그 폴더(원문 포함 — 검수 후 삭제 권장)")
    else:
        eng = factory()
        added, ex_names = [], set()
        for k, f in enumerate(todo, 1):
            print(f"\n[{k}/{len(todo)}] 판독: {os.path.basename(f)}")
            doc = analyze_cached(f, eng, a.dpi)
            out, nred, logp = review_console(doc, added, ex_names, cf)
            print(f"  → {os.path.basename(out)} (지운 곳 {nred}건)")


if __name__ == "__main__":
    try:
        sys.exit(1 if main() else 0)
    except SystemExit:
        raise
    except Exception as e:
        _err_log(" ".join(sys.argv[1:]))
        print("[오류]", e); sys.exit(1)
