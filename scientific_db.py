from fastapi import APIRouter
import sqlite3
import os

router = APIRouter()

if os.path.exists("/mnt/gcs/regulon.db"):
    DB_PATH = "/mnt/gcs/regulon.db"
else:
    DB_PATH = r"C:\Users\biobe\Desktop\API_Interactomes\regulon.db"

@router.get("/network")
async def get_targeted_network(seed: str, mode: str = 'All', all_seeds: str = '', limit: int = 500):
    seed = seed.upper()
    seed_list = [s.strip().upper() for s in all_seeds.split(',')] if all_seeds else []
    results = []
    seen = set()

    if os.path.exists(DB_PATH):
        try:
            # 🚀 終極解法：強制以「唯讀模式 (mode=ro)」開啟資料庫，完美避開雲端硬碟的鎖定衝突
            db_uri = f"file:{DB_PATH}?mode=ro"
            conn = sqlite3.connect(db_uri, uri=True)
            c = conn.cursor()
            
            query_base = "SELECT target, type, db FROM interactions WHERE seed = ?"
            params = [seed]
            
            if mode == 'RNA':
                if seed_list:
                    placeholders = ','.join(['?'] * len(seed_list))
                    query_base += f" AND (type = 'RNA' OR target IN ({placeholders}))"
                    params.extend(seed_list)
                else:
                    query_base += " AND type = 'RNA'"
            elif mode == 'Protein':
                if seed_list:
                    placeholders = ','.join(['?'] * len(seed_list))
                    query_base += f" AND (type = 'Protein' OR target IN ({placeholders}))"
                    params.extend(seed_list)
                else:
                    query_base += " AND type = 'Protein'"
                    
            if seed_list:
                placeholders = ','.join(['?'] * len(seed_list))
                order_clause = f" ORDER BY CASE WHEN target IN ({placeholders}) THEN 1 ELSE 0 END DESC, length(db) - length(replace(db, ',', '')) DESC LIMIT ?"
                params.extend(seed_list)
                params.append(limit)
                query_base += order_clause
            else:
                query_base += " ORDER BY length(db) - length(replace(db, ',', '')) DESC LIMIT ?"
                params.append(limit)
            
            c.execute(query_base, tuple(params))
            
            for row in c.fetchall():
                t = row[0]
                if t not in seen:
                    results.append({"target": t, "mol_type": row[1], "database": row[2]})
                    seen.add(t)
            conn.close()
        except Exception as e:
            print(f"Database Query Error: {e}")

    return {"seed": seed, "edges": results}
