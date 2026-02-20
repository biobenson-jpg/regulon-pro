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
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlparse
from zipfile import ZipFile, ZIP_DEFLATED

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx
import requests
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from pyvis.network import Network as PyVisNetwork

from networkx.algorithms.community import greedy_modularity_communities, asyn_lpa_communities

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

def _chunks(xs: List[str], n: int) -> Iterable[List[str]]:
    for i in range(0, len(xs), n):
        yield xs[i:i+n]

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

def string_partners_chunked(string_ids: List[str], species: int, required_score: int, limit: int, chunk_size: int = 60) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for ch in _chunks(string_ids, chunk_size):
        out.extend(string_interaction_partners(ch, species, required_score, limit))
    return out

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

def _build_graph(network: Dict[str, Any]) -> nx.Graph:
    g = nx.Graph()
    for n in network.get("nodes", []):
        g.add_node(n["id"], kind=n.get("kind", "unknown"))
    for e in network.get("edges", []):
        u, v = e["source"], e["target"]
        w = e.get("score")
        try:
            w = float(w) if w is not None else 1.0
        except Exception:
            w = 1.0
        g.add_edge(u, v, weight=w, kind=e.get("kind", "unknown"), db=e.get("source_db", ""))
    return g

def _prune_network(network: Dict[str, Any], seeds: List[str], max_nodes: int) -> Dict[str, Any]:
    if max_nodes <= 0:
        return network
    nodes = network.get("nodes", [])
    if len(nodes) <= max_nodes:
        return network

    g = _build_graph(network)
    deg = dict(g.degree())
    keep: Set[str] = set(seeds)

    ranked = sorted(deg.items(), key=lambda x: x[1], reverse=True)
    for n, _d in ranked:
        if len(keep) >= max_nodes:
            break
        keep.add(n)

    nodes2 = [n for n in nodes if n["id"] in keep]
    edges2 = [e for e in network.get("edges", []) if e["source"] in keep and e["target"] in keep]

    meta = dict(network.get("meta", {}))
    meta["pruned"] = True
    meta["pruned_max_nodes"] = max_nodes
    meta["counts"] = {"nodes": len(nodes2), "edges": len(edges2)}

    return {"nodes": nodes2, "edges": edges2, "meta": meta}

def build_network(
    *,
    seeds: List[str],
    sources: List[str],
    species_taxon: int,
    # STRING
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
    # global
    max_nodes: int,
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
                string_ids_1 = seeds

            items1 = string_partners_chunked(string_ids_1, species_taxon, string_required_score, string_limit)
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
            _merge_edge(edges, Edge(source=a, target=b, kind="ppi", source_db="STRING", score=score, meta={"depth": 1}))

        # ---- STRING 2-hop ----
        if string_depth >= 2:
            # expand from STRING proteins already present (cap to avoid explosion)
            string_names = [n.id for n in nodes.values() if n.kind == "protein" and "STRING" in n.sources]
            if len(string_names) > 250:
                string_names = string_names[:250]

            try:
                mapped2 = string_map_identifiers(string_names, species_taxon)
                string_ids_2 = [it.get("stringId") for it in mapped2 if it.get("stringId")]
                items2 = string_partners_chunked(string_ids_2, species_taxon, string_required_score, string_depth2_limit) if string_ids_2 else []
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
                _merge_edge(edges, Edge(source=a, target=b, kind="ppi", source_db="STRING", score=score, meta={"depth": 2}))

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

    net = {
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
                "max_nodes": max_nodes,
            },
            "counts": {"nodes": len(node_list), "edges": len(edge_list)},
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        },
    }

    if max_nodes and len(node_list) > max_nodes:
        net = _prune_network(net, seeds=seeds, max_nodes=max_nodes)

    return net

def compute_metrics(network: Dict[str, Any], ignore_nodes: Optional[Set[str]] = None) -> Dict[str, Any]:
    ignore_nodes = ignore_nodes or set()
    g = _build_graph(network)

    if g.number_of_nodes() == 0:
        return {"nodes": 0, "edges": 0, "top_degree": [], "top_betweenness": []}

    # degree (optionally exclude)
    deg_all = dict(g.degree())
    deg = {k: v for k, v in deg_all.items() if k not in ignore_nodes}

    top_degree = sorted(deg.items(), key=lambda x: x[1], reverse=True)[:20]

    # betweenness (skip big)
    btw = {}
    if g.number_of_nodes() <= 1200:
        btw_all = nx.betweenness_centrality(g, normalized=True)
        btw = {k: v for k, v in btw_all.items() if k not in ignore_nodes}
    top_betweenness = sorted(btw.items(), key=lambda x: x[1], reverse=True)[:20] if btw else []

    return {
        "nodes": g.number_of_nodes(),
        "edges": g.number_of_edges(),
        "top_degree": [{"node": n, "degree": d} for n, d in top_degree],
        "top_betweenness": [{"node": n, "betweenness": b} for n, b in top_betweenness],
    }

def communities(network: Dict[str, Any], min_size: int = 3) -> Tuple[List[List[str]], Dict[str, int]]:
    g = _build_graph(network)
    if g.number_of_nodes() == 0:
        return [], {}

    # pick algorithm by size
    if g.number_of_nodes() <= 2000 and g.number_of_edges() <= 20000:
        comms = list(greedy_modularity_communities(g))
    else:
        comms = list(asyn_lpa_communities(g, weight=None, seed=7))

    comm_list = [sorted(list(c)) for c in comms if len(c) >= min_size]
    comm_list.sort(key=len, reverse=True)

    node2c: Dict[str, int] = {}
    for i, c in enumerate(comm_list):
        for n in c:
            node2c[n] = i

    return comm_list, node2c

def hubs_png(network: Dict[str, Any], top_n: int = 20, ignore_nodes: Optional[Set[str]] = None) -> bytes:
    m = compute_metrics(network, ignore_nodes=ignore_nodes)
    top = m["top_degree"][:top_n]
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

def community_sizes_png(comm_list: List[List[str]], top_k: int = 15) -> bytes:
    sizes = [len(c) for c in comm_list[:top_k]][::-1]
    labels = [f"C{i}" for i in range(min(top_k, len(comm_list)))][::-1]

    fig = plt.figure(figsize=(10, 6))
    ax = fig.add_subplot(111)
    ax.barh(labels, sizes)
    ax.set_xlabel("Nodes")
    ax.set_title("Community sizes (top)")

    buf = BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()

def pyvis_html(network: Dict[str, Any], node2c: Optional[Dict[str, int]] = None) -> str:
    node2c = node2c or {}

    # degree for size
    deg = Counter()
    for e in network.get("edges", []):
        deg[e["source"]] += 1
        deg[e["target"]] += 1

    net = PyVisNetwork(height="750px", width="100%", notebook=False, directed=False)
    net.barnes_hut()

    for n in network.get("nodes", []):
        nid = n["id"]
        kind = n.get("kind", "unknown")
        title = f"kind: {kind}<br>sources: {', '.join(n.get('sources', []))}"
        # community group for coloring
        grp = node2c.get(nid, 9999)
        # shape by kind
        shape = "dot" if kind == "protein" else ("square" if kind == "rna" else "diamond")
        net.add_node(
            nid,
            label=n.get("label") or nid,
            title=title,
            group=grp,
            shape=shape,
            value=max(1, deg.get(nid, 1)),
        )

    for e in network.get("edges", []):
        title = f"{e.get('source_db')} | {e.get('kind')}"
        if e.get("score") is not None:
            title += f"<br>score: {e.get('score')}"
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

def make_report_html(network: Dict[str, Any], top_n: int = 20, ignore_seed_in_hubs: bool = False) -> str:
    meta = network.get("meta", {})
    seeds = meta.get("seeds", [])
    ignore = set(seeds) if ignore_seed_in_hubs else set()

    m = compute_metrics(network, ignore_nodes=ignore)
    comm_list, node2c = communities(network, min_size=3)

    hubs_b64 = base64.b64encode(hubs_png(network, top_n=top_n, ignore_nodes=ignore)).decode("ascii")
    comm_b64 = base64.b64encode(community_sizes_png(comm_list, top_k=15)).decode("ascii")

    kind_counts = Counter(n.get("kind", "unknown") for n in network.get("nodes", []))
    edge_kind_counts = Counter(e.get("kind", "unknown") for e in network.get("edges", []))

    top_deg_rows = "".join(
        f"<tr><td>{_html_escape(x['node'])}</td><td style='text-align:right'>{x['degree']}</td></tr>"
        for x in m.get("top_degree", [])
    )
    top_btw_rows = "".join(
        f"<tr><td>{_html_escape(x['node'])}</td><td style='text-align:right'>{x['betweenness']:.6f}</td></tr>"
        for x in m.get("top_betweenness", [])
    ) or "<tr><td colspan='2' style='color:#666'>skipped / none</td></tr>"

    header = f"""
    <div style="font-family:Segoe UI, Arial; padding:16px 20px; max-width:1200px;">
      <h2 style="margin:0 0 6px 0;">Interactome report</h2>
      <div style="color:#666; margin-bottom:14px;">
        seeds: <b>{_html_escape(', '.join(seeds))}</b> |
        sources: <b>{_html_escape(', '.join(meta.get('sources', [])))}</b> |
        nodes: <b>{m.get('nodes')}</b> |
        edges: <b>{m.get('edges')}</b> |
        communities: <b>{len(comm_list)}</b>
      </div>

      <div style="display:flex; gap:18px; flex-wrap:wrap; align-items:flex-start;">
        <div style="flex:1; min-width:320px;">
          <h3>Node kinds</h3>
          <table class="kv">{''.join(f"<tr><th>{_html_escape(k)}</th><td>{v}</td></tr>" for k,v in kind_counts.items())}</table>

          <h3>Edge kinds</h3>
          <table class="kv">{''.join(f"<tr><th>{_html_escape(k)}</th><td>{v}</td></tr>" for k,v in edge_kind_counts.items())}</table>
        </div>

        <div style="flex:2; min-width:420px;">
          <h3>Top hubs (degree){' (seed excluded)' if ignore_seed_in_hubs else ''}</h3>
          <img src="data:image/png;base64,{hubs_b64}" style="max-width:100%; border:1px solid #ddd; border-radius:6px;" />
          <h3 style="margin-top:14px;">Community sizes (top)</h3>
          <img src="data:image/png;base64,{comm_b64}" style="max-width:100%; border:1px solid #ddd; border-radius:6px;" />
        </div>
      </div>

      <div style="display:flex; gap:18px; flex-wrap:wrap; margin-top:10px;">
        <div style="flex:1; min-width:320px;">
          <h3>Top degree</h3>
          <table class="list"><tr><th>node</th><th style="text-align:right">degree</th></tr>{top_deg_rows}</table>
        </div>
        <div style="flex:1; min-width:320px;">
          <h3>Top betweenness</h3>
          <table class="list"><tr><th>node</th><th style="text-align:right">betweenness</th></tr>{top_btw_rows}</table>
        </div>
      </div>

      <details style="margin-top:12px;">
        <summary style="cursor:pointer;">Show params</summary>
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

    net_html = pyvis_html(network, node2c=node2c)
    return _inject_after_body_open(net_html, header)

def _csv_bytes(rows: List[Dict[str, Any]], fieldnames: List[str]) -> bytes:
    sio = StringIO()
    w = csv.DictWriter(sio, fieldnames=fieldnames, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return sio.getvalue().encode("utf-8")

# ----------------------------
# FastAPI
# ----------------------------
app = FastAPI(title=APP_NAME, version="0.5.0")

def network_params(
    seed: List[str] = Query(..., description="Repeatable: ?seed=TP53&seed=BRCA1"),
    sources: str = Query("string_ppi,encori_rbp_by_target", description="Comma-separated source ids"),
    species_taxon: int = Query(STRING_DEFAULT_SPECIES),

    # STRING
    string_required_score: int = Query(700, ge=0, le=1000),
    string_limit: int = Query(50, ge=1, le=500),
    string_depth: int = Query(1, ge=1, le=2),
    string_depth2_limit: int = Query(10, ge=1, le=100),

    # ENCORI
    assembly: str = Query("hg38"),
    gene_type: str = Query("mRNA"),
    cell_type: str = Query("all"),
    clip_exp_num: int = Query(5, ge=0),
    pancancer_num: int = Query(0, ge=0, le=32),
    inter_num: int = Query(1, ge=1),
    exp_num: int = Query(1, ge=1),

    # global prune
    max_nodes: int = Query(2000, ge=0, le=20000),
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
        "max_nodes": max_nodes,
    }

@app.get("/health")
def health():
    return {"status": "ok", "app": APP_NAME, "version": "0.5.0"}

@app.get("/sources")
def sources():
    return {"sources": [{"id": k, "description": v} for k, v in SUPPORTED_SOURCES.items()]}

@app.get("/network")
def network(p: Dict[str, Any] = Depends(network_params)):
    return build_network(**p)

@app.get("/viz/metrics")
def viz_metrics(ignore_seed_in_hubs: bool = Query(False), p: Dict[str, Any] = Depends(network_params)):
    netw = build_network(**p)
    seeds = set(netw.get("meta", {}).get("seeds", []))
    ignore = seeds if ignore_seed_in_hubs else set()
    return compute_metrics(netw, ignore_nodes=ignore)

@app.get("/viz/communities")
def viz_communities(min_size: int = Query(3, ge=1, le=50), p: Dict[str, Any] = Depends(network_params)):
    netw = build_network(**p)
    comm_list, _node2c = communities(netw, min_size=min_size)
    return {"community_count": len(comm_list), "sizes": [len(c) for c in comm_list], "communities": comm_list[:50]}

@app.get("/viz/community_sizes.png")
def viz_community_sizes_png(min_size: int = Query(3, ge=1, le=50), top_k: int = Query(15, ge=5, le=50), p: Dict[str, Any] = Depends(network_params)):
    netw = build_network(**p)
    comm_list, _ = communities(netw, min_size=min_size)
    png = community_sizes_png(comm_list, top_k=top_k)
    return StreamingResponse(BytesIO(png), media_type="image/png")

@app.get("/viz/hubs.png")
def viz_hubs_png(top_n: int = Query(20, ge=5, le=100), ignore_seed_in_hubs: bool = Query(False), p: Dict[str, Any] = Depends(network_params)):
    netw = build_network(**p)
    seeds = set(netw.get("meta", {}).get("seeds", []))
    ignore = seeds if ignore_seed_in_hubs else set()
    png = hubs_png(netw, top_n=top_n, ignore_nodes=ignore)
    return StreamingResponse(BytesIO(png), media_type="image/png")

@app.get("/viz/network", response_class=HTMLResponse)
def viz_network(min_comm_size: int = Query(3, ge=1, le=50), p: Dict[str, Any] = Depends(network_params)):
    netw = build_network(**p)
    comm_list, node2c = communities(netw, min_size=min_comm_size)
    return HTMLResponse(pyvis_html(netw, node2c=node2c))

@app.get("/report", response_class=HTMLResponse)
def report(top_n: int = Query(20, ge=5, le=100), ignore_seed_in_hubs: bool = Query(False), p: Dict[str, Any] = Depends(network_params)):
    netw = build_network(**p)
    return HTMLResponse(make_report_html(netw, top_n=top_n, ignore_seed_in_hubs=ignore_seed_in_hubs))

@app.get("/export.zip")
def export_zip(
    top_n: int = Query(20, ge=5, le=100),
    ignore_seed_in_hubs: bool = Query(False),
    min_comm_size: int = Query(3, ge=1, le=50),
    p: Dict[str, Any] = Depends(network_params),
):
    netw = build_network(**p)
    seeds = set(netw.get("meta", {}).get("seeds", []))
    ignore = seeds if ignore_seed_in_hubs else set()
    metrics = compute_metrics(netw, ignore_nodes=ignore)
    comm_list, _ = communities(netw, min_size=min_comm_size)

    report_html = make_report_html(netw, top_n=top_n, ignore_seed_in_hubs=ignore_seed_in_hubs).encode("utf-8")
    hubs = hubs_png(netw, top_n=top_n, ignore_nodes=ignore)
    comm_png = community_sizes_png(comm_list, top_k=15)

    nodes_csv = _csv_bytes(netw.get("nodes", []), ["id", "label", "kind", "sources"])
    edges_csv = _csv_bytes(netw.get("edges", []), ["source", "target", "kind", "source_db", "score", "support"])

    zbuf = BytesIO()
    with ZipFile(zbuf, mode="w", compression=ZIP_DEFLATED) as z:
        z.writestr("network.json", json.dumps(netw, ensure_ascii=False, indent=2))
        z.writestr("metrics.json", json.dumps(metrics, ensure_ascii=False, indent=2))
        z.writestr("communities.json", json.dumps({"sizes": [len(c) for c in comm_list], "communities": comm_list[:50]}, ensure_ascii=False, indent=2))
        z.writestr("nodes.csv", nodes_csv)
        z.writestr("edges.csv", edges_csv)
        z.writestr("report.html", report_html)
        z.writestr("hubs.png", hubs)
        z.writestr("community_sizes.png", comm_png)

    zbuf.seek(0)
    filename = "interactome_export.zip"
    return StreamingResponse(
        zbuf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

# ============================
# MODULE_PATCH_V1
# adds:
#   GET /module
#   GET /viz/module
#   GET /viz/module/hubs.png
#   GET /export_module.zip
# ============================

def _get_comm_list__module_v1(netw: Dict[str, Any], min_size: int = 3) -> List[List[str]]:
    # 盡量沿用你已經有的 community 函數（不同版本 patch 名字不同）
    if "_community_partition_v1" in globals():
        comm_list, _ = _community_partition_v1(netw, min_size=min_size)
        return comm_list
    if "_community_list__cpatch" in globals():
        return _community_list__cpatch(netw, min_size=min_size)
    if "_community_list__patch" in globals():
        return _community_list__patch(netw, min_size=min_size)

    # fallback：greedy modularity
    from networkx.algorithms.community import greedy_modularity_communities
    g = nx.Graph()
    for n in netw.get("nodes", []):
        g.add_node(n["id"])
    for e in netw.get("edges", []):
        g.add_edge(e["source"], e["target"])
    comms = list(greedy_modularity_communities(g)) if g.number_of_nodes() else []
    comm_list = [sorted(list(c)) for c in comms if len(c) >= min_size]
    comm_list.sort(key=len, reverse=True)
    return comm_list


def _subnetwork__module_v1(netw: Dict[str, Any], keep_nodes: List[str], meta_extra: Dict[str, Any]) -> Dict[str, Any]:
    keep = set(keep_nodes)
    nodes2 = [n for n in netw.get("nodes", []) if n.get("id") in keep]
    edges2 = [e for e in netw.get("edges", []) if e.get("source") in keep and e.get("target") in keep]

    meta = dict(netw.get("meta", {}))
    meta.update(meta_extra)
    meta["counts"] = {"nodes": len(nodes2), "edges": len(edges2)}

    return {"nodes": nodes2, "edges": edges2, "meta": meta}


def _csv_bytes__module_v1(rows: List[Dict[str, Any]], fieldnames: List[str]) -> bytes:
    import csv as _csv
    from io import StringIO as _StringIO
    sio = _StringIO()
    w = _csv.DictWriter(sio, fieldnames=fieldnames, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return sio.getvalue().encode("utf-8")


@app.get("/module")
def module_v1(
    cid: int = Query(0, ge=0, description="community id (C0 -> cid=0)"),
    min_size: int = Query(3, ge=1, le=50),
    p: Dict[str, Any] = Depends(network_params),
):
    netw = build_network(**p)
    comm_list = _get_comm_list__module_v1(netw, min_size=min_size)
    if cid >= len(comm_list):
        raise HTTPException(status_code=400, detail=f"cid out of range. cid={cid}, community_count={len(comm_list)}")
    keep = comm_list[cid]
    return _subnetwork__module_v1(netw, keep, {"module": {"cid": cid, "min_size": min_size, "size": len(keep)}})


@app.get("/viz/module", response_class=HTMLResponse)
def viz_module_v1(
    cid: int = Query(0, ge=0),
    min_size: int = Query(3, ge=1, le=50),
    p: Dict[str, Any] = Depends(network_params),
):
    netw = build_network(**p)
    comm_list = _get_comm_list__module_v1(netw, min_size=min_size)
    if cid >= len(comm_list):
        raise HTTPException(status_code=400, detail=f"cid out of range. cid={cid}, community_count={len(comm_list)}")
    keep = comm_list[cid]
    sub = _subnetwork__module_v1(netw, keep, {"module": {"cid": cid, "min_size": min_size, "size": len(keep)}})
    return HTMLResponse(pyvis_html(sub))


@app.get("/viz/module/hubs.png")
def viz_module_hubs_png_v1(
    cid: int = Query(0, ge=0),
    min_size: int = Query(3, ge=1, le=50),
    top_n: int = Query(20, ge=5, le=100),
    p: Dict[str, Any] = Depends(network_params),
):
    netw = build_network(**p)
    comm_list = _get_comm_list__module_v1(netw, min_size=min_size)
    if cid >= len(comm_list):
        raise HTTPException(status_code=400, detail=f"cid out of range. cid={cid}, community_count={len(comm_list)}")
    keep = comm_list[cid]
    sub = _subnetwork__module_v1(netw, keep, {"module": {"cid": cid, "min_size": min_size, "size": len(keep)}})
    png = hubs_png(sub, top_n=top_n)
    return StreamingResponse(BytesIO(png), media_type="image/png")


@app.get("/export_module.zip")
def export_module_zip_v1(
    cid: int = Query(0, ge=0),
    min_size: int = Query(3, ge=1, le=50),
    top_n: int = Query(20, ge=5, le=100),
    p: Dict[str, Any] = Depends(network_params),
):
    from zipfile import ZipFile, ZIP_DEFLATED

    netw = build_network(**p)
    comm_list = _get_comm_list__module_v1(netw, min_size=min_size)
    if cid >= len(comm_list):
        raise HTTPException(status_code=400, detail=f"cid out of range. cid={cid}, community_count={len(comm_list)}")

    keep = comm_list[cid]
    sub = _subnetwork__module_v1(netw, keep, {"module": {"cid": cid, "min_size": min_size, "size": len(keep)}})

    # files
    module_json = json.dumps(sub, ensure_ascii=False, indent=2).encode("utf-8")
    nodes_csv = _csv_bytes__module_v1(sub.get("nodes", []), ["id", "label", "kind", "sources"])
    edges_csv = _csv_bytes__module_v1(sub.get("edges", []), ["source", "target", "kind", "source_db", "score", "support"])
    html = pyvis_html(sub).encode("utf-8")
    png = hubs_png(sub, top_n=top_n)

    zbuf = BytesIO()
    with ZipFile(zbuf, mode="w", compression=ZIP_DEFLATED) as z:
        z.writestr("module_network.json", module_json)
        z.writestr("nodes.csv", nodes_csv)
        z.writestr("edges.csv", edges_csv)
        z.writestr("module.html", html)
        z.writestr("hubs.png", png)

    zbuf.seek(0)
    return StreamingResponse(
        zbuf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="module_C{cid}.zip"'},
    )


# ============================
# MODULE_REPORT_PATCH_V1
# adds:
#   GET /module/report
# ============================

def _inject_after_body_open__mreport_v1(html: str, extra: str) -> str:
    low = html.lower()
    i = low.find("<body")
    if i < 0:
        return extra + html
    j = html.find(">", i)
    if j < 0:
        return html + extra
    return html[: j + 1] + extra + html[j + 1 :]


def _qs_from_params__mreport_v1(p: Dict[str, Any], cid: int, min_size: int, top_n: int) -> str:
    from urllib.parse import urlencode

    q = []
    # seed is repeatable
    for s in p.get("seeds", []):
        q.append(("seed", s))
    # everything else
    q.append(("sources", ",".join(p.get("sources", []))))
    q.append(("species_taxon", p.get("species_taxon")))

    q.append(("string_required_score", p.get("string_required_score")))
    q.append(("string_limit", p.get("string_limit")))
    q.append(("string_depth", p.get("string_depth")))
    q.append(("string_depth2_limit", p.get("string_depth2_limit")))

    q.append(("assembly", p.get("assembly")))
    q.append(("gene_type", p.get("gene_type")))
    q.append(("cell_type", p.get("cell_type")))
    q.append(("clip_exp_num", p.get("clip_exp_num")))
    q.append(("pancancer_num", p.get("pancancer_num")))
    q.append(("inter_num", p.get("inter_num")))
    q.append(("exp_num", p.get("exp_num")))

    # module params
    q.append(("cid", cid))
    q.append(("min_size", min_size))
    q.append(("top_n", top_n))
    return urlencode(q, doseq=True)


@app.get("/module/report", response_class=HTMLResponse)
def module_report_v1(
    cid: int = Query(0, ge=0),
    min_size: int = Query(3, ge=1, le=50),
    top_n: int = Query(20, ge=5, le=100),
    p: Dict[str, Any] = Depends(network_params),
):
    # build module subnetwork (reuse module patch helpers)
    if "_get_comm_list__module_v1" not in globals() or "_subnetwork__module_v1" not in globals():
        raise HTTPException(status_code=500, detail="MODULE_PATCH_V1 not found. Please apply module patch first.")

    netw = build_network(**p)
    comm_list = _get_comm_list__module_v1(netw, min_size=min_size)
    if cid >= len(comm_list):
        raise HTTPException(status_code=400, detail=f"cid out of range. cid={cid}, community_count={len(comm_list)}")

    keep = comm_list[cid]
    sub = _subnetwork__module_v1(netw, keep, {"module": {"cid": cid, "min_size": min_size, "size": len(keep)}})

    # metrics
    m = compute_metrics(sub) if "compute_metrics" in globals() else {"nodes": len(sub.get("nodes", [])), "edges": len(sub.get("edges", [])), "top_degree": [], "top_betweenness": []}

    # embed hubs png
    import base64 as _b64
    hubs = hubs_png(sub, top_n=top_n)
    hubs_b64 = _b64.b64encode(hubs).decode("ascii")

    # build download link for module zip
    qs = _qs_from_params__mreport_v1(p, cid=cid, min_size=min_size, top_n=top_n)
    zip_href = f"/export_module.zip?{qs}"
    viz_href = f"/viz/module?{qs}"
    hubs_href = f"/viz/module/hubs.png?{qs}"

    # tables
    def _td(s): 
        return _html_escape(s) if "_html_escape" in globals() else ("" if s is None else str(s))

    top_deg_rows = "".join(
        f"<tr><td>{_td(x['node'])}</td><td style='text-align:right'>{_td(x['degree'])}</td></tr>"
        for x in (m.get("top_degree") or [])
    ) or "<tr><td colspan='2' style='color:#666'>no data</td></tr>"

    top_btw_rows = "".join(
        f"<tr><td>{_td(x['node'])}</td><td style='text-align:right'>{float(x['betweenness']):.6f}</td></tr>"
        for x in (m.get("top_betweenness") or [])
    ) or "<tr><td colspan='2' style='color:#666'>skipped / none</td></tr>"

    header = f"""
    <div style="font-family:Segoe UI, Arial; padding:16px 20px; max-width:1200px;">
      <h2 style="margin:0 0 6px 0;">Module report (C{cid})</h2>
      <div style="color:#666; margin-bottom:14px;">
        seed(s): <b>{', '.join(p.get('seeds', []))}</b> |
        sources: <b>{', '.join(p.get('sources', []))}</b> |
        module size: <b>{len(keep)}</b> |
        nodes: <b>{m.get('nodes')}</b> |
        edges: <b>{m.get('edges')}</b>
      </div>

      <div style="margin-bottom:10px;">
        <a href="{_td(zip_href)}">Download module zip</a> |
        <a href="{_td(viz_href)}">Open module interactive network</a> |
        <a href="{_td(hubs_href)}">Open module hubs png</a>
      </div>

      <div style="display:flex; gap:18px; flex-wrap:wrap; align-items:flex-start;">
        <div style="flex:2; min-width:420px;">
          <h3>Top hubs (degree)</h3>
          <img src="data:image/png;base64,{hubs_b64}" style="max-width:100%; border:1px solid #ddd; border-radius:6px;" />
        </div>

        <div style="flex:1; min-width:320px;">
          <h3>Top degree</h3>
          <table class="list"><tr><th>node</th><th style="text-align:right">degree</th></tr>{top_deg_rows}</table>

          <h3 style="margin-top:14px;">Top betweenness</h3>
          <table class="list"><tr><th>node</th><th style="text-align:right">betweenness</th></tr>{top_btw_rows}</table>
        </div>
      </div>

      <hr style="margin:16px 0;">
      <div style="color:#666; margin-bottom:6px;">Interactive module network below</div>
    </div>

    <style>
      table.list {{ border-collapse: collapse; width:100%; }}
      table.list th, table.list td {{ border:1px solid #eee; padding:6px 8px; }}
      table.list th {{ background:#fafafa; }}
    </style>
    """

    html = pyvis_html(sub)
    return HTMLResponse(_inject_after_body_open__mreport_v1(html, header))


# ============================
# MODULE_LABEL_PATCH_V1
# adds:
#   GET /module/label
#   GET /modules/labels
# ============================
import re
from collections import Counter

_LABEL_RULES_V1 = [
    ("Cell cycle / mitosis", [
        re.compile(r"^(CDK|CCN|CDC|MCM|AURK|PLK|BUB|MAD|E2F|SKP|GADD45|TOP2A|UBE2C|CDC20)")
    ]),
    ("DNA damage / repair", [
        re.compile(r"^(BRCA|RAD|ATM|ATR|CHEK|TP53BP|PARP|FANCD|FANCI|XRCC|MRE11|NBN|MSH|MLH|RRM2B)")
    ]),
    ("Apoptosis / cell death", [
        re.compile(r"^(BCL|CASP|FAS|TNFR|BAX|BAK|BBC3|BIRC|XIAP)")
    ]),
    ("Chromatin / transcription regulation", [
        re.compile(r"^(HDAC|KAT|EP300|CREBBP|SMARC|ARID|EZH|KDM|BRD|MED|POLR|SP1|MYC|JUN|FOS)")
    ]),
    ("Ubiquitin / proteasome", [
        re.compile(r"^(UBE|UBC|USP|PSM|PSMA|PSMB|CUL|RBX|FBX|TRIM)")
    ]),
    ("RNA processing / splicing", [
        re.compile(r"^(HNRNP|SRSF|SF3|PRPF|DDX|DHX|RBM|ELAVL|U2AF|FUS|TARDBP)")
    ]),
    ("Translation / ribosome", [
        re.compile(r"^(RPL|RPS|EIF|EEF)")
    ]),
    ("Signaling (MAPK/PI3K/AKT)", [
        re.compile(r"^(MAPK|MAP2K|PIK3|AKT|MTOR|RAS|RAF|STAT)")
    ]),
]

def _module_subnet_for_label_v1(p: Dict[str, Any], cid: int, min_size: int) -> Dict[str, Any]:
    if "_get_comm_list__module_v1" not in globals() or "_subnetwork__module_v1" not in globals():
        raise HTTPException(status_code=500, detail="MODULE_PATCH_V1 not found. Apply module patch first.")
    netw = build_network(**p)
    comm_list = _get_comm_list__module_v1(netw, min_size=min_size)
    if cid >= len(comm_list):
        raise HTTPException(status_code=400, detail=f"cid out of range. cid={cid}, community_count={len(comm_list)}")
    keep = comm_list[cid]
    return _subnetwork__module_v1(netw, keep, {"module": {"cid": cid, "min_size": min_size, "size": len(keep)}})

def _top_hubs_v1(netw: Dict[str, Any], top_n: int) -> List[str]:
    deg = Counter()
    for e in netw.get("edges", []):
        a = e.get("source")
        b = e.get("target")
        if a: deg[a] += 1
        if b: deg[b] += 1
    return [n for n,_d in deg.most_common(top_n)]

def _auto_label_v1(genes: List[str], hubs: List[str]) -> Dict[str, Any]:
    genes_u = [str(g).strip().upper() for g in genes if g and str(g).strip()]
    hubs_u  = [str(h).strip().upper() for h in hubs if h and str(h).strip()]
    gset = set(genes_u)
    hset = set(hubs_u)

    best = {"label": "Uncategorized", "score": 0, "hub_hits": [], "gene_hits": [], "rules": []}

    for label, regs in _LABEL_RULES_V1:
        hub_hits  = sorted({x for x in hset if any(r.search(x) for r in regs)})
        gene_hits = sorted({x for x in gset if any(r.search(x) for r in regs)})

        score = 3*len(hub_hits) + 1*len(gene_hits)
        if score > best["score"]:
            best = {
                "label": label,
                "score": score,
                "hub_hits": hub_hits[:30],
                "gene_hits": gene_hits[:80],
                "rules": [r.pattern for r in regs],
            }
    return best

@app.get("/module/label")
def module_label_v1(
    cid: int = Query(0, ge=0),
    min_size: int = Query(3, ge=1, le=50),
    top_hubs: int = Query(20, ge=5, le=100),
    p: Dict[str, Any] = Depends(network_params),
):
    sub = _module_subnet_for_label_v1(p, cid=cid, min_size=min_size)
    genes = [n.get("id") for n in sub.get("nodes", [])]
    hubs = _top_hubs_v1(sub, top_n=top_hubs)
    info = _auto_label_v1(genes, hubs)

    return {
        "cid": cid,
        "module_size": sub.get("meta", {}).get("module", {}).get("size"),
        "label": info["label"],
        "score": info["score"],
        "evidence": {
            "hub_hits": info["hub_hits"],
            "gene_hits": info["gene_hits"],
            "rule_patterns": info["rules"],
            "top_hubs": hubs,
        },
    }

@app.get("/modules/labels")
def modules_labels_v1(
    top_k: int = Query(5, ge=1, le=50),
    min_size: int = Query(3, ge=1, le=50),
    top_hubs: int = Query(20, ge=5, le=100),
    p: Dict[str, Any] = Depends(network_params),
):
    if "_get_comm_list__module_v1" not in globals():
        raise HTTPException(status_code=500, detail="MODULE_PATCH_V1 not found. Apply module patch first.")

    netw = build_network(**p)
    comm_list = _get_comm_list__module_v1(netw, min_size=min_size)
    k = min(top_k, len(comm_list))

    out = []
    for cid in range(k):
        sub = _module_subnet_for_label_v1(p, cid=cid, min_size=min_size)
        genes = [n.get("id") for n in sub.get("nodes", [])]
        hubs = _top_hubs_v1(sub, top_n=top_hubs)
        info = _auto_label_v1(genes, hubs)
        out.append({
            "cid": cid,
            "size": sub.get("meta", {}).get("module", {}).get("size"),
            "label": info["label"],
            "score": info["score"],
            "hub_hits": info["hub_hits"],
        })

    return {"count": len(out), "labels": out}


# ============================
# MODULE_CYTO_PATCH_V1
# adds:
#   GET /module/graphml
#   GET /network/graphml
#   GET /export_module_cytoscape.zip
# ============================
from zipfile import ZipFile, ZIP_DEFLATED

def _cyto_subnet_v1(p: Dict[str, Any], cid: int, min_size: int) -> Dict[str, Any]:
    # reuse existing module helpers if available
    if "_module_subnet_for_label_v1" in globals():
        return _module_subnet_for_label_v1(p, cid=cid, min_size=min_size)
    if "_a123_module_subnet" in globals():
        return _a123_module_subnet(p, cid=cid, min_size=min_size)
    if "_get_comm_list__module_v1" in globals() and "_subnetwork__module_v1" in globals():
        netw = build_network(**p)
        comm_list = _get_comm_list__module_v1(netw, min_size=min_size)
        if cid >= len(comm_list):
            raise HTTPException(status_code=400, detail=f"cid out of range. cid={cid}, community_count={len(comm_list)}")
        keep = comm_list[cid]
        return _subnetwork__module_v1(netw, keep, {"module": {"cid": cid, "min_size": min_size, "size": len(keep)}})
    raise HTTPException(status_code=500, detail="Module helpers not found. Apply module patch first.")

def _cyto_csv_bytes_v1(rows: List[Dict[str, Any]], fieldnames: List[str]) -> bytes:
    sio = StringIO()
    w = csv.DictWriter(sio, fieldnames=fieldnames, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return sio.getvalue().encode("utf-8")

def _cyto_graphml_bytes_v1(netw: Dict[str, Any]) -> bytes:
    g = nx.Graph()
    for n in netw.get("nodes", []):
        nid = n.get("id")
        if not nid:
            continue
        sources = n.get("sources", [])
        if isinstance(sources, list):
            sources = ",".join(sources)
        g.add_node(
            nid,
            label=str(n.get("label") or nid),
            kind=str(n.get("kind") or ""),
            sources=str(sources or ""),
        )

    for e in netw.get("edges", []):
        u, v = e.get("source"), e.get("target")
        if not u or not v:
            continue
        try:
            score = float(e.get("score")) if e.get("score") is not None else 0.0
        except Exception:
            score = 0.0
        try:
            support = int(e.get("support") or 1)
        except Exception:
            support = 1
        g.add_edge(
            u, v,
            kind=str(e.get("kind") or ""),
            source_db=str(e.get("source_db") or ""),
            score=score,
            support=support,
        )

    buf = BytesIO()
    nx.write_graphml(g, buf)
    buf.seek(0)
    return buf.getvalue()

@app.get("/module/graphml")
def module_graphml_v1(
    cid: int = Query(0, ge=0),
    min_size: int = Query(3, ge=1, le=50),
    p: Dict[str, Any] = Depends(network_params),
):
    sub = _cyto_subnet_v1(p, cid=cid, min_size=min_size)
    data = _cyto_graphml_bytes_v1(sub)
    fn = f"module_C{cid}.graphml"
    return StreamingResponse(BytesIO(data), media_type="application/graphml+xml",
                             headers={"Content-Disposition": f'attachment; filename="{fn}"'})

@app.get("/network/graphml")
def network_graphml_v1(p: Dict[str, Any] = Depends(network_params)):
    netw = build_network(**p)
    data = _cyto_graphml_bytes_v1(netw)
    fn = "network.graphml"
    return StreamingResponse(BytesIO(data), media_type="application/graphml+xml",
                             headers={"Content-Disposition": f'attachment; filename="{fn}"'})

@app.get("/export_module_cytoscape.zip")
def export_module_cytoscape_zip_v1(
    cid: int = Query(0, ge=0),
    min_size: int = Query(3, ge=1, le=50),
    top_hubs: int = Query(20, ge=5, le=100),
    p: Dict[str, Any] = Depends(network_params),
):
    sub = _cyto_subnet_v1(p, cid=cid, min_size=min_size)

    graphml = _cyto_graphml_bytes_v1(sub)
    nodes_csv = _cyto_csv_bytes_v1(sub.get("nodes", []), ["id", "label", "kind", "sources"])
    edges_csv = _cyto_csv_bytes_v1(sub.get("edges", []), ["source", "target", "kind", "source_db", "score", "support"])

    hub_png = hubs_png(sub, top_n=top_hubs) if "hubs_png" in globals() else b""

    zbuf = BytesIO()
    with ZipFile(zbuf, mode="w", compression=ZIP_DEFLATED) as z:
        z.writestr("module.graphml", graphml)
        z.writestr("nodes.csv", nodes_csv)
        z.writestr("edges.csv", edges_csv)
        if hub_png:
            z.writestr("hubs.png", hub_png)

    zbuf.seek(0)
    fn = f"module_C{cid}_cytoscape.zip"
    return StreamingResponse(zbuf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{fn}"'})


# ============================
# MODULE_ENRICH_PATCH_V1
# adds:
#   GET /module/enrich
#   GET /viz/module/enrich.png
# ============================
import math
from pathlib import Path

_GPROF_URL_V1 = os.getenv("GPROFILER_GOST_URL", "https://biit.cs.ut.ee/gprofiler/api/gost/profile")
_ENRICHR_BASE_V1 = os.getenv("ENRICHR_BASE_URL", "https://maayanlab.cloud/Enrichr")

def _enrich_cache_dir_v1() -> Path:
    try:
        d = Path(str(CACHE_DIR))
    except Exception:
        d = Path("app/cache")
    d.mkdir(parents=True, exist_ok=True)
    return d

def _enrich_cache_get_v1(key: str, ttl: int = 86400) -> Optional[Dict[str, Any]]:
    path = _enrich_cache_dir_v1() / f"enrich_{key}.json"
    if path.exists() and (time.time() - path.stat().st_mtime) < ttl:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None

def _enrich_cache_put_v1(key: str, obj: Dict[str, Any]) -> None:
    path = _enrich_cache_dir_v1() / f"enrich_{key}.json"
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

def _enrich_bar_png_v1(terms: List[Dict[str, Any]], title: str, top: int = 10) -> bytes:
    items = []
    for it in (terms or [])[:top]:
        name = it.get("term_name") or it.get("name") or it.get("term") or it.get("term_id") or "term"
        p = it.get("p_value")
        try:
            p = float(p)
        except Exception:
            continue
        items.append((str(name), -math.log10(max(p, 1e-300))))
    items = items[:top]

    if not items:
        fig = plt.figure(figsize=(10, 3))
        ax = fig.add_subplot(111)
        ax.text(0.5, 0.5, "No enrichment results", ha="center", va="center")
        ax.axis("off")
        buf = BytesIO()
        fig.tight_layout()
        fig.savefig(buf, format="png", dpi=150)
        plt.close(fig)
        buf.seek(0)
        return buf.getvalue()

    labels = [x[0] for x in items][::-1]
    vals = [x[1] for x in items][::-1]

    fig = plt.figure(figsize=(10, 6))
    ax = fig.add_subplot(111)
    ax.barh(labels, vals)
    ax.set_xlabel("-log10(p-value)")
    ax.set_title(title)

    buf = BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()

def _module_genes_for_enrich_v1(sub: Dict[str, Any], proteins_only: bool) -> List[str]:
    genes = []
    for n in sub.get("nodes", []):
        if proteins_only and n.get("kind") == "rna":
            continue
        g = n.get("id")
        if g:
            genes.append(str(g))
    # unique but keep stable order
    seen = set()
    out = []
    for g in genes:
        if g not in seen:
            out.append(g)
            seen.add(g)
    return out

def _gprofiler_v1(genes: List[str], organism: str, sources: List[str], user_threshold: float, top: int) -> Dict[str, Any]:
    key = hashlib.sha256(_stable_json({
        "tool":"gprofiler","genes":genes,"organism":organism,"sources":sources,"thr":user_threshold,"top":top
    }).encode("utf-8")).hexdigest()

    cached = _enrich_cache_get_v1(key, ttl=int(os.getenv("CACHE_TTL_SECONDS","86400")))
    if cached:
        return cached

    payload = {
        "organism": organism,
        "query": genes,
        "sources": sources,
        "user_threshold": user_threshold,
        "all_results": True,
    }
    r = requests.post(_GPROF_URL_V1, json=payload, timeout=int(os.getenv("REQUEST_TIMEOUT_SECONDS","30")))
    r.raise_for_status()
    data = r.json()
    res = data.get("result", []) or []

    terms = []
    for it in res:
        try:
            p = float(it.get("p_value"))
        except Exception:
            continue
        terms.append({
            "source": it.get("source"),
            "term_id": it.get("native"),
            "term_name": it.get("name"),
            "p_value": p,
            "intersection_size": it.get("intersection_size"),
        })
    terms.sort(key=lambda x: x["p_value"])
    out = {"tool":"gprofiler","organism":organism,"sources":sources,"terms":terms[:top]}
    _enrich_cache_put_v1(key, out)
    return out

def _enrichr_v1(genes: List[str], library: str, top: int) -> Dict[str, Any]:
    key = hashlib.sha256(_stable_json({
        "tool":"enrichr","genes":genes,"library":library,"top":top
    }).encode("utf-8")).hexdigest()

    cached = _enrich_cache_get_v1(key, ttl=int(os.getenv("CACHE_TTL_SECONDS","86400")))
    if cached:
        return cached

    add_url = f"{_ENRICHR_BASE_V1}/addList"
    r1 = requests.post(add_url, data={"list":"\n".join(genes), "description":"module"}, timeout=int(os.getenv("REQUEST_TIMEOUT_SECONDS","30")))
    r1.raise_for_status()
    uid = r1.json().get("userListId")
    if uid is None:
        raise RuntimeError("Enrichr addList did not return userListId")

    enr_url = f"{_ENRICHR_BASE_V1}/enrich"
    r2 = requests.get(enr_url, params={"userListId": uid, "backgroundType": library}, timeout=int(os.getenv("REQUEST_TIMEOUT_SECONDS","30")))
    r2.raise_for_status()
    data = r2.json()
    rows = data.get(library, []) or []

    terms = []
    for row in rows[:top]:
        # [rank, term_name, p_value, z, combined, overlap, adj_p, ...]
        try:
            terms.append({
                "term_name": row[1],
                "p_value": float(row[2]),
                "adj_p": float(row[6]) if len(row) > 6 and row[6] is not None else None,
            })
        except Exception:
            continue

    out = {"tool":"enrichr","library":library,"terms":terms}
    _enrich_cache_put_v1(key, out)
    return out

@app.get("/module/enrich")
def module_enrich_v1(
    cid: int = Query(0, ge=0),
    min_size: int = Query(3, ge=1, le=50),
    tool: str = Query("auto", description="auto | gprofiler | enrichr"),
    organism: str = Query("hsapiens"),
    gost_sources: str = Query("GO:BP", description="e.g. GO:BP,KEGG,REAC"),
    user_threshold: float = Query(0.05, gt=0.0, lt=1.0),
    enrichr_library: str = Query("GO_Biological_Process_2021"),
    top: int = Query(15, ge=5, le=50),
    proteins_only: bool = Query(True),
    p: Dict[str, Any] = Depends(network_params),
):
    sub = _cyto_subnet_v1(p, cid=cid, min_size=min_size) if "_cyto_subnet_v1" in globals() else (_module_subnet_for_label_v1(p, cid=cid, min_size=min_size) if "_module_subnet_for_label_v1" in globals() else None)
    if sub is None:
        raise HTTPException(status_code=500, detail="Subnet helper not found.")

    genes = _module_genes_for_enrich_v1(sub, proteins_only=proteins_only)
    srcs = [x.strip() for x in (gost_sources or "").split(",") if x.strip()]

    t = (tool or "auto").lower().strip()
    if t == "gprofiler":
        try:
            return _gprofiler_v1(genes, organism=organism, sources=srcs or ["GO:BP"], user_threshold=user_threshold, top=top)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"g:Profiler error: {e}")

    if t == "enrichr":
        try:
            return _enrichr_v1(genes, library=enrichr_library, top=top)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Enrichr error: {e}")

    # auto
    try:
        return _gprofiler_v1(genes, organism=organism, sources=srcs or ["GO:BP"], user_threshold=user_threshold, top=top)
    except Exception:
        try:
            return _enrichr_v1(genes, library=enrichr_library, top=top)
        except Exception as e2:
            raise HTTPException(status_code=502, detail=f"auto enrichment failed. Last error: {e2}")

@app.get("/viz/module/enrich.png")
def viz_module_enrich_png_v1(
    cid: int = Query(0, ge=0),
    min_size: int = Query(3, ge=1, le=50),
    tool: str = Query("auto"),
    organism: str = Query("hsapiens"),
    gost_sources: str = Query("GO:BP"),
    user_threshold: float = Query(0.05, gt=0.0, lt=1.0),
    enrichr_library: str = Query("GO_Biological_Process_2021"),
    top: int = Query(10, ge=5, le=20),
    proteins_only: bool = Query(True),
    p: Dict[str, Any] = Depends(network_params),
):
    data = module_enrich_v1(
        cid=cid, min_size=min_size, tool=tool, organism=organism,
        gost_sources=gost_sources, user_threshold=user_threshold,
        enrichr_library=enrichr_library, top=top, proteins_only=proteins_only, p=p
    )
    png = _enrich_bar_png_v1(data.get("terms", []) or [], title=f"C{cid} enrichment ({data.get('tool')})", top=top)
    return StreamingResponse(BytesIO(png), media_type="image/png")


# ============================
# ROUTES_HELPER_V1
# adds:
#   GET /
#   GET /routes
# ============================

@app.get("/")
def root():
    return {
        "app": "API_Interactomes",
        "hint": "Open /docs for interactive API, or /routes to list endpoints",
        "docs": "/docs",
        "openapi": "/openapi.json"
    }

@app.get("/routes")
def list_routes():
    out = []
    for r in app.routes:
        methods = sorted(list(getattr(r, "methods", []) or []))
        path = getattr(r, "path", None)
        name = getattr(r, "name", None)
        if path:
            out.append({"path": path, "methods": methods, "name": name})
    out = sorted(out, key=lambda x: x["path"])
    return {"count": len(out), "routes": out}

# ============================
# UI_REGULON_V1
# adds:
#   GET /ui
#   GET /regulon
#   GET /regulon/report
#   GET /viz/regulon
# ============================
from typing import Optional

_UI_HTML_V1 = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Interactome UI</title>
  <style>
    body{font-family:Segoe UI,Arial;padding:18px;max-width:980px}
    textarea{width:100%;height:120px;font-family:Consolas,monospace}
    .row{display:flex;gap:12px;flex-wrap:wrap;margin:10px 0}
    .card{border:1px solid #eee;border-radius:8px;padding:12px}
    label{font-size:13px;color:#444}
    input,select{padding:6px 8px}
    .btn{padding:8px 12px;border:1px solid #ccc;border-radius:8px;background:#fafafa;cursor:pointer}
    .btn:hover{background:#f2f2f2}
    code{background:#f7f7f7;padding:2px 6px;border-radius:4px}
    .muted{color:#666;font-size:13px}
  </style>
</head>
<body>
  <h2>Interactome UI</h2>
  <div class="muted">
    Enter one or multiple names (newline / comma / space). Then choose RNA or Protein (used to set sensible defaults).
    <br/>You can always override sources manually.
  </div>

  <div class="card" style="margin-top:12px">
    <label><b>1) Names</b></label>
    <textarea id="names">TP53&#10;BRCA1&#10;CDKN1A</textarea>

    <div class="row">
      <div>
        <label><b>2) Input type</b></label><br/>
        <select id="kind">
          <option value="auto">Auto (RNA-RNA + RNA-Protein + Protein-Protein)</option>
          <option value="rna">RNA</option>
          <option value="protein">Protein</option>
        </select>
      </div>

      <div>
        <label><b>Regulon mode</b></label><br/>
        <select id="regmode">
          <option value="majority">Majority (default)</option>
          <option value="strict">Strict (intersection of ALL)</option>
          <option value="any">Any (union; mostly for debugging)</option>
        </select>
      </div>

      <div>
        <label><b>Regulon top N</b></label><br/>
        <input id="regtop" type="number" value="50" min="10" max="200" />
      </div>
    </div>

    <div class="row">
      <div class="card" style="flex:1; min-width:280px">
        <label><b>Sources</b></label><br/>
        <label><input type="checkbox" id="src_string" checked> string_ppi (Protein-Protein)</label><br/>
        <label><input type="checkbox" id="src_rbp" checked> encori_rbp_by_target (RNA-Protein)</label><br/>
        <label><input type="checkbox" id="src_rnarna" checked> encori_rna_rna (RNA-RNA)</label>
        <div class="muted" style="margin-top:8px">
          Tip: Protein input usually needs <code>string_ppi</code>. RNA input usually needs <code>encori_rna_rna</code>.
        </div>
      </div>

      <div class="card" style="flex:1; min-width:280px">
        <label><b>STRING params</b></label><br/>
        <label>required_score</label><br/>
        <input id="string_required_score" type="number" value="400" min="0" max="1000"><br/>
        <label>depth</label><br/>
        <input id="string_depth" type="number" value="2" min="1" max="3"><br/>
        <label>limit</label><br/>
        <input id="string_limit" type="number" value="100" min="10" max="500"><br/>
        <label>depth2_limit</label><br/>
        <input id="string_depth2_limit" type="number" value="10" min="0" max="200"><br/>
      </div>
    </div>

    <div class="row">
      <button class="btn" onclick="openReport()">Open /report</button>
      <button class="btn" onclick="openRegulon()">Open regulon overlap</button>
      <button class="btn" onclick="openRegulonViz()">Open regulon network</button>
      <button class="btn" onclick="window.open('/docs','_blank')">/docs</button>
      <button class="btn" onclick="window.open('/routes','_blank')">/routes</button>
    </div>

    <div id="msg" class="muted"></div>
  </div>

<script>
function parseNames(){
  const raw = document.getElementById('names').value || "";
  const parts = raw.split(/[\s,;]+/).map(x=>x.trim()).filter(x=>x.length>0);
  // unique preserve order
  const seen = new Set();
  const out = [];
  for(const p of parts){ if(!seen.has(p)){ out.push(p); seen.add(p); } }
  return out;
}

function getSources(){
  const s = [];
  if(document.getElementById('src_string').checked) s.push("string_ppi");
  if(document.getElementById('src_rbp').checked) s.push("encori_rbp_by_target");
  if(document.getElementById('src_rnarna').checked) s.push("encori_rna_rna");
  return s;
}

function applyDefaults(){
  const k = document.getElementById('kind').value;
  // default sources by kind (user can override after)
  if(k==="protein"){
    document.getElementById('src_string').checked = true;
    document.getElementById('src_rbp').checked = true;
    document.getElementById('src_rnarna').checked = false;
  } else if(k==="rna"){
    document.getElementById('src_string').checked = false;
    document.getElementById('src_rbp').checked = true;
    document.getElementById('src_rnarna').checked = true;
  } else {
    document.getElementById('src_string').checked = true;
    document.getElementById('src_rbp').checked = true;
    document.getElementById('src_rnarna').checked = true;
  }
}

function buildQS(extraParams){
  const names = parseNames();
  if(names.length===0){
    document.getElementById('msg').textContent = "Please enter at least one name.";
    return null;
  }
  const src = getSources();
  if(src.length===0){
    document.getElementById('msg').textContent = "Please select at least one source.";
    return null;
  }

  const params = [];
  for(const n of names){ params.push(["seed", n]); }
  params.push(["sources", src.join(",")]);

  // string params
  params.push(["string_required_score", document.getElementById('string_required_score').value]);
  params.push(["string_depth", document.getElementById('string_depth').value]);
  params.push(["string_limit", document.getElementById('string_limit').value]);
  params.push(["string_depth2_limit", document.getElementById('string_depth2_limit').value]);

  if(extraParams){
    for(const [k,v] of extraParams){ params.push([k,v]); }
  }
  return new URLSearchParams(params).toString();
}

function openReport(){
  const qs = buildQS([]);
  if(!qs) return;
  window.open("/report?"+qs, "_blank");
}

function openRegulon(){
  const mode = document.getElementById('regmode').value;
  const top = document.getElementById('regtop').value;
  const qs = buildQS([["mode",mode],["top",top]]);
  if(!qs) return;
  window.open("/regulon/report?"+qs, "_blank");
}

function openRegulonViz(){
  const mode = document.getElementById('regmode').value;
  const top = Math.min(80, parseInt(document.getElementById('regtop').value || "50"));
  const qs = buildQS([["mode",mode],["top",top]]);
  if(!qs) return;
  window.open("/viz/regulon3?"+qs, "_blank");
}

document.getElementById('kind').addEventListener('change', applyDefaults);
applyDefaults();
</script>

</body>
</html>
"""

@app.get("/ui", response_class=HTMLResponse)
def ui_v1():
    return HTMLResponse(_UI_HTML_V1)

def _reg_edge_weight_v1(e: Dict[str, Any]) -> float:
    import math
    db = str(e.get("source_db") or "").lower()
    kind = str(e.get("kind") or "").lower()
    score = e.get("score")
    support = e.get("support") or 1

    if ("string" in db) or ("ppi" in db) or ("ppi" in kind):
        try:
            sc = float(score)
            if sc > 1.0:
                sc = sc / 1000.0
            return max(0.0, min(1.0, sc))
        except Exception:
            return 0.6

    try:
        sup = float(support)
    except Exception:
        sup = 1.0
    return max(0.3, min(1.0, 0.3 + 0.25 * math.log1p(sup)))

def _default_min_coverage_v1(mode: str, n: int) -> int:
    import math
    mode = (mode or "majority").lower()
    if n <= 1:
        return 1
    if mode == "strict":
        return n
    if mode == "any":
        return 1
    return int(math.ceil(n / 2))

def _regulon_overlap_v1(netw: Dict[str, Any], seeds: List[str], min_coverage: int, top: int, exclude_seed_nodes: bool = True) -> Dict[str, Any]:
    import math
    from collections import defaultdict

    seeds = [str(s).strip() for s in (seeds or []) if str(s).strip()]
    seen = set()
    seeds_u = []
    for s in seeds:
        if s not in seen:
            seeds_u.append(s)
            seen.add(s)
    seeds = seeds_u

    n_seeds = len(seeds)
    if n_seeds == 0:
        return {"seed_count": 0, "seeds": [], "top_interactors": [], "intersection_top": [], "min_coverage": min_coverage}

    node_kind = {n.get("id"): n.get("kind", "unknown") for n in netw.get("nodes", []) if n.get("id")}

    deg = defaultdict(int)
    adj = defaultdict(list)
    for e in netw.get("edges", []):
        a = e.get("source")
        b = e.get("target")
        if not a or not b:
            continue
        deg[a] += 1
        deg[b] += 1
        adj[a].append((b, e))
        adj[b].append((a, e))

    seeds_set = set(seeds)
    items: Dict[str, Any] = {}

    for seed in seeds:
        for nbr, e in adj.get(seed, []):
            if exclude_seed_nodes and nbr in seeds_set:
                continue

            it = items.get(nbr)
            if it is None:
                it = {
                    "node": nbr,
                    "kind": node_kind.get(nbr, "unknown"),
                    "hit_seeds": set(),
                    "seed_best": {},
                    "db_set": set(),
                    "evidence": [],
                    "degree": int(deg.get(nbr, 0)),
                }
                items[nbr] = it

            it["hit_seeds"].add(seed)
            w = _reg_edge_weight_v1(e)
            prev = float(it["seed_best"].get(seed, 0.0))
            if w > prev:
                it["seed_best"][seed] = w

            db = e.get("source_db")
            if db:
                it["db_set"].add(str(db))

            it["evidence"].append({
                "seed": seed,
                "source_db": e.get("source_db"),
                "kind": e.get("kind"),
                "score": e.get("score"),
                "support": e.get("support"),
            })

    out = []
    for it in items.values():
        cov = len(it["hit_seeds"])
        if cov < min_coverage:
            continue

        conf_sum = float(sum(it["seed_best"].values()))
        conf_mean = conf_sum / cov if cov > 0 else 0.0
        db_support = len(it["db_set"])
        specificity = conf_sum / (math.log1p(it["degree"]) + 1e-9)

        out.append({
            "node": it["node"],
            "kind": it["kind"],
            "coverage": cov,
            "coverage_ratio": cov / n_seeds,
            "confidence_sum": round(conf_sum, 6),
            "confidence_mean": round(conf_mean, 6),
            "db_support": db_support,
            "degree": it["degree"],
            "specificity_score": round(specificity, 6),
            "hit_seeds": sorted(list(it["hit_seeds"])),
            "evidence": it["evidence"][:50],
        })

    out.sort(key=lambda x: (-x["coverage"], -x["specificity_score"], -x["confidence_sum"], -x["db_support"], x["node"]))
    inter = [x for x in out if x["coverage"] == n_seeds]

    return {
        "seed_count": n_seeds,
        "seeds": seeds,
        "min_coverage": min_coverage,
        "total_candidates": len(out),
        "intersection_count": len(inter),
        "top_interactors": out[:top],
        "intersection_top": inter[:top],
    }

@app.get("/regulon")
def regulon_v1(
    mode: str = Query("majority", description="strict|majority|any"),
    min_coverage: Optional[int] = Query(None, ge=1),
    top: int = Query(50, ge=5, le=500),
    p: Dict[str, Any] = Depends(network_params),
):
    netw = build_network(**p)
    seeds = p.get("seeds", []) or []
    if min_coverage is None:
        min_coverage = _default_min_coverage_v1(mode, len(seeds))
    return _regulon_overlap_v1(netw, seeds=seeds, min_coverage=min_coverage, top=top)

@app.get("/regulon/report", response_class=HTMLResponse)
def regulon_report_v1(
    mode: str = Query("majority"),
    min_coverage: Optional[int] = Query(None, ge=1),
    top: int = Query(50, ge=5, le=200),
    p: Dict[str, Any] = Depends(network_params),
):
    netw = build_network(**p)
    seeds = p.get("seeds", []) or []
    if not seeds:
        return HTMLResponse("<h3>No seed provided</h3>")

    if min_coverage is None:
        min_coverage = _default_min_coverage_v1(mode, len(seeds))

    reg = _regulon_overlap_v1(netw, seeds=seeds, min_coverage=min_coverage, top=top)

    from urllib.parse import urlencode
    q = []
    for s in seeds:
        q.append(("seed", s))
    q.append(("sources", ",".join(p.get("sources", []))))

    for k in ["string_depth","string_required_score","string_limit","string_depth2_limit","assembly","cell_type","clip_exp_num","pancancer_num","inter_num","exp_num"]:
        if k in p and p[k] is not None:
            q.append((k, p[k]))

    q += [("mode", mode), ("min_coverage", min_coverage), ("top", top)]
    qs = urlencode(q, doseq=True)

    json_href = f"/regulon?{qs}"
    viz_href = f"/viz/regulon3?{qs}"

    rows = []
    for it in reg["top_interactors"]:
        k = str(it.get("kind") or "unknown").lower()
        rows.append(
            f"<tr data-kind='{k}'>"
            f"<td>{it['node']}</td><td>{it.get('kind','unknown')}</td>"
            f"<td style='text-align:right'>{it['coverage']}/{reg['seed_count']}</td>"
            f"<td style='text-align:right'>{it['confidence_mean']:.3f}</td>"
            f"<td style='text-align:right'>{it['db_support']}</td>"
            f"<td>{', '.join(it['hit_seeds'])}</td></tr>"
        )

    table = """
    <div style="margin:10px 0; display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
      <label><b>Filter by interactor kind:</b></label>
      <select id="kindFilter">
        <option value="all">All</option>
        <option value="protein">protein</option>
        <option value="rna">rna</option>
        <option value="unknown">unknown</option>
      </select>
      <span style="color:#666;">(Client-side filter; ranking unchanged)</span>
    </div>
    <table class='t'>
      <tr><th>Interactor</th><th>Kind</th><th>Coverage</th><th>Conf(mean)</th><th>DB</th><th>Seeds hit</th></tr>
    """ + "".join(rows) + "</table>" + """
    <script>
      const sel = document.getElementById('kindFilter');
      sel.addEventListener('change', () => {
        const v = sel.value;
        document.querySelectorAll('tr[data-kind]').forEach(tr => {
          const k = tr.getAttribute('data-kind') || 'unknown';
          tr.style.display = (v === 'all' || k === v) ? '' : 'none';
        });
      });
    </script>
    """

    html = f"""
    <html><head><meta charset="utf-8">
    <style>
      body{{font-family:Segoe UI, Arial; padding:18px; max-width:1200px}}
      .t{{border-collapse:collapse; width:100%}}
      .t th,.t td{{border:1px solid #eee; padding:6px 8px; vertical-align:top}}
      .t th{{background:#fafafa}}
      code{{background:#f7f7f7; padding:2px 6px; border-radius:4px}}
    </style></head><body>
    <h2>Regulon overlap</h2>
    <div style="color:#666; margin-bottom:10px;">
      seeds: <b>{', '.join(seeds)}</b> |
      mode: <b>{mode}</b> |
      min_coverage: <b>{min_coverage}</b> |
      intersection: <b>{reg['intersection_count']}</b> |
      candidates: <b>{reg['total_candidates']}</b>
    </div>
    <div style="margin-bottom:12px;">
      <a href="{json_href}">Download JSON</a> |
      <a href="{viz_href}">Open regulon network</a> |
      <a href="/ui">Back to UI</a>
    </div>
    <p style="color:#666; margin-top:0;">
      Ranking = coverage → specificity (penalize promiscuous hubs) → confidence → db_support.
    </p>
    {table}
    </body></html>
    """
    return HTMLResponse(html)
@app.get("/viz/regulon", response_class=HTMLResponse)
def viz_regulon_v1(
    mode: str = Query("majority"),
    min_coverage: Optional[int] = Query(None, ge=1),
    top: int = Query(30, ge=5, le=200),
    p: Dict[str, Any] = Depends(network_params),
):
    netw = build_network(**p)
    seeds = p.get("seeds", []) or []
    if not seeds:
        return HTMLResponse("<h3>No seed provided</h3>")

    if min_coverage is None:
        min_coverage = _default_min_coverage_v1(mode, len(seeds))

    reg = _regulon_overlap_v1(netw, seeds=seeds, min_coverage=min_coverage, top=top)
    interactors = [x["node"] for x in reg.get("top_interactors", [])[:top]]

    # If no shared interactors, show a friendly message instead of a messy seed-only graph
    if not interactors:
        from urllib.parse import urlencode
        q = []
        for s in seeds:
            q.append(("seed", s))
        q.append(("sources", ",".join(p.get("sources", []))))
        for k in ["string_depth","string_required_score","string_limit","string_depth2_limit","assembly","cell_type","clip_exp_num","pancancer_num","inter_num","exp_num"]:
            if k in p and p[k] is not None:
                q.append((k, p[k]))
        q += [("mode", mode), ("min_coverage", min_coverage), ("top", top)]
        qs = urlencode(q, doseq=True)
        return HTMLResponse(f"""
        <html><head><meta charset="utf-8">
        <style>
          body{{font-family:Segoe UI,Arial;padding:18px;max-width:900px}}
          code{{background:#f7f7f7;padding:2px 6px;border-radius:4px}}
        </style></head><body>
          <h2>Regulon network</h2>
          <p>No shared interactors found under current threshold
          (<code>mode={mode}</code>, <code>min_coverage={min_coverage}</code>).</p>
          <ul>
            <li>Try <code>mode=any</code> or lower <code>min_coverage</code> to include more candidates.</li>
            <li>Or open the overlap table to inspect coverage / kinds.</li>
          </ul>
          <p>
            <a href="/regulon/report?{qs}">Open regulon overlap table</a> |
            <a href="/ui">Back to UI</a>
          </p>
        </body></html>
        """)

    seed_set = set(seeds)
    inter_set = set(interactors)

    # nodes: seeds + selected interactors
    nodes = []
    node_map = {n.get("id"): n for n in netw.get("nodes", []) if n.get("id")}
    for nid in list(seed_set | inter_set):
        n = dict(node_map.get(nid, {"id": nid, "label": nid, "kind": "unknown"}))
        if nid in seed_set:
            n["group"] = "seed"
            n["role"] = "seed"
        else:
            n["group"] = n.get("kind", "interactor")
            n["role"] = "interactor"
        nodes.append(n)

    # edges: ONLY seed<->interactor, and dedup by pair keeping best evidence
    best = {}
    for e in netw.get("edges", []):
        u = e.get("source"); v = e.get("target")
        if not u or not v:
            continue

        # keep only bipartite edges: seed <-> interactor
        if (u in seed_set and v in inter_set) or (v in seed_set and u in inter_set):
            seed = u if u in seed_set else v
            other = v if seed == u else u
            key = (seed, other)

            w = _reg_edge_weight_v1(e)
            prev = best.get(key)
            if (prev is None) or (w > prev[0]):
                ee = dict(e)
                ee["source"] = seed
                ee["target"] = other
                ee["weight"] = w
                best[key] = (w, ee)

    edges = [x[1] for x in best.values()]

    sub = {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "regulon": {
                "seeds": seeds,
                "mode": mode,
                "min_coverage": min_coverage,
                "top": top,
                "view": "bipartite_seed_interactor",
                "dedup": True
            }
        }
    }

    if "pyvis_html" in globals():
        return HTMLResponse(pyvis_html(sub))

    return HTMLResponse(f"<pre>{json.dumps(sub, ensure_ascii=False, indent=2)}</pre>")


# ============================
# REGULON_VIZ_PRO_V3
# /viz/regulon3 : clean bipartite regulon view + dedup edges + LR layout + details panel
# ============================

@app.get("/viz/regulon3", response_class=HTMLResponse)
def viz_regulon_v3(
    mode: str = Query("majority"),
    min_coverage: Optional[int] = Query(None, ge=1),
    top: int = Query(30, ge=5, le=200),
    labels: str = Query("top"),  # top|all|none
    p: Dict[str, Any] = Depends(network_params),
):
    import json as _json

    netw = build_network(**p)
    seeds = p.get("seeds", []) or []
    if not seeds:
        return HTMLResponse("<h3>No seed provided</h3>")

    if min_coverage is None:
        min_coverage = _default_min_coverage_v1(mode, len(seeds))

    reg = _regulon_overlap_v1(netw, seeds=seeds, min_coverage=min_coverage, top=top)
    items = (reg.get("top_interactors") or [])[:top]
    inter = [x.get("node") for x in items if x.get("node")]

    if not inter:
        return HTMLResponse("<h3>No shared interactors under current thresholds</h3><p>Try mode=any or lower min_coverage.</p>")

    seedset = set(seeds)
    interset = set(inter)

    node_kind = {n.get("id"): (n.get("kind") or "unknown") for n in netw.get("nodes", []) if n.get("id")}
    metrics = {x["node"]: x for x in items}

    # 1) aggregate edges per (seed, interactor) => remove spaghetti (parallel edges)
    agg = {}
    for e in netw.get("edges", []):
        u = e.get("source"); v = e.get("target")
        if not u or not v:
            continue

        if u in seedset and v in interset:
            s, t = u, v
        elif v in seedset and u in interset:
            s, t = v, u
        else:
            continue

        key = (s, t)
        w = float(_reg_edge_weight_v1(e)) if "_reg_edge_weight_v1" in globals() else 0.5

        a = agg.get(key)
        if a is None:
            a = {"best_w": w, "dbs": set(), "kinds": set(), "support": 0, "best_score": None}
            agg[key] = a

        a["best_w"] = max(a["best_w"], w)
        if e.get("source_db"):
            a["dbs"].add(str(e.get("source_db")))
        if e.get("kind"):
            a["kinds"].add(str(e.get("kind")))

        try:
            a["support"] += int(e.get("support") or 1)
        except Exception:
            a["support"] += 1

        sc = e.get("score")
        if sc is not None:
            try:
                f = float(sc)
                if (a["best_score"] is None) or (f > a["best_score"]):
                    a["best_score"] = f
            except Exception:
                pass

    # label policy (avoid clutter)
    labels = (labels or "top").lower()
    if labels not in ("top", "all", "none"):
        labels = "top"

    def _lab(i: int, name: str) -> str:
        if labels == "all":
            return name
        if labels == "none":
            return ""
        return name if i < 12 else ""  # top 12 labels only

    # 2) build nodes + info
    nodes = []
    node_info = {}

    # seeds (level 0)
    for s in seeds:
        k = (node_kind.get(s, "unknown") or "unknown").lower()
        grp = f"seed_{k}" if k in ("rna","protein") else "seed_unknown"
        nodes.append({"id": s, "label": s, "group": grp, "level": 0})
        node_info[s] = {"id": s, "role": "seed", "kind": k}

    # interactors (level 1)
    for i, x in enumerate(inter):
        k = (node_kind.get(x, "unknown") or "unknown").lower()
        grp = k if k in ("rna","protein") else "unknown"
        m = metrics.get(x, {})

        node_info[x] = {
            "id": x,
            "role": "interactor",
            "kind": k,
            "coverage": m.get("coverage"),
            "seed_count": reg.get("seed_count", len(seeds)),
            "confidence_mean": m.get("confidence_mean"),
            "db_support": m.get("db_support"),
            "degree": m.get("degree"),
            "hit_seeds": m.get("hit_seeds", []),
        }

        title = (
            f"<b>{x}</b><br>"
            f"kind: {k}<br>"
            f"coverage: {m.get('coverage')}/{reg.get('seed_count', len(seeds))}<br>"
            f"conf_mean: {m.get('confidence_mean')}<br>"
            f"db_support: {m.get('db_support')}<br>"
            f"hit_seeds: {', '.join(m.get('hit_seeds', []))}"
        )

        nodes.append({
            "id": x,
            "label": _lab(i, x),
            "group": grp,
            "level": 1,
            "title": title,
        })

    # 3) build edges + info
    edges = []
    edge_info = {}
    j = 0
    for (s, t), a in agg.items():
        eid = f"e{j}"
        dbs = sorted(list(a["dbs"])) if a["dbs"] else []
        kds = sorted(list(a["kinds"])) if a["kinds"] else []

        title = (
            f"<b>{s} → {t}</b><br>"
            f"db: {', '.join(dbs) if dbs else 'n/a'}<br>"
            f"kinds: {', '.join(kds) if kds else 'n/a'}<br>"
            f"support(sum): {a['support']}<br>"
            f"best_score: {a['best_score']}"
        )

        edges.append({
            "id": eid,
            "from": s,
            "to": t,
            "value": 1 + 4 * float(a["best_w"]),
            "title": title,
            "arrows": "to",
        })

        edge_info[eid] = {
            "id": eid,
            "from": s,
            "to": t,
            "db": dbs,
            "kinds": kds,
            "support_sum": a["support"],
            "best_w": a["best_w"],
            "best_score": a["best_score"],
        }
        j += 1

    nodes_json = _json.dumps(nodes, ensure_ascii=False)
    edges_json = _json.dumps(edges, ensure_ascii=False)
    nodeinfo_json = _json.dumps(node_info, ensure_ascii=False)
    edgeinfo_json = _json.dumps(edge_info, ensure_ascii=False)

    options = {
        "layout": {"hierarchical": {"enabled": True, "direction": "LR", "sortMethod": "directed",
                                    "levelSeparation": 260, "nodeSpacing": 180, "treeSpacing": 220}},
        "physics": {"enabled": False},
        "edges": {"smooth": False, "arrows": {"to": {"enabled": True, "scaleFactor": 0.6}}},
        "interaction": {"hover": True, "tooltipDelay": 80, "navigationButtons": True, "keyboard": True},
        "nodes": {"font": {"size": 16}},
        "groups": {
            "seed_protein": {"shape": "box"},
            "seed_rna": {"shape": "box"},
            "seed_unknown": {"shape": "box"},
            "protein": {"shape": "dot"},
            "rna": {"shape": "triangle"},
            "unknown": {"shape": "dot"},
        }
    }
    options_json = _json.dumps(options, ensure_ascii=False)

    tpl = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Regulon network (pro)</title>
  <script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
  <style>
    body{font-family:Segoe UI,Arial;padding:18px}
    #wrap{display:flex;gap:12px}
    #net{flex:3;height:780px;border:1px solid #eee;border-radius:10px}
    #panel{flex:1;height:780px;border:1px solid #eee;border-radius:10px;padding:10px;overflow:auto;
           white-space:pre-wrap;font-family:Consolas,monospace;font-size:12px}
    .muted{color:#666;font-size:13px;margin-top:6px}
    .row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:10px}
    input{padding:6px 8px;min-width:240px}
    button{padding:6px 10px;cursor:pointer}
  </style>
</head>
<body>
  <h2>Regulon network</h2>
  <div class="muted">
    Clean bipartite view (seeds → shared interactors). Parallel edges are merged. Click node/edge for details.
  </div>

  <div class="row">
    <b>Find node:</b>
    <input id="q" placeholder="e.g. TP53, BRCA1, MALAT1">
    <button id="go">Go</button>
    <span class="muted">labels: __LABELS__ (use &labels=all|top|none)</span>
  </div>

  <div id="wrap">
    <div id="net"></div>
    <div id="panel">Click a node/edge to view details here.</div>
  </div>

<script>
  const nodes = new vis.DataSet(__NODES__);
  const edges = new vis.DataSet(__EDGES__);
  const nodeInfo = __NODEINFO__;
  const edgeInfo = __EDGEINFO__;
  const options = __OPTIONS__;

  const container = document.getElementById("net");
  const network = new vis.Network(container, {nodes, edges}, options);

  function show(obj){
    document.getElementById("panel").textContent = JSON.stringify(obj, null, 2);
  }

  network.on("click", (params) => {
    if (params.nodes && params.nodes.length){
      const id = params.nodes[0];
      show(nodeInfo[id] || {id});
    } else if (params.edges && params.edges.length){
      const id = params.edges[0];
      show(edgeInfo[id] || {id});
    }
  });

  document.getElementById("go").onclick = () => {
    const q = (document.getElementById("q").value || "").trim();
    if (!q) return;
    const n = nodes.get(q);
    if (!n){
      alert("Node not found: " + q);
      return;
    }
    network.selectNodes([q]);
    network.focus(q, {scale: 1.2, animation: true});
    show(nodeInfo[q] || {id: q});
  };
</script>
</body>
</html>
"""
    html = (tpl.replace("__NODES__", nodes_json)
               .replace("__EDGES__", edges_json)
               .replace("__NODEINFO__", nodeinfo_json)
               .replace("__EDGEINFO__", edgeinfo_json)
               .replace("__OPTIONS__", options_json)
               .replace("__LABELS__", labels))
    return HTMLResponse(html)


