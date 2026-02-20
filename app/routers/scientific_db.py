from fastapi import APIRouter
import sqlite3
import os
import shutil

router = APIRouter()
DB_COPIED = False

@router.get("/debug")
async def system_check():
    debug_info = {
        "1_gcs_folder_exists": os.path.exists("/mnt/gcs"),
        "2_files_in_gcs": os.listdir("/mnt/gcs") if os.path.exists("/mnt/gcs") else [],
        "3_files_in_ram": os.listdir("/tmp") if os.path.exists("/tmp") else []
    }
    return debug_info

@router.get("/network")
async def get_targeted_network(seed: str, mode: str = 'All', all_seeds: str = '', limit: int = 500):
    global DB_COPIED
    error_msg = "No error"
    
    # 尋找雲端隨身碟的資料庫 (自動適應大小寫)
    gcs_db_path = None
    if os.path.exists("/mnt/gcs/regulon.db"):
        gcs_db_path = "/mnt/gcs/regulon.db"
    elif os.path.exists("/mnt/gcs/Regulon.db"):
        gcs_db_path = "/mnt/gcs/Regulon.db"
        
    if gcs_db_path:
        LOCAL_DB = "/tmp/regulon.db"
        if not DB_COPIED or not os.path.exists(LOCAL_DB):
            try:
                print("🚀 [System] Copying 1.7GB DB to High-Speed RAM...")
                shutil.copy2(gcs_db_path, LOCAL_DB)
                DB_COPIED = True
            except Exception as e:
                error_msg = f"RAM Copy Error: {e}"
        db_path_to_use = LOCAL_DB
    else:
        # 筆電本機的備案路徑 (這樣你筆電本機跑也不會壞！)
        db_path_to_use = r"C:\Users\biobe\Desktop\API_Interactomes\regulon.db"
        error_msg = "DB not found in GCS"

    seed = seed.upper()
    seed_list = [s.strip().upper() for s in all_seeds.split(',')] if all_seeds else []
    results = []
    seen = set()

    if os.path.exists(db_path_to_use):
        try:
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
            error_msg = f"SQL Query Error: {e}"

    return {"seed": seed, "edges": results, "debug_status": error_msg}
