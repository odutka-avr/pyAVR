# -*- coding: utf-8 -*-
"""Tadpole — the waiter: takes the order to the kitchen and brings the dish back.

The layout is decided on the server (`kitchen/server.py`); the button collects, studies the sun, sends the order
(`tadpole.order`), and places what comes back (`tadpole.placing`). One run is one SESSION on the server: the dish
first, then - once the first group of each section stands in the model - the recipes of the other unique floors
(6d), from the same session, so the flats they name are the flats of the dish (`tadpole.wire`).

WHERE THE KITCHEN IS STAYS ON THE MACHINE, as the chat's webhook does (`chat.py`): whoever holds the token can use
the server, so it is read from %APPDATA%\\Tadpole\\kitchen.txt and kept out of the extension -

    url=https://kitchen.example:8077
    token=...

Plain .NET HTTP, as `chat.send`: IronPython has no requests. The bodies go gzip both ways - an order of a large
model is megabytes of numbers.
"""

import json
import os

TIMEOUT_MS = 60 * 60 * 1000             # a large file lays out in minutes, and the mix alone may take 5 of them


class KitchenError(Exception):
    """The kitchen could not be reached, or answered with an error - the text says which."""


def settings_path():
    base = os.environ.get('APPDATA') or os.path.expanduser('~')
    return os.path.join(base, 'Tadpole', 'kitchen.txt')


def settings():
    """(url, token) from kitchen.txt, or (None, None) where this machine has none."""
    url = token = None
    try:
        import io
        with io.open(settings_path(), encoding='utf-8-sig') as handle:
            for line in handle:
                key, _eq, value = line.strip().partition(u'=')
                if key.strip().lower() == u'url':
                    url = value.strip().rstrip(u'/')
                elif key.strip().lower() == u'token':
                    token = value.strip()
    except (IOError, OSError):
        pass
    return url, token


class Kitchen(object):
    """The server, as one run sees it."""

    def __init__(self, url=None, token=None):
        if url is None:
            url, token = settings()
        if not url or not token:
            raise KitchenError(u'no kitchen on this machine - {0} needs url= and token='.format(settings_path()))
        self.url = url
        self.token = token
        self.session = None

    def lay(self, order):
        """The dish for `order`, as it came over the wire - the session stays open for `floors`."""
        got = self._call('POST', u'/sessions', order)
        self.session = got['session']
        return got['dish']

    def floors(self, working_levels, placed):
        """The recipes of the other unique floors: {section: [recipe]}, as they came over the wire.
        `placed` - the numbers (`wire.Decoder.number_of`) of the plans with a roof in the model."""
        got = self._call('POST', u'/sessions/{0}/floors'.format(self.session),
                         dict(working_levels=[[k, v] for k, v in working_levels.items()], placed=placed))
        return got['recipes']

    def close(self):
        """Let the server drop the session. Never raises - the server drops it after a while anyway."""
        if self.session is None:
            return
        try:
            self._call('DELETE', u'/sessions/{0}'.format(self.session))
        except Exception:
            pass
        self.session = None

    def _call(self, method, path, data=None):
        from System import Text
        from System.IO import MemoryStream, StreamReader
        from System.IO.Compression import CompressionMode, GZipStream
        from System.Net import (DecompressionMethods, HttpWebRequest, ServicePointManager, SecurityProtocolType,
                                WebException, WebRequest)
        try:
            ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12
        except Exception:
            pass
        request = WebRequest.Create(self.url + path)
        request.Method = method
        request.Timeout = TIMEOUT_MS
        request.ReadWriteTimeout = TIMEOUT_MS
        request.Headers.Add('Authorization', 'Bearer ' + self.token)
        if isinstance(request, HttpWebRequest):
            request.AutomaticDecompression = DecompressionMethods.GZip
        if data is not None:
            # ensure_ascii=False: IronPython's json cannot escape Cyrillic (`chat.send`)
            raw = Text.Encoding.UTF8.GetBytes(json.dumps(data, ensure_ascii=False))
            packed = MemoryStream()
            gz = GZipStream(packed, CompressionMode.Compress, True)
            gz.Write(raw, 0, raw.Length)
            gz.Close()
            body = packed.ToArray()
            request.ContentType = 'application/json; charset=UTF-8'
            request.Headers.Add('Content-Encoding', 'gzip')
            request.ContentLength = body.Length
            stream = request.GetRequestStream()
            try:
                stream.Write(body, 0, body.Length)
            finally:
                stream.Close()
        try:
            response = request.GetResponse()
        except WebException as error:
            raise KitchenError(_why(error))
        try:
            reader = StreamReader(response.GetResponseStream(), Text.Encoding.UTF8)
            text = reader.ReadToEnd()
        finally:
            response.Close()
        return json.loads(text) if text else None


def _why(error):
    """What the server said, or why it could not be asked."""
    response = getattr(error, 'Response', None)
    if response is None:
        return u'the kitchen could not be reached: {0}'.format(error.Message)
    try:
        from System import Text
        from System.IO import StreamReader
        text = StreamReader(response.GetResponseStream(), Text.Encoding.UTF8).ReadToEnd()
        code = int(response.StatusCode)
    except Exception:
        text, code = u'', u'?'
    finally:
        response.Close()
    try:
        text = json.loads(text).get('detail') or text
    except Exception:
        pass
    return u'the kitchen answered {0}: {1}'.format(code, u'{0}'.format(text)[-2000:])
