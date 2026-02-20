import re
p = r'C:\Users\biobe\Desktop\API_Interactomes\app\main.py'
with open(p, 'r', encoding='utf-8') as f: c = f.read()

# 清除之前可能的失敗痕跡
c = re.sub(r'# --- Auto-Injected.*?--------------------------------------------\n', '', c, flags=re.DOTALL)

# 安全掛載
if 'viz_pro' not in c and '@app.' in c:
    i = c.find('@app.')
    c = c[:i] + 'from app.routers import viz_pro\napp.include_router(viz_pro.router)\n\n' + c[i:]
    with open(p, 'w', encoding='utf-8') as f: f.write(c)
    print('[成功] 路由已掛載至 main.py！')
