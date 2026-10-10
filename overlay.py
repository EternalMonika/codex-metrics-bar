"""Windows desktop companion for the Codex metrics-bar plugin.

Three small non-activating metrics pills follow the Codex composer. Each pill
opens a read-only detail card. The windows are not topmost, so IME candidates
and other system popups remain above them.
"""

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import time
import tkinter as tk

from metrics import Metrics, SessionReader, compact_tokens, find_session


if os.name != "nt":
    raise SystemExit("This overlay currently supports Windows only.")


ctypes.windll.shcore.SetProcessDpiAwareness(1)
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WS_EX_LAYERED = 0x80000
WS_EX_TOOLWINDOW = 0x80
WS_EX_NOACTIVATE = 0x08000000
GWL_EXSTYLE = -20
ERROR_ALREADY_EXISTS = 183
CHROMA = "#ff00ff"
SURFACE = "#ffffff"
PILL = "#f5f5f6"
PILL_ACTIVE = "#e9eaec"
LABEL = "#565b63"
STRONG = "#35383d"
MUTED = "#6f747c"
ACCENT = "#558cf6"

user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetParent.argtypes = [wintypes.HWND]
user32.GetParent.restype = wintypes.HWND
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD,
                                              wintypes.BYTE, wintypes.DWORD]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE


def _pid(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _process_path(pid):
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(4096)
        length = wintypes.DWORD(len(buffer))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
            return buffer.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def codex_window():
    windows = []
    foreground = user32.GetForegroundWindow()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width < 650 or height < 350:
            return True
        path = _process_path(_pid(hwnd)).lower()
        if "openai.codex" in path and path.endswith("chatgpt.exe"):
            area = width * height
            windows.append((hwnd == foreground, area, hwnd, rect))
        return True

    user32.EnumWindows(visit, 0)
    if not windows:
        return None
    windows.sort(key=lambda item: (item[0], item[1]), reverse=True)
    _, _, hwnd, rect = windows[0]
    return hwnd, rect


def _rounded(canvas, x1, y1, x2, y2, fill, radius=None):
    """Draw a filled round rectangle over a transparent window background."""
    radius = min((y2 - y1) / 2, radius if radius is not None else (y2 - y1) / 2)
    diameter = 2 * radius
    for left in (x1, x2 - diameter):
        for top in (y1, y2 - diameter):
            canvas.create_oval(left, top, left + diameter, top + diameter,
                               fill=fill, outline=fill)
    canvas.create_rectangle(x1 + radius, y1, x2 - radius, y2,
                            fill=fill, outline=fill)
    canvas.create_rectangle(x1, y1 + radius, x2, y2 - radius,
                            fill=fill, outline=fill)


def _window_style(window):
    window.update_idletasks()
    hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                          style | WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
    user32.SetLayeredWindowAttributes(hwnd, 0x00FF00FF, 255, 0x03)
    return hwnd


def _duration(seconds):
    total = max(0, round(seconds))
    minutes, remaining = divmod(total, 60)
    return f"{minutes}分{remaining}秒" if minutes else f"{remaining}秒"


def _number(value):
    return f"{value:,} tok"


class Bar:
    def __init__(self, data_dir: Path):
        self.scale = user32.GetDpiForSystem() / 96
        self.data_dir = data_dir
        self.codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        self.reader = None
        self.selected_path = None
        self.last_scan = 0.0
        self.cached_source = None
        self.metrics = Metrics()
        self.active_card = None
        self.previous_mouse_down = False
        self.bar_visible = False
        self.popup_visible = False
        self.foreground_before_start = user32.GetForegroundWindow()
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.configure(bg=CHROMA)
        self.root.wm_attributes("-transparentcolor", CHROMA)
        self.width = round(502 * self.scale)
        self.height = round(16 * self.scale)
        self.canvas = tk.Canvas(self.root, width=self.width, height=self.height,
                                bg=CHROMA, highlightthickness=0, bd=0, cursor="hand2")
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self._on_click)
        self.overlay_hwnd = _window_style(self.root)

        self.popup = tk.Toplevel(self.root)
        self.popup.withdraw()
        self.popup.overrideredirect(True)
        self.popup.configure(bg=CHROMA)
        self.popup.wm_attributes("-transparentcolor", CHROMA)
        self.popup_width = round(320 * self.scale)
        self.popup_height = round(190 * self.scale)
        self.popup_canvas = tk.Canvas(self.popup, width=self.popup_width,
                                      height=self.popup_height, bg=CHROMA,
                                      highlightthickness=0, bd=0)
        self.popup_canvas.pack()
        self.popup_canvas.bind("<Button-1>", lambda _event: self._close_popup())
        self.popup_hwnd = _window_style(self.popup)
        if _pid(user32.GetForegroundWindow()) == os.getpid():
            user32.SetForegroundWindow(self.foreground_before_start)
        self.root.after(300, self.tick)
        self.root.after(80, self._watch_external_click)

    def _watch_external_click(self):
        down = bool(user32.GetAsyncKeyState(0x01) & 0x8000)
        if self.active_card and down and not self.previous_mouse_down:
            point = wintypes.POINT()
            if user32.GetCursorPos(ctypes.byref(point)):
                in_bar = (self.bar_x <= point.x < self.bar_x + self.width and
                          self.bar_y <= point.y < self.bar_y + self.height)
                in_popup = (self.popup_x <= point.x < self.popup_x + self.popup_width and
                            self.popup_y <= point.y < self.popup_y + self.popup_height)
                if not in_bar and not in_popup:
                    self._close_popup()
        self.previous_mouse_down = down
        self.root.after(80, self._watch_external_click)

    def _on_click(self, event):
        x = event.x / self.scale
        if x < 188:
            selected = "session"
        elif 194 <= x < 424:
            selected = "tokens"
        elif 430 <= x < 502:
            selected = "context"
        else:
            return
        self.active_card = None if self.active_card == selected else selected
        if self.active_card is None:
            self.popup.withdraw()
            self.popup_visible = False
        self._paint_bar()
        self._paint_popup()

    def _close_popup(self):
        self.active_card = None
        self.popup.withdraw()
        self.popup_visible = False
        self._paint_bar()

    def _hide_all(self):
        self.root.withdraw()
        self.popup.withdraw()
        self.bar_visible = False
        self.popup_visible = False
        self.active_card = None

    def _paint_bar(self):
        c = self.canvas
        c.delete("all")
        for key, left, right in (("session", 0, 188), ("tokens", 194, 424),
                                 ("context", 430, 502)):
            _rounded(c, left, 0, right, 15, PILL_ACTIVE if self.active_card == key else PILL)
        c.create_oval(7, 3, 17, 13, outline=MUTED, width=1)
        c.create_line(12, 8, 14.5, 5.5, fill=MUTED, width=1)
        speed = self.metrics.tokens_per_second
        if self.metrics.last_update and time.time() - self.metrics.last_update > 10:
            speed = 0
        c.create_text(22, 8, anchor="w", fill=LABEL, font=("Microsoft YaHei UI", 8),
                      text=f"{self.metrics.turns}轮 {self.metrics.steps}步 · {round(speed)} tok/s")
        c.create_oval(201, 4, 210, 12, outline=MUTED, width=1)
        c.create_line(201, 6, 210, 6, fill=MUTED, width=1)
        c.create_line(201, 10, 210, 10, fill=MUTED, width=1)
        cache = f"{self.metrics.cache_percent}%" if self.metrics.cache_percent is not None else "—"
        c.create_text(216, 8, anchor="w", fill=LABEL, font=("Microsoft YaHei UI", 8),
                      text=f"{compact_tokens(self.metrics.tokens)} tok · 缓存命中 {cache}")
        c.create_oval(438, 3, 448, 13, outline="#d5d8dd", width=1.3)
        context = self.metrics.context_percent
        if context:
            c.create_arc(438, 3, 448, 13, start=90, extent=-3.6 * context,
                         style="arc", outline=ACCENT, width=1.5)
        c.create_text(453, 8, anchor="w", fill=LABEL, font=("Microsoft YaHei UI", 8),
                      text=f"{context}%" if context is not None else "—%")
        c.scale("all", 0, 0, self.scale, self.scale)

    def _row(self, label, value, y, swatch=None):
        c = self.popup_canvas
        x = 24
        if swatch:
            c.create_rectangle(24, y - 5, 32, y + 3, fill=swatch, outline=swatch)
            x = 39
        c.create_text(x, y, anchor="w", fill=LABEL, font=("Microsoft YaHei UI", 9), text=label)
        c.create_text(294, y, anchor="e", fill=STRONG, font=("Microsoft YaHei UI", 9), text=value)

    def _paint_popup(self):
        if self.active_card is None:
            return
        c = self.popup_canvas
        c.delete("all")
        _rounded(c, 2, 3, 318, 188, "#ececef", radius=16)
        _rounded(c, 0, 0, 316, 184, SURFACE, radius=16)
        c.create_line(22, 47, 294, 47, fill="#eeeff1", width=1)
        m = self.metrics
        if self.active_card == "session":
            c.create_text(24, 27, anchor="w", fill=STRONG,
                          font=("Microsoft YaHei UI", 10, "bold"), text="◴  会话统计")
            self._row("模型用时", _duration(m.model_seconds), 68)
            self._row("工具调用用时", _duration(m.tool_seconds), 93)
            self._row("首 Token 耗时 (TTFT)", "未提供", 118)
            average = m.average_tokens_per_second
            self._row("平均输出速度", f"{round(average)} tok/s" if average is not None else "—", 143)
        elif self.active_card == "tokens":
            c.create_text(24, 27, anchor="w", fill=STRONG,
                          font=("Microsoft YaHei UI", 10, "bold"), text="▤  Token 用量")
            c.create_text(294, 27, anchor="e", fill=STRONG,
                          font=("Microsoft YaHei UI", 10, "bold"), text=_number(m.tokens))
            self._row("缓存命中", f"{m.cache_percent}%" if m.cache_percent is not None else "—", 68)
            self._row("未缓存输入", _number(m.uncached_tokens), 93)
            self._row("缓存读取", _number(m.cached_tokens), 118)
            self._row("输出", _number(m.output_tokens), 143)
        else:
            pct = m.context_percent
            c.create_text(24, 27, anchor="w", fill=LABEL,
                          font=("Microsoft YaHei UI", 10), text="上下文已用")
            c.create_text(104, 27, anchor="w", fill=STRONG,
                          font=("Microsoft YaHei UI", 10, "bold"),
                          text=f"{pct}%" if pct is not None else "—%")
            c.create_text(294, 27, anchor="e", fill=STRONG,
                          font=("Microsoft YaHei UI", 9, "bold"),
                          text=f"{compact_tokens(m.context_tokens)} / {compact_tokens(m.context_window)}")
            _rounded(c, 24, 54, 294, 59, "#eceef1")
            if pct:
                fill_width = 270 * min(100, pct) / 100
                c.create_rectangle(24, 54, 24 + fill_width, 59, fill=ACCENT, outline=ACCENT)
            self._row("未缓存输入", _number(max(0, m.context_tokens - m.context_cached_tokens)), 91, "#a883fa")
            self._row("缓存输入", _number(m.context_cached_tokens), 120, ACCENT)
            self._row("剩余窗口", _number(m.context_remaining), 149, "#c4c9d0")

        c.scale("all", 0, 0, self.scale, self.scale)
        # Keep the popup above the composer; an IME candidate can still be on top.
        rect = self.last_rect
        if self.active_card == "session":
            popup_x = self.bar_x
        elif self.active_card == "tokens":
            popup_x = self.bar_x + round(90 * self.scale)
        else:
            popup_x = self.bar_x + self.width - self.popup_width
        margin = round(8 * self.scale)
        popup_x = max(rect.left + margin, min(popup_x, rect.right - self.popup_width - margin))
        popup_y = max(rect.top + margin, rect.bottom - round(120 * self.scale) - self.popup_height)
        self.popup_x = popup_x
        self.popup_y = popup_y
        self.popup.geometry(f"{self.popup_width}x{self.popup_height}+{popup_x}+{popup_y}")
        if not self.popup_visible:
            self.popup.deiconify()
            user32.SetWindowPos(self.popup_hwnd, -1, popup_x, popup_y,
                                self.popup_width, self.popup_height, 0x0010 | 0x0040)
            self.popup_visible = True
        else:
            user32.SetWindowPos(self.popup_hwnd, 0, popup_x, popup_y,
                                self.popup_width, self.popup_height, 0x0010 | 0x0004)

    def _source(self):
        pointer = self.data_dir / "active.json"
        try:
            selection = json.loads(pointer.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            selection = {}
        path = selection.get("transcript_path")
        if path and Path(path).is_file():
            return Path(path)
        if time.time() - self.last_scan > 10:
            self.cached_source = find_session(self.codex_home, selection.get("session_id"))
            self.last_scan = time.time()
        return self.cached_source

    def tick(self):
        try:
            self._update()
        except Exception:
            # A corrupt or rotating transcript should not kill the companion.
            pass
        self.root.after(1000, self.tick)

    def _update(self):
        target = codex_window()
        if target is None:
            self._hide_all()
            return
        hwnd, rect = target
        foreground_pid = _pid(user32.GetForegroundWindow())
        if foreground_pid == os.getpid():
            restore = hwnd if self.bar_visible else self.foreground_before_start
            user32.SetForegroundWindow(restore)
            foreground_pid = _pid(user32.GetForegroundWindow())
        if foreground_pid != _pid(hwnd):
            self._hide_all()
            return
        path = self._source()
        if path and path != self.selected_path:
            self.selected_path = path
            self.reader = SessionReader(path)
        if self.reader:
            self.metrics = self.reader.refresh()
        self._paint_bar()
        window_width = rect.right - rect.left
        sidebar = min(round(290 * self.scale), round(window_width * 0.2)) if window_width >= 1100 * self.scale else 0
        chat_center = (rect.left + sidebar + rect.right) // 2
        margin = round(8 * self.scale)
        x = max(rect.left + margin, min(chat_center - self.width // 2, rect.right - self.width - margin))
        y = rect.bottom - self.height - round(2 * self.scale)
        self.bar_x = x
        self.bar_y = y
        self.last_rect = rect
        self.root.geometry(f"{self.width}x{self.height}+{x}+{y}")
        if not self.bar_visible:
            self.root.deiconify()
            user32.SetWindowPos(self.overlay_hwnd, -1, x, y, self.width, self.height,
                                0x0010 | 0x0040)  # SWP_NOACTIVATE | SWP_SHOWWINDOW
            self.bar_visible = True
        else:
            user32.SetWindowPos(self.overlay_hwnd, 0, x, y, self.width, self.height,
                                0x0010 | 0x0004)  # SWP_NOACTIVATE | SWP_NOZORDER
        self._paint_popup()
        if _pid(user32.GetForegroundWindow()) == os.getpid():
            user32.SetForegroundWindow(hwnd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path,
                        default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "metrics-bar")
    args = parser.parse_args()
    mutex = kernel32.CreateMutexW(None, False, "Local\\CodexMetricsBar")
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        if mutex:
            kernel32.CloseHandle(mutex)
        return
    try:
        Bar(args.data_dir).root.mainloop()
    finally:
        if mutex:
            kernel32.CloseHandle(mutex)


if __name__ == "__main__":
    main()
