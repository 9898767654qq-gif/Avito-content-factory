"""Small HTTP helpers for the Russian admin UI."""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote, unquote, urlencode

from fastapi.responses import RedirectResponse

FLASH = {
    "created": "Черновик собран из брифа. На Авито он ещё не уходил.",
    "validated": "Проверка пройдена. Можно отправлять на согласование.",
    "validation_failed": "Проверка остановила объявление. Исправьте замечания и соберите текст заново.",
    "submitted": "Объявление ждёт решения. Без согласования служба публикации его не возьмёт.",
    "approved": "Согласовано. Задача на публикацию стоит в очереди.",
    "rejected": "Версия отклонена и не будет опубликована.",
    "rework": "Собрана новая версия. Её снова нужно согласовать.",
    "regenerated": "Текст собран заново из брифа.",
    "title": "Заголовок заменён на выбранный вариант.",
    "text": "Текст сохранён и проверен ещё раз.",
    "photo": "Карточка фото добавлена. Пиксели не обрабатывались — это заглушка.",
    "main_photo": "Главное фото сменено.",
    "queued": "Задача снова стоит в очереди публикации.",
    "metrics_queued": "Задача на съём метрик отдана службе публикации.",
    "rework_queued": "Слабое объявление: собрана новая версия и она снова ждёт согласования.",
    "no_issue": "По этим цифрам правило доработки не срабатывает.",
    "step": "Служба публикации сделала один шаг.",
    "step_idle": "В очереди публикации пусто.",
    "actor_saved": "Имя записано и будет попадать в журнал.",
}

FLASH_ERROR = {
    "actor_required": "Укажите имя: оно нужно для журнала согласования.",
}


def safe_next(value: str | None) -> str:
    raw = (value or "/").strip()
    if raw.startswith("/") and not raw.startswith("//") and "\\" not in raw:
        return raw
    return "/"


def redirect(path: str, **params: str) -> RedirectResponse:
    clean = {key: value for key, value in params.items() if value}
    target = path
    if clean:
        separator = "&" if "?" in path else "?"
        target = path + separator + urlencode(clean)
    return RedirectResponse(target, status_code=303)


def set_actor(response: RedirectResponse, name: str) -> None:
    cleaned = " ".join(name.split())
    if not cleaned:
        return
    response.set_cookie(
        "approver_name",
        quote(cleaned[:200]),
        max_age=60 * 60 * 24 * 180,
        httponly=False,
        samesite="lax",
    )


def actor_from(request, form_value: str = "") -> str:
    typed = " ".join(str(form_value or "").split())
    if typed:
        return typed[:200]
    raw = request.cookies.get("approver_name", "")
    try:
        return unquote(raw).strip()[:200]
    except Exception:
        return ""


def format_price(value) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return "—"
    return f"{number:,}".replace(",", "\u00a0") + "\u00a0₽"


def format_dt(value: datetime | None) -> str:
    if not value:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
