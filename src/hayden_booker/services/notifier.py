from __future__ import annotations

import platform
import shutil
import subprocess

from hayden_booker.config import NotificationsConfig


class Notifier:
    def __init__(self, config: NotificationsConfig) -> None:
        self.config = config

    def success(self, message: str) -> None:
        if self.config.notify_on_success:
            self._send("Hayden booking confirmed", message)

    def auth_required(self) -> None:
        if self.config.notify_on_auth_required:
            self._send("Hayden Booker needs sign-in", "Run: hayden-booker auth setup")

    def failure(self, title: str, message: str) -> None:
        if self.config.notify_on_failure:
            self._send(title, message)

    def _send(self, title: str, message: str) -> None:
        if not self.config.desktop_enabled:
            return
        system = platform.system()
        try:
            if system == "Darwin" and shutil.which("osascript"):
                safe_title = title.replace('"', "'")
                safe_message = message.replace('"', "'")
                subprocess.run(
                    [
                        "osascript",
                        "-e",
                        f'display notification "{safe_message}" with title "{safe_title}"',
                    ],
                    check=False,
                    timeout=5,
                )
            elif system == "Linux" and shutil.which("notify-send"):
                subprocess.run(["notify-send", title, message], check=False, timeout=5)
            elif system == "Windows":
                self._windows_toast(title, message)
        except (OSError, subprocess.SubprocessError):
            return

    @staticmethod
    def _windows_toast(title: str, message: str) -> None:
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if not powershell:
            return
        escaped_title = title.replace("'", "''")
        escaped_message = message.replace("'", "''")
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$n=New-Object System.Windows.Forms.NotifyIcon; "
            "$n.Icon=[System.Drawing.SystemIcons]::Information; "
            f"$n.BalloonTipTitle='{escaped_title}'; "
            f"$n.BalloonTipText='{escaped_message}'; "
            "$n.Visible=$true; $n.ShowBalloonTip(5000); Start-Sleep -Milliseconds 5500; "
            "$n.Dispose()"
        )
        subprocess.Popen(
            [powershell, "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
