"""Ошибки слоя совместимости."""


class AiogramMaxError(Exception):
    """Базовая ошибка библиотеки."""


class UnsupportedByMax(AiogramMaxError):
    """Метод Telegram Bot API не имеет аналога в MAX.

    Поднимается в строгом режиме. В мягком — метод логируется и превращается
    в no-op: см. ``UnsupportedPolicy``.
    """

    def __init__(self, method_name: str) -> None:
        super().__init__(
            f"{method_name} не поддерживается MAX Bot API. "
            "Уберите вызов или переключите MaxSession в режим LENIENT."
        )
        self.method_name = method_name


class MaxApiError(AiogramMaxError):
    """MAX вернул не-2xx ответ."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"MAX API вернул {status}: {body[:500]}")
        self.status = status
        self.body = body
