"""Ошибки слоя совместимости."""


class AiogramMaxError(Exception):
    """Базовая ошибка библиотеки."""


class UnsupportedByMax(AiogramMaxError):
    """У метода Telegram нет аналога в MAX — и не появится с нашей стороны.

    Поднимается в строгом режиме. В режиме WARN метод логируется и
    превращается в no-op: см. ``UnsupportedPolicy``.
    """

    def __init__(self, method_name: str) -> None:
        super().__init__(
            f"{method_name} не поддерживается MAX Bot API. "
            "Уберите вызов или переключите MaxSession в режим WARN."
        )
        self.method_name = method_name


class NotImplementedYet(AiogramMaxError):
    """Аналог в MAX есть, но мы его ещё не написали.

    Отличать это от ``UnsupportedByMax`` важно: первое — предложение прислать
    PR, второе — свойство самой платформы, и PR тут не поможет.
    """

    def __init__(self, method_name: str, hint: str = "") -> None:
        super().__init__(
            f"{method_name}: в MAX аналог есть, в aiogram-max ещё не написан. "
            f"PR welcome! {hint}".strip()
        )
        self.method_name = method_name


class MaxApiError(AiogramMaxError):
    """MAX вернул не-2xx ответ."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"MAX API вернул {status}: {body[:500]}")
        self.status = status
        self.body = body
