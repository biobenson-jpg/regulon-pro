import sqlite3
import os
import csv

DIR_PATH = r"C:\Users\biobe\Desktop\API_Interactomes"
NPI_FILE = os.path.join(DIR_PATH, "interaction_NPInterv5.txt")
RPI_FILE = os.path.join(DIR_PATH, "Download_data_RP.txt")
RRI_FILE = os.path.join(DIR_PATH, "Download_data_RR.txt")
DB_PATH  = os.path.join(DIR_PATH, "regulon.db")

def build_fusion_db():
    print("🚀 啟動【最終破甲版】資料庫融合引擎...")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('DROP TABLE IF EXISTS raw_edges')
    c.execute('CREATE TABLE raw_edges (seed TEXT, target TEXT, target_type TEXT, source_db TEXT)')
    
    # 1. 處理 NPInter v5
    if os.path.exists(NPI_FILE):
        print("📥 [1/3] 正在載入 NPInter v5.0...")
        count = 0
        with open(NPI_FILE, 'r', encoding='utf-8', errors='ignore') as f:
            reader = csv.DictReader(f, delimiter='\t')
            for row in reader:
                nc = row.get('ncName', '').strip().upper()
                tar = row.get('tarName', '').strip().upper()
                if nc and tar:
                    c.execute('INSERT INTO raw_edges VALUES (?,?,?,?)', (nc, tar, 'Protein', 'NPInter_v5'))
                    c.execute('INSERT INTO raw_edges VALUES (?,?,?,?)', (tar, nc, 'RNA', 'NPInter_v5'))
                    count += 1
        print(f"  └─ 完成！載入 {count} 筆 NPInter 數據。")

    # 2. 處理 RNAInter (動態欄位追蹤)
    def process_rnainter(file_path, db_label):
        if not os.path.exists(file_path): return
        print(f"📥 正在載入 {db_label} (啟動動態欄位追蹤與光速過濾)...")
        count = 0
        
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            header_line = f.readline().strip('\n')
            header = header_line.split('\t')
            
            # 動態尋找真正的欄位位置
            idx_int1 = next((i for i, x in enumerate(header) if 'INTERACTOR1' in x.upper()), 1)
            idx_cat1 = next((i for i, x in enumerate(header) if 'CATEGORY1' in x.upper()), 2)
            idx_int2 = next((i for i, x in enumerate(header) if 'INTERACTOR2' in x.upper()), 4)
            idx_cat2 = next((i for i, x in enumerate(header) if 'CATEGORY2' in x.upper()), 5)
            
            for line in f:
                line_lower = line.lower()
                # 暴力光速過濾：整行沒有人類關鍵字直接踢掉，連 split 都省了，速度極快
                if 'sapiens' not in line_lower and 'human' not in line_lower and '9606' not in line_lower:
                    continue
                    
                cols = line.strip('\n').split('\t')
                if len(cols) <= max(idx_int1, idx_cat1, idx_int2, idx_cat2): continue
                
                int1 = cols[idx_int1].strip().upper()
                cat1 = cols[idx_cat1].strip().upper()
                int2 = cols[idx_int2].strip().upper()
                cat2 = cols[idx_cat2].strip().upper()
                
                if int1 and int2:
                    t1 = 'Protein' if 'PROTEIN' in cat1 else 'RNA'
                    t2 = 'Protein' if 'PROTEIN' in cat2 else 'RNA'
                    
                    c.execute('INSERT INTO raw_edges VALUES (?,?,?,?)', (int1, int2, t2, db_label))
                    c.execute('INSERT INTO raw_edges VALUES (?,?,?,?)', (int2, int1, t1, db_label))
                    count += 1
                    if count % 200000 == 0: print(f"  └─ 已擷取 {count} 筆人類精華...")
                    
        print(f"  └─ 完成！成功搶救 {count} 筆 {db_label} 數據。")

    process_rnainter(RPI_FILE, 'RNAInter_RPI')
    process_rnainter(RRI_FILE, 'RNAInter_RRI')
    
    print("⚡ [核心] 啟動 SQL 聯集與去重複...")
    c.execute('DROP TABLE IF EXISTS interactions')
    c.execute('''
        CREATE TABLE interactions AS 
        SELECT 
            seed, 
            target, 
            MAX(target_type) as type, 
            GROUP_CONCAT(DISTINCT source_db) as db 
        FROM raw_edges 
        GROUP BY seed, target
    ''')
    
    print("🗑️ 清理暫存並建立極速索引...")
    c.execute('DROP TABLE raw_edges')
    c.execute('CREATE INDEX idx_seed ON interactions(seed)')
    conn.commit()
    
    c.execute('SELECT COUNT(*) FROM interactions')
    final_count = c.fetchone()[0]
    conn.close()
    print(f"✅ 大功告成！全網域融合完成，總庫存: {final_count} 筆不重複交互作用！")

if __name__ == "__main__":
    build_fusion_db()