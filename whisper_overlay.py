#!/usr/bin/env python3
"""Screenshot-invisible status overlay for Whisper Dictation.

Hammerspoon's hs.alert renders into an NSPanel that Hammerspoon never exposes the
sharingType of, so the "Диктую…/Транскрибирую…" banners land in every screen
capture (macOS screenshot, Monosnap, the Claude desktop app's capture button,
ScreenCaptureKit, …). Intercepting the Cmd+Shift+3/4/5 hotkeys cannot help: the
Claude app's capture button fires no key event at all.

This helper draws the banner itself in a borderless window whose sharingType is
NSWindowSharingNone — the same flag password managers use — so the OS omits the
window from any capture while it stays visible on the live screen. Hammerspoon
just writes the desired text to STATE_FILE; an empty/absent file hides it.
"""
from __future__ import annotations

import json
from pathlib import Path

import objc
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSAttributedString,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSMakeRect,
    NSScreen,
    NSView,
    NSWindow,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorIgnoresCycle,
    NSWindowCollectionBehaviorStationary,
    NSWindowSharingNone,
)
from Foundation import NSMakePoint, NSObject, NSString, NSTimer

STATE_FILE = Path("/tmp/whisper_overlay.json")

NS_WINDOW_STYLE_BORDERLESS = 0
NS_STATUS_WINDOW_LEVEL = 25  # NSStatusWindowLevel: above menus/most panels

STYLE_BORDERS = {
    "red":   (1.0, 0.2, 0.2),
    "green": (0.2, 0.85, 0.2),
    "gray":  (0.5, 0.5, 0.5),
}


class BannerView(NSView):
    """Draws the rounded-rect background, border, and text all in drawRect_."""

    def initWithText_style_(self, text, style):  # noqa: N802 - objc selector
        self = objc.super(BannerView, self).init()
        if self is None:
            return None
        self._text = text
        self._rgb = STYLE_BORDERS.get(style, STYLE_BORDERS["gray"])
        return self

    def drawRect_(self, rect):  # noqa: N802 - objc selector
        radius = 12.0
        inset = 1.5
        body = NSMakeRect(
            rect.origin.x + inset,
            rect.origin.y + inset,
            rect.size.width - 2 * inset,
            rect.size.height - 2 * inset,
        )
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(body, radius, radius)
        NSColor.colorWithCalibratedWhite_alpha_(0.08, 0.92).setFill()
        path.fill()
        r, g, b = self._rgb
        NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0).setStroke()
        path.setLineWidth_(3.0)
        path.stroke()

        # Draw text centred in the view.
        font = NSFont.boldSystemFontOfSize_(20.0)
        attrs = {
            NSFontAttributeName: font,
            NSForegroundColorAttributeName: NSColor.whiteColor(),
        }
        astr = NSAttributedString.alloc().initWithString_attributes_(self._text, attrs)
        ts = astr.size()
        tx = (rect.size.width - ts.width) / 2.0
        ty = (rect.size.height - ts.height) / 2.0
        astr.drawAtPoint_(NSMakePoint(tx, ty))


class Overlay(NSObject):
    def init(self):
        self = objc.super(Overlay, self).init()
        if self is None:
            return None
        self._window = None
        self._last = None
        return self

    @objc.python_method
    def _ensure_window(self):
        if self._window is not None:
            return
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 200, 56),
            NS_WINDOW_STYLE_BORDERLESS,
            NSBackingStoreBuffered,
            False,
        )
        win.setOpaque_(False)
        win.setBackgroundColor_(NSColor.clearColor())
        win.setLevel_(NS_STATUS_WINDOW_LEVEL)
        win.setIgnoresMouseEvents_(True)
        win.setHasShadow_(False)
        win.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorIgnoresCycle
        )
        # The point of this whole helper: omit the window from every screen capture.
        win.setSharingType_(NSWindowSharingNone)
        self._window = win

    @objc.python_method
    def _render(self, text, style):
        self._ensure_window()
        font = NSFont.boldSystemFontOfSize_(20.0)
        attrs = {NSFontAttributeName: font}
        size = NSString.stringWithString_(text or " ").sizeWithAttributes_(attrs)
        pad_x, pad_y = 28.0, 18.0
        w = float(size.width) + 2 * pad_x
        h = float(size.height) + 2 * pad_y

        view = BannerView.alloc().initWithText_style_(text, style)
        view.setFrame_(NSMakeRect(0, 0, w, h))

        self._window.setContentSize_((w, h))
        self._window.setContentView_(view)

        screen = NSScreen.mainScreen()
        if screen is not None:
            vf = screen.frame()
            x = vf.origin.x + (vf.size.width - w) / 2.0
            y = vf.origin.y + vf.size.height * 0.62
            self._window.setFrameOrigin_(NSMakePoint(x, y))
        self._window.orderFrontRegardless()

    @objc.python_method
    def _hide(self):
        if self._window is not None:
            self._window.orderOut_(None)

    def tick_(self, _timer):  # noqa: N802 - objc selector
        try:
            raw = STATE_FILE.read_text(encoding="utf-8")
        except FileNotFoundError:
            raw = ""
        except Exception:
            return
        if raw == self._last:
            return
        self._last = raw
        text, style = "", "gray"
        raw = raw.strip()
        if raw:
            try:
                payload = json.loads(raw)
                text = str(payload.get("text", "")).strip()
                style = str(payload.get("style", "gray"))
            except Exception:
                text = ""
        if text:
            self._render(text, style)
        else:
            self._hide()

    def start(self):
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.08, self, b"tick:", None, True
        )


def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    overlay = Overlay.alloc().init()
    overlay.start()
    app.run()


if __name__ == "__main__":
    main()
