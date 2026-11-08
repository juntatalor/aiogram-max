"""TLS для нового домена MAX: сертификаты Минцифры в доверенных.

С 19 июля 2026 документация MAX требует ходить на ``platform-api2.max.ru``,
и его цепочка заканчивается корневым сертификатом Минцифры, которого нет ни
в certifi, ни в большинстве образов. Без него соединение падает
``CERTIFICATE_VERIFY_FAILED`` — на вид «библиотека сломалась», хотя сломан
траст-стор.

Сертификаты лежат в пакете (``aiogram_max/certs/``), чтобы потребитель не
занимался их скачиванием сам. Источник и отпечатки — в README; там же дата,
когда промежуточный сертификат придётся обновить (он короче корневого).
"""

import ssl
from functools import lru_cache
from importlib import resources

import certifi

# Имена файлов в aiogram_max/certs/. Префикс russian_trusted_* совпадает
# с официальными адресами gu-st.ru — так файл в пакете ищется в источнике
# за секунды, без гадания по содержимому.
_ROOT_CA = "russian_trusted_root_ca.pem"
_SUB_CA = "russian_trusted_sub_ca.pem"


@lru_cache(maxsize=1)
def mincifry_context() -> ssl.SSLContext:
    """SSL-контекст: доверенные центры httpx (certifi) плюс Минцифры.

    certifi остаётся основой сознательно: на старый домен, редиректы и
    вложения по чужим ссылкам сертификаты Минцифры не нужны, а утрата
    обычных центров ломала бы всё остальное. ``lru_cache`` — контекст
    один на процесс, его создание не бесплатное, а сессий может быть
    несколько (мультиботовые сценарии).

    Именно контекстом, а не ``verify=True`` у отдельного клиента: httpx
    под капотом собирает его сам, и путь «добавить Минцифры к certifi»
    иначе, чем через собственный контекст, не выражается.
    """
    ctx = ssl.create_default_context(cafile=certifi.where())
    for name in (_ROOT_CA, _SUB_CA):
        # cadata, а не cafile: файлы читаются через importlib.resources,
        # который умеет пакеты внутри zip — путь до файла там не всегда
        # существует как строка.
        pem = (
            resources.files("aiogram_max.certs")
            .joinpath(name)
            .read_text(encoding="ascii")
        )
        ctx.load_verify_locations(cadata=pem)
    return ctx
