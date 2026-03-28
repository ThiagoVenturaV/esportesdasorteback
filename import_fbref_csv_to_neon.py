"""
Importa o CSV FBref (temporada 2025/2026) para o Neon/PostgreSQL.

Uso:
  python import_fbref_csv_to_neon.py
  python import_fbref_csv_to_neon.py --csv-path players_data-2025_2026.csv --season 2025/2026
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from psycopg2.extras import execute_values

from db_neon import get_db_connection


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS tb_fbref_player_stats (
    id_fbref SERIAL PRIMARY KEY,
    season VARCHAR(20) NOT NULL,
    source_file VARCHAR(255),
    player VARCHAR(150) NOT NULL,
    nation VARCHAR(50),
    position VARCHAR(50),
    squad VARCHAR(150) NOT NULL,
    competition VARCHAR(150) NOT NULL,
    age NUMERIC(6, 2),
    born INTEGER,
    mp INTEGER,
    starts INTEGER,
    minutes INTEGER,
    goals INTEGER,
    assists INTEGER,
    yellow_cards INTEGER,
    red_cards INTEGER,
    raw_data JSONB NOT NULL,
    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_fbref_player_row UNIQUE (season, player, squad, competition, position, born)
);

CREATE INDEX IF NOT EXISTS idx_fbref_season ON tb_fbref_player_stats(season);
CREATE INDEX IF NOT EXISTS idx_fbref_player ON tb_fbref_player_stats(player);
CREATE INDEX IF NOT EXISTS idx_fbref_squad ON tb_fbref_player_stats(squad);
CREATE INDEX IF NOT EXISTS idx_fbref_competition ON tb_fbref_player_stats(competition);
"""


INSERT_SQL = """
INSERT INTO tb_fbref_player_stats (
    season, source_file, player, nation, position, squad, competition,
    age, born, mp, starts, minutes, goals, assists, yellow_cards, red_cards, raw_data
)
VALUES %s
ON CONFLICT (season, player, squad, competition, position, born)
DO UPDATE SET
    source_file = EXCLUDED.source_file,
    nation = EXCLUDED.nation,
    age = EXCLUDED.age,
    mp = EXCLUDED.mp,
    starts = EXCLUDED.starts,
    minutes = EXCLUDED.minutes,
    goals = EXCLUDED.goals,
    assists = EXCLUDED.assists,
    yellow_cards = EXCLUDED.yellow_cards,
    red_cards = EXCLUDED.red_cards,
    raw_data = EXCLUDED.raw_data,
    imported_at = CURRENT_TIMESTAMP;
"""


def to_int(value: Any) -> Optional[int]:
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace(",", "").replace("+", "")
    try:
        return int(float(text))
    except Exception:
        return None


def to_float(value: Any) -> Optional[float]:
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace(",", ".")
    try:
        return float(text)
    except Exception:
        return None


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def map_row(row: Dict[str, Any], season: str, source_file: str) -> Optional[Tuple[Any, ...]]:
    player = normalize_text(row.get("Player"))
    squad = normalize_text(row.get("Squad"))
    competition = normalize_text(row.get("Comp"))
    position = normalize_text(row.get("Pos"))

    if not player or not squad or not competition:
        return None

    raw_json = json.dumps(row, ensure_ascii=False)

    return (
        season,
        source_file,
        player,
        normalize_text(row.get("Nation")) or None,
        position or None,
        squad,
        competition,
        to_float(row.get("Age")),
        to_int(row.get("Born")),
        to_int(row.get("MP")),
        to_int(row.get("Starts")),
        to_int(row.get("Min")),
        to_int(row.get("Gls")),
        to_int(row.get("Ast")),
        to_int(row.get("CrdY")),
        to_int(row.get("CrdR")),
        raw_json,
    )


def chunked(items: List[Tuple[Any, ...]], size: int) -> Iterable[List[Tuple[Any, ...]]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def import_fbref_csv(csv_path: Path, season: str, batch_size: int = 500) -> int:
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV não encontrado: {csv_path}")

    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("CSV inválido: sem cabeçalho")

        payload: List[Tuple[Any, ...]] = []
        for row in reader:
            mapped = map_row(row, season=season, source_file=csv_path.name)
            if mapped:
                payload.append(mapped)

    if not payload:
        print("Nenhuma linha válida encontrada para importar.")
        return 0

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
            total = 0
            for batch in chunked(payload, batch_size):
                execute_values(cur, INSERT_SQL, batch)
                total += len(batch)
        conn.commit()
        return total
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Importa CSV FBref para Neon")
    parser.add_argument(
        "--csv-path",
        default="players_data-2025_2026.csv",
        help="Caminho do CSV FBref",
    )
    parser.add_argument(
        "--season",
        default="2025/2026",
        help="Rótulo da temporada para gravação no banco",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Tamanho do lote para inserção",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    imported = import_fbref_csv(
        csv_path=Path(args.csv_path),
        season=args.season,
        batch_size=max(1, int(args.batch_size)),
    )
    print(f"Importação concluída. Linhas processadas: {imported}")
