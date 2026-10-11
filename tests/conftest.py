import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from recon import extract


@pytest.fixture
def disagreeing_extraction(monkeypatch):
    original = extract.extract_pages

    def disagree(*args, **kwargs):
        pages = original(*args, **kwargs)
        second = pages[0].passes[0].model_copy(deep=True)
        second.rows.append(second.rows[-1].model_copy(deep=True))
        second.rows[-1].row_index = 15
        pages[0].passes.append(second)
        return pages

    monkeypatch.setattr(extract, "extract_pages", disagree)
