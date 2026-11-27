# -*- coding: utf-8 -*-
"""
시연용 '가상' 스캔 PDF 만들기 — 실제 사건·인물과 무관한 꾸며낸 내용입니다.
(이름·주민번호·전화·계좌·주소는 모두 가짜이며, 시연·시험 목적으로만 씁니다)

    python samples/make_samples.py            → samples/ 에 PDF 2개 생성
필요: pillow, numpy, 한글 글꼴(Noto Serif CJK KR 또는 바탕/명조 계열)
"""
import os, random, glob
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
DPI = 300
A4 = (int(210 / 25.4 * DPI), int(297 / 25.4 * DPI))
A4L = (A4[1], A4[0])
NOTE = "※ 시연용 가상 문서 — 등장 인물·번호·주소는 모두 꾸며낸 것입니다."


def _font(size, bold=False):
    cands = (["NotoSerifCJK-Bold.ttc", "NotoSerifCJKkr-Bold.otf"] if bold else
             ["NotoSerifCJK-Regular.ttc", "NotoSerifCJKkr-Regular.otf"]) + \
            ["batang.ttc", "HMKMMAG.TTF", "NanumMyeongjo.ttf", "malgun.ttf", "NotoSansCJK-Regular.ttc"]
    roots = ["/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts"),
             "C:/Windows/Fonts", "/Library/Fonts", "/System/Library/Fonts"]
    for c in cands:
        for r in roots:
            hits = glob.glob(os.path.join(r, "**", c), recursive=True)
            if hits:
                try:
                    return ImageFont.truetype(hits[0], size, index=1 if c.endswith(".ttc") and "CJK" in c else 0)
                except Exception:
                    return ImageFont.truetype(hits[0], size)
    raise SystemExit("한글 글꼴을 찾지 못했습니다(Noto Serif CJK 등 설치 필요)")


def pt(p):            # pt → px
    return int(round(p / 72 * DPI))

def mm(v):
    return int(round(v / 25.4 * DPI))


def scanify(img, seed):
    """스캔본처럼: 약간 기울기·흐림·잡티·종이색."""
    rnd = random.Random(seed)
    img = img.rotate(rnd.uniform(-0.35, 0.35), resample=Image.BICUBIC, fillcolor=255, expand=False)
    img = img.filter(ImageFilter.GaussianBlur(0.5))
    a = np.asarray(img).astype(np.float32)
    a = a * 0.93 + 10                                  # 잉크가 완전 검정이 아니게
    a += np.random.default_rng(seed).normal(0, 6, a.shape)
    a = np.clip(a * 0.985 + 4, 0, 255)                 # 살짝 누런 종이 대신 회백색
    return Image.fromarray(a.astype(np.uint8)).convert("L")


def footer(d, W, H, page, total, f):
    d.text((mm(20), H - mm(14)), "가상지방검찰청-2026-000123  출력일 2026-11-27", font=f, fill=0)
    s = f"{page} / {total}"
    d.text((W - mm(20) - d.textlength(s, font=f), H - mm(14)), s, font=f, fill=0)


def wrap(d, text, font, width):
    out, cur = [], ""
    for ch in text:
        if d.textlength(cur + ch, font=font) > width:
            out.append(cur); cur = ch
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


def report():
    """수사보고(가상) 2쪽: 인적사항 표 + 줄글."""
    W, H = A4
    ft, fb, fs, fn = _font(pt(20), True), _font(pt(12.5)), _font(pt(9)), _font(pt(12.5), True)
    pages = []
    L, R = mm(25), W - mm(22)
    # ── 1쪽 ──
    im = Image.new("L", (W, H), 255); d = ImageDraw.Draw(im)
    d.text((L, mm(10)), NOTE, font=fs, fill=90)
    t = "수 사 보 고"
    d.text(((W - d.textlength(t, font=ft)) / 2, mm(24)), t, font=ft, fill=0)
    d.text((L, mm(42)), "제목 : 가상 사기 사건 피의자 인적사항 및 수사 경과", font=fn, fill=0)
    # 인적사항 표
    rows = [("성      명", "홍길동"), ("주민등록번호", "900101-1234567"),
            ("직      업", "회사원"), ("휴대전화", "010-1234-5678"),
            ("주      거", "전라남도 가상시 해오름면 새빛로 123, 101동 1502호"),
            ("등록기준지", "전라남도 가상시 달빛동 45-6")]
    y0, rh = mm(54), mm(10.5)
    c1, c2 = L, L + mm(38)
    d.rectangle((c1, y0, R, y0 + rh * len(rows)), outline=0, width=3)
    for i, (k, v) in enumerate(rows):
        y = y0 + rh * i
        d.rectangle((c1 + 2, y + 2, c2 - 1, y + rh - 2), fill=232)
        if i:
            d.line((c1, y, R, y), fill=0, width=3)
        d.text((c1 + mm(3), y + mm(2.6)), k, font=fb, fill=0)
        d.text((c2 + mm(3), y + mm(2.6)), v, font=fb, fill=0)
    d.line((c2, y0, c2, y0 + rh * len(rows)), fill=0, width=2)
    body = [
        "1. 피의자 홍길동은 2026. 3. 2. 14:20경 가상시 해오름면 소재 커피숍에서 피해자 김영희에게 "
        "\"투자하면 매달 10%의 수익을 보장하겠다\"고 거짓말하여 이에 속은 피해자로부터 "
        "같은 날 피의자 명의 국민은행 계좌 123-456-789012 로 5,000,000원을 송금받았다.",
        "2. 참고인 이철수는 \"피의자가 같은 방법으로 다른 사람에게도 투자를 권유하는 것을 보았다\"고 "
        "진술하였고, 피해자 김영희의 휴대전화(010-9876-5432) 통화내역에서도 피의자와의 "
        "통화가 여러 차례 확인되었다.",
        "3. 피의자는 2026. 3. 15. 위 돈 중 3,000,000원을 신한은행 계좌 110-234-567890 으로 "
        "옮긴 뒤 현금으로 인출하였으며, 현재 연락이 닿지 않는 상태이다.",
    ]
    y = y0 + rh * len(rows) + mm(12)
    for para in body:
        for ln in wrap(d, para, fb, R - L - mm(6)):
            d.text((L + mm(3), y), ln, font=fb, fill=0); y += mm(8.2)
        y += mm(3)
    footer(d, W, H, 1, 2, fs)
    pages.append(scanify(im, 1))
    # ── 2쪽 ──
    im = Image.new("L", (W, H), 255); d = ImageDraw.Draw(im)
    d.text((L, mm(10)), NOTE, font=fs, fill=90)
    y = mm(26)
    d.text((L, y), "4. 향후 수사 계획", font=fn, fill=0); y += mm(10)
    plan = [
        "가. 피해자 김영희를 다시 불러 추가 피해 여부를 확인하고, 참고인 이철수와 대질 조사를 할 예정임.",
        "나. 피의자 명의 계좌 및 이체된 계좌(신한은행 110-234-567890)의 거래내역을 금융기관에 요청함.",
        "다. 피의자의 휴대전화(010-1234-5678) 통신사실 확인자료를 확보하여 소재를 파악함.",
    ]
    for para in plan:
        for k, ln in enumerate(wrap(d, para, fb, R - L - mm(10))):
            d.text((L + mm(3 if k == 0 else 8), y), ln, font=fb, fill=0); y += mm(8.2)
        y += mm(3)
    y += mm(14)
    s = "2026. 11. 27."
    d.text(((W - d.textlength(s, font=fb)) / 2, y), s, font=fb, fill=0); y += mm(14)
    s = "가상경찰서  수사과  경위  박민수"
    d.text((R - d.textlength(s, font=fn), y), s, font=fn, fill=0)
    footer(d, W, H, 2, 2, fs)
    pages.append(scanify(im, 2))
    return pages


def ledger():
    """범죄일람표(가상) 1쪽, 가로."""
    W, H = A4L
    ft, fb, fs, fh = _font(pt(18), True), _font(pt(11)), _font(pt(9)), _font(pt(11), True)
    im = Image.new("L", (W, H), 255); d = ImageDraw.Draw(im)
    L, R = mm(18), W - mm(18)
    d.text((L, mm(8)), NOTE, font=fs, fill=90)
    t = "【범죄일람표】"
    d.text(((W - d.textlength(t, font=ft)) / 2, mm(16)), t, font=ft, fill=0)
    t = "- 사기 -"
    d.text(((W - d.textlength(t, font=fb)) / 2, mm(27)), t, font=fb, fill=0)
    hdr = ["연번", "일시", "피해자", "입금 은행", "입금 계좌", "금액(원)", "비고"]
    rows = [
        ["1", "2026. 3. 2. 14:35", "김영희", "국민은행", "123-456-789012", "5,000,000", "투자 명목"],
        ["2", "2026. 3. 5. 10:12", "최수진", "국민은행", "123-456-789012", "2,500,000", "투자 명목"],
        ["3", "2026. 3. 9. 16:48", "정하늘", "농협은행", "301-2345-6789-01", "7,000,000", "원금 보장"],
        ["4", "2026. 3. 12. 09:05", "한소라", "농협은행", "301-2345-6789-01", "1,200,000", "수익금 명목"],
        ["5", "2026. 3. 14. 20:30", "오민준", "신한은행", "110-234-567890", "3,800,000", "투자 명목"],
        ["6", "2026. 3. 18. 11:47", "강다은", "신한은행", "110-234-567890", "4,500,000", "원금 보장"],
    ]
    cw = [mm(14), mm(46), mm(30), mm(30), mm(52), mm(36), 0]
    cw[-1] = (R - L) - sum(cw[:-1])
    xs = [L]
    for w in cw:
        xs.append(xs[-1] + w)
    y0, rh = mm(38), mm(12)
    nrow = len(rows) + 2
    d.rectangle((L + 2, y0 + 2, R - 2, y0 + rh - 2), fill=228)            # 머리행 음영(선보다 먼저)
    d.rectangle((L, y0, R, y0 + rh * nrow), outline=0, width=5)
    for i in range(1, nrow):
        d.line((L, y0 + rh * i, R, y0 + rh * i), fill=0, width=3)
    d.line((L, y0 + rh + 5, R, y0 + rh + 5), fill=0, width=2)      # 머리행 두줄선
    for x in xs[1:-1]:
        d.line((x, y0, x, y0 + rh * (nrow - 1)), fill=0, width=3)
    def cell(ci, ri, s, f, align="c"):
        x0, x1, y = xs[ci], xs[ci + 1], y0 + rh * ri + mm(3)
        w = d.textlength(s, font=f)
        x = x0 + (x1 - x0 - w) / 2 if align == "c" else x1 - w - mm(2.5)
        d.text((x, y), s, font=f, fill=0)
    for ci, h in enumerate(hdr):
        cell(ci, 0, h, fh)
    for ri, r in enumerate(rows, 1):
        for ci, v in enumerate(r):
            cell(ci, ri, v, fb, "r" if ci == 5 else "c")
    # 합계행(라벨 병합)
    yy = y0 + rh * (nrow - 1)
    d.line((xs[5], yy, xs[5], yy + rh), fill=0, width=2)
    d.line((xs[6], yy, xs[6], yy + rh), fill=0, width=2)
    s = "합      계"
    d.text((L + (xs[5] - L - d.textlength(s, font=fh)) / 2, yy + mm(3)), s, font=fh, fill=0)
    cell(5, nrow - 1, "24,000,000", fh, "r")
    footer(d, W, H, 1, 1, fs)
    return [scanify(im, 3)]


def save(pages, name):
    out = os.path.join(HERE, name)
    rgb = [p.convert("RGB") for p in pages]
    rgb[0].save(out, "PDF", resolution=DPI, save_all=True, append_images=rgb[1:])
    print("●", out)


if __name__ == "__main__":
    save(report(), "예시_수사보고(가상).pdf")
    save(ledger(), "예시_범죄일람표(가상).pdf")
