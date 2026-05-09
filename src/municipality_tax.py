"""ZH municipality tax lookup. Pydantic-typed CSV rows."""
from __future__ import annotations
import csv, re, unicodedata
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict

DEFAULT_CSV_PATH = Path("/home/renny/doc/suisse/tax/tax.csv")

class TaxRow(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")
    municipality: str = Field(alias="COMMUNITY")
    bfsnr: int = Field(alias="BFSNR")
    year: int = Field(alias="YEAR")
    flag: str = Field(alias="FLAG")
    tax_rate: float = Field(alias="STF_O_KIRCHE1")  # mun_tax (no church)

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower().strip()
    return re.sub(r"\s+", " ", s).replace(".", "").replace("-", " ")

class MunicipalityTax:
    def __init__(self, csv_path: Path | str | None = None):
        self.path = Path(csv_path or DEFAULT_CSV_PATH)
        with self.path.open(encoding="utf-8") as f:
            rows = [TaxRow.model_validate(r) for r in csv.DictReader(f) if r.get("STF_O_KIRCHE1")]
        self.rows = {r.bfsnr: r for r in rows}
        self.by_name = {_norm(r.municipality): r for r in rows}

    def lookup(self, name: str) -> Optional[float]:
        r = self.by_name.get(_norm(name)); return r.tax_rate if r else None

    def lookup_from_address(self, address: str) -> Optional[float]:
        if not address: return None
        cands = []
        if (m := re.search(r"\b\d{4}\b", address)):
            cands.append(address[m.end():].strip(" ,").split(",")[0].strip())
        cands += [p.strip() for p in reversed(address.split(",")) if p.strip()]
        cands.append(address.split()[-1] if address.split() else "")
        return next((self.lookup(c) for c in cands if self.lookup(c) is not None), None)


