"""App: Firmware Update, in the System menu (airOS 8, as the web's System page: its version,
"Check for Updates", "Check Now" with the download it offers, "Upload Firmware" and the
notice to update or discard an uploaded firmware). And, for a device without internet, its
check made from this computer: the firmware downloaded here and uploaded to it."""
from __future__ import annotations

import glob
import html
import json
import os
import re
import threading
import time

from ..core.i18n import N_, tr
from ..core.errors import AirOSError
from ..core.config import DATA_DIR, _int
from ..device.firmware import (AUTO_CHECK_EVERY, check_update_here, download_firmware,
                               DOWNLOADS_URL, fetch_license, fetch_text, file_md5, firmware_file,
                               firmware_size, FW_DIR, FW_MAX, fw_build, fw_error, fw_prefix,
                               fw_uploaded_text, fw_version, has_update, update_check_on,
                               update_query, UPGRADE_MARGIN, UPGRADE_POLL_DELAY, UPGRADE_TIME)
from ..device.status import short_status

# When each device was last checked for updates (its web keeps it in the browser's storage).
STATE_FILE = os.path.join(DATA_DIR, "auto-update.json")
# What it offers when the device cannot check (as one without internet).
FROM_HERE_OFFER = N_("Check from this computer instead, through its internet? It asks "
                     "Ubiquiti what the device would ask and, if there is a new firmware, "
                     "downloads it here and uploads it to the device.")


def _text_of(page: str) -> str:
    """HTML (the license) as plain text."""
    page = re.sub(r"(?is)<(script|style).*?</\1>", "", page)
    page = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>|</div>", "\n", page)
    page = html.unescape(re.sub(r"<[^>]+>", "", page))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(line.strip() for line in page.splitlines())).strip()


class FirmwareScreens:
    """Firmware Update (airOS 8)."""

    def firmware_menu(self) -> None:
        title = tr("Firmware Update")
        if self.dev.flavor != "v8":
            self.ui.msgbox(title, tr("On airOS 6 the firmware is updated from the device's web "
                                     "(System page)."))
            return
        last = "n"
        while True:
            on, was = update_check_on(self.working), update_check_on(self.original)
            items = [("c", ("* " if on != was else "") + (tr("Check for Updates: enabled") if on
                                                          else tr("Check for Updates: disabled"))),
                     ("n", tr("Check Now")),
                     ("h", tr("Check Now from this computer (through its internet)")),
                     ("u", tr("Upload Firmware (a .bin file)"))]
            choice = self.ui.menu(title, self._fw_header(), items, default=last)
            if choice is None:
                return
            last = choice
            if choice == "c":
                new = "disabled" if on else "enabled"
                if self.original.get("update.check.status") is None and (new == "enabled") == was:
                    self._accept({"update.check.status": None})     # as the device has it
                else:
                    self._accept({"update.check.status": new})
            elif choice == "n":
                self.check_for_update(force=True)
            elif choice == "h":
                self.check_from_here()
            else:
                self.upload_firmware()

    def _fw_header(self) -> str:
        prefix = fw_prefix(self.dev.full_version)
        version = (self.status.get("host") or {}).get("fwversion") or "?"
        return "\n".join([tr("Firmware Version: {version}",
                             version=f"{version} ({prefix})" if prefix else version),
                          tr("Build Number: {build}",
                             build=fw_build(self.dev.full_version) or tr("Unknown")),
                          tr("* = changed, not applied yet.")])

    # -- the check (Check Now, and once a day as it starts) --

    def check_for_update(self, force: bool) -> None:
        """As its web: force (Check Now) says what it found; the daily check only speaks if
        there is a new firmware."""
        title = tr("Firmware Update")
        if force:
            self.ui.infobox(title, tr("Checking for updates…"))
        try:
            res = self.dev.fw_update_check(force)
        except AirOSError as e:
            if force:
                self._offer_from_here(f"{e}.")
            return
        if res.get("error"):
            if force:
                self._offer_from_here(tr("Could not connect to update server.") + " "
                                      + tr("Go to Ubiquiti downloads page: {url}",
                                           url=DOWNLOADS_URL))
            return
        if res.get("date") and res.get("version") and res.get("update"):
            self._offer_update(res)
        elif force:
            self.ui.msgbox(title, tr("You have latest available firmware version."))

    def _changelog(self, url) -> str | None:
        """The release notes the web shows (it fetches them from the address the check
        gives)."""
        return fetch_text(url, timeout=5, limit=200_000)

    def _release_notes(self, info: dict) -> tuple[str, str, str | None]:
        """A new firmware as its web's dialog shows it: (its heading, which says whether it is
        a security update; its text: the security note and its release notes, or where to
        find them; its release notes, None if they could not be had)."""
        version = str(info.get("version") or "")
        heading = (tr("New version {version} is available.", version=version)
                   if info.get("security") else
                   tr("Firmware update {version} is available.", version=version))
        notes = self._changelog(info.get("changelog"))
        text = "\n\n".join(([str(info["security"])] if info.get("security") else [])
                           + [notes or tr("Go to Ubiquiti downloads page for release notes: "
                                          "{url}", url=DOWNLOADS_URL)])
        return heading, text, notes

    def _agree_license(self, title: str, eula: str, version: str) -> bool:
        """The license of a firmware, as its web shows it, and whether the user agrees."""
        self.ui.textbox(tr("License"), eula)
        return self.ui.yesno(title, tr("Do you agree with the license of firmware {version}?",
                                       version=version),
                             yes=tr("Agree"), no=tr("Don't Agree")) == 0

    def _offer_from_here(self, said: str) -> None:
        """The device could not check (what it said): as its web, said; and offered from this
        computer, through its internet."""
        if self.ui.ask(tr("Firmware Update"), said + "\n\n" + tr(FROM_HERE_OFFER),
                       yes=tr("Check from here"), no=tr("Close")):
            self.check_from_here()

    def check_from_here(self) -> None:
        """Its check for updates made from this computer, through its internet (for a device
        without it): Ubiquiti's update server asked what the device would ask; a new firmware,
        with its notes, downloaded here (into FW_DIR, kept for other devices too; asked before,
        with its size) and checked against the checksum the server gives; then the license
        the device's own download shows and, agreed, uploaded to the device as "Upload
        Firmware" does (the device checks it too), with its notice to update."""
        title = tr("Firmware Update")
        board = self._board()
        if not (board.get("board.sysid") and board.get("board.hwaddr")
                and self.dev.full_version):     # (the server would answer for no device)
            self.ui.msgbox(title, tr("The device's model or firmware version is not known: its "
                                     "update cannot be checked from here."))
            return
        self.ui.infobox(title, tr("Checking for updates from this computer…"))
        try:
            info = check_update_here(update_query(self._board(), self.dev.full_version,
                                                  self.original))
        except AirOSError as e:
            self.ui.msgbox(title, f"{e}\n\n" + tr("Go to Ubiquiti downloads page: {url}",
                                                   url=DOWNLOADS_URL))
            return
        if not has_update(info):
            self.ui.msgbox(title, tr("You have latest available firmware version."))
            return
        version = str(info.get("version") or "")
        heading, text, _ = self._release_notes(info)
        self.ui.textbox(heading, text)
        path = firmware_file(info)
        if file_md5(path) == str(info.get("checksum") or "").strip().lower():
            question, yes = tr("Firmware {version} was downloaded to this computer already: "
                               "{path}\n\nUpload it to the device?", version=version,
                               path=path), tr("Upload")
        else:
            size = firmware_size(str(info.get("url") or ""))
            question = tr("Download firmware {version} to this computer ({size}) and upload it "
                          "to the device?", version=version,
                          size=f"{size / 1048576:.1f} MB" if size else tr("its size unknown"))
            yes = tr("Download")
        if self.ui.yesno(heading, question, yes=yes, no=tr("Dismiss")) != 0:
            return
        gauge = self.ui.gauge(title, tr("Please wait. Downloading firmware..."))
        shown = {"at": 0.0}

        def progress(done, total):
            now = time.monotonic()
            if now - shown["at"] >= 0.3:
                shown["at"] = now
                gauge.update(int(done * 100 / total) if total else 0,
                             tr("Please wait. Downloading firmware...")
                             + f"\n{done / 1048576:.1f}"
                             + (f" / {total / 1048576:.1f} MB" if total else " MB"))
        try:
            path = download_firmware(info, progress)
        except AirOSError as e:
            path, error = None, str(e)
        finally:
            gauge.close()                        # (also if it is interrupted: Ctrl+C)
        if path is None:
            self.ui.msgbox(title, error)
            return
        try:
            eula = fetch_license()
        except AirOSError as e:
            self.ui.msgbox(title, tr("Could not get the license of the firmware: {error}",
                                     error=e) + "\n\n" + tr(
                "The firmware stays in {path}: Upload Firmware takes it.", path=path))
            return
        if self._agree_license(title, _text_of(eula), version):
            self._upload_firmware_file(path, title)

    def _offer_update(self, info: dict) -> None:
        """The web's dialog of a new firmware: its notes, then Download or Dismiss."""
        version = str(info.get("version") or "")
        title, text, notes = self._release_notes(info)
        if notes:
            self.ui.textbox(title, text)
            text = tr("Download firmware {version} to the device?", version=version)
        if self.ui.yesno(title, text, yes=tr("Download"), no=tr("Dismiss")) == 0:
            self._download_update(version)
        else:                                    # (as its web: dismissing it discards it)
            self.dev.fw_discard()

    def _download_update(self, version: str) -> None:
        """As the web: the device downloads it while its progress is shown; then the license
        and, if agreed, the notice to update."""
        title = tr("Firmware Update")
        result: dict = {}

        def download():
            try:
                result["res"] = self.dev.fw_download()
            except AirOSError as e:
                result["error"] = e
        worker = threading.Thread(target=download, daemon=True)
        worker.start()
        gauge = self.ui.gauge(title, tr("Please wait. Downloading firmware..."))
        try:
            while worker.is_alive():
                progress = self.dev.fw_download_progress()
                if progress is not None:
                    gauge.update(progress, tr("Please wait. Downloading firmware..."))
                worker.join(1)
        finally:
            gauge.close()
        if "error" in result:
            self.ui.msgbox(title, tr("Error") + f" : {result['error']}")
            return
        res = result.get("res") or {}
        if not res.get("ok"):
            self.ui.msgbox(title, str(res.get("error") or tr("Firmware upload failed")))
            return
        try:
            eula = _text_of(self.dev.fw_download_eula())
        except AirOSError as e:
            self.ui.msgbox(title, tr("Could not get the license of the firmware: {error}",
                                     error=e))
            return
        if self._agree_license(title, eula, version):
            self.firmware_uploaded(version, False)
        else:
            self.dev.fw_discard()

    # -- uploading one --

    def upload_firmware(self) -> None:
        """The web's "Upload Firmware": the file to the device, which checks it; then its
        notice."""
        title = tr("Upload Firmware")
        path = self._choose_firmware(title)
        if path:
            self._upload_firmware_file(path, title)

    def _upload_firmware_file(self, path: str, title: str) -> None:
        """A firmware file of this computer to the device, which checks it; then its notice."""
        data = self._read_file(path, FW_MAX)
        if data is None:
            return
        self.ui.infobox(title, tr("Uploading") + "\n" + tr("Checking uploaded file") + "...")
        res = self.dev.fw_upload(os.path.basename(path), data)
        if str(res.get("code")) != "0":
            self.ui.msgbox(title, fw_error(res.get("err"), fw_prefix(self.dev.full_version),
                                           self._board()))
            return
        self.firmware_uploaded(str(res.get("version") or ""), bool(res.get("thirdParty")))

    def _choose_firmware(self, title: str) -> str | None:
        """The firmware file to upload: typed, or chosen from a folder (at first, the one of
        the firmware downloaded here, if it has any)."""
        start = (FW_DIR if glob.glob(os.path.join(glob.escape(FW_DIR), "*.bin"))
                 else os.path.expanduser("~"))
        path = self.ui.inputbox(title, tr("The firmware file (.bin), or a folder to choose it "
                                          "from:"), start + os.sep)
        if not path or not path.strip():
            return None
        path = os.path.expanduser(path.strip())
        if not os.path.isdir(path):
            return path
        files = sorted((f for f in glob.glob(os.path.join(glob.escape(path), "*.bin"))
                        if os.path.isfile(f)), key=os.path.getmtime, reverse=True)[:40]
        if not files:
            self.ui.msgbox(title, tr("There is no .bin file in {path}.", path=path))
            return None
        choice = self.ui.menu(title, tr("Firmware files in {path}:", path=path),
                              [(str(i), os.path.basename(p)) for i, p in enumerate(files, 1)])
        return files[int(choice) - 1] if choice else None

    # -- the notice of an uploaded firmware, and updating --

    def firmware_uploaded(self, version: str, third_party: bool) -> None:
        """The web's notice of an uploaded firmware, which does not go away: update or
        discard."""
        title = tr("Firmware Update")
        host, wireless = self.status.get("host") or {}, self.status.get("wireless") or {}
        text = fw_uploaded_text(version, third_party,
                                fw_version(self.dev.full_version, host.get("fwversion")),
                                _int(wireless.get("chanbw")))
        if self.changes():                       # (updating reads it all again: see load)
            text += "\n\n" + tr("If you update, the {count} unapplied change(s) are lost.",
                                count=len(self.changes()))
        while True:
            answer = self.ui.yesno(title, text, yes=tr("Update"), no=tr("Discard"),
                                   default_no=True)      # (writing a firmware: never by chance)
            if answer == 0:
                self._update_firmware()
                return
            if answer == 1:
                self.ui.infobox(title, tr("Please wait"))
                self.dev.fw_discard()
                self.ui.msgbox(title, tr("The uploaded firmware was discarded."))
                return

    def _update_firmware(self) -> None:
        """POST /fwflash.cgi do_update=1, then wait for the device as its web does: from 10
        seconds on (never sooner, whatever --wait says), until it answers; board.upgrade is
        what it takes. It is back when it answers with another firmware or having restarted
        since (its uptime went back; if it does not give it, having stopped answering once):
        until then what answers is the old firmware, still writing the new one, and saying
        that nothing changed would invite turning it off in the middle."""
        title = tr("Firmware Update")
        try:
            self.status = self.dev.status()         # (its uptime now, to tell a restart)
        except AirOSError:
            pass                                   # (the one read before)
        t0 = time.monotonic()
        host = self.status.get("host") if isinstance(self.status.get("host"), dict) else {}
        before, up_before = host.get("fwversion"), _int(host.get("uptime"))
        self.ui.infobox(title, tr("Initializing firmware update") + "...")
        res = self.dev.fw_flash()
        if str(res.get("code")) != "0":
            self.ui.msgbox(title, str(res.get("message") or tr("Firmware update request failed")))
            return
        self._last_check(None)                   # (as its web: it checks again afterwards)
        upgrade = _int(self._board().get("board.upgrade")) or UPGRADE_TIME
        seen = {"down": False, "last": None}

        def back():
            if not self.dev.ping():
                seen.update(down=True, last=None)
                return None
            try:
                st = self.dev.status()
            except AirOSError:
                return None                          # (it answers, but not with its data yet)
            h = st.get("host") if isinstance(st.get("host"), dict) else {}
            up = _int(h.get("uptime"))
            if up is not None and up_before is not None:
                restarted = up + 5 < up_before + (time.monotonic() - t0)
            else:
                restarted = seen["down"]
            if h.get("fwversion") != before or restarted:
                return tr("The device answered.")
            seen["last"] = h.get("fwversion")        # (the old one, writing the new one)
            return None

        def waiting(spent):
            if seen["last"] is not None:
                return tr("Upgrading the firmware… it still answers with {version} and has not "
                          "restarted: it is writing the new one. Do not turn it off. "
                          "({seconds} s)", version=seen["last"], seconds=spent)
            return tr("Upgrading the firmware… waiting for the device at {host} ({seconds} s)",
                      host=self.dev.host, seconds=spent)
        ok = self._wait(
            tr("Upgrading"), max(UPGRADE_POLL_DELAY, self.wait or 0), upgrade + UPGRADE_MARGIN,
            lambda left: tr("Upgrading the firmware. Do not turn off the device.\nI will check "
                            "whether it came back in {seconds} s.", seconds=left),
            waiting, back, pause=2)
        if not ok and seen["last"] is not None:
            self.ui.msgbox(title, tr("The device still answers with firmware {version} and has "
                                     "not restarted: the update may not have started yet, or "
                                     "still be running. Do not turn it off; check its firmware "
                                     "in a few minutes (System → Firmware).",
                                     version=seen["last"]))
            return
        if not ok:
            self.ui.msgbox(tr("No answer"), tr("The device does not answer at {address} yet. "
                                               "The update may still be running: do not turn "
                                               "it off, wait a few minutes and connect again.",
                                               address=self.dev.base))
            return
        self.load(restarted=True)
        self.dev.set_firmware(self.status)
        now = (self.status.get("host") or {}).get("fwversion")
        if now != before:
            self.ui.msgbox(title, tr("Firmware updated: {old} → {new}.", old=before, new=now)
                           + "\n\n" + short_status(self.status))
        else:
            self.ui.msgbox(title, tr("The device answers, but its firmware is still {version}.",
                                     version=now))

    # -- as it starts --

    def firmware_startup(self, warnings: dict | None = None) -> None:
        """As its web when it starts (airOS 8): an uploaded firmware waiting is asked about
        and, with "Check for Updates" on, it checks once a day. warnings: what
        /api/warnings said, if it has been read already."""
        if self.dev.flavor != "v8":
            return
        fw = (self.dev.warnings() if warnings is None else warnings).get("firmware")
        fw = fw if isinstance(fw, dict) else {}
        if fw.get("uploaded"):
            self.firmware_uploaded(str(fw.get("version") or ""), bool(fw.get("isThirdParty")))
        last = self._last_check()
        if update_check_on(self.original) and (
                last is None or time.time() - last > AUTO_CHECK_EVERY):
            self._last_check(time.time())
            self.check_for_update(force=False)

    def _last_check(self, *when) -> float | None:
        """When this device was last checked; with an argument, it is remembered (None
        forgets it)."""
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                state = json.load(f)
        except (OSError, ValueError):
            state = {}
        state = state if isinstance(state, dict) else {}
        if not when:
            value = state.get(self.dev.base)
            return float(value) if isinstance(value, (int, float)) else None
        if when[0] is None:
            state.pop(self.dev.base, None)
        else:
            state[self.dev.base] = when[0]
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f)
        except OSError:
            pass
        return None
