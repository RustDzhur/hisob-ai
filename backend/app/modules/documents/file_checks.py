"""Проверка загружаемых файлов (ТЗ §8): тип определяется по СОДЕРЖИМОМУ, а не по расширению или
заголовку клиента; базовые проверки активного содержимого.

Это не антивирус: полноценное сканирование (ClamAV и т.п.) — отдельная задача этапа 9.
"""

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePath

from PIL import Image, UnidentifiedImageError

from app.platform.errors import AppError

MAX_PIXELS = 50_000_000
MAX_ZIP_ENTRIES = 5000
MAX_ZIP_UNCOMPRESSED = 200 * 1024 * 1024
MAX_ZIP_RATIO = 200

# вид -> (content-type, допустимые расширения, показывать в браузере)
KINDS: dict[str, tuple[str, tuple[str, ...], bool]] = {
    "pdf": ("application/pdf", (".pdf",), True),
    "jpeg": ("image/jpeg", (".jpg", ".jpeg"), True),
    "png": ("image/png", (".png",), True),
    "xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        (".xlsx",),
        False,
    ),
    "csv": ("text/csv", (".csv",), False),
}
_PDF_ACTIVE = re.compile(rb"/(JavaScript|JS|Launch|EmbeddedFile|RichMedia)\b")


@dataclass(frozen=True)
class FileInfo:
    kind: str
    content_type: str
    inline: bool  # можно ли показывать прямо в браузере


def _err(status: int, code: str, message: str) -> AppError:
    return AppError(status, code, message, [{"field": "file", "code": code, "message": message}])


def safe_filename(name: str | None) -> str:
    """Имя только для показа: без путей и управляющих символов; в ключ хранилища не попадает."""
    base = PurePath((name or "").replace("\\", "/")).name
    base = "".join(ch for ch in base if ch.isprintable() and ch not in '<>:"|?*').strip(" .")
    return (base or "file")[:255]


def _sniff(data: bytes) -> str | None:
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"PK\x03\x04"):
        return "xlsx"
    return None


def _check_image(data: bytes, kind: str) -> None:
    try:
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            if width * height > MAX_PIXELS:
                raise _err(422, "file_unsafe", "Изображение слишком большого разрешения.")
            expected = "JPEG" if kind == "jpeg" else "PNG"
            if img.format != expected:
                raise _err(422, "file_type_mismatch", "Содержимое не соответствует типу файла.")
            img.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise _err(422, "file_corrupt", "Файл повреждён или не является изображением.") from exc


def _check_xlsx(data: bytes) -> None:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise _err(422, "file_corrupt", "Файл повреждён.") from exc
    with zf:
        infos = zf.infolist()
        names = {i.filename for i in infos}
        if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
            raise _err(422, "file_type_unsupported", "Это не рабочая книга XLSX.")
        if len(infos) > MAX_ZIP_ENTRIES:
            raise _err(422, "file_unsafe", "Слишком много вложенных частей в файле.")
        total = 0
        for i in infos:
            total += i.file_size
            if i.filename.startswith("/") or ".." in PurePath(i.filename).parts:
                raise _err(422, "file_unsafe", "Недопустимые пути внутри файла.")
            if i.flag_bits & 0x1:
                raise _err(422, "file_unsafe", "Зашифрованные книги не поддерживаются.")
            if "vbaproject" in i.filename.lower():
                raise _err(422, "file_unsafe", "Книги с макросами не принимаются.")
            if (
                i.file_size > 10 * 1024 * 1024
                and i.file_size / max(i.compress_size, 1) > MAX_ZIP_RATIO
            ):
                raise _err(422, "file_unsafe", "Подозрительная степень сжатия.")
        if total > MAX_ZIP_UNCOMPRESSED:
            raise _err(422, "file_unsafe", "Слишком большой распакованный размер.")


def _check_csv(data: bytes) -> None:
    if b"\x00" in data:
        raise _err(422, "file_corrupt", "Файл не похож на текстовый CSV.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = data.decode("cp1251")  # частая кодировка российских/узбекских выгрузок
        except UnicodeDecodeError as exc:
            raise _err(
                422, "file_corrupt", "Не удалось прочитать текст (нужна UTF-8 или Windows-1251)."
            ) from exc
    if any(ord(ch) < 32 and ch not in "\t\r\n" for ch in text):
        raise _err(422, "file_corrupt", "В файле есть управляющие символы.")


def inspect_upload(data: bytes, filename: str) -> FileInfo:
    if not data:
        raise _err(422, "file_empty", "Файл пустой.")
    ext = PurePath(filename.replace("\\", "/")).suffix.lower()
    kind = _sniff(data)
    if kind is None:
        # CSV не имеет сигнатуры: принимаем только по расширению и только текст
        if ext == ".csv":
            kind = "csv"
        else:
            raise _err(422, "file_type_unsupported", "Поддерживаются PDF, JPEG, PNG, XLSX и CSV.")
    _, extensions, _inline = KINDS[kind]
    if ext not in extensions:
        raise _err(422, "file_type_mismatch", "Расширение не соответствует содержимому файла.")

    if kind == "pdf":
        if _PDF_ACTIVE.search(data):
            raise _err(
                422, "file_unsafe", "PDF с активным содержимым (скрипты, вложения) не принимается."
            )
    elif kind in ("jpeg", "png"):
        _check_image(data, kind)
    elif kind == "xlsx":
        _check_xlsx(data)
    else:
        _check_csv(data)
    content_type, _ext, inline = KINDS[kind]
    return FileInfo(kind=kind, content_type=content_type, inline=inline)
