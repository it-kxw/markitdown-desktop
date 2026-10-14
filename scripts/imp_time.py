"""P0 辅助：测量各重量级依赖的导入耗时（dev 模式）。"""
import os
import sys
import time


def t(label, fn):
    s = time.perf_counter()
    r = fn()
    print(f"{label:34s} {(time.perf_counter() - s) * 1000:8.1f} ms")
    return r


t("import pandas", lambda: __import__("pandas"))
t("import markitdown", lambda: __import__("markitdown"))
t("import PySide6.QtWidgets", lambda: __import__("PySide6.QtWidgets", fromlist=["x"]))
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
t("QApplication()", lambda: QApplication(sys.argv[:1]))
from markitdown import MarkItDown

t("MarkItDown() construct", lambda: MarkItDown())
