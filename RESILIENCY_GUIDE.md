# Whisper Dictation Resiliency Guide

Практический playbook по эксплуатации, диагностике и безопасному развитию проекта `whisper-dictation`.

Цель документа: если диктовка снова “тупит”, “теряет фразы”, “не вставляет текст”, “зависает перевод” или “умирает демон” — не гадать, а быстро определить слой отказа и выполнить правильное действие.

## 1. Архитектура в одну минуту

```text
Alt key in Hammerspoon
        ↓
~/.hammerspoon/init.lua
        ↓ Unix socket commands
/tmp/whisper_daemon.sock
        ↓
dictate_daemon.py
        ↓
MLX Whisper transcription
        ↓
optional Google Translate RU→EN
        ↓
/tmp/whisper_result.txt + /tmp/whisper_paste.trigger
        ↓
Hammerspoon path watcher
        ↓
clipboard + Cmd+V into active app
```

Текущий активный runtime:

- daemon: `/Users/a1/.whisper-dictation/dictate_daemon.py`
- Hammerspoon config: `/Users/a1/.hammerspoon/init.lua`
- repository config source: `/Users/a1/.whisper-dictation/hammerspoon_init.lua`
- log: `/tmp/whisper_dictation.log`
- status: `/tmp/whisper_status.json`
- socket: `/tmp/whisper_daemon.sock`

## 2. Главный принцип диагностики

Никогда не начинаем с “поменять модель” или “поменять timeout”. Сначала определяем слой:

1. **Hammerspoon** — сработала ли клавиша, watcher, вставка?
2. **Socket** — отвечает ли daemon?
3. **Daemon state** — recording/transcribing/queue/error?
4. **Audio** — записалась ли речь, не была ли тишина?
5. **Whisper** — сколько заняла транскрипция?
6. **Translate** — сколько занял RU→EN перевод?
7. **Handoff** — записан ли результат?
8. **Paste** — попал ли текст в активное окно?

## 3. Быстрая проверка здоровья

```bash
cd /Users/a1/.whisper-dictation

# 1. Должен быть ровно один daemon
pgrep -fl '/Users/a1/.whisper-dictation/dictate_daemon.py'

# 2. Socket должен отвечать pong
printf ping | nc -U -w1 /tmp/whisper_daemon.sock

# 3. Status должен быть валидным JSON
printf status | nc -U -w1 /tmp/whisper_daemon.sock | python3 -m json.tool

# 4. Persisted status тоже должен быть валидным JSON
python3 -m json.tool /tmp/whisper_status.json

# 5. Hammerspoon hooks должны быть живы
hs -c 'return daemonWatchdog ~= nil and "daemonWatchdog OK" or "daemonWatchdog NIL"'
hs -c 'return whisperTap ~= nil and "whisperTap OK" or "whisperTap NIL"'
hs -c 'return whisperTranslateTap ~= nil and "translateTap OK" or "translateTap NIL"'
```

Ожидаемое состояние в покое:

```json
{
  "ok": true,
  "state": "done" или "idle",
  "recording": false,
  "transcribing": false,
  "queue_size": 0
}
```

## 4. Как читать status

Команда:

```bash
printf status | nc -U -w1 /tmp/whisper_daemon.sock | python3 -m json.tool
```

Ключевые поля:

| Поле | Значение |
| --- | --- |
| `state` | состояние state machine: `idle`, `recording`, `queued`, `transcribing`, `translating`, `ready_to_paste`, `done`, `error` |
| `recording` | прямо сейчас идёт запись с микрофона |
| `transcribing` | прямо сейчас работает Whisper/перевод |
| `queue_size` | сколько записей ждёт обработки |
| `session_id` | ID последней/текущей сессии |
| `record_seconds` | длительность записи |
| `error` | последняя ошибка, если state=`error` |

## 5. Как читать логи

```bash
tail -120 /tmp/whisper_dictation.log
```

Важные маркеры:

```text
🧾 event=ready
```
Daemon поднялся и слушает socket.

```text
🧾 event=recording_started session_id=...
```
Hammerspoon отправил start, daemon начал запись.

```text
⏹ Stop by hotkey
```
Alt отпущен, daemon остановил запись по hotkey.

```text
🔇 Silence stop
```
Daemon остановил запись по длинной тишине.

```text
📥 Queued transcription
```
Запись поставлена в очередь обработки.

```text
✂️ audio_compact: 12.40s → 4.10s
```
Тишина внутри записи была сжата перед Whisper. Это хорошо.

```text
⏱ transcribe: 1.30s
⏱ transcribe_ru: 2.80s
```
Скорость локального Whisper.

```text
⏱ translate_ru_en: 1.10s
```
Скорость сетевого перевода.

```text
⏱ handoff_to_hammerspoon: 0.00s
```
Передача результата Hammerspoon. Если тут долго — проблема в файловом handoff.

```text
🧾 event=result_ready
```
Daemon закончил и отдал текст на вставку.

## 6. Симптомы → причины → действия

### Симптом: диктовка вообще не реагирует

Проверить:

```bash
hs -c 'return whisperTap ~= nil and "OK" or "NIL"'
printf ping | nc -U -w1 /tmp/whisper_daemon.sock
pgrep -fl dictate_daemon.py
```

Вероятные причины:

- Hammerspoon не перезагрузился;
- IPC Hammerspoon умер;
- daemon не поднят;
- socket stale.

Действия:

```bash
hs -c 'hs.reload()'
# если daemon не отвечает:
kill $(pgrep -f '/Users/a1/.whisper-dictation/dictate_daemon.py') 2>/dev/null || true
cd /Users/a1/.whisper-dictation
exec env PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin /Users/a1/.pyenv/versions/3.11.11/bin/python3 dictate_daemon.py >> /tmp/whisper_dictation.log 2>&1
```

### Симптом: текст распознался, но не вставился

Проверить:

```bash
tail -80 /tmp/whisper_dictation.log | grep -E 'result_ready|handoff|paste|trigger'
ls -la /tmp/whisper_result.txt /tmp/whisper_paste.trigger 2>/dev/null
hs -c 'return whisperPasteWatcher ~= nil and "watcher OK" or "watcher NIL"'
```

Вероятные причины:

- Hammerspoon watcher не активен;
- активное приложение потеряло focus;
- clipboard/Cmd+V не дошёл;
- trigger/result файл завис.

Действия:

```bash
hs -c 'hs.reload()'
```

Если текст важный — сначала посмотреть `/tmp/whisper_result.txt`, чтобы не потерять.

### Симптом: перевод сильно дольше обычного

Проверить:

```bash
tail -120 /tmp/whisper_dictation.log | grep -E 'transcribe_ru|translate_ru_en|job_total|error|timeout'
```

Интерпретация:

- `transcribe_ru` большой → Whisper/аудио/тишина.
- `translate_ru_en` большой → сеть/Google Translate.
- `job_total` большой, но transcribe/translate нормальные → очередь или handoff.

Дальнейшая работа:

- добавить timeout/retry/fallback для `GoogleTranslator`;
- при timeout вставлять русский текст или копировать его в clipboard;
- логировать `translate_timeout`.

### Симптом: длинная диктовка обрывается

Проверить:

```bash
grep -E 'Silence stop|Stop by hotkey|recording_started|queued' /tmp/whisper_dictation.log | tail -40
```

Если видим `Silence stop`, значит сработал автостоп по тишине.

Текущий режим:

- hold Alt → запись;
- release Alt → мгновенный stop;
- `SILENCE_AFTER = 10.0` — аварийный автостоп.

Если всё равно обрывает — смотреть RMS/микрофон и логи `No speech`.

### Симптом: система “тупит” после нескольких попыток подряд

Проверить:

```bash
pgrep -fl '/Users/a1/.whisper-dictation/dictate_daemon.py'
printf status | nc -U -w1 /tmp/whisper_daemon.sock | python3 -m json.tool
tail -120 /tmp/whisper_dictation.log | grep -E 'queue|Still transcribing|recording next phrase|job_total'
```

Если daemon больше одного — это регрессия. Single-instance guard должен предотвращать это.

Если `queue_size > 0`, значит предыдущие записи ещё обрабатываются.

## 7. Safe restart procedure

Использовать, когда нужно применить новый daemon-код.

```bash
cd /Users/a1/.whisper-dictation

# Проверить, что код компилируется
/Users/a1/.pyenv/versions/3.11.11/bin/python3 -m py_compile dictation_runtime.py dictate_daemon.py

# Остановить старый daemon
old=$(pgrep -f '/Users/a1/.whisper-dictation/dictate_daemon.py' | head -1)
[ -n "$old" ] && kill "$old"
sleep 2

# Запустить новый daemon
exec env PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin \
  /Users/a1/.pyenv/versions/3.11.11/bin/python3 \
  /Users/a1/.whisper-dictation/dictate_daemon.py \
  >> /tmp/whisper_dictation.log 2>&1
```

В обычной работе это делает Hammerspoon/watchdog, но для ручной отладки лучше явно видеть шаги.

## 8. Safe Hammerspoon apply procedure

```bash
cp /Users/a1/.whisper-dictation/hammerspoon_init.lua /Users/a1/.hammerspoon/init.lua
hs -c 'hs.reload()'
sleep 5
hs -c 'return daemonWatchdog ~= nil and "daemonWatchdog OK" or "daemonWatchdog NIL"'
hs -c 'return whisperTap ~= nil and "whisperTap OK" or "whisperTap NIL"'
hs -c 'return whisperTranslateTap ~= nil and "translateTap OK" or "translateTap NIL"'
```

Если `hs.reload()` временно пишет `message port was invalidated`, это нормально во время reload. Через несколько секунд IPC должен восстановиться.

## 9. Тесты перед изменениями

```bash
cd /Users/a1/.whisper-dictation
/Users/a1/.pyenv/versions/3.11.11/bin/python3 -m unittest -v test_runtime.py
/Users/a1/.pyenv/versions/3.11.11/bin/python3 -m py_compile dictation_runtime.py dictate_daemon.py dictate.py dictate_standalone.py
hs -c 'local f, err = loadfile("/Users/a1/.whisper-dictation/hammerspoon_init.lua"); return f and "loadfile OK" or tostring(err)'
git diff --check
```

## 10. Что ещё нужно сделать для полной отказоустойчивости

### 10.1 Session-scoped result handoff

Сейчас всё ещё используется общий файл:

```text
/tmp/whisper_result.txt
/tmp/whisper_paste.trigger
```

Нужно перейти на:

```text
/tmp/whisper_result_<session_id>.json
/tmp/whisper_result_<session_id>.trigger
```

Это уберёт риск перетирания результата при очереди.

### 10.2 Ack от Hammerspoon

Нужны события:

```text
paste_started
paste_done
paste_failed
```

И ack-файл:

```text
/tmp/whisper_ack_<session_id>.json
```

Тогда daemon будет знать, что текст реально обработан.

### 10.3 Translation timeout/retry/fallback

Для Google Translate нужен guard:

- timeout, например 4 секунды;
- retry 1 раз;
- fallback: если перевод не удался — вставить русский текст или оставить его в clipboard;
- лог: `translate_timeout`, `translate_retry`, `translate_fallback`.

### 10.4 Два режима диктовки

Один режим не идеален для всех случаев.

Нужно разделить:

- **Hold mode** — длинная диктовка, пока держишь Alt.
- **Fast auto mode** — короткие команды, автостоп по тишине 0.8–1.2с.

### 10.5 Self-test command

Добавить socket-команду:

```text
self_test
```

Она должна проверять:

- socket writable;
- status writable;
- temp dir writable;
- model loaded;
- translator available;
- Hammerspoon watcher alive, если возможно.

## 11. Правила разработки

1. Не менять одновременно модель, timeout, Hammerspoon и protocol — иначе невозможно понять причину.
2. Перед каждым runtime-изменением запускать тесты и compile-check.
3. Каждый новый reliability helper должен иметь unittest.
4. После перезапуска всегда проверять:
   - daemon один;
   - `ping -> pong`;
   - `status -> JSON`;
   - Hammerspoon taps/watchdog живы.
5. Если пользователь говорит “тупит”, сначала читать status + последние 120 строк лога, потом менять код.

## 12. Быстрый rescue checklist

```bash
cd /Users/a1/.whisper-dictation

echo '--- daemon processes ---'
pgrep -fl '/Users/a1/.whisper-dictation/dictate_daemon.py' || true

echo '--- ping ---'
printf ping | nc -U -w1 /tmp/whisper_daemon.sock || true

echo '--- status ---'
printf status | nc -U -w1 /tmp/whisper_daemon.sock | python3 -m json.tool || true

echo '--- hammerspoon ---'
hs -c 'return daemonWatchdog ~= nil and "daemonWatchdog OK" or "daemonWatchdog NIL"' 2>&1
hs -c 'return whisperTap ~= nil and "whisperTap OK" or "whisperTap NIL"' 2>&1
hs -c 'return whisperTranslateTap ~= nil and "translateTap OK" or "translateTap NIL"' 2>&1

echo '--- recent log markers ---'
tail -160 /tmp/whisper_dictation.log | grep -E '🧾|⏱|✂️|Queued|Error|error|No speech|InputStream|Still transcribing|result_ready|ready|shutdown' || true
```
