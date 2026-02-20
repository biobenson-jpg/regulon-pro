from fastapi import APIRouter
import sqlite3
import os
import shutil

router = APIRouter()
DB_COPIED = False

@router.get("/network")
async def get_targeted_network(seed: str, mode: str = 'All', all_seeds: str = '', limit: int = 500):
    global DB_COPIED
    
    # 🚀 終極黑科技：延遲載入 (Lazy Loading) 記憶體資料庫
    # 檢查是否在雲端環境
    if os.path.exists("/mnt/gcs/regulon.db"):
        LOCAL_DB = "/tmp/regulon.db" # Cloud Run 的 /tmp 是超高速的 RAM 記憶體
        
        # 只有在「第一次」有人查詢時，才把資料庫拷貝到高速記憶體
        if not DB_COPIED or not os.path.exists(LOCAL_DB):
            try:
                print("🚀 [System] Initializing... Copying 1.7GB DB to RAM (/tmp).")
                shutil.copy2("/mnt/gcs/regulon.db", LOCAL_DB)
                DB_COPIED = True
            except Exception as e:
                print(f"❌ [System] DB Copy failed: {e}")
        db_path_to_use = LOCAL_DB
    else:
        db_path_to_use = r"C:\Users\biobe\Desktop\API_Interactomes\regulon.db"

    seed = seed.upper()
    seed_list = [s.strip().upper() for s in all_seeds.split(',')] if all_seeds else []
    results = []
    seen = set()

    if os.path.exists(db_path_to_use):
        try:
            # 使用唯讀模式開啟記憶體中的資料庫
            db_uri = f"file:{db_path_to_use}?mode=ro"
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
