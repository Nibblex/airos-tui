"""The errors of the device client."""
from __future__ import annotations


class AirOSError(Exception):
    """Error talking to the device. The message is meant to be shown as it is."""


class AuthError(AirOSError):
    """Its login refused. detail: what the device said, if it said something."""

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.detail = detail


class SetupRefused(AirOSError):
    """Its first setup refused (airOS 8: AirOS.first_setup), with what the device said: its
    web shows it on its setup screen, which stays."""


class SessionEnded(Exception):
    """This session can no longer use the device (it was reset to its factory defaults): the
    application shows why and what to do, and ends."""

    def __init__(self, title: str, message: str):
        super().__init__(message)
        self.title = title


class SwitchDevice(Exception):
    """Leave this device for another one (Tools → Discovery → Connect): the session ends and
    the connection screen goes to that address."""

    def __init__(self, target: str):
        super().__init__(target)
        self.target = target
