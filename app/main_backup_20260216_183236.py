from __future__ import annotations

import base64
import csv
import hashlib
import json
import os
import time
from collections import Counter
from dataclasses import dataclass
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx
import requests
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from pyvis.network import Network as PyVisNetwork

load_dotenv()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


APP_NAME = os.getenv("APP_NAME", "API_Interactomes")

CACHE_DIR = Path(os.getenv("CACHE_DIR", "app/cache"))
CACHE_TTL_SECONDS = _env_int("CACHE_TTL_SECONDS", 86400)
REQUEST_TIMEOUT_SECONDS = _env_int("REQUEST_TIMEOUT_SECONDS", 30)
POLITE_DELAY_SECONDS = float(os.getenv("POLITE_DELAY_SECONDS", "0"))

STRING_API_BASE = os.getenv("STRING_API_BASE", "https://string-db.org/api")
STRING_CALLER_IDENTITY = os.getenv("STRING_CALLER_IDENTITY", "API_Interactomes")
STRING_DEFAULT_SPECIES = _env_int("STRING_DEFAULT_SPECIES", 9606)

ENCORI_API_BASE = os.getenv("ENCORI_API_BASE", "https://rnasysu.com/encori/api")

_HOST_LAST_CALL: Dict[str, float] = {}


# ----------------------------
# Helpers: rate limit + cache
# ----------------------------
def _sleep_if_needed(url: str) -> None:
    if POLITE_DELAY_SECONDS <= 0:
        return
    host = urlparse(url).netloc
    last = _HOST_LAST_CALL.get(host, 0.0)
    now = time.time()
    wait = POLITE_DELAY_SECONDS - (now - last)
    if wait > 0:
        time.sleep(wait)
    _HOST_LAST_CALL[host] = time.time()


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _cache_key(method: str, url: str, params: Optional[Dict[str, Any]] = None, data: Any = None) -> str:
    payload = {"m": method.upper(), "u": url, "p": params or {}, "d": data or {}}
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()


def cached_request(
    method: str,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    data: Any = None,
    headers: Optional[Dict[str, str]] = None,
    ttl_seconds: int = CACHE_TTL_SECONDS,
) -> bytes:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = _cache_key(method, url, params=params, data=data)
    path = CACHE_DIR / f"{key}.bin"

    if path.exists() and (time.time() - path.stat().st_mtime) < ttl_seconds:
        return path.read_bytes()

    _sleep_if_needed(url)
    r = requests.request(
        method=method.upper(),
        url=url,
        params=params,
        data=data,
        headers=headers,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    r.raise_for_status()
    path.write_bytes(r.content)
    return r.content


def _parse_tsv(text: str) -> Tuple[List[str], List[List[str]]]:
    sio = StringIO(text)
    reader = csv.reader(sio, delimiter="\t")
    rows = [row for row in reader if row and any(cell.strip() for cell in row)]
    if not rows:
        return [], []
    first = rows[0]
    header_markers = {"RBP", "geneID", "geneName", "pairGeneName", "miRNAname", "miRNAid"}
    if any(cell in header_markers for cell in first):
        return first, rows[1:]
    return [], rows


def _html_escape(s: Any) -> str:
    s = "" if s is None else str(s)
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
         .replace("'", "&#39;")
    )


# ----------------------------
# Data structures
# ----------------------------
@dataclass
class Node:
    id: str
    label: str
    kind: str
    sources: List[str]


@dataclass
class Edge:
    source: str
    target: str
    kind: str
    source_db: str
    score: Optional[float] = None
    support: int = 1
    meta: Optional[Dict[str, Any]] = None


def _add_node(nodes: Dict[str, Node], node_id: str, label: str, kind: str, source_db: str) -> None:
    if node_id in nodes:
        n = nodes[node_id]
        if source_db not in n.sources:
            n.sources.append(source_db)
        if n.kind == "unknown" and kind != "unknown":
            n.kind = kind
        return
    nodes[node_id] = Node(id=node_id, label=label, kind=kind, sources=[source_db])


def _edge_key(u: str, v: str, kind: str, db: str) -> Tuple[str, str, str, str]:
    if v < u:
        u, v = v, u
    return (u, v, kind, db)


def _merge_edge(edges: Dict[Tuple[str, str, str, str], Edge], e: Edge) -> None:
    key = _edge_key(e.source, e.target, e.kind, e.source_db)
    if key in edges:
        edges[key].support += 1
        if e.score is not None:
            if edges[key].score is None or e.score > edges[key].score:
                edges[key].score = e.score
        return
    edges[key] = e


# ----------------------------
# STRING
# ----------------------------
def string_map_identifiers(identifiers: List[str], species: int) -> List[Dict[str, Any]]:
    url = f"{STRING_API_BASE}/json/get_string_ids"
    data = {
        "identifiers": "\r".join(identifiers),
        "species": species,
        "limit": 1,
        "echo_query": 1,
        "caller_identity": STRING_CALLER_IDENTITY,
    }
    raw = cached_request("POST", url, data=data)
    return json.loads(raw.decode("utf-8"))


def string_interaction_partners(string_ids: List[str], species: int, required_score: int, limit: int) -> List[Dict[str, Any]]:
    url = f"{STRING_API_BASE}/json/interaction_partners"
    data = {
        "identifiers": "\r".join(string_ids),
        "species": species,
        "required_score": required_score,
        "limit": limit,
        "caller_identity": STRING_CALLER_IDENTITY,
    }
    raw = cached_request("POST", url, data=data)
    return json.loads(raw.decode("utf-8"))


# ----------------------------
# ENCORI
# ----------------------------
def encori_rbp_by_target(
    target: str,
    *,
    assembly: str,
    gene_type: str,
    cell_type: str,
    clip_exp_num: int,
    pancancer_num: int,
) -> List[Dict[str, Any]]:
    url = f"{ENCORI_API_BASE}/RBPTarget/"
    params = {
        "assembly": assembly,
        "geneType": gene_type,
        "RBP": "all",
        "clipExpNum": clip_exp_num,
        "pancancerNum": pancancer_num,
        "target": target,
        "cellType": cell_type,
    }
    raw = cached_request("GET", url, params=params)
    header, rows = _parse_tsv(raw.decode("utf-8", errors="replace"))

    out: List[Dict[str, Any]] = []
    if header:
        idx = {name: i for i, name in enumerate(header)}
        for r in rows:
            rbp = r[idx.get("RBP", 0)] if idx.get("RBP", 0) < len(r) else ""
            geneName = r[idx.get("geneName", 2)] if idx.get("geneName", 2) < len(r) else target
            if rbp:
                out.append({"RBP": rbp, "geneName": geneName})
    else:
        for r in rows:
            if len(r) >= 3 and r[0]:
                out.append({"RBP": r[0], "geneName": r[2]})
    return out


def encori_rna_rna(
    rna: str,
    *,
    assembly: str,
    gene_type: str,
    cell_type: str,
    inter_num: int,
    exp_num: int,
) -> List[Dict[str, Any]]:
    url = f"{ENCORI_API_BASE}/RNARNA/"
    params = {
        "assembly": assembly,
        "geneType": gene_type,
        "RNA": rna,
        "interNum": inter_num,
        "expNum": exp_num,
        "cellType": cell_type,
    }
    raw = cached_request("GET", url, params=params)
    header, rows = _parse_tsv(raw.decode("utf-8", errors="replace"))

    out: List[Dict[str, Any]] = []
    if header:
        idx = {name: i for i, name in enumerate(header)}
        for r in rows:
            a = r[idx.get("geneName", 1)] if idx.get("geneName", 1) < len(r) else ""
            b = r[idx.get("pairGeneName", 4)] if idx.get("pairGeneName", 4) < len(r) else ""
            if a and b:
                out.append({"geneName": a, "pairGeneName": b})
    else:
        for r in rows:
            if len(r) >= 5:
                out.append({"geneName": r[1], "pairGeneName": r[4]})
    return out


SUPPORTED_SOURCES = {
    "string_ppi": "STRING protein-protein interaction partners",
    "encori_rbp_by_target": "ENCORI RBP-RNA interactions (RBPs binding a target RNA/gene)",
    "encori_rna_rna": "ENCORI RNA-RNA interaction network",
}


def _normalize_sources(src_csv: str) -> List[str]:
    aliases = {
        "string": "string_ppi",
        "ppi": "string_ppi",
        "encori_rbp": "encori_rbp_by_target",
        "encori_rna": "encori_rna_rna",
    }
    out: List[str] = []
    for s in (src_csv or "").split(","):
        s = s.strip().lower()
        if not s:
            continue
        out.append(aliases.get(s, s))
    return [s for s in out if s in SUPPORTED_SOURCES]


def build_network(
    *,
    seeds: List[str],
    sources: List[str],
    species_taxon: int,
    string_required_score: int,
    string_limit: int,
    string_depth: int,
    string_depth2_limit: int,
    # ENCORI
    assembly: str,
    gene_type: str,
    cell_type: str,
    clip_exp_num: int,
    pancancer_num: int,
    inter_num: int,
    exp_num: int,
) -> Dict[str, Any]:
    nodes: Dict[str, Node] = {}
    edges: Dict[Tuple[str, str, str, str], Edge] = {}

    if not sources:
        raise HTTPException(status_code=400, detail=f"No valid sources. Supported: {sorted(SUPPORTED_SOURCES)}")

    # ---- STRING 1-hop ----
    if "string_ppi" in sources:
        try:
            mapped = string_map_identifiers(seeds, species_taxon)
            string_ids_1 = [it.get("stringId") for it in mapped if it.get("stringId")]
            if not string_ids_1:
                string_ids_1 = seeds  # fallback

            items1 = string_interaction_partners(string_ids_1, species_taxon, string_required_score, string_limit)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"STRING error: {e}")

        for it in items1:
            a = it.get("preferredName_A") or it.get("stringId_A") or ""
            b = it.get("preferredName_B") or it.get("stringId_B") or ""
            if not a or not b:
                continue
            _add_node(nodes, a, a, "protein", "STRING")
            _add_node(nodes, b, b, "protein", "STRING")
            try:
                score = float(it.get("score")) if it.get("score") is not None else None
            except Exception:
                score = None
            _merge_edge(
                edges,
                Edge(
                    source=a,
                    target=b,
                    kind="ppi",
                    source_db="STRING",
                    score=score,
                    meta={"stringId_A": it.get("stringId_A"), "stringId_B": it.get("stringId_B")},
                ),
            )

        # ---- STRING 2-hop (optional) ----
        if string_depth >= 2:
            # 只用目前已抓到的 STRING 蛋白節點做第二跳（避免爆炸）
            string_names = [n.id for n in nodes.values() if n.kind == "protein" and "STRING" in n.sources]
            # 保守上限：避免一次塞太多 identifiers
            if len(string_names) > 300:
                string_names = string_names[:300]

            try:
                mapped2 = string_map_identifiers(string_names, species_taxon)
                string_ids_2 = [it.get("stringId") for it in mapped2 if it.get("stringId")]
                if string_ids_2:
                    items2 = string_interaction_partners(string_ids_2, species_taxon, string_required_score, string_depth2_limit)
                else:
                    items2 = []
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"STRING depth=2 error: {e}")

            for it in items2:
                a = it.get("preferredName_A") or it.get("stringId_A") or ""
                b = it.get("preferredName_B") or it.get("stringId_B") or ""
                if not a or not b:
                    continue
                _add_node(nodes, a, a, "protein", "STRING")
                _add_node(nodes, b, b, "protein", "STRING")
                try:
                    score = float(it.get("score")) if it.get("score") is not None else None
                except Exception:
                    score = None
                _merge_edge(
                    edges,
                    Edge(
                        source=a,
                        target=b,
                        kind="ppi",
                        source_db="STRING",
                        score=score,
                        meta={"stringId_A": it.get("stringId_A"), "stringId_B": it.get("stringId_B"), "depth": 2},
                    ),
                )

    # ---- ENCORI RBP-target ----
    if "encori_rbp_by_target" in sources:
        for seed in seeds:
            try:
                items = encori_rbp_by_target(
                    seed,
                    assembly=assembly,
                    gene_type=gene_type,
                    cell_type=cell_type,
                    clip_exp_num=clip_exp_num,
                    pancancer_num=pancancer_num,
                )
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"ENCORI RBPTarget error: {e}")

            for it in items:
                rbp = (it.get("RBP") or "").strip()
                tgt = (it.get("geneName") or seed).strip()
                if rbp and tgt:
                    _add_node(nodes, rbp, rbp, "protein", "ENCORI")
                    _add_node(nodes, tgt, tgt, "rna", "ENCORI")
                    _merge_edge(edges, Edge(source=rbp, target=tgt, kind="rbp_target", source_db="ENCORI", meta=it))

    # ---- ENCORI RNA-RNA ----
    if "encori_rna_rna" in sources:
        for seed in seeds:
            try:
                items = encori_rna_rna(
                    seed,
                    assembly=assembly,
                    gene_type=gene_type,
                    cell_type=cell_type,
                    inter_num=inter_num,
                    exp_num=exp_num,
                )
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"ENCORI RNARNA error: {e}")

            for it in items:
                a = (it.get("geneName") or "").strip()
                b = (it.get("pairGeneName") or "").strip()
                if a and b:
                    _add_node(nodes, a, a, "rna", "ENCORI")
                    _add_node(nodes, b, b, "rna", "ENCORI")
                    _merge_edge(edges, Edge(source=a, target=b, kind="rna_rna", source_db="ENCORI", meta=it))

    node_list = [vars(n) for n in nodes.values()]
    edge_list = [vars(e) for e in edges.values()]
    return {
        "nodes": node_list,
        "edges": edge_list,
        "meta": {
            "seeds": seeds,
            "sources": sources,
            "species_taxon": species_taxon,
            "params": {
                "string_required_score": string_required_score,
                "string_limit": string_limit,
                "string_depth": string_depth,
                "string_depth2_limit": string_depth2_limit,
                "assembly": assembly,
                "gene_type": gene_type,
                "cell_type": cell_type,
                "clip_exp_num": clip_exp_num,
                "pancancer_num": pancancer_num,
                "inter_num": inter_num,
                "exp_num": exp_num,
            },
            "counts": {"nodes": len(node_list), "edges": len(edge_list)},
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        },
    }


def compute_metrics(network: Dict[str, Any]) -> Dict[str, Any]:
    g = nx.Graph()
    for n in network["nodes"]:
        g.add_node(n["id"], kind=n.get("kind", "unknown"))

    for e in network["edges"]:
        u, v = e["source"], e["target"]
        w = e.get("score")
        try:
            w = float(w) if w is not None else 1.0
        except Exception:
            w = 1.0
        g.add_edge(u, v, weight=w, kind=e.get("kind"))

    if g.number_of_nodes() == 0:
        return {"nodes": 0, "edges": 0, "top_degree": [], "top_betweenness": []}

    deg = dict(g.degree())
    # 大網路不要算 betweenness（會很慢）
    btw = nx.betweenness_centrality(g, normalized=True) if g.number_of_nodes() <= 1000 else {}

    top_degree = sorted(deg.items(), key=lambda x: x[1], reverse=True)[:20]
    top_betweenness = sorted(btw.items(), key=lambda x: x[1], reverse=True)[:20] if btw else []

    return {
        "nodes": g.number_of_nodes(),
        "edges": g.number_of_edges(),
        "top_degree": [{"node": n, "degree": d} for n, d in top_degree],
        "top_betweenness": [{"node": n, "betweenness": b} for n, b in top_betweenness],
    }


def hubs_png(network: Dict[str, Any], top_n: int = 20) -> bytes:
    metrics = compute_metrics(network)
    top = metrics["top_degree"][:top_n]
    labels = [x["node"] for x in top][::-1]
    values = [x["degree"] for x in top][::-1]

    fig = plt.figure(figsize=(10, 6))
    ax = fig.add_subplot(111)
    ax.barh(labels, values)
    ax.set_xlabel("Degree")
    ax.set_title("Top hubs (degree)")

    buf = BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def pyvis_html(network: Dict[str, Any]) -> str:
    # degree for node size
    deg = Counter()
    for e in network["edges"]:
        deg[e["source"]] += 1
        deg[e["target"]] += 1

    net = PyVisNetwork(height="750px", width="100%", notebook=False, directed=False)
    net.barnes_hut()

    for n in network["nodes"]:
        title = f"kind: {n.get('kind','unknown')}<br>sources: {', '.join(n.get('sources', []))}"
        group = n.get("kind", "unknown")
        net.add_node(
            n["id"],
            label=n.get("label") or n["id"],
            title=title,
            group=group,
            value=max(1, deg.get(n["id"], 1)),
        )

    for e in network["edges"]:
        title = f"{e.get('source_db')} | {e.get('kind')}"
        if e.get("score") is not None:
            title += f"<br>score: {e.get('score')}"
        if e.get("support", 1) > 1:
            title += f"<br>support: {e.get('support')}"
        try:
            value = float(e.get("score")) if e.get("score") is not None else 1.0
        except Exception:
            value = 1.0
        net.add_edge(e["source"], e["target"], value=value, title=title)

    return net.generate_html()


def _inject_after_body_open(html: str, extra: str) -> str:
    low = html.lower()
    i = low.find("<body")
    if i < 0:
        return extra + html
    j = html.find(">", i)
    if j < 0:
        return html + extra
    return html[: j + 1] + extra + html[j + 1 :]


def make_report_html(network: Dict[str, Any], top_n: int = 20) -> str:
    meta = network.get("meta", {})
    metrics = compute_metrics(network)

    kind_counts = Counter(n.get("kind", "unknown") for n in network.get("nodes", []))
    edge_kind_counts = Counter(e.get("kind", "unknown") for e in network.get("edges", []))
    source_counts = Counter(s for n in network.get("nodes", []) for s in (n.get("sources") or []))

    hubs_b64 = base64.b64encode(hubs_png(network, top_n=top_n)).decode("ascii")

    def _small_table(title: str, d: Dict[str, Any]) -> str:
        rows = "".join(f"<tr><th>{_html_escape(k)}</th><td>{_html_escape(v)}</td></tr>" for k, v in d.items())
        return f"<h3>{_html_escape(title)}</h3><table class='kv'>{rows}</table>"

    top_deg_rows = "".join(
        f"<tr><td>{_html_escape(x['node'])}</td><td style='text-align:right'>{x['degree']}</td></tr>"
        for x in metrics.get("top_degree", [])
    )
    top_btw_rows = "".join(
        f"<tr><td>{_html_escape(x['node'])}</td><td style='text-align:right'>{x['betweenness']:.6f}</td></tr>"
        for x in metrics.get("top_betweenness", [])
    )

    header = f"""
    <div style="font-family:Segoe UI, Arial; padding:16px 20px; max-width:1200px;">
      <h2 style="margin:0 0 6px 0;">Interactome report</h2>
      <div style="color:#666; margin-bottom:14px;">
        seeds: <b>{_html_escape(', '.join(meta.get('seeds', [])))}</b> |
        sources: <b>{_html_escape(', '.join(meta.get('sources', [])))}</b> |
        nodes: <b>{metrics.get('nodes')}</b> |
        edges: <b>{metrics.get('edges')}</b> |
        generated_at: <b>{_html_escape(meta.get('generated_at'))}</b>
      </div>

      <div style="display:flex; gap:18px; flex-wrap:wrap; align-items:flex-start;">
        <div style="flex:1; min-width:320px;">
          {_small_table("Node kinds", dict(kind_counts))}
          {_small_table("Edge kinds", dict(edge_kind_counts))}
          {_small_table("Node sources", dict(source_counts))}
        </div>

        <div style="flex:2; min-width:420px;">
          <h3>Top hubs (degree)</h3>
          <img src="data:image/png;base64,{hubs_b64}" style="max-width:100%; border:1px solid #ddd; border-radius:6px;" />
        </div>
      </div>

      <div style="display:flex; gap:18px; flex-wrap:wrap; margin-top:10px;">
        <div style="flex:1; min-width:320px;">
          <h3>Top degree</h3>
          <table class="list"><tr><th>node</th><th style="text-align:right">degree</th></tr>{top_deg_rows}</table>
        </div>
        <div style="flex:1; min-width:320px;">
          <h3>Top betweenness</h3>
          <table class="list"><tr><th>node</th><th style="text-align:right">betweenness</th></tr>{top_btw_rows or "<tr><td colspan='2' style='color:#666'>skipped (graph too large)</td></tr>"}</table>
        </div>
      </div>

      <details style="margin-top:12px;">
        <summary style="cursor:pointer;">Show meta params</summary>
        <pre style="background:#f7f7f7; padding:10px; border:1px solid #eee; border-radius:6px; overflow:auto;">{_html_escape(json.dumps(meta.get('params', {}), indent=2, ensure_ascii=False))}</pre>
      </details>

      <hr style="margin:16px 0;">
      <div style="color:#666; margin-bottom:6px;">Interactive network below (drag / zoom / click nodes)</div>
    </div>

    <style>
      table.kv {{ border-collapse: collapse; width:100%; margin:6px 0 14px 0; }}
      table.kv th, table.kv td {{ border:1px solid #eee; padding:6px 8px; text-align:left; }}
      table.kv th {{ background:#fafafa; width:45%; }}
      table.list {{ border-collapse: collapse; width:100%; }}
      table.list th, table.list td {{ border:1px solid #eee; padding:6px 8px; }}
      table.list th {{ background:#fafafa; }}
    </style>
    """

    net_html = pyvis_html(network)
    return _inject_after_body_open(net_html, header)


# ----------------------------
# FastAPI
# ----------------------------
app = FastAPI(title=APP_NAME, version="0.4.0")


def network_params(
    seed: List[str] = Query(..., description="Repeatable: ?seed=TP53&seed=BRCA1"),
    sources: str = Query("string_ppi,encori_rbp_by_target", description="Comma-separated source ids"),
    species_taxon: int = Query(STRING_DEFAULT_SPECIES),

    # STRING
    string_required_score: int = Query(700, ge=0, le=1000),
    string_limit: int = Query(50, ge=1, le=500),
    string_depth: int = Query(1, ge=1, le=2, description="STRING expansion depth (1 or 2)"),
    string_depth2_limit: int = Query(10, ge=1, le=100, description="Per-node limit for depth=2 expansion"),

    # ENCORI
    assembly: str = Query("hg38"),
    gene_type: str = Query("mRNA"),
    cell_type: str = Query("all"),
    clip_exp_num: int = Query(5, ge=0),
    pancancer_num: int = Query(0, ge=0, le=32),
    inter_num: int = Query(1, ge=1),
    exp_num: int = Query(1, ge=1),
) -> Dict[str, Any]:
    src_list = _normalize_sources(sources)
    return {
        "seeds": seed,
        "sources": src_list,
        "species_taxon": species_taxon,
        "string_required_score": string_required_score,
        "string_limit": string_limit,
        "string_depth": string_depth,
        "string_depth2_limit": string_depth2_limit,
        "assembly": assembly,
        "gene_type": gene_type,
        "cell_type": cell_type,
        "clip_exp_num": clip_exp_num,
        "pancancer_num": pancancer_num,
        "inter_num": inter_num,
        "exp_num": exp_num,
    }


@app.get("/health")
def health():
    return {"status": "ok", "app": APP_NAME, "version": "0.4.0"}


@app.get("/sources")
def sources():
    return {"sources": [{"id": k, "description": v} for k, v in SUPPORTED_SOURCES.items()]}


@app.get("/network")
def network(p: Dict[str, Any] = Depends(network_params)):
    return build_network(**p)


@app.get("/viz/network", response_class=HTMLResponse)
def viz_network(p: Dict[str, Any] = Depends(network_params)):
    netw = build_network(**p)
    return HTMLResponse(pyvis_html(netw))


@app.get("/viz/hubs.png")
def viz_hubs_png(top_n: int = Query(20, ge=5, le=100), p: Dict[str, Any] = Depends(network_params)):
    netw = build_network(**p)
    png = hubs_png(netw, top_n=top_n)
    return StreamingResponse(BytesIO(png), media_type="image/png")


@app.get("/viz/metrics")
def viz_metrics(p: Dict[str, Any] = Depends(network_params)):
    netw = build_network(**p)
    return compute_metrics(netw)


@app.get("/report", response_class=HTMLResponse)
def report(top_n: int = Query(20, ge=5, le=100), p: Dict[str, Any] = Depends(network_params)):
    netw = build_network(**p)
    return HTMLResponse(make_report_html(netw, top_n=top_n))
