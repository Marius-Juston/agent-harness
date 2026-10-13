# ruff: noqa: S101
import pytest
from textread import read_text_any

SAMPLES = {
    "utf-8": "Les élèves étudient la géométrie à l'école, déjà très tôt.",
    "utf-16": "Die Schüler lernen Größen und Maße in der Schule.",
    "cp1251": "Ученики изучают геометрию в школе с восьми лет.",
    "shift_jis": "生徒たちは学校で数学と科学を勉強しています。",
}


@pytest.mark.parametrize("enc", sorted(SAMPLES))
def test_decodes(tmp_path, enc):
    p = tmp_path / "f.txt"
    p.write_bytes(SAMPLES[enc].encode(enc))
    assert read_text_any(p) == SAMPLES[enc]
