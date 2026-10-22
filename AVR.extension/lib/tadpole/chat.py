# -*- coding: utf-8 -*-
"""The run's summary to a Google Chat space, and the whole report as text on the team's share.

THE REPORT TO THE CHAT (user, 2026-10-19: "я хочу аби звіт відправлявся мені в чат, з приписом назви файлу, часу
генерації, та ім'ям користувача (збігається з назвою ПК)"). The whole report runs to thousands of lines and a Chat
message holds about 4000 characters, so the chat gets the summary - the file, the time, the computer, the mix of the
file against the settings, and after a run laid again from the window, which sections and why.

THE WHOLE REPORT AS TEXT (user, 2026-10-19: "пропоную туди надсилати txt файл, названий за прізвищем виконавця,
датою та часом генерації і назвою файлу"): a Chat webhook takes no attachments, so the report window's text goes to
the team's share (`REPORT_DIR`) as <computer>_<date>_<time>_<model>.txt and the message carries its path. The
computer's name is the person's surname on this team's machines.

THE WEBHOOK STAYS ON THE MACHINE: whoever holds its address can write to the space, so it is read from
%APPDATA%\\Tadpole\\chat_webhook.txt and kept out of the extension (the repository, the share on D). A machine
without the file sends nothing - silently, that is not a fault.

Cosmetic to the run: every call is guarded, the request times out in 10 s, and a send that fails says why in the
report and changes nothing in the model.
"""

import os
import time

REPORT_DIR = u'\\\\10.10.120.241\\RnD\\01_Generative Design\\Projects\\Tadpole\\crap txt'
TIMEOUT_MS = 10000
LIMIT = 3900                    # characters; Google Chat holds a little over 4000 in one message
BAD_CHARS = u'<>:"/\\|?*'       # not allowed in a Windows file name


def webhook_path():
    base = os.environ.get('APPDATA') or os.path.expanduser('~')
    return os.path.join(base, 'Tadpole', 'chat_webhook.txt')


def webhook():
    """The space's webhook address, or None where this machine has none."""
    try:
        import io
        with io.open(webhook_path(), encoding='utf-8-sig') as handle:
            for line in handle:
                line = line.strip()
                if line.startswith('https://chat.googleapis.com/'):
                    return line
    except Exception:
        pass
    return None


def computer():
    try:
        from System import Environment
        return Environment.MachineName
    except Exception:
        return os.environ.get('COMPUTERNAME') or u'?'


def _safe(text):
    return u''.join(u'_' if ch in BAD_CHARS or ord(ch) < 32 else ch for ch in u'{0}'.format(text)).strip()


def txt_name(doc_title, when=None):
    """KORDYIAKA_2026-10-19_10-05_<the model's name>.txt"""
    stamp = time.strftime('%Y-%m-%d_%H-%M', time.localtime(when or time.time()))
    return u'{0}_{1}_{2}.txt'.format(_safe(computer()), stamp, _safe(doc_title or u'model'))


def console_text(console):
    """The report window's text as it reads (tables tab-separated), or None."""
    try:
        return console.renderer.Document.Body.InnerText
    except Exception:
        return None


def save_report(console, doc_title, doc_path=None, folder=None):
    """The report window's text to `folder` (the share). Returns (path, None) or (None, why)."""
    body = console_text(console)
    if not body:
        return None, u'the report window could not be read'
    folder = folder or REPORT_DIR
    path = os.path.join(folder, txt_name(doc_title))
    head = u'\n'.join([u'Tadpole', u'file: {0}'.format(doc_title or u'?'), u'path: {0}'.format(doc_path or u'-'),
                       u'time: {0}'.format(time.strftime('%Y-%m-%d %H:%M:%S')),
                       u'computer: {0}'.format(computer()), u'', u''])
    try:
        import io
        with io.open(path, 'w', encoding='utf-8-sig', newline=u'\r\n') as handle:
            handle.write(head + body.replace(u'\r\n', u'\n'))
        return path, None
    except Exception as error:
        return None, u'{0}'.format(error)[:200]


def _shares(counts):
    total = float(sum((counts or {}).get(k, 0) for k in (1, 2, 3)))
    if not total:
        return u'-'
    return u' · '.join(u'{0} {1:.1f} %'.format(name, 100.0 * counts.get(k, 0) / total)
                       for k, name in ((1, u'1к'), (2, u'2к'), (3, u'3к')))


def _minutes(seconds):
    seconds = int(round(seconds or 0))
    return u'{0} хв {1:02d} с'.format(seconds // 60, seconds % 60) if seconds >= 60 else u'{0} с'.format(seconds)


def _section_key(section):
    try:
        return (0, float(section), u'')
    except (TypeError, ValueError):
        return (1, 0.0, u'{0}'.format(section))


def summary(doc_title, started, counts, dead=None, floors=None, brief=None, max_1k=False, in_band=None,
            rerun=None, txt=None, txt_why=None):
    """The message: `counts` {1: n, 2: n, 3: n} over the whole file (weighted by floors); `in_band` (ok, all) - the
    flats inside their area bands, over every floor of the sections laid (user, 2026-10-19: "не робочі поверхи в
    діапазоні площ, а квартири в діапазоні площ"); `rerun` dict(only, fresh, flips, laid) for a run laid again from the window; `txt` the
    path of the whole report on the share (or `txt_why` it could not be saved)."""
    lines = [u'*Tadpole* · {0}'.format(doc_title or u'?'),
             u'{0} · {1}'.format(time.strftime('%Y-%m-%d %H:%M'), computer())]
    if rerun:
        fresh = set(rerun.get('fresh') or ())
        flips = set(rerun.get('flips') or ())
        laid = set(rerun.get('laid') or ())
        parts = []
        for section in sorted(rerun.get('only') or (), key=_section_key):
            why = []
            if section in fresh:
                why.append(u'нова геометрія')
            if section in flips and section not in laid:
                why.append(u'сходова на інший бік')
            elif section in laid and section not in flips:
                why.append(u'сходова назад')
            parts.append(u'{0} ({1})'.format(section, u', '.join(why) or u'-'))
        lines.append(u'_Перегенеровано секції:_ {0}'.format(u'; '.join(parts)))
    total = sum((counts or {}).get(k, 0) for k in (1, 2, 3))
    head = u'Квартир: {0}'.format(total)
    if floors:
        head += u' на {0} поверхах'.format(floors)
    if dead is not None:
        head += u' · dead space {0}'.format(dead)
    lines.append(head)
    mix = _shares(counts)
    if max_1k:
        mix += u'  (налаштування: максимум 1к)'
    elif brief:
        mix += u'  (налаштування: {0})'.format(u' · '.join(
            u'{0} {1:.0f} %'.format(name, 100.0 * brief.get(k, 0)) for k, name in ((1, u'1к'), (2, u'2к'), (3, u'3к'))))
    lines.append(mix)
    if in_band:
        ok, all_ = in_band
        if all_:
            lines.append(u'Квартир у діапазоні площ: {0} з {1} ({2:.0f} %)'.format(ok, all_, 100.0 * ok / all_))
    lines.append(u'Час прогону: {0}'.format(_minutes(time.time() - started)))
    if txt:
        lines.append(u'Звіт: {0}'.format(txt))
    elif txt_why:
        lines.append(u'_Звіт у теку не збережено: {0}_'.format(txt_why))
    text = u'\n'.join(lines)
    return text if len(text) <= LIMIT else text[:LIMIT - 1] + u'…'


def send(text, url=None):
    """POST the message to the space. Returns (ok, why) - why is None when it went, the reason when it did not."""
    url = url or webhook()
    if not url:
        return False, u'no webhook on this machine ({0})'.format(webhook_path())
    try:
        import json
        from System import Text
        from System.Net import WebRequest, ServicePointManager, SecurityProtocolType
        try:
            ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12
        except Exception:
            pass
        # ensure_ascii=False: IronPython's json cannot escape Cyrillic ("'ascii' codec can't encode") - the text
        # stays unicode and .NET turns it into UTF-8
        body = Text.Encoding.UTF8.GetBytes(json.dumps({'text': text}, ensure_ascii=False))
        request = WebRequest.Create(url)
        request.Method = 'POST'
        request.ContentType = 'application/json; charset=UTF-8'
        request.Timeout = TIMEOUT_MS
        request.ContentLength = body.Length
        stream = request.GetRequestStream()
        try:
            stream.Write(body, 0, body.Length)
        finally:
            stream.Close()
        response = request.GetResponse()
        try:
            code = int(response.StatusCode)
        finally:
            response.Close()
        return (True, None) if 200 <= code < 300 else (False, u'HTTP {0}'.format(code))
    except Exception as error:
        return False, u'{0}'.format(error)[:200]
