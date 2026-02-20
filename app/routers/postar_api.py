from fastapi import APIRouter
import random

router = APIRouter()

@router.get("/postar/search")
async def search_postar(gene: str):
    # 模擬從 POSTAR3 抓取的 CLIP-seq 支持數據
    # 在實際生產環境中，這裡會改為 requests.get("https://postar.ncrnalab.org/api/...")
    return {
        "gene": gene,
        "source": "POSTAR3",
        "interactions": [
            {"target": f"{gene}_lncRNA", "type": "RNA", "method": "CLIP-seq", "score": 0.95},
            {"target": "MALAT1", "type": "RNA", "method": "PAR-CLIP", "score": 0.88}
        ]
    }