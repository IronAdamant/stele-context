"""PDF bytes are preserved from disk through the chunker."""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock, patch

import pytest

from stele_context.chunkers.base import Chunk
from stele_context.chunkers.pdf import HAS_PYMUPDF, PDFChunker
from stele_context.engine import Stele
from stele_context.engine_utils import read_and_hash


_PDF_BYTES = b"%PDF-1.4\n%\xff\xfe not-utf8\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


class TestReadAndHashPdf:
    def test_pdf_modality_returns_raw_bytes(self, tmp_path):
        path = tmp_path / "note.pdf"
        path.write_bytes(_PDF_BYTES)

        content, digest = read_and_hash(path, "pdf")

        assert content == _PDF_BYTES
        assert isinstance(content, bytes)
        assert digest == hashlib.sha256(_PDF_BYTES).hexdigest()

    def test_text_modality_still_returns_str(self, tmp_path):
        path = tmp_path / "note.txt"
        path.write_text("hello", encoding="utf-8")

        content, digest = read_and_hash(path, "text")

        assert content == "hello"
        assert digest == hashlib.sha256(b"hello").hexdigest()

    def test_index_passes_pdf_bytes_to_the_chunker(self, tmp_path):
        """The crash was fitz.open() receiving a decoded string as a path."""
        path = tmp_path / "note.pdf"
        path.write_bytes(_PDF_BYTES)
        seen: dict[str, object] = {}

        class RecordingChunker:
            def supported_extensions(self) -> list[str]:
                return [".pdf"]

            def chunk(self, content, document_path, **_kwargs):
                seen["content"] = content
                seen["path"] = document_path
                return [
                    Chunk(
                        content="extracted text",
                        modality="pdf",
                        start_pos=0,
                        end_pos=1,
                        document_path=document_path,
                        chunk_index=0,
                    )
                ]

        engine = Stele(storage_dir=str(tmp_path / "storage"))
        engine.chunkers["pdf"] = RecordingChunker()
        result = engine.index_documents([str(path)])

        assert result["total_chunks"] == 1
        assert seen["content"] == _PDF_BYTES
        assert isinstance(seen["content"], bytes)


class TestPDFChunkerOpensBytes:
    def _make(self):
        chunker = PDFChunker.__new__(PDFChunker)
        chunker.chunk_size = 256
        chunker.max_chunk_size = 4096
        chunker.pages_per_chunk = 1
        return chunker

    def _doc(self):
        doc = MagicMock()
        doc.metadata = {}
        doc.page_count = 0
        return doc

    def test_bytes_open_as_a_stream(self):
        chunker = self._make()
        with patch("stele_context.chunkers.pdf.fitz") as fake:
            fake.open.return_value = self._doc()
            chunker.chunk(b"%PDF-1.4", "note.pdf")
        fake.open.assert_called_once_with(stream=b"%PDF-1.4", filetype="pdf")

    def test_decoded_text_is_not_opened_as_a_path(self):
        chunker = self._make()
        mangled = "%PDF-1.4\n" + ("\ufffd" * 32)
        with patch("stele_context.chunkers.pdf.fitz") as fake:
            with pytest.raises(ValueError, match="raw bytes"):
                chunker.chunk(mangled, "note.pdf")
        fake.open.assert_not_called()

    def test_existing_file_path_still_opens_as_a_path(self, tmp_path):
        pdf = tmp_path / "note.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        chunker = self._make()
        with patch("stele_context.chunkers.pdf.fitz") as fake:
            fake.open.return_value = self._doc()
            chunker.chunk(str(pdf), "note.pdf")
        fake.open.assert_called_once_with(str(pdf))


@pytest.mark.skipif(not HAS_PYMUPDF, reason="pymupdf not installed")
class TestPDFIndexWithPyMuPDF:
    def test_real_pdf_indexes_without_path_crash(self, tmp_path):
        import pymupdf

        pdf = tmp_path / "hello.pdf"
        document = pymupdf.open()
        page = document.new_page()
        page.insert_text((72, 72), "Hello PDF bytes")
        document.save(pdf)
        document.close()

        engine = Stele(storage_dir=str(tmp_path / "storage"))
        result = engine.index_documents([str(pdf)])

        assert result["total_chunks"] >= 1
        indexed = result["indexed"][0]
        assert indexed["modality"] == "pdf"
        context = engine.get_context([str(pdf)])
        chunks = context["unchanged"][0]["chunks"]
        assert any("Hello PDF bytes" in chunk["content"] for chunk in chunks)
