import codecs
import ctypes
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QPoint, QProcess, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
)

# ===================== 配置区 =====================
BALL_SIZE = 64
SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "qidong_config.json"
ICON_PATH = SCRIPT_DIR / "R-C.jpg"
WINDOWS_OEM_CODE_PAGE = ctypes.windll.kernel32.GetOEMCP()
WINDOWS_OEM_ENCODING = f"cp{WINDOWS_OEM_CODE_PAGE}"

DEFAULT_APP_CONFIG = {
    "ollama": {"path": shutil.which("ollama") or r"C:\Users\你的用户名\AppData\Local\Programs\Ollama\ollama.exe"},
    "notepad": {"path": shutil.which("notepad") or r"C:\Windows\notepad.exe"},
    "cmd": {"path": shutil.which("cmd") or r"C:\Windows\System32\cmd.exe"},
}
# =================================================
def save_default_config():
    config = {name: {"path": info.get("path", "")} for name, info in DEFAULT_APP_CONFIG.items()}
    try:
        with CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
def load_app_config():
    config = {name: {"path": info.get("path", "")} for name, info in DEFAULT_APP_CONFIG.items()}

    if not CONFIG_PATH.exists():
        save_default_config()
        return config

    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            loaded = json.load(f)
    except (OSError, json.JSONDecodeError):
        return config

    if isinstance(loaded, dict):
        for name, value in loaded.items():
            if isinstance(value, dict):
                path_value = value.get("path", "")
                if isinstance(path_value, str):
                    config[str(name).lower()] = {"path": path_value}

    return config


def resolve_executable(path_value):
    if not path_value:
        return None

    candidate = str(path_value).strip()
    if not candidate:
        return None

    if os.path.isfile(candidate):
        return candidate

    found = shutil.which(candidate)
    if found:
        return found

    return None


APP_CONFIG = load_app_config()
ACTIVE_COMMAND_PROCESS = None
ACTIVE_COMMAND_DIALOG = None
COMMAND_OUTPUT_BUFFER = ""
COMMAND_OUTPUT_DECODERS = {}


def collect_command_output(process):
    global COMMAND_OUTPUT_BUFFER
    data = bytes(process.readAllStandardOutput())
    decoder = COMMAND_OUTPUT_DECODERS.get(id(process))
    output = decoder.decode(data) if decoder else data.decode("utf-8", errors="replace")
    if not output:
        return

    COMMAND_OUTPUT_BUFFER = (COMMAND_OUTPUT_BUFFER + output)[-100_000:]
    dialog = ACTIVE_COMMAND_DIALOG
    if dialog is not None and dialog.command_process is process:
        dialog.append_output(output)


def release_command_process(process):
    global ACTIVE_COMMAND_PROCESS, COMMAND_OUTPUT_BUFFER
    decoder = COMMAND_OUTPUT_DECODERS.pop(id(process), None)
    if decoder:
        remaining = decoder.decode(b"", final=True)
        if remaining:
            COMMAND_OUTPUT_BUFFER = (COMMAND_OUTPUT_BUFFER + remaining)[-100_000:]
            dialog = ACTIVE_COMMAND_DIALOG
            if dialog is not None and dialog.command_process is process:
                dialog.append_output(remaining)
    if ACTIVE_COMMAND_PROCESS is process:
        ACTIVE_COMMAND_PROCESS = None
    process.deleteLater()


def build_ball_pixmap(size=BALL_SIZE):
    if ICON_PATH.exists():
        try:
            pixmap = QPixmap(str(ICON_PATH))
            if not pixmap.isNull():
                return pixmap.scaled(
                    size,
                    size,
                    Qt.KeepAspectRatioByExpanding,
                    Qt.SmoothTransformation,
                )
        except Exception:
            pass

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(0, 0, 0, 40))
    painter.drawEllipse(3, 5, size - 6, size - 6)

    painter.setBrush(QColor(255, 255, 255))
    painter.setPen(QPen(QColor(220, 230, 241), 1.2))
    painter.drawEllipse(0, 0, size, size)

    center_x = size // 2
    center_y = size // 2

    painter.setBrush(QColor(64, 128, 255))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(center_x - 14, center_y - 11, 20, 18)

    painter.setBrush(QColor(89, 155, 255))
    painter.drawEllipse(center_x - 6, center_y - 15, 18, 20)

    painter.setBrush(QColor(255, 255, 255, 180))
    painter.drawEllipse(center_x - 10, center_y - 13, 6, 5)

    painter.end()
    return pixmap


class DoubaoFloatBall(QLabel):
    def __init__(self):
        super().__init__()
        self.setFixedSize(BALL_SIZE, BALL_SIZE)
        self.setWindowFlags(
            Qt.WindowStaysOnTopHint
            | Qt.FramelessWindowHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.PointingHandCursor)

        self._drag_pos = QPoint()
        self.dragging = False
        self._start_pos = QPoint()
        self.click_threshold = 6

        self.normal_opacity = 0.92
        self.hover_opacity = 1.0
        self.drag_opacity = 0.8

        self.setWindowOpacity(self.normal_opacity)
        self.setPixmap(build_ball_pixmap(BALL_SIZE))

    def enterEvent(self, event):
        self.setWindowOpacity(self.hover_opacity)
        super().enterEvent(event)

    def leaveEvent(self, event):
        if not self.dragging:
            self.setWindowOpacity(self.normal_opacity)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.dragging = False
            self._start_pos = event.globalPosition().toPoint()
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.setWindowOpacity(self.drag_opacity)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton:
            dist = (event.globalPosition().toPoint() - self._start_pos).manhattanLength()
            if dist > self.click_threshold:
                self.dragging = True
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.setWindowOpacity(self.normal_opacity)
            if not self.dragging:
                self._command_dialog = CommandDialog()
                self._command_dialog.show()
                self._command_dialog.raise_()
                self._command_dialog.activateWindow()
                QTimer.singleShot(0, self._command_dialog.focus_input)
                QTimer.singleShot(100, self._command_dialog.focus_input)
        super().mouseReleaseEvent(event)


class CommandDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.resize(440, 188)
        self.setWindowFlags(
            Qt.WindowStaysOnTopHint | Qt.Tool | Qt.FramelessWindowHint
        )
        self.setAttribute(Qt.WA_DeleteOnClose)
        self._background_pixmap = QPixmap(str(ICON_PATH))
        self._drag_offset = None
        self.setStyleSheet("QDialog { background: transparent; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 10)
        layout.setSpacing(8)

        title_bar = QHBoxLayout()
        title_bar.setContentsMargins(0, 0, 0, 0)
        title_bar.addStretch()
        self.close_btn = QPushButton("×")
        self.close_btn.setFixedSize(25, 22)
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setStyleSheet(
            "QPushButton { background: rgba(14, 27, 47, 190); color: #c6f7ff;"
            " border: 1px solid #38bdf8; border-radius: 6px; font-size: 16px; }"
            "QPushButton:hover { background: #e7445a; border-color: #ff7588; color: white; }"
        )
        self.close_btn.clicked.connect(self.close)
        title_bar.addWidget(self.close_btn)
        layout.addLayout(title_bar)

        self.input_box = QLineEdit()
        self.input_box.setPlaceholderText("输入指令，例如：ollama 或 notepad C:\\test\\demo.txt")
        self.input_box.setFont(QFont("Microsoft YaHei", 11))
        self.input_box.setClearButtonEnabled(True)
        self.input_box.setMinimumHeight(42)
        self.input_box.setStyleSheet(
            "QLineEdit {"
            "  border: 2px solid #3a7afe;"
            "  border-radius: 8px;"
            "  padding: 6px 10px;"
            "  background: #ffffff;"
            "  color: #1f1f1f;"
            "}"
            "QLineEdit:focus {"
            "  border: 2px solid #1f5fff;"
            "}"
        )
        self.run_btn = QPushButton("执行")
        self.run_btn.setDefault(True)
        self.run_btn.setAutoDefault(True)
        self.run_btn.setFixedHeight(36)
        self.run_btn.setStyleSheet(
            "QPushButton {"
            "  background: #2b74ff;"
            "  color: white;"
            "  border: none;"
            "  border-radius: 8px;"
            "  font-size: 12px;"
            "  font-weight: 600;"
            "}"
            "QPushButton:hover { background: #1f66eb; }"
            "QPushButton:pressed { background: #185dd9; }"
        )
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setFixedSize(68, 36)
        self.stop_btn.setToolTip("停止当前命令")
        self.stop_btn.setAccessibleName("停止当前命令")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setVisible(False)
        self.stop_btn.setStyleSheet(
            "QPushButton { background: rgba(9, 24, 40, 210); color: #ffc0cb;"
            " border: 1px solid rgba(255, 113, 141, 150); border-radius: 8px;"
            " font-size: 11px; font-weight: 600; padding: 0 10px; }"
            "QPushButton:hover { background: #d83b58; color: white; border-color: #ff718d; }"
            "QPushButton:pressed { background: #ad2944; }"
            "QPushButton:disabled { background: rgba(40, 50, 64, 180);"
            " color: #8994a3; border-color: rgba(101, 112, 128, 130); }"
        )
        self.stop_btn.clicked.connect(self.stop_command)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        button_row.addWidget(self.run_btn, 1)
        button_row.addWidget(self.stop_btn)

        self.output_view = QPlainTextEdit()
        self.output_view.setReadOnly(True)
        self.output_view.setPlaceholderText("命令行输出会显示在这里")
        self.output_view.document().setMaximumBlockCount(500)
        self.output_view.setStyleSheet(
            "QPlainTextEdit { background: rgba(4, 12, 25, 225); color: #8df7d0;"
            " border: 1px solid #2389a5; border-radius: 7px; padding: 7px;"
            " font-family: Consolas; font-size: 10px; selection-background-color: #245c83; }"
        )
        self.output_view.setVisible(False)

        self.status_label = QLabel("输入命令后按 Enter 执行")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(
            "QLabel { color: #72dfff; font-size: 10px; padding: 0 4px; }"
        )
        layout.addWidget(self.input_box)
        layout.addLayout(button_row)
        layout.addWidget(self.output_view, 1)
        layout.addWidget(self.status_label)
        self.input_box.returnPressed.connect(self.execute_command)
        self.run_btn.clicked.connect(self.execute_command)

        self.command_process = None
        self._closed = False
        if ACTIVE_COMMAND_PROCESS is not None and (
            ACTIVE_COMMAND_PROCESS.state() != QProcess.NotRunning
        ):
            self._attach_command_process(ACTIVE_COMMAND_PROCESS)
            self._show_status("命令仍在运行")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        bounds = self.rect().adjusted(1, 1, -1, -1)
        clip_path = QPainterPath()
        clip_path.addRoundedRect(bounds, 10, 10)
        painter.setClipPath(clip_path)
        if not self._background_pixmap.isNull():
            scaled = self._background_pixmap.scaled(
                self.size(),
                Qt.KeepAspectRatioByExpanding,
                Qt.SmoothTransformation,
            )
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        else:
            painter.fillRect(bounds, QColor("#101b31"))
        painter.fillRect(bounds, QColor(5, 12, 28, 105))
        painter.setClipping(False)
        painter.setPen(QPen(QColor("#38bdf8"), 1.5))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(bounds, 10, 10)
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)
    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = None
        super().mouseReleaseEvent(event)
    def showEvent(self, event):
        global ACTIVE_COMMAND_DIALOG
        super().showEvent(event)
        ACTIVE_COMMAND_DIALOG = self
        if ACTIVE_COMMAND_PROCESS is not None:
            self.output_view.setPlainText(COMMAND_OUTPUT_BUFFER)
            self.output_view.moveCursor(QTextCursor.End)
        QTimer.singleShot(0, self.focus_input)

    def closeEvent(self, event):
        global ACTIVE_COMMAND_DIALOG
        self._closed = True
        if ACTIVE_COMMAND_DIALOG is self:
            ACTIVE_COMMAND_DIALOG = None
        super().closeEvent(event)
    def focus_input(self):
        if not self.isVisible():
            return
        self.raise_()
        self.activateWindow()
        self.input_box.setFocus(Qt.OtherFocusReason)
        if self.input_box.hasFocus():
            self.input_box.selectAll()
    def _parse_command(self, raw_cmd):
        try:
            return shlex.split(raw_cmd, posix=False)
        except ValueError:
            return raw_cmd.split()
    def _show_status(self, message, success=False):
        color = "#65f2c2" if success else "#ff718d"
        self.status_label.setText(message)
        self.status_label.setStyleSheet(
            f"QLabel {{ color: {color}; font-size: 10px; padding: 0 4px; }}"
        )
    def _attach_command_process(self, process):
        already_attached = self.command_process is process
        self.command_process = process
        self.output_view.setVisible(True)
        self.stop_btn.setVisible(True)
        self.stop_btn.setEnabled(process.state() != QProcess.NotRunning)
        self.resize(440, 340)
        if already_attached:
            return
        self.output_view.setPlainText(COMMAND_OUTPUT_BUFFER)
        self.output_view.moveCursor(QTextCursor.End)
        process.finished.connect(self._command_finished)
        process.errorOccurred.connect(self._command_error)

    def append_output(self, text):
        cursor = self.output_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        self.output_view.setTextCursor(cursor)
        self.output_view.ensureCursorVisible()

    def _start_command_process(self, command, executable=None, arguments=None):
        global ACTIVE_COMMAND_PROCESS, COMMAND_OUTPUT_BUFFER
        if ACTIVE_COMMAND_PROCESS is not None and (
            ACTIVE_COMMAND_PROCESS.state() != QProcess.NotRunning
        ):
            self._attach_command_process(ACTIVE_COMMAND_PROCESS)
            self._show_status("已有命令正在运行，请等待结束或先停止")
            return

        COMMAND_OUTPUT_BUFFER = ""
        self.output_view.clear()
        self.output_view.setVisible(True)
        self.stop_btn.setVisible(True)
        self.stop_btn.setEnabled(True)
        self.run_btn.setEnabled(False)
        self.resize(440, 340)

        process = QProcess(QApplication.instance())
        if executable:
            process.setProgram(executable)
            process.setArguments(arguments or [])
            output_encoding = "utf-8"
        else:
            process.setProgram(os.environ.get("COMSPEC", "cmd.exe"))
            process.setArguments([
                "/d",
                "/c",
                f"chcp {WINDOWS_OEM_CODE_PAGE}>nul & {command}",
            ])
            output_encoding = WINDOWS_OEM_ENCODING
        process.setProcessChannelMode(QProcess.MergedChannels)
        COMMAND_OUTPUT_DECODERS[id(process)] = codecs.getincrementaldecoder(output_encoding)(
            errors="replace"
        )
        process.readyReadStandardOutput.connect(
            lambda proc=process: collect_command_output(proc)
        )
        ACTIVE_COMMAND_PROCESS = process
        self._attach_command_process(process)
        self._show_status(f"正在执行：{command}", success=True)
        process.start()

    def _command_finished(self, exit_code, _exit_status):
        process = self.sender()
        if process is not None:
            collect_command_output(process)
            release_command_process(process)
            if self.command_process is process:
                self.command_process = None
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._show_status(f"命令已结束，退出代码：{exit_code}")

    def _command_error(self, error):
        process = self.sender()
        if error == QProcess.FailedToStart and process is not None:
            collect_command_output(process)
            release_command_process(process)
            if self.command_process is process:
                self.command_process = None
            self.run_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self._show_status(f"命令启动失败：{process.errorString()}")

    def stop_command(self):
        process = self.command_process
        if process is None or process.state() == QProcess.NotRunning:
            return
        self.stop_btn.setEnabled(False)
        self._show_status("正在停止命令...")
        process.terminate()
        QTimer.singleShot(
            1500,
            lambda proc=process:
                proc.kill() if proc.state() != QProcess.NotRunning else None,
        )

    def execute_command(self):
        raw_cmd = self.input_box.text().strip()
        if not raw_cmd:
            self._show_status("请输入要执行的命令")
            self.focus_input()
            return
        command_parts = self._parse_command(raw_cmd)
        if not command_parts:
            self._show_status("没有解析到有效命令")
            self.focus_input()
            return

        command_name = command_parts[0].lower()
        app_info = APP_CONFIG.get(command_name)
        command = raw_cmd
        executable = None
        if app_info:
            executable = resolve_executable(app_info.get("path"))
            if not executable:
                self._show_status(f"找不到程序“{command_name}”，请检查配置路径")
                self.focus_input()
                return
            if command_name == "ollama":
                self._start_command_process(
                    f"ollama {' '.join(command_parts[1:])}".strip(),
                    executable,
                    command_parts[1:],
                )
                self.focus_input()
                return
            command = subprocess.list2cmdline([executable] + command_parts[1:])

        self._start_command_process(command)
        self.focus_input()
def build_tray_icon():
    if ICON_PATH.exists():
        try:
            icon = QIcon(str(ICON_PATH))
            if not icon.isNull():
                return icon
        except Exception:
            pass
    pixmap = build_ball_pixmap(32)
    return QIcon(pixmap)
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("启动器")
    tray = QSystemTrayIcon(build_tray_icon())
    tray_menu = QMenu()
    action_show = tray_menu.addAction("显示悬浮球")
    action_quit = tray_menu.addAction("退出")
    tray.setContextMenu(tray_menu)
    tray.show()
    ball = DoubaoFloatBall()
    ball.show()
    action_show.triggered.connect(lambda: (ball.show(), ball.raise_(), ball.activateWindow()))
    action_quit.triggered.connect(app.quit)
    sys.exit(app.exec())
