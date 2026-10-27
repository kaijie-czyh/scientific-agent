"""Production PDF bytes must preserve evidence, conditions and scientific units."""
import shutil
import subprocess

import pytest
from fastapi import HTTPException


@pytest.fixture
def pdf_api(monkeypatch):
    monkeypatch.setenv("SRA_WEB_SKIP_GUARD", "1")
    from web import api

    return api


def extract_pdf(response, tmp_path):
    executable = shutil.which("pdftotext")
    if not executable:
        pytest.skip("Poppler pdftotext is required for actual PDF text verification")
    target = tmp_path / "delivery.pdf"
    target.write_bytes(response.body)
    result = subprocess.run([executable, "-enc", "UTF-8", str(target), "-"],
                            capture_output=True, check=True, timeout=30)
    return result.stdout.decode("utf-8")


def test_pdf_safety_keeps_complete_text(pdf_api):
    text = "引文和条件必须完整保留。" * 100 + " NOT_MEASURED_AT_300K"
    rendered = pdf_api._pdf_safe_text(text)
    assert rendered.endswith("NOT_MEASURED_AT_300K")
    assert "".join(rendered.split()) == "".join(text.split())


def test_actual_pdf_keeps_long_evidence_conditions_code_and_tables(pdf_api, tmp_path):
    markdown = (
        "# 科研方案交付检查\n\n"
        "> " + "source evidence " * 30 + "SOURCE_NOT_MEASURED_AT_300K\n\n"
        "- " + "condition supplied by user " * 20 + "USER_CONDITION_UNCONFIRMED\n\n"
        + "plain paragraph " * 30 + "PARAGRAPH_END\n\n"
        "```\n" + "long_code_" * 35 + "CODE_END\n```\n\n"
        "| " + "long header " * 20 + "HEADER_END | source |\n"
        "|---|---|\n"
        "| " + "source detail " * 40 + "TABLE_END | 未验证 |\n"
    )
    response = pdf_api._md_to_pdf_response(markdown, "delivery")
    assert response.media_type == "application/pdf" and response.body.startswith(b"%PDF")
    text = "".join(extract_pdf(response, tmp_path).split())
    for marker in ["SOURCE_NOT_MEASURED_AT_300K", "USER_CONDITION_UNCONFIRMED",
                   "PARAGRAPH_END", "CODE_END", "HEADER_END", "TABLE_END"]:
        assert marker in text


def test_actual_pdf_retains_scientific_unit_glyphs_and_bold_table_headers(pdf_api, tmp_path, caplog):
    if not pdf_api.os.path.isfile("C:/Windows/Fonts/segoeui.ttf"):
        pytest.skip("The supported Windows scientific fallback font is unavailable")
    markdown = (
        "# 材料性质与比较条件\n\n"
        "> 实测电导率 1.2 mS cm⁻¹；计算值 1.5×10⁻³ S/cm。\n\n"
        "- Li₆PS₅Cl；温度 −25 °C；原文线索，不能认定可比。\n\n"
        "| 电导率 S cm⁻¹ | 温度 °C |\n|---|---|\n| 1.2×10⁻³ | −25 |\n"
    )
    response = pdf_api._md_to_pdf_response(markdown, "scientific")
    text = "".join(extract_pdf(response, tmp_path).split())
    for value in ["1.2mScm⁻¹", "1.5×10⁻³S/cm", "Li₆PS₅Cl", "−25°C", "电导率Scm⁻¹"]:
        assert value in text
    assert "missing the following glyphs" not in caplog.text


def test_pdf_reports_oversized_table_without_silently_truncating_it(pdf_api):
    markdown = "| source |\n|---|\n| " + "too long " * 4000 + " SOURCE_TAIL |\n"
    with pytest.raises(HTTPException) as error:
        pdf_api._md_to_pdf_response(markdown, "too-large")
    assert error.value.status_code == 422
    assert "未截断" in error.value.detail and "DOCX" in error.value.detail
