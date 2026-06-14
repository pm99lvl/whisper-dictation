-- Whisper Dictation — Left Alt (hold) = RU, Right Alt (hold) = RU→EN

local PYTHON       = "/Users/a1/.pyenv/versions/3.11.11/bin/python3"
local DAEMON       = "/Users/a1/.whisper-dictation/dictate_daemon.py"
local LOG          = "/tmp/whisper_dictation.log"
local SOCKET       = "/tmp/whisper_daemon.sock"
local PID_FILE     = "/tmp/whisper_daemon.pid"
local STATE_FILE   = "/tmp/whisper_dictation_state"
local RESULT_FILE  = "/tmp/whisper_result.txt"
local TRIGGER_FILE = "/tmp/whisper_paste.trigger"
local FOCUS_FILE   = "/tmp/whisper_focus_app.txt"
local LALT         = 58
local RALT         = 61

local styleRed = {
    fillColor={red=0,green=0,blue=0,alpha=0.88},
    strokeColor={red=1,green=0.2,blue=0.2,alpha=1},
    strokeWidth=3, textColor={white=1,alpha=1}, textSize=22, radius=12,
    fadeInDuration=0.08, fadeOutDuration=0.25,
}
local styleGreen = hs.fnutils.copy(styleRed)
styleGreen.strokeColor = {red=0.2,green=0.85,blue=0.2,alpha=1}
local styleGray = hs.fnutils.copy(styleRed)
styleGray.strokeColor = {red=0.5,green=0.5,blue=0.5,alpha=1}

local alertId = nil
local alertType = nil -- dictating | transcribing | translate_dictating | translate_transcribing | nil
local sessionStarted = false

local function closeAlert()
    if alertId then hs.alert.closeSpecific(alertId); alertId = nil end
end

local function showAlert(kind, text, style, seconds)
    closeAlert()
    alertType = kind
    alertId = hs.alert.show(text, style, seconds)
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
        hs.alert.show("  🎙  Whisper готов\n  Удержи Alt = запись  ", styleGreen, 3)
        return
    end
    if isDaemonProcessRunning() then
        hs.alert.show("  ⏳  Модель грузится...  ", styleGray, 3)
        return
    end
    killOldDaemons()
    hs.alert.show("  ⏳  Загружаю модель Whisper...  ", styleGray, 9)
    hs.execute("PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin " .. PYTHON .. " " .. DAEMON .. " >> " .. LOG .. " 2>&1 &")
    hs.timer.doAfter(9, function()
        if isDaemonAlive() then
            hs.alert.show("  🎙  Whisper готов!\n  Удержи Alt = запись  ", styleGreen, 3)
        else
            hs.alert.show("  ❌  Демон не запустился — проверь лог  ", styleRed, 5)
        end
    end)
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
    if alertId and (alertType == "dictating" or alertType == "translate_dictating") and not isRecording() then
        closeAlert()
        alertType = nil
    end
end):start()

whisperPasteWatcher = hs.pathwatcher.new("/private/tmp", function(paths)
    for _, p in ipairs(paths) do
        if p == TRIGGER_FILE or p == "/private/tmp/whisper_paste.trigger" then
            hs.timer.doAfter(0.01, function()
                if not hs.fs.attributes(TRIGGER_FILE) then return end
                local f = io.open(RESULT_FILE, "r")
                if not f then return end
                local text = f:read("*a")
                f:close()
                os.remove(TRIGGER_FILE)
                os.remove(RESULT_FILE)
                if text and #text > 0 then
                    hs.pasteboard.setContents(text)
                    activateSavedFocusApp()
                    -- keycode 9 = physical V key on ANSI keyboards; layout-independent Cmd+V.
                    hs.eventtap.keyStroke({"cmd"}, 9)
                    alertType = nil
                end
            end)
        end
    end
end)
whisperPasteWatcher:start()

local screenshotKeys = {[20]=true, [21]=true, [23]=true} -- Cmd+Shift+3/4/5
screenshotTap = hs.eventtap.new({hs.eventtap.event.types.keyDown}, function(event)
    local flags = event:getFlags()
    if not screenshotKeys[event:getKeyCode()] or not (flags.cmd and flags.shift) then return false end
    local savedType = alertType
    closeAlert()
    hs.alert.closeAll()
    hs.timer.doAfter(0.7, function()
        if savedType == "dictating" and isRecording() then
            showAlert("dictating", "  🎙  Диктую...  ", styleRed, 99)
        elseif savedType == "translate_dictating" and isRecording() then
            showAlert("translate_dictating", "  🌐  Диктую → EN...  ", styleRed, 99)
        elseif savedType == "transcribing" then
            showAlert("transcribing", "  ⚙️  Транскрибирую...  ", styleGreen, 4)
        elseif savedType == "translate_transcribing" then
            showAlert("translate_transcribing", "  ⚙️  Перевожу на EN...  ", styleGreen, 4)
        end
    end)
    return false
end)
screenshotTap:start()

startDaemon()
require("hs.ipc")
