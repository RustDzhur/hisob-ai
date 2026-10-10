from app.platform.errors import AppError


def validate_password(password: str, *, email: str, min_length: int) -> None:
    """Минимальная политика; полноценная (словари утечек и т.п.) — отдельное решение."""
    problems: list[str] = []
    if len(password) < min_length:
        problems.append(f"Минимум {min_length} символов.")
    if len(password) > 128:
        problems.append("Максимум 128 символов.")
    if password.strip().lower() == email.strip().lower():
        problems.append("Пароль не должен совпадать с email.")
    if problems:
        raise AppError(
            422,
            "weak_password",
            "Пароль не соответствует требованиям.",
            [{"field": "password", "message": p} for p in problems],
        )
