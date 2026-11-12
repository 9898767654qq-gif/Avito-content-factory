"""Russian labels for statuses, tasks, and the approval journal."""

from __future__ import annotations

STATUS_LABELS = {
    "draft": "Черновик",
    "validated": "Проверено",
    "pending_approval": "Ждёт согласования",
    "approved": "Согласовано",
    "publishing": "Публикуется",
    "published": "Опубликовано",
    "rejected": "Отклонено",
    "needs_rework": "На доработку",
}

TASK_LABELS = {
    "create_listing": "Публикация объявления",
    "fetch_avito_metrics": "Снять метрики",
    "rework_listing": "Новая версия по доработке",
}

TASK_STATUS_LABELS = {
    "pending": "В очереди",
    "processing": "В работе",
    "done": "Готово",
    "failed": "Ошибка",
}

AUDIT_LABELS = {
    "generate": "Собран текст",
    "validate": "Проверка",
    "edit": "Текст поправлен",
    "submit": "Отправлено на согласование",
    "approve": "Согласовано",
    "reject": "Отклонено",
    "request_rework": "Возврат на доработку",
    "publish_started": "Публикация начата",
    "publish_succeeded": "Служба публикации закончила",
    "publish_failed": "Публикация не удалась",
}

PHOTO_KIND_LABELS = {
    "real": "Живое фото",
    "synthetic": "Синтетический рендер",
}

SOURCE_LABELS = {
    "template": "Шаблон по брифу",
    "llm": "Модель, сверено с брифом",
}


def status_label(status: str, *, dry_run: bool = False) -> str:
    if status == "published" and dry_run:
        return "Опубликовано в прогоне"
    return STATUS_LABELS.get(status, status)
