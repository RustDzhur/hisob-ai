"""Генераторы тестовых файлов (в репозитории нет бинарных образцов)."""

import io
import zipfile

from PIL import Image

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def pdf(n: int | str = 0) -> bytes:
    """Разные n — разное содержимое, а значит разный SHA-256."""
    return PDF + f"%unique-{n}\n".encode()


def png_bytes(size: tuple[int, int] = (4, 4), color: str = "white") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(buf, "JPEG")
    return buf.getvalue()


def xlsx_bytes(extra: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/workbook.xml", "<workbook/>")
        for k, v in (extra or {}).items():
            z.writestr(k, v)
    return buf.getvalue()
