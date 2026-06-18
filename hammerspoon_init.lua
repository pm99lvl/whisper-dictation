-- Whisper Dictation — Left Alt (hold) = RU, Right Alt (hold) = RU→EN

local PYTHON       = "/Users/a1/.pyenv/versions/3.11.11/bin/python3"
local DAEMON       = "/Users/a1/.whisper-dictation/dictate_daemon.py"
local OVERLAY      = "/Users/a1/.whisper-dictation/whisper_overlay.py"
local OVERLAY_FILE = "/tmp/whisper_overlay.json"
local LOG          = "/tmp/whisper_dictation.log"
local HS_LOG       = "/tmp/whisper_hammerspoon.log"
local SOCKET       = "/tmp/whisper_daemon.sock"
local PID_FILE     = "/tmp/whisper_daemon.pid"
local STATE_FILE   = "/tmp/whisper_dictation_state"
local RESULT_FILE  = "/tmp/whisper_result.txt"
local TRIGGER_FILE = "/tmp/whisper_paste.trigger"
local FOCUS_FILE   = "/tmp/whisper_focus_app.txt"
local LALT         = 58
local RALT         = 61

-- Status styles are now just colour names handed to the overlay helper.
local styleRed   = "red"
local styleGreen = "green"
local styleGray  = "gray"

local alertType = nil -- dictating | transcribing | translate_dictating | translate_transcribing | nil
local sessionStarted = false
local overlayClearTimer = nil

local function hsLog(message)
    local f = io.open(HS_LOG, "a")
    if not f then return end
    f:write(string.format("%.3f %s\n", hs.timer.secondsSinceEpoch(), message))
    f:close()
end

-- ── Screenshot-invisible status overlay ─────────────────────────────
-- whisper_overlay.py draws the banner in an NSWindowSharingNone window, so it is
-- omitted from every screen capture (macOS screenshot, Monosnap, the Claude app's
-- capture button, ScreenCaptureKit, …) while staying visible live. Hammerspoon
-- just publishes the desired text/colour to OVERLAY_FILE.
local function jsonEscape(s)
    return (s:gsub("\\", "\\\\"):gsub('"', '\\"'):gsub("\n", "\\n"):gsub("\r", "\\r"):gsub("\t", "\\t"))
end

local function overlayWrite(text, style)
    local f = io.open(OVERLAY_FILE, "w")
    if not f then return end
    f:write(string.format('{"text":"%s","style":"%s"}', jsonEscape(text or ""), style or "gray"))
    f:close()
end

local function overlayHide()
    if overlayClearTimer then overlayClearTimer:stop(); overlayClearTimer = nil end
    overlayWrite("", "gray")
end

-- seconds >= 90 means "persistent until closed" (matches old hs.alert 99s usage).
local function overlayShow(text, style, seconds)
    if overlayClearTimer then overlayClearTimer:stop(); overlayClearTimer = nil end
    overlayWrite(text, style)
    if seconds and seconds < 90 then
        overlayClearTimer = hs.timer.doAfter(seconds, overlayHide)
    end
end

local function isOverlayRunning()
    -- [w] bracket trick: the pattern must not match hs.execute's own shell wrapper,
    -- whose command line literally contains "whisper_overlay.py".
    local rc = hs.execute("pgrep -f '[w]hisper_overlay.py' >/dev/null 2>&1; echo $?")
    return rc and rc:match("^0") ~= nil
end

local function startOverlay()
    if isOverlayRunning() then return end
    hs.execute("PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin nohup " .. PYTHON .. " " .. OVERLAY .. " >/tmp/whisper_overlay_helper.log 2>&1 &")
end

local function closeAlert()
    overlayHide()
end

local function showAlert(kind, text, style, seconds)
    alertType = kind
    overlayShow(text, style, seconds)
end

local function sendCmd(cmd)
    hs.execute("printf '" .. cmd .. "' | nc -U " .. SOCKET .. " 2>/dev/null")
end

local function isRecording()
    return hs.fs.attributes(STATE_FILE) ~= nil
end

local function isDaemonAlive()
    if not hs.fs.attributes(SOCKET) then return false end
    local rc = hs.execute("printf ping | nc -U -w1 " .. SOCKET .. " >/dev/null 2>&1; echo $?")
    return rc and rc:match("^0") ~= nil
end

local function isDaemonProcessRunning()
    local f = io.open(PID_FILE, "r")
    if not f then return false end
    local pid = f:read("*a"):match("%d+")
    f:close()
    if not pid then return false end
    local rc = hs.execute("kill -0 " .. pid .. " 2>/dev/null; echo $?")
    return rc and rc:match("^0") ~= nil
end

local function killOldDaemons()
    if isDaemonProcessRunning() then return end
    local f = io.open(PID_FILE, "r")
    if f then
        local pid = f:read("*a"):match("%d+")
        f:close()
        if pid then hs.execute("kill -9 " .. pid .. " 2>/dev/null") end
    end
    hs.execute("pkill -9 -f 'dictate_daemon.py' 2>/dev/null")
    hs.execute("rm -f " .. SOCKET .. " " .. PID_FILE .. " " .. STATE_FILE)
end

local function startDaemon()
    if isDaemonAlive() then
        showAlert(nil, "  🎙  Whisper готов\n  Удержи Alt = запись  ", styleGreen, 3)
        return
    end
    if isDaemonProcessRunning() then
        showAlert(nil, "  ⏳  Модель грузится...  ", styleGray, 3)
        return
    end
    killOldDaemons()
    showAlert(nil, "  ⏳  Загружаю модель Whisper...  ", styleGray, 9)
    hs.execute("PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin " .. PYTHON .. " " .. DAEMON .. " >> " .. LOG .. " 2>&1 &")
    hs.timer.doAfter(9, function()
        if isDaemonAlive() then
            showAlert(nil, "  🎙  Whisper готов!\n  Удержи Alt = запись  ", styleGreen, 3)
        else
            showAlert(nil, "  ❌  Демон не запустился — проверь лог  ", styleRed, 5)
        end
    end)
end

local function daemonStatus()
    return hs.execute("printf status | nc -U -w1 " .. SOCKET .. " 2>/dev/null")
end

local function saveFocusApp()
    local app = hs.application.frontmostApplication()
    if app then
        local f = io.open(FOCUS_FILE, "w")
        if f then f:write(app:name()); f:close() end
    end
end

local function activateSavedFocusApp()
    local f = io.open(FOCUS_FILE, "r")
    if not f then return end
    local appName = f:read("*a"):match("^%s*(.-)%s*$")
    f:close()
    os.remove(FOCUS_FILE)
    if not appName or appName == "" then return end
    local app = hs.application.get(appName)
    if app and (not hs.application.frontmostApplication() or hs.application.frontmostApplication():name() ~= appName) then
        app:activate()
        hs.timer.usleep(80000)
    end
end

local function handleAlt(event, keyCode, startCmd, stopCmd, dictatingKind, transcribingKind, dictatingText, transcribingText)
    if event:getKeyCode() ~= keyCode then return false end
    local flags = event:getFlags()
    if flags.alt then
        sessionStarted = false
        if not isDaemonAlive() then startDaemon(); return false end
        saveFocusApp()
        hs.sound.getByName("Tink"):play()
        showAlert(dictatingKind, dictatingText, styleRed, 99)
        sendCmd(startCmd)
        sessionStarted = true
    else
        if not sessionStarted then return false end
        sessionStarted = false
        closeAlert()
        showAlert(transcribingKind, transcribingText, styleGreen, 4)
        sendCmd(stopCmd)
    end
    return false
end

whisperTap = hs.eventtap.new({hs.eventtap.event.types.flagsChanged}, function(event)
    return handleAlt(event, LALT, "start", "stop", "dictating", "transcribing", "  🎙  Диктую...  ", "  ⚙️  Транскрибирую...  ")
end)
whisperTap:start()

whisperTranslateTap = hs.eventtap.new({hs.eventtap.event.types.flagsChanged}, function(event)
    return handleAlt(event, RALT, "start_translate", "stop_translate", "translate_dictating", "translate_transcribing", "  🌐  Диктую → EN...  ", "  ⚙️  Перевожу на EN...  ")
end)
whisperTranslateTap:start()

-- Auto-hide only recording alerts. Do not kill transcribing/translation alerts early.
hs.timer.new(0.5, function()
    if (alertType == "dictating" or alertType == "translate_dictating") and not isRecording() then
        closeAlert()
        alertType = nil
    end
end):start()

whisperPasteWatcher = hs.pathwatcher.new("/private/tmp", function(paths)
    for _, p in ipairs(paths) do
        if p == TRIGGER_FILE or p == "/private/tmp/whisper_paste.trigger" then
            local seenAt = hs.timer.secondsSinceEpoch()
            hsLog("paste_trigger_seen path=" .. p)
            hs.timer.doAfter(0.01, function()
                if not hs.fs.attributes(TRIGGER_FILE) then
                    hsLog("paste_trigger_missing")
                    return
                end
                local f = io.open(RESULT_FILE, "r")
                if not f then
                    hsLog("paste_result_missing")
                    return
                end
                local text = f:read("*a")
                f:close()
                os.remove(TRIGGER_FILE)
                os.remove(RESULT_FILE)
                if text and #text > 0 then
                    local readAt = hs.timer.secondsSinceEpoch()
                    hs.pasteboard.setContents(text)
                    activateSavedFocusApp()
                    local focusAt = hs.timer.secondsSinceEpoch()
                    -- keycode 9 = physical V key on ANSI keyboards; layout-independent Cmd+V.
                    hs.eventtap.keyStroke({"cmd"}, 9)
                    local pasteAt = hs.timer.secondsSinceEpoch()
                    hsLog(string.format(
                        "paste_sent chars=%d trigger_to_read=%.3fs read_to_focus=%.3fs focus_to_paste=%.3fs total=%.3fs frontmost=%s",
                        #text,
                        readAt - seenAt,
                        focusAt - readAt,
                        pasteAt - focusAt,
                        pasteAt - seenAt,
                        hs.application.frontmostApplication() and hs.application.frontmostApplication():name() or "nil"
                    ))
                    alertType = nil
                end
            end)
        end
    end
end)
whisperPasteWatcher:start()

-- No more Cmd+Shift+3/4/5 interception: the overlay window is excluded from every
-- capture at the OS level (NSWindowSharingNone), and that hook never covered the
-- Claude app's capture button (which fires no key event) anyway.

-- Watchdog: keep daemon + overlay alive and refresh status snapshot.
daemonWatchdog = hs.timer.new(60, function()
    startOverlay()
    if not isDaemonAlive() and not isDaemonProcessRunning() then
        showAlert(nil, "  ⚠️  Whisper демон умер — перезапускаю...  ", styleGray, 3)
        startDaemon()
    else
        daemonStatus()
    end
end)
daemonWatchdog:start()

startOverlay()
startDaemon()
require("hs.ipc")
