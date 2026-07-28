"""Трансляция методов aiogram в вызовы MAX, разложенная по областям.

Каждый модуль — миксин с методами одной темы; ``MaxSession`` наследует их
все. Разделение чисто организационное: до него session.py разросся до
тысячи строк, и найти в нём нужный метод стало трудно.
"""

from aiogram_max.methods.chats import ChatsMixin
from aiogram_max.methods.media import MediaMixin
from aiogram_max.methods.settings import SettingsMixin

__all__ = ["ChatsMixin", "MediaMixin", "SettingsMixin"]
