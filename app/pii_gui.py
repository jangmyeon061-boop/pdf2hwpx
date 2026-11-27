# -*- coding: utf-8 -*-
"""
비식별 화면 — 진행 안내판 · 이름 검토 화면 · 이름유지 진행 화면 (tkinter)
pii_redact.py 의 판독·비식별 함수를 화면에서 쓰기 위한 모듈.
"""
import os
import pdf2hwpx as P
from pii_redact import (analyze_cached, finish_file, out_path_for, OUT_SUFFIX,
                        find_name_occurrences, name_items, char_box, _parse_names, _norm_name)

from ui_theme import *  # noqa: F401,F403  (색·글꼴·shadow_btn)


def _tk_setup():
    import tkinter as tk
    try:
        import ctypes; ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    return tk


def _open_folder(path):
    try:
        os.startfile(os.path.dirname(os.path.abspath(path)))
    except Exception:
        pass


class ProgressPanel:
    """진행 안내판(창 맨 위): 모래시계 + 큰 안내문 + 단계 표시 + 진행바 2개(이 파일 / 전체)."""
    def __init__(self, tk, parent, title, n_files, stages=STAGES):
        self.tk, self.n, self.stages = tk, n_files, tuple(stages)
        wrap = tk.Frame(parent, bg=C_PEDGE)
        wrap.pack(fill="x", padx=10, pady=(8, 6))
        hf = tk.Frame(wrap, bg=C_FOOT, pady=4)
        hf.pack(fill="x", padx=2, pady=(2, 0))
        tk.Label(hf, text=f"⚖  {title}", font=(UI_FONT, 13, "bold"), bg=C_FOOT, fg=C_WHITE).pack(side="left", padx=12)
        self.file_lbl = tk.Label(hf, text="", font=(UI_FONT, 11, "bold"), bg=C_FOOT, fg=C_LTEAL)
        self.file_lbl.pack(side="right", padx=12)
        body = tk.Frame(wrap, bg=C_PANEL, padx=14, pady=6)
        body.pack(fill="x", padx=2, pady=(0, 2))
        self.icon = tk.Label(body, text="⏳", font=(EMOJI_FONT, 28), bg=C_PANEL, fg=C_ECHO, width=2)
        self.icon.pack(side="left", padx=(4, 14))
        mid = tk.Frame(body, bg=C_PANEL)
        mid.pack(side="left", fill="x", expand=True)
        top = tk.Frame(mid, bg=C_PANEL); top.pack(fill="x")
        self.head = tk.Label(top, text="준비 중…", font=(UI_FONT, 16, "bold"), bg=C_PANEL, fg=C_WHITE, anchor="w")
        self.head.pack(side="left")
        self.steps = []
        sf = tk.Frame(top, bg=C_PANEL); sf.pack(side="right")
        for k, nm in enumerate(self.stages, 1):
            lb = tk.Label(sf, text=f" {k} {nm} ", font=(UI_FONT, 10, "bold"), bg=C_CARD, fg=C_STEP,
                          padx=6, pady=2, highlightthickness=1, highlightbackground=C_CBORD)
            lb.pack(side="left", padx=2)
            if k < len(self.stages):
                tk.Label(sf, text="›", font=(UI_FONT, 11, "bold"), bg=C_PANEL, fg=C_FTXT).pack(side="left")
            self.steps.append(lb)
        self.detail = tk.Label(mid, text="", font=(UI_FONT, 11), bg=C_PANEL, fg=C_BODY, anchor="w", justify="left")
        self.detail.pack(fill="x", pady=(0, 4))
        self.bars = {}
        for key, cap in (("file", "이 파일"), ("all", "전체")):
            r = tk.Frame(mid, bg=C_PANEL); r.pack(fill="x", pady=1)
            tk.Label(r, text=cap, font=(UI_FONT, 10, "bold"), bg=C_PANEL, fg=C_LTEAL, width=6, anchor="w").pack(side="left")
            outer = tk.Frame(r, bg=C_BAR, height=14); outer.pack(side="left", fill="x", expand=True, padx=(0, 8))
            outer.pack_propagate(False)
            bar = tk.Frame(outer, bg=C_TEAL if key == "all" else C_LTEAL, height=14)
            bar.place(x=0, y=0, relwidth=0, height=14)
            txt = tk.Label(r, text="", font=(UI_FONT, 10, "bold"), bg=C_PANEL, fg=C_WHITE, width=24, anchor="w")
            txt.pack(side="left")
            self.bars[key] = (bar, txt)
        self.sub = tk.Label(mid, text="", font=(UI_FONT, 9), bg=C_PANEL, fg="#5E7C92", anchor="w")
        self.sub.pack(fill="x")
        self.mode = "busy"; self._flip = False
        self._tick()

    def _tick(self):
        try:
            if self.mode == "busy":
                self._flip = not self._flip
                self.icon.configure(text="⌛" if self._flip else "⏳", fg=C_ECHO)
            self.icon.after(650, self._tick)
        except Exception:
            pass

    def set(self, head=None, detail=None, stage=None, mode=None, file_frac=None, file_txt=None,
            all_frac=None, all_txt=None, sub=None, file_no=None):
        if head is not None: self.head.configure(text=head)
        if detail is not None: self.detail.configure(text=detail)
        if sub is not None: self.sub.configure(text=sub)
        if file_no is not None: self.file_lbl.configure(text=file_no)
        if mode is not None:
            self.mode = mode
            ic = {"user": ("✏️", C_ECHO), "done": ("✅", "#4ADE80"), "error": ("⚠", "#FF8080")}.get(mode)
            if ic: self.icon.configure(text=ic[0], fg=ic[1])
        if stage is not None:
            for k, lb in enumerate(self.steps):
                if k < stage:
                    lb.configure(bg=C_CARD, fg=C_LTEAL, highlightbackground=C_TEAL,
                                 text=f" ✓ {self.stages[k]} ")
                elif k == stage:
                    lb.configure(bg=C_TEAL, fg=C_WHITE, highlightbackground=C_LTEAL,
                                 text=f" {k + 1} {self.stages[k]} ")
                else:
                    lb.configure(bg=C_CARD, fg=C_STEP, highlightbackground=C_CBORD,
                                 text=f" {k + 1} {self.stages[k]} ")
        for key, fr, tx in (("file", file_frac, file_txt), ("all", all_frac, all_txt)):
            bar, lbl = self.bars[key]
            if fr is not None:
                bar.place_configure(relwidth=max(0.0, min(1.0, fr)))
            if tx is not None:
                lbl.configure(text=tx)


def _file_progress(state):
    """파일 하나의 진행률(판독 50% + 비식별 50%)."""
    if state.get("done"):
        return 1.0
    r = state.get("read", (0, 1)); m = state.get("mask", (0, 1))
    return 0.5 * r[0] / max(1, r[1]) + 0.5 * m[0] / max(1, m[1])


def keep_names_gui(files, engine_factory, dpi=300, clear_footer=True):
    """'이름은 그대로' 실행: 묻지 않고 폴더 전체를 차례로 처리하며 진행 안내판만 보여 준다."""
    import threading, queue
    tk = _tk_setup()
    root = tk.Tk()
    root.title("비식별 — 이름은 그대로")
    root.configure(bg=C_PAGE)
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    W, H = min(980, sw - 60), min(640, sh - 100)
    root.geometry(f"{W}x{H}+{(sw - W) // 2}+{max(10, (sh - H) // 3)}")
    N = len(files)
    pp = ProgressPanel(tk, root, "개인정보 비식별  ·  이름은 그대로", N, stages=("판독", "비식별", "저장"))
    lst = tk.Frame(root, bg=C_CBORD); lst.pack(fill="both", expand=True, padx=10, pady=(0, 8))
    inner = tk.Frame(lst, bg=C_CARD); inner.pack(fill="both", expand=True, padx=1, pady=1)
    tk.Label(inner, text="처리할 파일", font=(UI_FONT, 10, "bold"), bg=C_CARD, fg=C_LTEAL, anchor="w").pack(
        fill="x", padx=12, pady=(8, 4))
    rows = []
    for f in files:
        r = tk.Frame(inner, bg=C_CARD); r.pack(fill="x", padx=12, pady=1)
        ic = tk.Label(r, text="○", font=(UI_FONT, 11, "bold"), bg=C_CARD, fg=C_STEP, width=2)
        ic.pack(side="left")
        tk.Label(r, text=os.path.basename(f), font=(UI_FONT, 10), bg=C_CARD, fg=C_BODY, anchor="w").pack(side="left")
        stt = tk.Label(r, text="대기", font=(UI_FONT, 10), bg=C_CARD, fg=C_STEP, anchor="e")
        stt.pack(side="right")
        rows.append((ic, stt))
    bf = tk.Frame(root, bg=C_PAGE); bf.pack(fill="x", padx=10, pady=(0, 10))
    q = queue.Queue()
    ST = [{"read": (0, 1), "mask": (0, 1)} for _ in files]
    res = {"ok": 0, "bad": 0, "out": None, "done": False}

    def worker():
        try:
            q.put(("prep",))
            eng = engine_factory()
        except Exception as ex:
            q.put(("fatal", str(ex))); return
        for i, f in enumerate(files):
            try:
                q.put(("start", i))
                doc = analyze_cached(f, eng, dpi, progress=lambda m: None,
                                     step=lambda s, a, b, i=i: q.put(("step", i, s, a, b)))
                q.put(("step", i, "read", 1, 1))
                out, nred, logp = finish_file(doc, [], set(), clear_footer, progress=lambda m: None,
                                              keep_names=True,
                                              step=lambda s, a, b, i=i: q.put(("step", i, s, a, b)))
                q.put(("ok", i, out, nred))
            except Exception as ex:
                import traceback
                with open(os.path.join(P.WORK_ROOT, "pii_redact_log.txt"), "a", encoding="utf-8") as fo:
                    fo.write(f"\n[ERROR] {f}\n" + traceback.format_exc())
                q.put(("bad", i, str(ex)))
        q.put(("end",))

    def total():
        return sum(_file_progress(s) for s in ST) / max(1, N)

    def poll():
        try:
            while True:
                m = q.get_nowait()
                k = m[0]
                if k == "prep":
                    pp.set(head="글자 인식 엔진을 준비하고 있습니다…", detail="처음 한 번은 수십 초 걸릴 수 있습니다.",
                           stage=0, all_frac=0, all_txt=f"0 / {N} 파일")
                elif k == "fatal":
                    pp.set(head="엔진을 불러오지 못했습니다", detail=m[1], mode="error")
                elif k == "start":
                    i = m[1]
                    rows[i][0].configure(text="▶", fg=C_ECHO); rows[i][1].configure(text="판독 중", fg=C_ECHO)
                    pp.set(file_no=f"파일 {i + 1} / {N}", detail=os.path.basename(files[i]), stage=0,
                           head=f"{i + 1}번째 파일 글자 판독 중…", file_frac=0, file_txt="")
                elif k == "step":
                    _, i, s, a, b = m
                    ST[i][s] = (a, b)
                    if s == "read":
                        pp.set(head=f"글자 판독 중 — {a} / {b}쪽", stage=0)
                        rows[i][1].configure(text=f"판독 {a}/{b}쪽")
                    else:
                        pp.set(head=f"비식별 처리 중 — {a} / {b}쪽", stage=1 if a < b else 2)
                        rows[i][1].configure(text=f"비식별 {a}/{b}쪽")
                    pp.set(file_frac=_file_progress(ST[i]), file_txt=f"{int(_file_progress(ST[i]) * 100)}%",
                           all_frac=total(), all_txt=f"{sum(1 for x in ST if x.get('done'))} / {N} 파일 · {int(total() * 100)}%")
                elif k == "ok":
                    _, i, out, nred = m
                    ST[i]["done"] = True; res["ok"] += 1; res["out"] = out
                    rows[i][0].configure(text="✓", fg="#4ADE80"); rows[i][1].configure(text=f"완료 ({nred}곳)", fg="#4ADE80")
                elif k == "bad":
                    _, i, err = m
                    ST[i]["done"] = True; res["bad"] += 1
                    rows[i][0].configure(text="!", fg="#FF8080"); rows[i][1].configure(text="오류 — 건너뜀", fg="#FF8080")
                elif k == "end":
                    res["done"] = True
                    pp.set(head=f"모두 끝났습니다 — 완료 {res['ok']}개" + (f", 오류 {res['bad']}개" if res["bad"] else ""),
                           detail="결과: 같은 폴더의 *_비식별_이름유지.pdf  ·  로그: _로그 폴더(원문 포함 — 확인 후 삭제)",
                           mode="done", stage=3, file_frac=1, file_txt="100%", all_frac=1, all_txt=f"{N} / {N} 파일 · 100%")
                    if res["out"]:
                        shadow_btn(tk, bf, "📂  결과 폴더 열기", BTN_B,
                                   lambda: _open_folder(res["out"]), bg=C_PAGE).pack(side="left", fill="x", expand=True, padx=(0, 6))
                    shadow_btn(tk, bf, "닫기", BTN_G, root.destroy, bg=C_PAGE).pack(side="left", fill="x", expand=True)
        except queue.Empty:
            pass
        if not res["done"] or not q.empty():
            root.after(200, poll)

    pp.set(all_frac=0, all_txt=f"0 / {N} 파일", file_no=f"파일 0 / {N}")
    threading.Thread(target=worker, daemon=True).start()
    root.after(100, poll)
    if os.environ.get("PII_GUI_TEST"):
        return root, {"pp": pp, "res": res}
    root.mainloop()
    return res


def review_gui(files, engine_factory, dpi=300, clear_footer=True):
    """'이름도 감추는' 비식별 화면.
    맨 위 = 진행 안내판 / 왼쪽 = 원문 보기 / 오른쪽 = ① 이름이 아닌 곳에 체크
    ② 추가하고 싶은 이름 입력 · 선택영역 추가 → [추가 완료 / 실행]. 판독은 뒤에서 파일 순서대로 진행."""
    import threading, queue
    tk = _tk_setup()
    from tkinter import messagebox
    from PIL import Image, ImageTk
    root = tk.Tk()
    root.title("비식별 — 이름도 감추기")
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{min(1560, sw - 40)}x{min(1000, sh - 90)}+10+10")
    if sw <= 1600 or sh <= 1100:
        try: root.state("zoomed")
        except Exception: pass
    root.configure(bg=C_PAGE)
    FN = UI_FONT

    N = len(files)
    F = [{"pdf": f, "doc": None, "state": "대기", "regions": [], "read": (0, 1), "mask": (0, 1),
          "done": os.path.exists(out_path_for(f)), "err": None} for f in files]
    S = {"cur": None, "added": [], "ex_names": set(), "occ": [], "items": [], "rows": {}, "page": 0,
         "zoom": 1.0, "sel": None, "selocc": None, "cyc": {}, "cache": {}, "photo": None,
         "results": [], "busy": False, "drawmode": False, "drag": None, "reading": None}
    DISP_DPI = 100
    k300 = DISP_DPI / dpi
    q = queue.Queue()

    def worker():
        q.put(("prep", None))
        try:
            eng = engine_factory()
        except Exception as ex:
            q.put(("fatal", str(ex))); return
        for i, x in enumerate(F):
            if x["done"]:
                continue
            q.put(("start", i))
            try:
                d = analyze_cached(x["pdf"], eng, dpi, progress=lambda m: None,
                                   step=lambda s, a, b, i=i: q.put(("step", i, a, b)))
                q.put(("doc", i, d))
            except Exception as ex:
                q.put(("err", i, str(ex)))
        q.put(("allread", None))
    threading.Thread(target=worker, daemon=True).start()

    # ════ 맨 위: 진행 안내판 ════
    pp = ProgressPanel(tk, root, "개인정보 비식별  ·  이름도 감추기", N)
    main = tk.Frame(root, bg=C_PAGE); main.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    # ════ 오른쪽(먼저 자리 잡음) ════
    right = tk.Frame(main, bg=C_PAGE, width=640); right.pack(side="right", fill="y", padx=(8, 0))
    right.pack_propagate(False)

    def section(parent, title, edge, title_bg):
        box = tk.Frame(parent, bg=edge)
        tk.Label(box, text=title, font=(FN, 17, "bold"), fg=C_WHITE, bg=title_bg, anchor="w",
                 padx=14, pady=8).pack(fill="x", padx=4, pady=(4, 0))
        inner = tk.Frame(box, bg=C_CARD)
        inner.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        return inner

    # ── ① 이름이 아닌 곳에 체크하세요 ──
    s1 = section(right, "①  이름이 아닌 곳에 체크하세요", C_PEDGE, C_TEAL)
    tk.Label(s1, text="체크한 이름은 가리지 않습니다 · 글자를 누르면 왼쪽 원문에서 위치를 보여 줍니다(다시 누르면 다음 자리)",
             font=(FN, 9), fg=C_NOTES, bg=C_CARD, anchor="w", padx=10, pady=5).pack(fill="x")
    lw = tk.Frame(s1, bg=C_CARD); lw.pack(fill="both", expand=True, padx=4, pady=(0, 4))
    lc = tk.Canvas(lw, bg=C_CARD, highlightthickness=0)
    lsb = tk.Scrollbar(lw, orient="vertical", command=lc.yview)
    lc.configure(yscrollcommand=lsb.set)
    lsb.pack(side="right", fill="y"); lc.pack(side="left", fill="both", expand=True)
    inner = tk.Frame(lc, bg=C_CARD)
    lc_win = lc.create_window(0, 0, window=inner, anchor="nw")
    inner.bind("<Configure>", lambda e: lc.configure(scrollregion=lc.bbox("all")))
    lc.bind("<Configure>", lambda e: lc.itemconfigure(lc_win, width=e.width))

    # ── ② 추가하고 싶은 이름을 입력하세요 ──
    s2 = section(right, "②  추가하고 싶은 이름을 입력하세요", BTN_G[1], "#1F5F24")
    r1 = tk.Frame(s2, bg=C_CARD); r1.pack(fill="x", padx=10, pady=(8, 2))
    ent = tk.Entry(r1, font=(FN, 15), relief="flat", bd=0, bg=C_FIELD, fg=C_WHITE, insertbackground=C_WHITE,
                   highlightthickness=2, highlightbackground=C_CBORD, highlightcolor=BTN_G[0])
    ent.pack(side="left", fill="x", expand=True, ipady=6)
    add_btn = shadow_btn(tk, r1, "추가", BTN_G, lambda: add_names(), height=44, font_size=13, width=96)
    add_btn.pack(side="left", padx=(8, 0))
    r2 = tk.Frame(s2, bg=C_CARD); r2.pack(fill="x", padx=10, pady=(6, 0))
    tk.Label(r2, text="여러 명은 쉼표(,)로 이어서 입력 · Enter 로도 추가", font=(FN, 9), fg=C_NOTES, bg=C_CARD,
             anchor="w").pack(side="left")
    reg_btn = shadow_btn(tk, r2, "▭  선택영역 추가", BTN_Y, lambda: toggle_draw(), height=38, font_size=11, width=190)
    reg_btn.pack(side="right")
    tk.Label(s2, text="추가된 이름 · 선택영역  (누르면 빠집니다)", font=(FN, 10, "bold"), fg=BTN_G[0], bg=C_CARD,
             anchor="w", padx=10).pack(fill="x", pady=(8, 0))
    chips = tk.Text(s2, height=2, wrap="word", font=(FN, 12), bg=C_FIELD, fg=C_BODY, relief="flat", bd=0,
                    cursor="arrow", padx=6, pady=6, highlightthickness=1, highlightbackground=C_CBORD)
    chips.pack(fill="x", padx=10, pady=(2, 10))
    chips.configure(state="disabled")
    run_wrap = tk.Frame(s2, bg=C_CARD); run_wrap.pack(fill="x", padx=10, pady=(0, 10))
    run_btn = tk.Button(run_wrap, text="추가 완료 / 실행  ▶", font=(FN, 17, "bold"), bg=C_TEAL, fg=C_WHITE,
                        activebackground="#145F5A", activeforeground=C_WHITE, disabledforeground="#5A7488",
                        relief="flat", cursor="hand2", command=lambda: run())
    run_btn.pack(fill="x", ipady=9)
    s2.master.pack(side="bottom", fill="x", pady=(10, 0))
    s1.master.pack(side="top", fill="both", expand=True)

    # ════ 왼쪽: 원문 보기 ════
    left = tk.Frame(main, bg=C_PAGE); left.pack(side="left", fill="both", expand=True)
    bar = tk.Frame(left, bg=C_PAGE); bar.pack(fill="x", pady=(0, 6))
    def tbtn(t, cmd, w=None):
        b = shadow_btn(tk, bar, t, (C_BODY, C_CBORD, C_FOOT), cmd, height=34, font_size=10, bg=C_PAGE, width=w or 100)
        b.pack(side="left", padx=3); return b
    tbtn("◀ 이전 쪽", lambda: goto(S["page"] - 1))
    pg_var = tk.StringVar()
    tk.Label(bar, textvariable=pg_var, font=(FN, 12, "bold"), bg=C_PAGE, fg=C_WHITE, width=9).pack(side="left")
    tbtn("다음 쪽 ▶", lambda: goto(S["page"] + 1))
    tbtn("－ 축소", lambda: zoom(1 / 1.2), 80); tbtn("＋ 확대", lambda: zoom(1.2), 80)
    file_var = tk.StringVar()
    tk.Label(bar, textvariable=file_var, font=(FN, 11, "bold"), fg=C_LTEAL, bg=C_PAGE).pack(side="right", padx=6)
    cf = tk.Frame(left, bg=C_CBORD); cf.pack(fill="both", expand=True)
    cv = tk.Canvas(cf, bg="#0A1A2C", highlightthickness=0)
    vs = tk.Scrollbar(cf, orient="vertical", command=cv.yview)
    hs = tk.Scrollbar(cf, orient="horizontal", command=cv.xview)
    cv.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
    vs.pack(side="right", fill="y"); hs.pack(side="bottom", fill="x"); cv.pack(fill="both", expand=True, padx=1, pady=1)

    # ════ 동작 ════
    def doc():
        return F[S["cur"]]["doc"] if S["cur"] is not None else None

    def page_img(i):
        key = (S["cur"], i)
        im = S["cache"].get(key)
        if im is None:
            im = P._rasterize(doc()["pdf"], dpi=DISP_DPI, page=i)[0].convert("RGB")
            if len(S["cache"]) > 8:
                S["cache"].pop(next(iter(S["cache"])))
            S["cache"][key] = im
        return im

    def occ_box(o):
        return char_box(doc()["pages"][o["page"]][o["li"]], o["s"], o["e"])

    def is_ex(it):
        return it["name"] in S["ex_names"]

    def draw():
        cv.delete("all")
        d = doc()
        if not d:
            pg_var.set(""); return
        i = S["page"]
        im = page_img(i)
        z = S["zoom"]
        if abs(z - 1.0) > 1e-3:
            im = im.resize((max(1, int(im.width * z)), max(1, int(im.height * z))), Image.LANCZOS)
        S["photo"] = ImageTk.PhotoImage(im)
        cv.create_image(0, 0, image=S["photo"], anchor="nw")
        cv.configure(scrollregion=(0, 0, im.width, im.height))
        sc = k300 * z
        sel = S["selocc"]["key"] if S["selocc"] else None
        for it in S["items"]:
            for o in it["occ"]:
                if o["page"] != i:
                    continue
                b = occ_box(o)
                if not b:
                    continue
                x0, y0, x1, y1 = [v * sc for v in b]
                if o["key"] == sel:
                    cv.create_rectangle(x0 - 4, y0 - 4, x1 + 4, y1 + 4, outline="#0050ff", width=3)
                elif it["name"] in S["ex_names"]:
                    cv.create_rectangle(x0 - 2, y0 - 2, x1 + 2, y1 + 2, outline="#888888", width=2, dash=(4, 3))
                else:
                    cv.create_rectangle(x0 - 2, y0 - 2, x1 + 2, y1 + 2, outline="#e00000", width=2)
        for k, (pg, a, b, c, e) in enumerate(F[S["cur"]]["regions"]):
            if pg == i:
                cv.create_rectangle(a * sc, b * sc, c * sc, e * sc, fill="black", outline=BTN_Y[0], width=2,
                                    tags=("reg", f"reg{k}"))
        pg_var.set(f"{i + 1} / {d['n']} 쪽")

    def goto(i):
        if not doc():
            return
        S["page"] = max(0, min(doc()["n"] - 1, i)); draw(); cv.yview_moveto(0)

    def zoom(f):
        if doc():
            S["zoom"] = max(0.3, min(4.0, S["zoom"] * f)); draw()

    def fit():
        if doc():
            S["zoom"] = max(200, cv.winfo_width() - 4) / page_img(S["page"]).width; draw()

    # ── ① 목록(큰 체크칸 + 이름 강조) ──
    def paint_row(it):
        r = S["rows"].get(it["no"])
        if not r:
            return
        ex = is_ex(it)
        selected = S["sel"] is it
        bg = "#133655" if selected else ("#101B27" if ex else C_FIELD)
        r["frame"].configure(bg=bg, highlightbackground=C_ECHO if selected else C_CBORD)
        r["chk"].configure(text="☑" if ex else "☐", fg=C_ECHO if ex else C_LTEAL, bg=bg)
        r["no"].configure(bg=bg, fg="#5A7488" if ex else C_STEP)
        r["txt"].configure(state="normal", bg=bg)
        r["txt"].tag_configure("n", foreground="#5A7488" if ex else C_ECHO, overstrike=ex)
        r["txt"].tag_configure("t", foreground="#5A7488" if ex else C_BODY)
        r["txt"].configure(state="disabled")

    def build_rows():
        for w in inner.winfo_children():
            w.destroy()
        S["rows"] = {}
        if not doc():
            tk.Label(inner, text="판독이 끝나면 이름 후보가 여기에 나옵니다…", font=(FN, 12), fg=C_STEP,
                     bg=C_CARD, pady=20).pack(fill="x")
            return
        shown = [it for it in S["items"] if it["src"] == "자동"]   # ②에서 직접 추가한 이름은 ①에 넣지 않음
        if not shown:
            tk.Label(inner, text="이 파일에서 찾은 이름 후보가 없습니다.\n필요하면 아래 ②에 이름을 입력하세요.",
                     font=(FN, 12), fg=C_STEP, bg=C_CARD, pady=20).pack(fill="x")
            return
        for n, it in enumerate(shown, 1):
            fr = tk.Frame(inner, bg=C_FIELD, highlightthickness=1, highlightbackground=C_CBORD)
            fr.pack(fill="x", padx=2, pady=2)
            chk = tk.Label(fr, text="☐", font=(FN, 24), bg=C_FIELD, fg=C_LTEAL, cursor="hand2", width=2)
            chk.pack(side="left", padx=(4, 2))
            no = tk.Label(fr, text=f"{n}.", font=(FN, 11, "bold"), bg=C_FIELD, fg=C_STEP, width=3)
            no.pack(side="left")
            txt = tk.Text(fr, height=1, wrap="none", font=(FN, 13), relief="flat", bd=0, bg=C_FIELD,
                          cursor="hand2", padx=4, pady=8)
            txt.pack(side="left", fill="x", expand=True)
            txt.tag_configure("n", font=(FN, 13, "bold"))
            for t, isn in it["parts"]:
                txt.insert("end", t, ("n" if isn else "t",))
            more = f"   ({it['page'] + 1}쪽" + (f" 외 {it['count'] - 1}곳)" if it["count"] > 1 else ")")
            txt.insert("end", more, ("c",))
            txt.tag_configure("c", foreground=C_LTEAL, font=(FN, 10))
            txt.configure(state="disabled")
            S["rows"][it["no"]] = {"frame": fr, "chk": chk, "no": no, "txt": txt}
            chk.bind("<Button-1>", lambda e, it=it: (toggle(it), show(it, step=False)))
            for w in (fr, no, txt):
                w.bind("<Button-1>", lambda e, it=it: show(it))
            for w in (fr, chk, no, txt):
                w.bind("<MouseWheel>", list_wheel); w.bind("<Button-4>", list_wheel); w.bind("<Button-5>", list_wheel)
            paint_row(it)
        lc.yview_moveto(0)

    def refresh_items():
        d = doc()
        if d:
            S["occ"] = find_name_occurrences(d, S["added"])
            S["items"] = name_items(d, S["occ"])
        else:
            S["occ"], S["items"] = [], []
        S["sel"] = None; S["selocc"] = None; S["cyc"] = {}
        build_rows(); draw()

    def toggle(it):
        (S["ex_names"].discard if is_ex(it) else S["ex_names"].add)(it["name"])
        paint_row(it); draw()

    def show(it, step=True):
        prev, S["sel"] = S["sel"], it
        if prev is not None and prev is not it:
            paint_row(prev)
        paint_row(it)
        k = S["cyc"].get(it["no"], -1)
        k = (k + 1) % len(it["occ"]) if (step or k < 0) else k
        S["cyc"][it["no"]] = k
        o = it["occ"][k]; S["selocc"] = o
        S["page"] = o["page"]; draw()
        if len(it["occ"]) > 1:
            pp.set(sub=f"'{it['name']}'  {k + 1} / {len(it['occ'])}번째 자리 ({o['page'] + 1}쪽) — 다시 누르면 다음 자리")
        b = occ_box(o)
        if b:
            sc = k300 * S["zoom"]
            im = page_img(S["page"])
            H, W = im.height * S["zoom"], im.width * S["zoom"]
            cv.yview_moveto(max(0.0, (b[1] * sc - cv.winfo_height() / 3) / max(1.0, H)))
            cv.xview_moveto(max(0.0, (b[0] * sc - cv.winfo_width() / 3) / max(1.0, W))
                            if W > cv.winfo_width() else 0.0)

    # ── 원문 위 마우스: 빨간 상자 누르면 목록으로 / 선택영역 그리기 ──
    def to_doc(e):
        sc = k300 * S["zoom"]
        return cv.canvasx(e.x) / sc, cv.canvasy(e.y) / sc

    def cv_press(e):
        if not doc():
            return
        if S["drawmode"]:
            x, y = cv.canvasx(e.x), cv.canvasy(e.y)
            S["drag"] = (x, y, cv.create_rectangle(x, y, x, y, outline=BTN_Y[0], width=3, dash=(6, 3)))
            return
        x, y = cv.canvasx(e.x), cv.canvasy(e.y)
        sc = k300 * S["zoom"]
        for it in S["items"]:
            for o in it["occ"]:
                b = occ_box(o) if o["page"] == S["page"] else None
                if b and b[0] * sc - 4 <= x <= b[2] * sc + 4 and b[1] * sc - 4 <= y <= b[3] * sc + 4:
                    r = S["rows"].get(it["no"])
                    if r:
                        inner.update_idletasks()
                        lc.yview_moveto(max(0.0, r["frame"].winfo_y() / max(1, inner.winfo_height()) - 0.1))
                    prev, S["sel"], S["selocc"] = S["sel"], it, o
                    if prev is not None:
                        paint_row(prev)
                    paint_row(it); draw(); return

    def cv_motion(e):
        if S["drawmode"] and S["drag"]:
            x0, y0, rid = S["drag"]
            cv.coords(rid, x0, y0, cv.canvasx(e.x), cv.canvasy(e.y))

    def cv_release(e):
        if not (S["drawmode"] and S["drag"]):
            return
        x0, y0, rid = S["drag"]; S["drag"] = None
        x1, y1 = cv.canvasx(e.x), cv.canvasy(e.y)
        cv.delete(rid)
        if abs(x1 - x0) < 6 or abs(y1 - y0) < 6:
            return
        sc = k300 * S["zoom"]
        F[S["cur"]]["regions"].append((S["page"], min(x0, x1) / sc, min(y0, y1) / sc,
                                       max(x0, x1) / sc, max(y0, y1) / sc))
        draw(); paint_chips()
        pp.set(sub=f"선택영역 {len(F[S['cur']]['regions'])}개 — 실행하면 검정 블럭으로 가립니다. 더 그리거나 [선택영역 추가]를 다시 눌러 끝내세요.")

    def cv_right(e):                               # 오른쪽 클릭: 그 선택영역 빼기
        if not doc():
            return
        x, y = to_doc(e)
        regs = F[S["cur"]]["regions"]
        for k in range(len(regs) - 1, -1, -1):
            pg, a, b, c, d2 = regs[k]
            if pg == S["page"] and a <= x <= c and b <= y <= d2:
                regs.pop(k); draw(); paint_chips(); return

    def toggle_draw():
        if not doc():
            return
        S["drawmode"] = not S["drawmode"]
        if S["drawmode"]:
            reg_btn.set_state(True, "■  그리는 중 (끝내기)", active=True)
            cv.configure(cursor="crosshair")
            pp.set(mode="user", head="왼쪽 원문에서 가릴 곳을 마우스로 끌어 사각형을 그리세요",
                   sub="여러 개 그릴 수 있습니다 · 잘못 그린 것은 그 위에서 오른쪽 클릭하거나 아래 목록에서 누르면 빠집니다")
        else:
            reg_btn.set_state(True, "▭  선택영역 추가", active=False)
            cv.configure(cursor="")
            user_prompt()

    # ── ② 추가 이름·선택영역(차곡차곡 쌓이는 칸) ──
    def paint_chips():
        chips.configure(state="normal"); chips.delete("1.0", "end")
        for nm in S["added"]:
            k = sum(1 for o in S["occ"] if o["name"] == nm)
            lab = tk.Label(chips, text=f" {nm}  ✕ ", font=(FN, 12, "bold"), bg=BTN_G[2] if k else "#1A2633",
                           fg=BTN_G[0] if k else "#5A7488", relief="flat", bd=0, cursor="hand2", pady=3,
                           highlightthickness=1, highlightbackground=BTN_G[1] if k else C_CBORD)
            lab.bind("<Button-1>", lambda e, nm=nm: del_name(nm))
            chips.window_create("end", window=lab, padx=3, pady=3)
        if S["cur"] is not None:
            for k, (pg, *_r) in enumerate(F[S["cur"]]["regions"]):
                lab = tk.Label(chips, text=f" ■ {pg + 1}쪽 영역  ✕ ", font=(FN, 11, "bold"), bg=BTN_Y[2], fg=BTN_Y[0],
                               relief="flat", bd=0, cursor="hand2", pady=3,
                               highlightthickness=1, highlightbackground=BTN_Y[1])
                lab.bind("<Button-1>", lambda e, k=k: del_region(k))
                chips.window_create("end", window=lab, padx=3, pady=3)
        if not S["added"] and not (S["cur"] is not None and F[S["cur"]]["regions"]):
            chips.insert("end", "(아직 추가한 이름·영역이 없습니다)")
        chips.configure(state="disabled")

    def add_names():
        new = [n for n in (_norm_name(x) for x in _parse_names(ent.get())) if n not in S["added"]]
        ent.delete(0, "end")
        if not new:
            return
        S["added"] += new
        refresh_items(); paint_chips()
        miss = [n for n in new if not any(o["name"] == n for o in S["occ"])]
        pp.set(sub="추가: " + ", ".join(new) + (("   ※ 이 파일에는 없음(회색): " + ", ".join(miss)) if miss else ""))

    def del_name(nm):
        if nm in S["added"]:
            S["added"].remove(nm); refresh_items(); paint_chips()
            pp.set(sub=f"뺐습니다: {nm}")

    def del_region(k):
        regs = F[S["cur"]]["regions"]
        if 0 <= k < len(regs):
            pg = regs.pop(k)[0]; draw(); paint_chips()
            pp.set(sub=f"{pg + 1}쪽 선택영역을 뺐습니다")

    # ── 진행 안내판 갱신 ──
    def total():
        return sum(_file_progress(x) for x in F) / max(1, N)

    def ndone():
        return sum(1 for x in F if x["done"])

    def update_bars(i=None):
        i = S["cur"] if i is None else i
        kw = {"all_frac": total(), "all_txt": f"{ndone()} / {N} 파일 · {int(total() * 100)}%"}
        if i is not None:
            fp = _file_progress(F[i])
            kw.update(file_frac=fp, file_txt=f"{int(fp * 100)}%")
        pp.set(**kw)

    def bg_reading_text():
        r = S["reading"]
        if r is None:
            return ""
        i, a, b = r
        return f"뒤에서 {i + 1}번째 파일 판독 중 — {a} / {b}쪽"

    def user_prompt():
        if S["cur"] is None:
            return
        x = F[S["cur"]]
        pp.set(mode="user", stage=1, file_no=f"파일 {S['cur'] + 1} / {N}",
               head="이름을 확인하고 [추가 완료 / 실행]을 누르세요",
               detail=os.path.basename(x["pdf"]) + f"  ·  {doc()['n']}쪽  ·  이름 후보 "
                      f"{sum(1 for it in S['items'] if it['src'] == '자동')}개",
               sub=bg_reading_text() or ("앞 파일에서 체크·추가한 내용이 그대로 적용되어 있습니다"
                                         if (S["ex_names"] or S["added"]) else ""))
        update_bars()

    # ── 파일 열기·실행 ──
    def open_file(i):
        x = F[i]
        if x["doc"] is None:
            return False
        S["cur"], S["page"], S["sel"], S["selocc"] = i, 0, None, None
        if S["drawmode"]:
            toggle_draw()
        file_var.set(os.path.basename(x["pdf"]))
        refresh_items(); paint_chips()
        root.after(60, fit)
        run_btn.configure(state="normal", bg=C_TEAL)
        user_prompt()
        return True

    def next_pending():
        for i in range(N):
            if not F[i]["done"] and not F[i]["err"]:
                return i
        return None

    def all_done():
        ok = S["results"]
        bad = [x for x in F if x["err"]]
        pp.set(mode="done", stage=4, head=f"모두 끝났습니다 — 완료 {len(ok)}개" + (f", 오류 {len(bad)}개" if bad else ""),
               detail=f"결과: 같은 폴더의 *{OUT_SUFFIX}.pdf  ·  로그: _로그 폴더(원문 포함 — 확인 후 삭제)",
               file_frac=1, file_txt="100%", all_frac=1, all_txt=f"{N} / {N} 파일 · 100%", sub="")
        msg = f"비식별 완료 {len(ok)}개 파일.\n결과: 같은 폴더의 *{OUT_SUFFIX}.pdf\n로그: _로그 폴더(원문 포함 — 검수 후 삭제 권장)"
        if bad:
            msg += f"\n\n판독 오류로 건너뛴 파일 {len(bad)}개: " + ", ".join(os.path.basename(x['pdf']) for x in bad)
        if messagebox.askyesno("모두 완료", msg + "\n\n결과 폴더를 열까요?"):
            if ok:
                _open_folder(ok[-1][1])
        root.destroy()

    def load_next():
        S["cur"] = None; S["sel"] = None; S["selocc"] = None
        S["occ"], S["items"] = [], []
        build_rows(); paint_chips(); draw()
        run_btn.configure(state="disabled", bg=C_CARD)
        check_waiting()

    def check_waiting():
        if S["cur"] is not None or S.get("closed"):
            return
        nxt = next_pending()
        if nxt is None:
            S["closed"] = True; root.after(10, all_done); return
        if F[nxt]["doc"] is not None:
            open_file(nxt)
        else:
            file_var.set(os.path.basename(F[nxt]["pdf"]))
            a, b = F[nxt]["read"]
            pp.set(mode="busy", stage=0, file_no=f"파일 {nxt + 1} / {N}",
                   head=f"{nxt + 1}번째 파일 글자 판독 중" + (f" — {a} / {b}쪽" if a else "…"),
                   detail=os.path.basename(F[nxt]["pdf"]) + "  ·  끝나면 자동으로 열립니다", sub="")
            update_bars(nxt)

    def run():
        if S["cur"] is None or S["busy"]:
            return
        if S["drawmode"]:
            toggle_draw()
        if ent.get().strip():
            add_names()
        x = F[S["cur"]]; i = S["cur"]
        S["busy"] = True; run_btn.configure(state="disabled", bg=C_CARD)
        pp.set(mode="busy", stage=2, head="비식별 처리 중…", detail=os.path.basename(x["pdf"]), sub="")
        def stepf(s, a, b):
            x["mask"] = (a, b)
            pp.set(head=f"비식별 처리 중 — {a} / {b}쪽", stage=2 if a < b else 3)
            update_bars(i); root.update()
        try:
            excl = {o["key"] for o in S["occ"] if o["name"] in S["ex_names"]}
            out, nred, logp = finish_file(x["doc"], S["occ"], excl, clear_footer, progress=lambda m: None,
                                          step=stepf, regions=list(x["regions"]))
            x["done"] = True; x["state"] = "완료"
            S["results"].append((x["pdf"], out, nred, logp))
        except Exception as ex:
            S["busy"] = False; run_btn.configure(state="normal", bg=C_TEAL)
            pp.set(mode="error", head="비식별 중 오류가 났습니다", detail=str(ex))
            messagebox.showerror("오류", f"비식별 중 오류: {ex}"); return
        S["busy"] = False
        update_bars(i)
        load_next()

    def poll():
        try:
            while True:
                m = q.get_nowait()
                k = m[0]
                if k == "prep":
                    pp.set(mode="busy", stage=0, head="글자 인식 엔진을 준비하고 있습니다…",
                           detail="처음 한 번은 수십 초 걸릴 수 있습니다.")
                elif k == "fatal":
                    pp.set(mode="error", head="엔진을 불러오지 못했습니다", detail=m[1])
                elif k == "start":
                    S["reading"] = (m[1], 0, 1)
                elif k == "step":
                    _, i, a, b = m
                    F[i]["read"] = (a, b); S["reading"] = (i, a, b)
                    if S["cur"] is None:
                        check_waiting()
                    elif not S["busy"] and not S["drawmode"]:
                        pp.set(sub=bg_reading_text())
                    update_bars(S["cur"] if S["cur"] is not None else i)
                elif k == "doc":
                    _, i, d = m
                    F[i]["doc"] = d; F[i]["read"] = (1, 1); S["reading"] = None
                    if S["cur"] is not None and not S["busy"] and not S["drawmode"]:
                        pp.set(sub="")
                elif k == "err":
                    _, i, err = m
                    F[i]["err"] = err; F[i]["done"] = True; S["reading"] = None
                elif k == "allread":
                    S["reading"] = None
        except queue.Empty:
            pass
        check_waiting()
        if not S.get("closed"):
            root.after(250, poll)

    def view_wheel(e):
        if not doc():
            return
        d = -1 if (getattr(e, "delta", 0) > 0 or getattr(e, "num", 0) == 4) else 1
        if e.state & 0x0004:
            return zoom(1.1 if d < 0 else 1 / 1.1)
        top, bot = cv.yview()
        if d > 0 and bot >= 0.999 and S["page"] < doc()["n"] - 1:
            goto(S["page"] + 1)
        elif d < 0 and top <= 0.001 and S["page"] > 0:
            goto(S["page"] - 1); cv.yview_moveto(1.0)
        else:
            cv.yview_scroll(d * 3, "units")

    def list_wheel(e):
        d = -1 if (getattr(e, "delta", 0) > 0 or getattr(e, "num", 0) == 4) else 1
        lc.yview_scroll(d * 2, "units")
        return "break"

    ent.bind("<Return>", lambda e: add_names())
    cv.bind("<ButtonPress-1>", cv_press); cv.bind("<B1-Motion>", cv_motion); cv.bind("<ButtonRelease-1>", cv_release)
    cv.bind("<Button-3>", cv_right)
    cv.bind("<MouseWheel>", view_wheel); cv.bind("<Button-4>", view_wheel); cv.bind("<Button-5>", view_wheel)
    for w in (lc, inner):
        w.bind("<MouseWheel>", list_wheel); w.bind("<Button-4>", list_wheel); w.bind("<Button-5>", list_wheel)
    root.bind("<Prior>", lambda e: goto(S["page"] - 1))
    root.bind("<Next>", lambda e: goto(S["page"] + 1))
    build_rows(); paint_chips()
    run_btn.configure(state="disabled", bg=C_CARD)
    if next_pending() is None:
        messagebox.showinfo("안내", "이 폴더의 파일은 모두 이미 비식별되어 있습니다.\n"
                                  "다시 하려면 해당 _비식별.pdf 파일을 지우고 실행하세요.")
        root.destroy(); return []
    pp.set(all_frac=0, all_txt=f"0 / {N} 파일", file_no=f"파일 1 / {N}", stage=0)
    poll()
    S.update(root=root, ent=ent, add=add_names, toggle=toggle, show=show, run=run, F=F, fit=fit,
             toggle_draw=toggle_draw, cv=cv, pp=pp, draw=draw)
    if os.environ.get("PII_GUI_TEST"):          # (개발용) 화면 자동 시험
        return root, S
    root.mainloop()
    return S["results"]
