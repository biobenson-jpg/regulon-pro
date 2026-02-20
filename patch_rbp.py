import sqlite3
import os

DB_PATH = r"C:\Users\biobe\Desktop\API_Interactomes\regulon.db"

def patch_rbps():
    print("🚀 啟動修正引擎...")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    core_rbps = ["ELAVL1", "WDR33", "RBM15", "YTHDF2", "PTBP1", "HNRNPK", "AGO2", "CTSS", "USP24", "KLHL20", "CD274"]
    placeholders = ','.join(['?'] * len(core_rbps))
    
    print(f"⏳ 正在 6,000 萬筆資料中執行單次掃描，強制校正核心標靶...")
    print("⚠️ 這大約需要 1 到 3 分鐘 (取決於 SSD 速度)，請讓游標閃一下，不要關閉視窗！")
    
    # 核心優化：用 IN 語法，只掃描一次資料庫
    query = f"UPDATE interactions SET type = 'Protein' WHERE target IN ({placeholders})"
    c.execute(query, core_rbps)
    
    conn.commit()
    conn.close()
    print("✅ 史詩級 BUG 修復完成！ELAVL1 等分子已經成功穿回蛋白質的外衣！")

if __name__ == "__main__":
    patch_rbps()