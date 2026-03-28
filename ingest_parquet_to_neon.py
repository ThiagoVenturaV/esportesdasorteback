import os
import json
import pandas as pd
from db_neon import get_db_connection

PARQUET_DIR = os.getenv(
    "PARQUET_DIR",
    r"c:\Users\thzin\Desktop\EDScript_\archive\out_parquet_optimized",
)

def load_matches_to_neon(limit_files=5):
    """
    Lê os arquivos de partidas (matches_*.parquet) do StatsBomb
    Insere na tabela tb_partida_historico no Neon Postgres.
    """
    print(f"Buscando arquivos parquet em: {PARQUET_DIR}")

    if not os.path.isdir(PARQUET_DIR):
        print("Diretório de parquets não encontrado. Nada para ingerir.")
        return 0
    
    arquivos = [f for f in os.listdir(PARQUET_DIR) if f.startswith("matches_") and f.endswith(".parquet")]
    arquivos = sorted(arquivos, reverse=True)[:limit_files]
    
    if not arquivos:
        print("Nenhum arquivo de partidas encontrado.")
        return 0
        
    conn = None
    processados = 0
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        for arquivo in arquivos:
            caminho = os.path.join(PARQUET_DIR, arquivo)
            print(f"Lendo e processando: {arquivo}...")
            
            try:
                df = pd.read_parquet(caminho)
                
                for _, row in df.iterrows():
                    match_id = int(row.get('match_id')) if pd.notna(row.get('match_id')) else None
                    
                    if not match_id:
                        continue
                    
                    home_team = str(row.get('home_team_name', 'Desconhecido')).strip()
                    away_team = str(row.get('away_team_name', 'Desconhecido')).strip()
                    gols_casa = int(row.get('home_score', 0)) if pd.notna(row.get('home_score')) else 0
                    gols_fora = int(row.get('away_score', 0)) if pd.notna(row.get('away_score')) else 0
                    competicao = str(row.get('competition_name', 'Desconhecida')).strip()
                    temporada = str(row.get('season_name', 'Desconhecida')).strip()
                    
                    dados_extras = json.dumps({
                        "match_date": str(row.get('match_date', '')),
                        "stadium": str(row.get('stadium', '')),
                        "referee": str(row.get('referee', '')),
                        "match_week": int(row.get('match_week', 0)) if pd.notna(row.get('match_week')) else 0
                    })
                    
                    sql = """
                        INSERT INTO tb_partida_historico 
                        (id_partida, competicao, temporada, time_casa, time_fora, gols_casa, gols_fora, dados_extras)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id_partida) DO NOTHING
                    """
                    
                    valores = (match_id, competicao, temporada, home_team, away_team, gols_casa, gols_fora, dados_extras)
                    cursor.execute(sql, valores)
                    processados += 1
                    
                conn.commit()
                print(f"✓ {arquivo} carregado com sucesso!")
                
            except Exception as e:
                print(f"✗ Erro ao processar {arquivo}: {e}")
                if conn:
                    conn.rollback()

        print(f"\n✓ Total processado: {processados} linhas de partidas")
        return processados
        
    except Exception as e:
        print(f"✗ Erro na conexão: {e}")
        return 0
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    load_matches_to_neon(limit_files=2)
