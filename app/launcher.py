# -*- coding: utf-8 -*-
"""
PDF2HWPX 실행판(런처) — 배포판의 PDF2HWPX.exe 가 이 파일입니다.

  ┌ PDF2HWPX/
  │   PDF2HWPX.exe        ← 이 런처
  │   python/             ← 파이썬 실행환경(패키지 포함, 설치 불필요)
  │   app/                ← pdf2hwpx.py · pii_redact.py · pii_gui.py · vendor/poppler · paddlex_home(모델)
  │   변환_폴더/          ← 한글로 바꿀 PDF를 넣는 곳
  │   비식별_대상/        ← 개인정보를 가릴 PDF를 넣는 곳
  └

버튼을 누르면 해당 기능을 새 명령창에서 실행합니다(진행 상황이 보이고, 끝나면 창이 기다립니다).
명령줄로도 쓸 수 있습니다:  PDF2HWPX.exe convert [--fast] [PDF|폴더...]  /  PDF2HWPX.exe redact [옵션...]
"""
import os, sys, subprocess

VERSION = "1.0.0"

def _root():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 저장소: app/ 의 위

ROOT = _root()
APP = os.path.join(ROOT, "app")
PY = os.path.join(ROOT, "python", "python.exe")
CONVERT_DIR = os.path.join(ROOT, "변환_폴더")
REDACT_DIR = os.path.join(ROOT, "비식별_대상")

ACTIONS = {
    "accurate": ("pdf2hwpx.py", [], "정확하게 변환"),
    "fast":     ("pdf2hwpx.py", ["--fast"], "빠르게 변환"),
    "keep":     ("pii_redact.py", ["--keep-names"], "비식별 · 이름은 그대로"),
    "hide":     ("pii_redact.py", [], "비식별 · 이름도 감춤"),
}


def _python():
    if os.path.isfile(PY):
        return PY
    if not getattr(sys, "frozen", False):
        return sys.executable                 # 개발 중: 지금 파이썬
    return "python"


def _env():
    e = dict(os.environ)
    e["PDF2HWPX_WORKDIR"] = ROOT
    e["PYTHONIOENCODING"] = "utf-8"
    e["PYTHONUTF8"] = "1"
    return e


def run_action(key, extra=(), wait=False):
    script, args, title = ACTIONS[key]
    for d in (CONVERT_DIR, REDACT_DIR):
        os.makedirs(d, exist_ok=True)
    target = CONVERT_DIR if script == "pdf2hwpx.py" else REDACT_DIR
    cmd = [_python(), os.path.join(APP, script), *args, *(list(extra) or [target])]
    if wait or os.name != "nt":
        return subprocess.call(cmd, env=_env(), cwd=ROOT)
    # 윈도우: 새 명령창에서 실행하고, 끝나면 창이 닫히지 않게 pause
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    subprocess.Popen(["cmd", "/c", f"title PDF2HWPX - {title}", "&", *cmd, "&", "pause"],
                     env=_env(), cwd=ROOT, creationflags=flags)


def open_folder(path):
    os.makedirs(path, exist_ok=True)
    try:
        os.startfile(path)
    except Exception:
        subprocess.Popen(["xdg-open", path])


def gui():
    import tkinter as tk
    try:
        import ctypes; ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    from ui_theme import (shadow_btn, UI_FONT, C_PAGE, C_CARD, C_CBORD, C_TEAL, C_PEDGE, C_LTEAL, C_WHITE,
                         C_BODY, C_NOTES, C_FOOT, C_FTXT, BTN_B, BTN_G, BTN_Y, BTN_R)
    root = tk.Tk()
    root.title(f"PDF2HWPX {VERSION}")
    root.configure(bg=C_PAGE)
    W, H = 760, 500
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{W}x{H}+{(sw - W) // 2}+{max(10, (sh - H) // 3)}")
    root.resizable(False, False)

    head = tk.Frame(root, bg=C_FOOT); head.pack(fill="x")
    tk.Label(head, text="⚖  PDF2HWPX", font=(UI_FONT, 20, "bold"), bg=C_FOOT, fg=C_WHITE).pack(side="left", padx=18, pady=12)
    tk.Label(head, text=f"v{VERSION}  ·  오프라인", font=(UI_FONT, 10, "bold"), bg=C_FOOT, fg=C_LTEAL).pack(side="right", padx=18)
    tk.Label(root, text="스캔 PDF → 편집 가능한 한글(HWPX)  ·  개인정보 비식별 PDF",
             font=(UI_FONT, 12), bg=C_PAGE, fg=C_BODY).pack(anchor="w", padx=22, pady=(14, 6))

    def card(title, sub, edge):
        box = tk.Frame(root, bg=edge); box.pack(fill="x", padx=18, pady=8)
        inner = tk.Frame(box, bg=C_CARD); inner.pack(fill="both", padx=2, pady=2)
        top = tk.Frame(inner, bg=C_CARD); top.pack(fill="x", padx=14, pady=(10, 2))
        tk.Label(top, text=title, font=(UI_FONT, 15, "bold"), bg=C_CARD, fg=C_WHITE).pack(side="left")
        tk.Label(inner, text=sub, font=(UI_FONT, 10), bg=C_CARD, fg=C_NOTES, anchor="w",
                 justify="left").pack(fill="x", padx=14)
        row = tk.Frame(inner, bg=C_CARD); row.pack(fill="x", padx=14, pady=(8, 12))
        return top, row

    top, row = card("①  한글(HWPX)로 변환", "‘변환_폴더’에 PDF를 넣고 누르세요. 결과 .hwpx 는 PDF 옆에 저장됩니다.", C_PEDGE)
    shadow_btn(tk, top, "📂 변환_폴더", (C_BODY, C_CBORD, C_FOOT), lambda: open_folder(CONVERT_DIR),
               height=30, font_size=10, width=130).pack(side="right")
    shadow_btn(tk, row, "정확하게 변환  (쪽당 ~15초)", BTN_B, lambda: run_action("accurate"),
               height=48, font_size=12).pack(side="left", fill="x", expand=True, padx=(0, 6))
    shadow_btn(tk, row, "빠르게 변환  (쪽당 ~5초)", BTN_G, lambda: run_action("fast"),
               height=48, font_size=12).pack(side="left", fill="x", expand=True, padx=(6, 0))

    top, row = card("②  개인정보 비식별", "‘비식별_대상’에 PDF를 넣고 누르세요. 주민번호·전화·계좌·주소·이름을 * 로 가린 PDF를 만듭니다.", BTN_Y[1])
    shadow_btn(tk, top, "📂 비식별_대상", (C_BODY, C_CBORD, C_FOOT), lambda: open_folder(REDACT_DIR),
               height=30, font_size=10, width=130).pack(side="right")
    shadow_btn(tk, row, "이름은 그대로", BTN_Y, lambda: run_action("keep"),
               height=48, font_size=12).pack(side="left", fill="x", expand=True, padx=(0, 6))
    shadow_btn(tk, row, "이름도 감춤  (검토 화면)", BTN_R, lambda: run_action("hide"),
               height=48, font_size=12).pack(side="left", fill="x", expand=True, padx=(6, 0))

    tk.Label(root, text="※ 스캔 문서의 숫자·계좌·금액은 반드시 원문과 대조하세요.  ·  인터넷 연결 없이 동작합니다.",
             font=(UI_FONT, 9), bg=C_PAGE, fg=C_FTXT).pack(side="bottom", pady=10)
    if os.environ.get("PII_GUI_TEST"):
        return root
    root.mainloop()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        return gui()
    cmd, rest = argv[0], argv[1:]
    if cmd in ("-V", "--version", "version"):
        print(f"PDF2HWPX {VERSION}"); return 0
    if cmd == "convert":
        key = "fast" if "--fast" in rest else "accurate"
        return run_action(key, [a for a in rest if a != "--fast"], wait=True)
    if cmd == "redact":
        script = os.path.join(APP, "pii_redact.py")
        return subprocess.call([_python(), script, *rest], env=_env(), cwd=ROOT)
    print("사용법: PDF2HWPX.exe [convert [--fast] PDF|폴더...] | [redact [--keep-names|--auto] PDF|폴더...]")
    return 2


if __name__ == "__main__":
    sys.exit(main() or 0)
