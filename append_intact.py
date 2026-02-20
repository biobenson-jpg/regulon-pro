import sqlite3
import os
import re

INTACT_FILE = r"C:\Users\biobe\Desktop\API_Interactomes\intact.txt"
DB_PATH = r"C:\Users\biobe\Desktop\API_Interactomes\regulon.db"

# 嚴格遵照你的指示：絕對不亂猜，依靠官方 MI Ontology 來判斷分子屬性
def get_mol_type(type_str):
    t = type_str.lower()
    if 'mi:0320' in t or 'rna' in t or 'ribonucleic acid' in t: return 'RNA'
    if 'mi:0326' in t or 'protein' in t or 'peptide' in t: return 'Protein'
    if 'mi:0250' in t or 'gene' in t: return 'Gene'
    if 'mi:0328' in t or 'small molecule' in t: return 'Compound'
    if 'mi:0319' in t or 'dna' in t: return 'DNA'
    return 'Other'

# 從 Alias 欄位精準萃取 Gene Name (例如：提取 DROSHA 而不是 Uniprot ID)
def extract_gene_name(alias_str, id_str):
    # 優先找 (gene name)
    m = re.search(r'([a-zA-Z0-9_-]+)\(gene name\)', alias_str)
    if m: return m.group(1).upper()
    # 退而求其次找 (display_short)
    m = re.search(r'([a-zA-Z0-9_-]+)\(display_short\)', alias_str)
    if m: return m.group(1).upper()
    # 如果都沒有，才用原本的 ID
    return id_str.split(':')[1].upper() if ':' in id_str else id_str.upper()

def append_intact_data():
    if not os.path.exists(INTACT_FILE):
        print("❌ 找不到 intact.txt，請確認路徑。")
        return

    print("🚀 啟動 Phase 2 蛋白質體追加引擎...")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # 建立一個暫存表來放 IntAct 數據
    c.execute('DROP TABLE IF EXISTS raw_intact')
    c.execute('CREATE TABLE raw_intact (seed TEXT, target TEXT, target_type TEXT, source_db TEXT)')
    
    print("📥 正在解讀 10.9GB IntAct 巨獸 (啟動光速人類過濾 & 嚴格屬性判定)...")
    count = 0
    with open(INTACT_FILE, 'r', encoding='utf-8', errors='ignore') as f:
        header = f.readline()
        
        for line in f:
            # 光速過濾：整行沒有人類 Taxid (9606) 就不浪費 CPU 去 split
            if 'taxid:9606' not in line:
                continue
                
            cols = line.split('\t')
            if len(cols) < 22: continue
            
            # 二次確認：雙方都必須是人類
            if 'taxid:9606' not in cols[9] or 'taxid:9606' not in cols[10]:
                continue
            
            # 萃取精準的 Gene Name
            intA = extract_gene_name(cols[4], cols[0])
            intB = extract_gene_name(cols[5], cols[1])
            
            # 嚴格遵照使用者指示：依賴官方欄位判斷分子屬性
            typeA = get_mol_type(cols[20])
            typeB = get_mol_type(cols[21])
            
            if intA and intB:
                c.execute('INSERT INTO raw_intact VALUES (?,?,?,?)', (intA, intB, typeB, 'IntAct'))
                c.execute('INSERT INTO raw_intact VALUES (?,?,?,?)', (intB, intA, typeA, 'IntAct'))
                count += 1
                if count % 100000 == 0:
                    print(f"  └─ 已成功萃取 {count} 筆高純度人類交互作用...")

    print("⚡ [核心] 正在將 IntAct 完美融入現有 Regulon 資料庫...")
    # 把原來的資料跟新的資料聯集，並去重複
    c.execute('''
        CREATE TABLE new_interactions AS 
        SELECT seed, target, MAX(type) as type, GROUP_CONCAT(DISTINCT db) as db
        FROM (
            SELECT seed, target, type, db FROM interactions
            UNION ALL
            SELECT seed, target, target_type as type, source_db as db FROM raw_intact
        )
        GROUP BY seed, target
    ''')
    
    print("🗑️ 清理暫存並重新建立極速索引...")
    c.execute('DROP TABLE interactions')
    c.execute('DROP TABLE raw_intact')
    c.execute('ALTER TABLE new_interactions RENAME TO interactions')
    c.execute('CREATE INDEX idx_seed ON interactions(seed)')
    
    c.execute('SELECT COUNT(*) FROM interactions')
    final_count = c.fetchone()[0]
    conn.commit()
    conn.close()
    
    print(f"✅ 史詩級大統一完成！資料庫現擁有 {final_count} 筆包含全轉錄體與蛋白質體的不重複交互作用！")

if __name__ == "__main__":
    append_intact_data()