"""validate_file_upload() dependency behaviors (specs/04 §3 errors + §6).

Covers the four error paths the B3 acceptance criteria require, at the
dependency level: guest 403, over-size 413, wrong-extension .exe->csv 415,
MIME mismatch 415, and the new explicit 0-byte 400. A valid file passes.

Drives the async dependency with asyncio.run like the rest of the suite.
"""

import asyncio
import io

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from app.middlewares.file_validator import validate_file_upload

FREE_LIMIT = 3 * 1024 * 1024


def run(coro):
    return asyncio.run(coro)


class FakeUser:
    def __init__(self, plan="free", auth_provider="email"):
        self.plan = plan
        self.auth_provider = auth_provider


def make_file(filename, content, content_type):
    upload = UploadFile(filename=filename, file=io.BytesIO(content))
    upload.headers = Headers({"content-type": content_type})
    return upload


def upload(filename, content, content_type, user=None):
    return validate_file_upload(
        file=make_file(filename, content, content_type),
        user=user or FakeUser(),
    )


class TestRejections:
    def test_guest_rejected_403_before_bytes(self):
        content = b"a,b\n1,2\n"
        with pytest.raises(HTTPException) as exc:
            run(
                upload(
                    "sales.csv",
                    content,
                    "text/csv",
                    user=FakeUser(auth_provider="guest"),
                )
            )
        assert exc.value.status_code == 403
        assert "Guest" in str(exc.value.detail)

    def test_invalid_plan_rejected_403(self):
        with pytest.raises(HTTPException) as exc:
            run(
                upload(
                    "sales.csv",
                    b"a,b\n1,2\n",
                    "text/csv",
                    user=FakeUser(plan="mega"),
                )
            )
        assert exc.value.status_code == 403

    def test_wrong_extension_rejected_415(self):
        # .exe renamed to .csv: extension check passes, content-type catches it.
        with pytest.raises(HTTPException) as exc:
            run(upload("evil.exe", b"a,b\n", "text/csv"))
        assert exc.value.status_code == 415

    def test_mime_mismatch_rejected_415(self):
        # .csv extension with a non-allowed content-type is rejected by design.
        with pytest.raises(HTTPException) as exc:
            run(upload("data.csv", b"a,b\n", "application/pdf"))
        assert exc.value.status_code == 415

    def test_free_user_over_limit_413(self):
        content = b"x" * (FREE_LIMIT + 1)
        with pytest.raises(HTTPException) as exc:
            run(upload("big.csv", content, "text/csv", user=FakeUser(plan="free")))
        assert exc.value.status_code == 413
        assert "too large" in str(exc.value.detail).lower()

    def test_pro_user_15mb_rejected_413(self):
        # 15MB > pro cap of 10MB (specs/04 §6 second checkbox).
        content = b"x" * (15 * 1024 * 1024)
        with pytest.raises(HTTPException) as exc:
            run(upload("big.csv", content, "text/csv", user=FakeUser(plan="pro")))
        assert exc.value.status_code == 413

    def test_zero_byte_rejected_400(self):
        with pytest.raises(HTTPException) as exc:
            run(upload("empty.csv", b"", "text/csv"))
        assert exc.value.status_code == 400
        assert "empty" in str(exc.value.detail).lower()


class TestAccepted:
    def test_valid_csv_passes_and_returns_file(self):
        f = make_file("sales.csv", b"a,b\n1,2\n", "text/csv")
        result = run(validate_file_upload(file=f, user=FakeUser()))
        assert result is f

    def test_pro_under_limit_passes(self):
        f = make_file("ok.csv", b"a,b\n1,2\n", "text/csv")
        result = run(validate_file_upload(file=f, user=FakeUser(plan="pro")))
        assert result is f