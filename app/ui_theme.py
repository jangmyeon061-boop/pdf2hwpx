# -*- coding: utf-8 -*-
"""화면 공통 색·글꼴·버튼(어두운 보드판 스타일). tkinter 만 사용 — 런처 exe 에도 그대로 들어간다."""
import os

C_PAGE = "#081525"; C_CARD = "#0D2035"; C_CBORD = "#1E3A52"; C_TEAL = "#1B7F79"
C_PANEL = "#0C2136"; C_PEDGE = "#2E7D8E"; C_LTEAL = "#5EC8C0"; C_WHITE = "#FFFFFF"
C_BODY = "#A0BDD0"; C_NOTES = "#8AAEC4"; C_STEP = "#7099B0"; C_FOOT = "#040E1A"; C_FTXT = "#2A4A60"
C_FIELD = "#05101C"; C_ECHO = "#FFD166"; C_BAR = "#16304A"
BTN_R = ("#FF8080", "#D32F2F", "#6B0F0F"); BTN_B = ("#7AB3F0", "#1565C0", "#082A5E")
BTN_G = ("#6FCF7A", "#2E7D32", "#0F3A13"); BTN_Y = ("#FFD166", "#C9A227", "#4A3A08")
UI_FONT = "맑은 고딕" if os.name == "nt" else "Noto Sans CJK KR"
EMOJI_FONT = "Segoe UI Emoji" if os.name == "nt" else "Noto Color Emoji"
STAGES = ("판독", "이름 확인", "비식별", "저장")



def shadow_btn(tk, parent, text, colors, command, height=42, font_size=11, bg=C_CARD, width=None):
    """어두운 판 위 색 테두리 + 그림자 버튼, 마우스를 올리면 살짝 떠오름."""
    fg_c, bd_c, shd_c = colors
    SHD = 4
    outer = tk.Frame(parent, bg=bg, height=height + SHD)
    if width:
        outer.configure(width=width)
    outer.pack_propagate(False)
    tk.Frame(outer, bg=shd_c, height=height).place(x=SHD, y=SHD, relwidth=1)
    surf = tk.Frame(outer, bg=C_CARD, highlightbackground=bd_c, highlightthickness=2, cursor="hand2", height=height)
    surf.place(x=0, y=0, relwidth=1, height=height)
    surf.pack_propagate(False)
    lbl = tk.Label(surf, text=text, font=(UI_FONT, font_size, "bold"), bg=C_CARD, fg=fg_c, cursor="hand2")
    lbl.pack(expand=True)
    st = {"on": True, "fg": fg_c, "bd": bd_c}
    def enter(_):
        if st["on"]: surf.place(x=-3, y=-4, relwidth=1, height=height)
    def leave(_):
        surf.place(x=0, y=0, relwidth=1, height=height)
    def click(_):
        if st["on"]:
            leave(None); command()
    for w in (surf, lbl):
        w.bind("<Enter>", enter); w.bind("<Leave>", leave); w.bind("<ButtonRelease-1>", click)
    def set_state(on=True, text=None, active=False):
        st["on"] = on
        lbl.configure(text=text if text is not None else lbl.cget("text"),
                      fg=(C_FIELD if active else st["fg"]) if on else "#5A7488",
                      bg=st["fg"] if active else C_CARD)
        surf.configure(bg=st["fg"] if active else C_CARD,
                       highlightbackground=st["bd"] if on else C_CBORD)
    outer.set_state = set_state
    outer.label = lbl
    return outer
