"""CSV exports of the auditable records."""

from __future__ import annotations

import csv
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .. import paths
from ..db import Database
from ..portfolio.ledger import Ledger
from ..portfolio.tax import compute_disposals, summary_by_tax_year

TABLES = {
    "transactions": "SELECT * FROM transactions WHERE account_id=? ORDER BY trade_date, id",
    "orders": "SELECT * FROM orders WHERE account_id=? ORDER BY created_at",
    "order_events": "SELECT e.* FROM order_events e JOIN orders o ON o.id=e.order_id WHERE o.account_id=? ORDER BY e.id",
    "fills": "SELECT * FROM fills WHERE account_id=? ORDER BY trade_date, ts",
    "equity_snapshots": "SELECT * FROM equity_snapshots WHERE account_id=? ORDER BY date",
    "signals": "SELECT * FROM signals WHERE account_id=? ORDER BY as_of_date, id",
}


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        if not rows:
            fh.write("")
            return
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def export_account(db: Database, account_id: int, label: str = "export", account_type: str = "gia") -> dict:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = paths.EXPORT_DIR / f"{label}-{stamp}"
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for name, sql in TABLES.items():
        rows = [dict(r) for r in db.query(sql, (account_id,))]
        write_csv(folder / f"{name}.csv", rows)
        written.append(f"{name}.csv")
    txs = Ledger(db).transactions(account_id)
    disposals = compute_disposals(txs)
    write_csv(folder / "tax_disposals_ESTIMATE.csv",
              [{**asdict(d), "date": d.date.isoformat(), "gain_gbp": round(d.gain_gbp, 2)} for d in disposals])
    write_csv(folder / "tax_year_summary_ESTIMATE.csv",
              summary_by_tax_year(disposals, [t for t in txs if t["type"] == "DIVIDEND"], account_type))
    (folder / "README.txt").write_text(
        "Exported from the local trading bot (SIMULATED paper account unless stated otherwise).\n"
        "Files marked ESTIMATE are record-keeping aids, not tax calculations.\n", encoding="utf-8")
    written += ["tax_disposals_ESTIMATE.csv", "tax_year_summary_ESTIMATE.csv"]
    return {"folder": str(folder), "files": written}
