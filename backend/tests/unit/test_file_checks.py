import io
import zipfile

import pytest

from app.modules.documents.file_checks import inspect_upload, safe_filename
from app.platform.errors import AppError
from tests.files import PDF, jpeg_bytes, png_bytes, xlsx_bytes


def code(data: bytes, name: str) -> str:
    with pytest.raises(AppError) as exc:
        inspect_upload(data, name)
    return exc.value.code


def test_valid_files_detected_by_content() -> None:
    assert inspect_upload(PDF, "Счёт.PDF").kind == "pdf"
    assert inspect_upload(png_bytes(), "scan.png").content_type == "image/png"
    assert inspect_upload(jpeg_bytes(), "photo.jpeg").kind == "jpeg"
    assert inspect_upload(xlsx_bytes(), "book.xlsx").inline is False
    assert inspect_upload("номер;сумма\n1;100\n".encode(), "export.csv").kind == "csv"
    assert inspect_upload("номер;сумма\n1;100\n".encode("cp1251"), "old.csv").kind == "csv"
    assert inspect_upload(PDF, "a.pdf").inline is True


def test_type_comes_from_content_not_extension() -> None:
    assert code(png_bytes(), "evil.pdf") == "file_type_mismatch"
    assert code(PDF, "photo.png") == "file_type_mismatch"
    assert code(b"MZ\x90\x00 this is an exe", "invoice.pdf") == "file_type_unsupported"
    assert code(b"MZ\x90\x00 this is an exe", "invoice.csv") == "file_corrupt"
    assert code(b"#!/bin/sh\necho hi\n", "run.sh") == "file_type_unsupported"
    assert code(b"<html><script>alert(1)</script></html>", "page.html") == "file_type_unsupported"


def test_empty_and_corrupt() -> None:
    assert code(b"", "a.pdf") == "file_empty"
    assert code(b"\x89PNG\r\n\x1a\nnot really a png", "a.png") == "file_corrupt"
    assert code(b"\xff\xd8\xff garbage", "a.jpg") == "file_corrupt"
    assert code(b"PK\x03\x04 broken zip", "a.xlsx") == "file_corrupt"
    assert code(b"a;b\x00c\n", "a.csv") == "file_corrupt"
    assert code(b"a;b\x07c\n", "a.csv") == "file_corrupt"


def test_pdf_active_content_rejected() -> None:
    for marker in (
        b"/JavaScript",
        b"/JS (app.alert(1))",
        b"/Launch",
        b"/EmbeddedFile",
        b"/RichMedia",
    ):
        assert code(PDF + marker + b"\n", "a.pdf") == "file_unsafe", marker
    assert (
        inspect_upload(PDF + b"/OpenAction\n", "a.pdf").kind == "pdf"
    )  # частый безобидный элемент


def test_xlsx_unsafe_content() -> None:
    assert code(xlsx_bytes({"xl/vbaProject.bin": b"x"}), "a.xlsx") == "file_unsafe"
    assert code(xlsx_bytes({"../evil.txt": b"x"}), "a.xlsx") == "file_unsafe"
    not_workbook = io.BytesIO()
    with zipfile.ZipFile(not_workbook, "w") as z:
        z.writestr("readme.txt", "x")
    assert code(not_workbook.getvalue(), "a.xlsx") == "file_type_unsupported"
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/workbook.xml", "<workbook/>")
        z.writestr("xl/big.xml", b"0" * (20 * 1024 * 1024))  # степень сжатия > 200
    assert code(bomb.getvalue(), "a.xlsx") == "file_unsafe"


def test_huge_image_dimensions_rejected() -> None:
    assert code(png_bytes((8000, 8000)), "big.png") == "file_unsafe"


def test_safe_filename() -> None:
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("C:\\Users\\x\\счёт №5.pdf") == "счёт №5.pdf"
    assert safe_filename("a<b>:c|d?.pdf") == "abcd.pdf"
    assert safe_filename("") == "file" and safe_filename(None) == "file"
    assert safe_filename("x" * 400 + ".pdf").__len__() == 255
    assert safe_filename("bad\x00name\n.pdf") == "badname.pdf"
