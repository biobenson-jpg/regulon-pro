from fastapi import APIRouter
import sqlite3
import os
import traceback

router = APIRouter()

@router.get("/debug")
async def system_check():
    try:
        return {
            "1_gcs_folder_exists": os.path.exists("/mnt/gcs"),
            "2_files_in_gcs": os.listdir("/mnt/gcs") if os.path.exists("/mnt/gcs") else [],
            "3_tmp_exists": os.path.exists("/tmp")
        }
    except Exception as e:
        return {"error": str(e)}

@router.get("/network")
async def get_targeted_network(seed: str, mode: str = 'All', all_seeds: str = '', limit: int = 500):
    try:
        error_msg = "No error"
        
        # 取消 RAM 複製，直接讀取隨身碟 (測試是否為記憶體不足導致 500)
        gcs_db_path = None
        if os.path.exists("/mnt/gcs/regulon.db"):
            gcs_db_path = "/mnt/gcs/regulon.db"
        elif os.path.exists("/mnt/gcs/Regulon.db"):
            gcs_db_path = "/mnt/gcs/Regulon.db"
            
        if gcs_db_path:
            db_path_to_use = gcs_db_path
        else:
            db_path_to_use = r"C:\Users\biobe\Desktop\API_Interactomes\regulon.db"

        seed = seed.upper()
        seed_list = [s.strip().upper() for s in all_seeds.split(',')] if all_seeds else []
        results = []
        seen = set()

        if os.path.exists(db_path_to_use):
            # 採用最單純的連線方式，且加上唯讀模式避免鎖定檔案
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
        else:
            error_msg = "DB file not found at path"

        return {"seed": seed, "edges": results, "debug_status": error_msg, "db_used": db_path_to_use}
        
    except Exception as e:
        # 這是防止 HTTP 500 的終極護城河，把所有崩潰原因印在網頁上
        return {
            "seed": seed, 
            "edges": [], 
            "debug_status": "CRASH_PREVENTED", 
            "error_detail": str(e),
            "traceback": traceback.format_exc()
        }
