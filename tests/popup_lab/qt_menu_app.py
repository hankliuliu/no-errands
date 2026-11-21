#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Popup-menu lab target (CI only). A Qt window with a [More] button that opens a QMenu popup:
  Search / Save chat / Leave group
- "Save chat" opens the native save dialog (default name carries the chat name) and writes the file.
- "Leave group" opens a confirmation; confirming writes a VIOLATION record (the tool must never confirm).
--no-a11y sets QT_ACCESSIBILITY=0 to imitate apps whose popup menus are not in the UIA tree.
Events go to <state>/events.jsonl."""
import json, os, sys, time

if "--no-a11y" in sys.argv:
    os.environ["QT_ACCESSIBILITY"] = "0"
from PySide6.QtWidgets import (QApplication, QFileDialog, QMainWindow, QMenu, QMessageBox, QPushButton,
                               QVBoxLayout, QWidget, QLabel)

STATE = sys.argv[sys.argv.index("--state") + 1] if "--state" in sys.argv else os.path.join(os.getcwd(), "qtlab-state")
os.makedirs(STATE, exist_ok=True)


def log(kind, **kw):
    kw.update(kind=kind, ts=time.time())
    with open(os.path.join(STATE, "events.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(kw, ensure_ascii=False) + "\n")


class W(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NE-QTLAB")
        self.resize(520, 300)
        c = QWidget(); lay = QVBoxLayout(c)
        hdr = QLabel("Lab Group, 4人"); lay.addWidget(hdr)
        self.more = QPushButton("More"); self.more.setAccessibleName("More")
        self.more.clicked.connect(self.show_menu)
        lay.addWidget(self.more)
        self.setCentralWidget(c)

    def show_menu(self):
        m = QMenu(self)
        m.addAction("Search").triggered.connect(lambda: log("search"))
        m.addAction("Save chat").triggered.connect(self.save_chat)
        m.addAction("Leave group").triggered.connect(self.leave)
        log("menu-open")
        m.popup(self.more.mapToGlobal(self.more.rect().bottomLeft()))
        self._menu = m

    def save_chat(self):
        log("save-dialog-open")
        path, _ = QFileDialog.getSaveFileName(self, "Save chat", "[LINE] Chat with Lab Group.txt", "Text (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write("# Lab Group\n2026/11/21 10:00 Ann hello\n2026/11/21 10:01 Ben hi\n")
            log("saved", path=path)
        else:
            log("save-cancelled")

    def leave(self):
        r = QMessageBox.question(self, "Leave group", "Really leave this group?")
        log("VIOLATION-leave-confirmed" if r == QMessageBox.Yes else "leave-declined")


app = QApplication(sys.argv)
w = W(); w.show()
log("start", pid=os.getpid())
app.exec()
