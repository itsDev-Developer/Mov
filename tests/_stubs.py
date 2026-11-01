"""Shared test bootstrap: environment variables + stand-ins for Pyrogram, Mongo, httpx and aiohttp, so the tests run
without those libraries, a network or a database. Import it FIRST:   import _stubs"""
import importlib
import os
import sys
import types

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.update(API_ID="1", API_HASH="hash123", BOT_TOKEN="1:abcdef", MONGO_URI="mongodb://user:pw12345@x", OWNER_IDS="1", TMDB_KEY="")


def stub(name, **attrs):
    try:
        importlib.import_module(name)
    except Exception:
        m = types.ModuleType(name)
        m.__dict__.update(attrs)
        sys.modules[name] = m


class E(Exception):
    pass


class FloodWait(E):
    value = 1


class MessageNotModified(E):
    pass


class Btn:
    def __init__(self, text, callback_data=None, url=None, style=None, web_app=None):
        self.text, self.callback_data, self.url, self.web_app = text, callback_data, url, web_app


class Markup:
    def __init__(self, rows):
        self.rows = rows


class WebAppInfo:
    def __init__(self, url):
        self.url = url


class BotCommand:
    def __init__(self, command, description):
        self.command, self.description = command, description


class Scope:
    def __init__(self, chat_id=None):
        self.chat_id = chat_id


class _F:                                   # a Pyrogram filter: supports & | ~
    def __and__(s, o): return _F()
    __or__ = __rand__ = __and__
    def __invert__(s): return _F()


class _Filters:
    private = _F()
    def __getattr__(s, name): return lambda *a, **k: _F()


class Client:                               # plugin decorators just return the function
    @staticmethod
    def on_message(*a, **k): return lambda fn: fn
    on_callback_query = on_message


stub("pyrogram")
sys.modules["pyrogram"].Client, sys.modules["pyrogram"].filters = Client, _Filters()
sys.modules["pyrogram"].StopPropagation = type("StopPropagation", (Exception,), {})
stub("pyrogram.errors", FloodWait=FloodWait, MessageNotModified=MessageNotModified, RPCError=E, UserNotParticipant=E)
stub("pyrogram.enums", ParseMode=types.SimpleNamespace(HTML="html"), ChatMemberStatus=types.SimpleNamespace(OWNER=1, ADMINISTRATOR=2))
stub("pyrogram.types", InlineKeyboardButton=Btn, InlineKeyboardMarkup=Markup, WebAppInfo=WebAppInfo, BotCommand=BotCommand,
     BotCommandScopeDefault=Scope, BotCommandScopeChat=Scope, CallbackQuery=object, Message=object)
stub("pymongo", AsyncMongoClient=object)
stub("httpx", AsyncClient=object, Timeout=lambda *a, **k: None, Limits=lambda **k: None)
stub("aiohttp")
stub("aiohttp.web", Application=object, Request=object, Response=object, json_response=lambda *a, **k: None)


async def nosleep(*a, **k):
    return None
