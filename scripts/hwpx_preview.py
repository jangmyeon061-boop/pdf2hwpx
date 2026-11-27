# -*- coding: utf-8 -*-
"""
HWPX → HTML 간이 미리보기 (한글 프로그램이 없는 PC에서 결과를 대략 확인하는 용도)

    python scripts/hwpx_preview.py 문서.hwpx [출력.html]

쪽 크기·여백, 문단 정렬·들여쓰기·고정 줄간격·쪽 나눔, 글자 크기·굵기·자간,
표(칸 병합·너비·높이·음영)를 반영합니다. 글꼴은 설치된 명조 계열로 대신 보여 줍니다.
실제 한글 프로그램의 조판과는 조금 다를 수 있습니다.
"""
import sys, os, re, zipfile, html
import xml.etree.ElementTree as ET

NS = {"hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
      "hh": "http://www.hancom.co.kr/hwpml/2011/head",
      "hc": "http://www.hancom.co.kr/hwpml/2011/core",
      "hs": "http://www.hancom.co.kr/hwpml/2011/section"}
HP = "{%s}" % NS["hp"]
U = 283.465          # HWPUNIT per mm
PX = 96 / 25.4       # px per mm


def mm(v):
    return float(v) / U


def load_header(z):
    root = ET.fromstring(z.read("Contents/header.xml"))
    chars, paras, fills = {}, {}, {}
    for c in root.iter("{%s}charPr" % NS["hh"]):
        sp = c.find("hh:spacing", NS)
        chars[c.get("id")] = {"pt": int(c.get("height", "1000")) / 100.0,
                              "bold": c.find("hh:bold", NS) is not None,
                              "ls": int(sp.get("hangul", "0")) if sp is not None else 0,
                              "color": c.get("textColor", "#000000")}
    for p in root.iter("{%s}paraPr" % NS["hh"]):
        al = p.find("hh:align", NS)
        left = p.find(".//hc:left", NS)
        ind = p.find(".//hc:intent", NS)
        ls = p.find(".//hh:lineSpacing", NS)
        bs = p.find("hh:breakSetting", NS)
        paras[p.get("id")] = {"align": al.get("horizontal", "JUSTIFY") if al is not None else "JUSTIFY",
                              "left": mm(left.get("value", "0")) if left is not None else 0.0,
                              "indent": mm(ind.get("value", "0")) if ind is not None else 0.0,
                              "lstype": ls.get("type", "PERCENT") if ls is not None else "PERCENT",
                              "lsval": int(ls.get("value", "160")) if ls is not None else 160,
                              "pbb": bs is not None and bs.get("pageBreakBefore") == "1"}
    for b in root.iter("{%s}borderFill" % NS["hh"]):
        wb = b.find(".//hc:winBrush", NS)
        fills[b.get("id")] = wb.get("faceColor") if wb is not None and wb.get("faceColor") not in (None, "none") else None
    return chars, paras, fills


ALIGN = {"LEFT": "left", "RIGHT": "right", "CENTER": "center", "JUSTIFY": "justify",
         "DISTRIBUTE": "justify", "DISTRIBUTE_SPACE": "justify"}


def runs_html(p, chars, scale=1.0):
    out, sizes = [], []
    for r in p.findall("hp:run", NS):
        c = chars.get(r.get("charPrIDRef"), {"pt": 10, "bold": False, "ls": 0, "color": "#000"})
        txt = "".join(t.text or "" for t in r.findall("hp:t", NS))
        if not txt:
            continue
        sizes.append(c["pt"])
        st = f"font-size:{c['pt'] * scale:.2f}pt;letter-spacing:{c['ls'] / 100:.2f}em;color:{c['color']}"
        if c["bold"]:
            st += ";font-weight:700"
        out.append(f'<span style="{st}">{html.escape(txt).replace(" ", "&nbsp;") if txt.strip() == "" else html.escape(txt)}</span>')
    return "".join(out), (max(sizes) if sizes else 10.0)


def para_style(pp, size):
    st = [f"text-align:{ALIGN.get(pp['align'], 'justify')}", f"margin-left:{pp['left']:.2f}mm",
          f"text-indent:{pp['indent']:.2f}mm"]
    if pp["align"] == "DISTRIBUTE" or pp["align"] == "DISTRIBUTE_SPACE":
        st.append("text-align-last:justify")
    if pp["lstype"] == "FIXED":
        st.append(f"line-height:{mm(pp['lsval']):.2f}mm;height:{mm(pp['lsval']):.2f}mm")
    else:
        st.append(f"line-height:{pp['lsval'] / 100 * 1.0:.2f}")
    st.append("white-space:pre;overflow:visible")
    return ";".join(st)


def table_html(tbl, chars, paras, fills):
    rows = []
    for tr in tbl.findall("hp:tr", NS):
        cells = []
        for tc in tr.findall("hp:tc", NS):
            span = tc.find("hp:cellSpan", NS); sz = tc.find("hp:cellSz", NS)
            cs = int(span.get("colSpan", "1")) if span is not None else 1
            rs = int(span.get("rowSpan", "1")) if span is not None else 1
            w = mm(sz.get("width", "0")) if sz is not None else 0
            h = mm(sz.get("height", "0")) if sz is not None else 0
            bg = fills.get(tc.get("borderFillIDRef"))
            inner = []
            for p in tc.iter(HP + "p"):
                pp = paras.get(p.get("paraPrIDRef"), {"align": "CENTER", "left": 0, "indent": 0,
                                                       "lstype": "PERCENT", "lsval": 100, "pbb": False})
                body, _ = runs_html(p, chars)
                inner.append(f'<div style="text-align:{ALIGN.get(pp["align"], "center")};line-height:1.15">{body or "&nbsp;"}</div>')
            st = f"width:{w:.2f}mm;height:{h:.2f}mm" + (f";background:{bg}" if bg else "")
            cells.append(f'<td colspan="{cs}" rowspan="{rs}" style="{st}">{"".join(inner)}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return '<table class="t">' + "".join(rows) + "</table>"


def render(path):
    z = zipfile.ZipFile(path)
    chars, paras, fills = load_header(z)
    secs = sorted(n for n in z.namelist() if re.match(r"Contents/section\d+\.xml", n))
    pages, cur = [], []
    W, H, M = 210.0, 297.0, (20, 20, 15, 15)
    for s in secs:
        root = ET.fromstring(z.read(s))
        pg = root.find(".//hp:pagePr", NS)
        if pg is not None:
            W, H = mm(pg.get("width")), mm(pg.get("height"))
            if pg.get("landscape") == "NARROWLY" or (W < H and pg.get("landscape") == "LANDSCAPE"):
                W, H = H, W
            mg = pg.find("hp:margin", NS)
            M = tuple(mm(mg.get(k)) for k in ("left", "right", "top", "bottom"))
        for p in root.findall("hp:p", NS):
            pp = paras.get(p.get("paraPrIDRef"), {"align": "JUSTIFY", "left": 0, "indent": 0,
                                                   "lstype": "PERCENT", "lsval": 160, "pbb": False})
            if (p.get("pageBreak") == "1" or pp["pbb"]) and cur:
                pages.append(cur); cur = []
            tbls = p.findall(".//hp:tbl", NS)
            if tbls:
                for t in tbls:
                    cur.append(f'<div style="text-align:{ALIGN.get(pp["align"], "left")};margin-left:{pp["left"]:.2f}mm">'
                               + table_html(t, chars, paras, fills) + "</div>")
                continue
            body, size = runs_html(p, chars)
            cur.append(f'<div class="p" style="{para_style(pp, size)}">{body or "&nbsp;"}</div>')
    if cur:
        pages.append(cur)
    L, R, T, B = M
    css = f"""
    body{{background:#5b6470;margin:0;padding:24px;font-family:'Noto Serif CJK KR','HCR Batang','Batang',serif}}
    .pg{{width:{W:.1f}mm;height:{H:.1f}mm;background:#fff;margin:0 auto 24px;box-sizing:border-box;
         padding:{T:.1f}mm {R:.1f}mm {B:.1f}mm {L:.1f}mm;overflow:hidden;box-shadow:0 4px 18px rgba(0,0,0,.35)}}
    .p{{margin:0}}
    table.t{{border-collapse:collapse;display:inline-table;table-layout:fixed}}
    table.t td{{border:0.12mm solid #000;padding:0 0.6mm;vertical-align:middle;overflow:hidden}}
    """
    body = "".join(f'<div class="pg">{"".join(c)}</div>' for c in pages)
    return f"<!DOCTYPE html><html><head><meta charset='utf-8'><style>{css}</style></head><body>{body}</body></html>"


if __name__ == "__main__":
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(src)[0] + "_미리보기.html"
    open(dst, "w", encoding="utf-8").write(render(src))
    print("●", dst)
