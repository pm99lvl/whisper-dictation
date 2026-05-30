-- Whisper Dictation — Left Alt (удержать) = RU, Right Alt (удержать) = EN перевод

local PYTHON        = "/Users/a1/.pyenv/versions/3.11.11/bin/python3"
local DAEMON        = "/Users/a1/.whisper-dictation/dictate_daemon.py"
local LOG           = "/tmp/whisper_dictation.log"
local SOCKET        = "/tmp/whisper_daemon.sock"
local PID_FILE      = "/tmp/whisper_daemon.pid"
local STATE_FILE    = "/tmp/whisper_dictation_state"
local RESULT_FILE   = "/tmp/whisper_result.txt"
local TRIGGER_FILE  = "/tmp/whisper_paste.trigger"
local FOCUS_FILE    = "/tmp/whisper_focus_app.txt"
local LALT          = 58

local styleRed = {
    fillColor={red=0,green=0,blue=0,alpha=0.88},
    strokeColor={red=1,green=0.2,blue=0.2,alpha=1},
    strokeWidth=3, textColor={white=1,alpha=1},
    textSize=22, radius=12,
    fadeInDuration=0.08, fadeOutDuration=0.25,
}
local styleGreen = hs.fnutils.copy(styleRed)
styleGreen.strokeColor = {red=0.2,green=0.85,blue=0.2,alpha=1}
local styleGray = hs.fnutils.copy(styleRed)
styleGray.strokeColor = {red=0.5,green=0.5,blue=0.5,alpha=1}

local recAlertId     = nil
local sessionStarted = false
local LALT           = 58
local RALT           = 61

local function sendCmd(cmd)
    hs.execute("echo '" .. cmd .. "' | nc -U " .. SOCKET .. " 2>/dev/null")
end

local function isRecording()
    return hs.fs.attributes(STATE_FILE) ~= nil
end

local function isDaemonAlive()
    if not hs.fs.attributes(SOCKET) then return false end
    local ok = hs.execute("echo 'ping' | nc -U -w1 " .. SOCKET .. " 2>/dev/null; echo $?")
    return ok ~= nil
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
    hs.execute("PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin " ..
               PYTHON .. " " .. DAEMON .. " >> " .. LOG .. " 2>&1 &")
    hs.timer.doAfter(9, function()
        if isDaemonAlive() then
            hs.alert.show("  🎙  Whisper готов!\n  Удержи Alt = запись  ", styleGreen, 3)
        else
            hs.alert.show("  ❌  Демон не запустился — проверь лог  ", styleRed, 5)
        end
    end)
end

-- ── Общая функция начала/конца записи ─────────────────────────────
local function saveFocusApp()
    local focusApp = hs.application.frontmostApplication()
    if focusApp then
        local ff = io.open(FOCUS_FILE, "w")
        if ff then ff:write(focusApp:name()); ff:close() end
    end
end

-- ── Left Alt — диктовка на русском ────────────────────────────────
whisperTap = hs.eventtap.new({hs.eventtap.event.types.flagsChanged}, function(event)
    if event:getKeyCode() ~= LALT then return false end
    local flags = event:getFlags()

    if flags.alt then
        sessionStarted = false
        if not isDaemonAlive() then startDaemon(); return false end
        saveFocusApp()
        hs.sound.getByName("Tink"):play()
        recAlertId = hs.alert.show("  🎙  Диктую...  ", styleRed, 99)
        sendCmd("start")
        sessionStarted = true
    else
        if not sessionStarted then return false end
        sessionStarted = false
        if recAlertId then hs.alert.closeSpecific(recAlertId); recAlertId = nil end
        hs.alert.show("  ⚙️  Транскрибирую...  ", styleGreen, 4)
        sendCmd("stop")
    end
    return false
end)
whisperTap:start()

-- ── Right Alt — диктовка с переводом на английский ────────────────
whisperTranslateTap = hs.eventtap.new({hs.eventtap.event.types.flagsChanged}, function(event)
    if event:getKeyCode() ~= RALT then return false end
    local flags = event:getFlags()

    if flags.alt then
        sessionStarted = false
        if not isDaemonAlive() then startDaemon(); return false end
        saveFocusApp()
        hs.sound.getByName("Tink"):play()
        recAlertId = hs.alert.show("  🌐  Диктую → EN...  ", styleRed, 99)
        sendCmd("start_translate")
        sessionStarted = true
    else
        if not sessionStarted then return false end
        sessionStarted = false
        if recAlertId then hs.alert.closeSpecific(recAlertId); recAlertId = nil end
        hs.alert.show("  ⚙️  Перевожу на EN...  ", styleGreen, 4)
        sendCmd("stop_translate")
    end
    return false
end)
whisperTranslateTap:start()

-- Авто-убирать алерт если запись завершилась по тишине
hs.timer.new(0.5, function()
    if recAlertId and not isRecording() then
        hs.alert.closeSpecific(recAlertId)
        recAlertId = nil
    end
end):start()

-- Watcher: демон написал результат → Hammerspoon вставляет
whisperPasteWatcher = hs.pathwatcher.new("/private/tmp", function(paths)
    for _, p in ipairs(paths) do
        if p == TRIGGER_FILE or p == "/private/tmp/whisper_paste.trigger" then
            hs.timer.doAfter(0.05, function()
                if not hs.fs.attributes(TRIGGER_FILE) then return end
                local f = io.open(RESULT_FILE, "r")
                if not f then return end
                local text = f:read("*a")
                f:close()
                os.remove(TRIGGER_FILE)
                os.remove(RESULT_FILE)
                if text and #text > 0 then
                    hs.pasteboard.setContents(text)
                    local ff = io.open(FOCUS_FILE, "r")
                    if ff then
                        local appName = ff:read("*a"):match("^%s*(.-)%s*$")
                        ff:close()
                        os.remove(FOCUS_FILE)
                        if appName and appName ~= "" then
                            local app = hs.application.get(appName)
                            if app then app:activate() end
                            hs.timer.usleep(150000)
                        end
                    end
                    hs.eventtap.keyStroke({"cmd"}, "v")
                end
            end)
        end
    end
end)
whisperPasteWatcher:start()

-- Стартуем демон при загрузке Hammerspoon
startDaemon()

-- Enable IPC для диагностики
require("hs.ipc")
