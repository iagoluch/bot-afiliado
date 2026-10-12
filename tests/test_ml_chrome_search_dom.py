from __future__ import annotations
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
JS=ROOT/"tests/js/test_ml_search_dom.js"
SOURCE=ROOT/"extensions/ml-chrome/background.js"

def test_search_dom_parses_known_cards_and_fallback_using_real_extension_function():
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    out=subprocess.run(
        ["node",str(JS)],capture_output=True,text=True,
        cwd=ROOT,timeout=15,
    )
    assert out.returncode==0, out.stderr
    assert "searchCards DOM tests OK" in out.stdout

def test_empty_search_reports_diagnostic_instead_of_zero_rejected():
    code=SOURCE.read_text(encoding="utf-8")
    assert "BUSCA_SEM_CONTEUDO" in code
    assert "BUSCA_SEM_LINKS_DE_PRODUTO" in code
    assert "BUSCA_SEM_TITULOS_DE_PRODUTO" in code
    assert "searchDiag.productLinks" in code
    assert "searchDiag.layoutCards" in code
    assert "for(let attempt=0;attempt<9;attempt++)" in code
    assert "await report(reason," in code
    assert 'if(generated?.status!=="LINK_GERADO")' in code
    assert "await native({action:" in code
