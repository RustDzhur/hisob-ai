from app.platform.errors import AppError


def validate_password(
    password: str, *, email: str, min_length: int, field: str = "password"
) -> None:
    """Минимальная политика; полноценная (словари утечек и т.п.) — отдельное решение."""
    problems: list[tuple[str, str, dict[str, int]]] = []
    if len(password) < min_length:
        problems.append(
            ("password_too_short", f"Минимум {min_length} символов.", {"min": min_length})
        )
    if len(password) > 128:
        problems.append(("password_too_long", "Максимум 128 символов.", {"max": 128}))
    if password.strip().lower() == email.strip().lower():
        problems.append(("password_equals_email", "Пароль не должен совпадать с email.", {}))
    if problems:
        raise AppError(
            422,
            "weak_password",
            "Пароль не соответствует требованиям.",
            [
                {"field": field, "code": code, "message": msg, "params": params}
                for code, msg, params in problems
            ],
        )
