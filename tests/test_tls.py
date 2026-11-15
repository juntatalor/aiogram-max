"""TLS для нового домена MAX: сертификаты Минцифры в доверенных."""

import logging
import ssl

import httpx
import pytest

from aiogram_max.session import MAX_API_URL_V2, MaxSession
from aiogram_max.tls import mincifry_context


def test_mincifry_context_trusts_russian_root_and_sub_ca() -> None:
    """Контекст доверяет обоим сертификатам Минцифры — по субъекту, не по количеству."""
    subjects = [str(cert["subject"]) for cert in mincifry_context().get_ca_certs()]
    assert any("Russian Trusted Root CA" in s for s in subjects)
    assert any("Russian Trusted Sub CA" in s for s in subjects)


def test_mincifry_context_keeps_regular_cas() -> None:
    """Обычные центры сертификации никуда не делись: старый домен и прочий HTTPS живы."""
    regular = ssl.create_default_context()
    assert (
        mincifry_context().cert_store_stats()["x509_ca"]
        > regular.cert_store_stats()["x509_ca"]
    )


def test_session_with_trust_mincifry_uses_the_context() -> None:
    """trust_mincifry=True — клиент собран на нашем контексте, а не на дефолтном.

    Заглядываем во внутренности httpx сознательно: публичного способа узнать
    SSL-контекст клиента нет, а проверять это поведение живым TLS в тестах
    нельзя. При смене мажорной версии httpx тест сломается — и это правильно:
    место, где хранится контекст, важно пересогласовать руками.
    """
    session = MaxSession("token", trust_mincifry=True)
    pool = session._client._transport._pool  # type: ignore[attr-defined]
    assert pool._ssl_context is mincifry_context()


def test_session_without_flag_keeps_default_verification() -> None:
    """Без флага — дефолтный траст-стор httpx, Минцифры не подмешаны."""
    session = MaxSession("token")
    # Публичного способа узнать SSL-контекст у httpx нет — см. тест выше
    # про внутренности; здесь важно ровно то, что контекст НЕ наш.
    pool = session._client._transport._pool  # type: ignore[attr-defined]
    assert pool._ssl_context is not mincifry_context()


def test_v2_domain_without_flag_warns(caplog: pytest.LogCaptureFixture) -> None:
    """Новый домен без Минцифры — предупреждение с причиной грядущего отказа."""
    with caplog.at_level(logging.WARNING, logger="aiogram_max.session"):
        MaxSession("token", api_url=MAX_API_URL_V2)
    assert "Минцифры" in caplog.text


async def test_custom_client_with_flag_warns_and_keeps_client(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Свой клиент важнее флага: чужой TLS не перекраиваем, но и молчать нельзя."""
    client = httpx.AsyncClient()
    try:
        with caplog.at_level(logging.WARNING, logger="aiogram_max.session"):
            session = MaxSession("token", trust_mincifry=True, client=client)
        assert session._client is client
        assert "trust_mincifry" in caplog.text
    finally:
        await client.aclose()
