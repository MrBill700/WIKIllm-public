#!/usr/bin/env python3
"""Export a WIKIllm vault's page graph to ONE self-contained interactive HTML file.

Walks wiki/ (optionally _meta/ + CLAUDE.md), parses YAML frontmatter (title/tags) and
[[wikilinks]] (body + frontmatter), builds a node/edge graph, and emits a single
portable HTML file with a vanilla-JS canvas force-directed graph -- no CDN, no
dependencies, no Obsidian required. Share the file with anyone; it opens in any
browser and nothing leaves the file.

  python scripts/graph_export.py                        # -> knowledge-graph.html
  python scripts/graph_export.py --out map.html --open  # write + open in browser
  python scripts/graph_export.py --include-meta         # also graph _meta/ + CLAUDE.md

Stdlib only. O(n^2) layout -- fine to a few hundred pages; thousands will be sluggish.
"""
from __future__ import annotations

import argparse
import datetime
import html
import json
import re
import sys
from pathlib import Path

WIKILINK = re.compile(r"(!?)\[\[([^\]\n]+?)\]\]")
FENCE = re.compile(r"```.*?```", re.DOTALL)

# top-level wiki/ folder -> (legend label, color)
CATEGORIES = {
    "entities": ("Entities", "#e0803a"),
    "concepts": ("Concepts", "#3a86e0"),
    "sources": ("Sources", "#8a63d2"),
    "analysis": ("Analysis", "#2fb37f"),
    "data": ("Data", "#d24f6e"),
}
DEFAULT_CAT = ("Other", "#8a94a6")


def parse_frontmatter(text: str):
    title, tags = None, []
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm = text[3:end]
            m = re.search(r"^title:\s*(.+)$", fm, re.M)
            if m:
                title = m.group(1).strip().strip("\"'")
            m = re.search(r"^tags:\s*\[([^\]]*)\]", fm, re.M)
            if m:
                tags = [t.strip().strip("\"'") for t in m.group(1).split(",") if t.strip()]
            else:
                m = re.search(r"^tags:\s*\n((?:\s*-\s*.+\n?)+)", fm, re.M)
                if m:
                    tags = [re.sub(r"^\s*-\s*", "", l).strip().strip("\"'")
                            for l in m.group(1).splitlines() if l.strip()]
    return title, tags


def norm(t: str) -> str:
    return t.replace("\\", "/").strip().strip("/")


def main() -> int:
    ap = argparse.ArgumentParser(description="Export a WIKIllm vault to a self-contained interactive HTML graph.")
    ap.add_argument("--root", default=".", help="Vault root (default: cwd)")
    ap.add_argument("--out", default="knowledge-graph.html", help="Output HTML path (relative to root unless absolute)")
    ap.add_argument("--include-meta", action="store_true", help="Also include _meta/*.md and CLAUDE.md")
    ap.add_argument("--open", action="store_true", help="Open the result in a browser when done")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    files = []
    if (root / "wiki").exists():
        files += sorted((root / "wiki").rglob("*.md"))
    if args.include_meta:
        if (root / "_meta").exists():
            files += sorted((root / "_meta").rglob("*.md"))
        if (root / "CLAUDE.md").exists():
            files.append(root / "CLAUDE.md")
    if not files:
        print(f"No markdown found under {root}/wiki -- run from a vault root (or pass --root).", file=sys.stderr)
        return 1

    nodes: dict[str, dict] = {}
    basename_map: dict[str, list[str]] = {}
    for p in files:
        rel = norm(str(p.relative_to(root)))
        nid = rel[5:] if rel.startswith("wiki/") else rel
        if nid.endswith(".md"):
            nid = nid[:-3]
        text = p.read_text(encoding="utf-8", errors="replace")
        title, tags = parse_frontmatter(text)
        top = nid.split("/")[0] if "/" in nid else ""
        nodes[nid] = {
            "id": nid, "label": title or Path(nid).name, "cat": top,
            "tags": tags, "central": "central" in tags, "_text": text,
        }
        basename_map.setdefault(Path(nid).name, []).append(nid)

    def resolve(target: str):
        t = norm(target).split("|")[0].split("#")[0].strip()
        if not t:
            return None
        if t.startswith("wiki/"):
            t = t[5:]
        if "." in Path(t).name:  # asset embed (.base/.png/...), not a page
            return None
        if t in nodes:
            return t
        cand = basename_map.get(Path(t).name)
        if cand:
            if len(cand) == 1:
                return cand[0]
            for c in cand:
                if c.endswith(t):
                    return c
            return cand[0]
        return None

    edges = set()
    for nid, n in nodes.items():
        body = FENCE.sub("", n.pop("_text"))
        for _bang, tgt in WIKILINK.findall(body):
            dst = resolve(tgt)
            if dst and dst != nid:
                edges.add((nid, dst))

    inbound = {nid: 0 for nid in nodes}
    for _s, t in edges:
        inbound[t] += 1

    node_list = [{
        "id": n["id"], "label": n["label"], "cat": n["cat"], "tags": n["tags"],
        "central": n["central"], "inbound": inbound[n["id"]],
    } for n in nodes.values()]
    edge_list = [{"s": s, "t": t} for s, t in sorted(edges)]

    data = {
        "nodes": node_list, "edges": edge_list, "vault": root.name,
        "cats": {k: {"label": v[0], "color": v[1]} for k, v in CATEGORIES.items()},
        "defaultCat": {"label": DEFAULT_CAT[0], "color": DEFAULT_CAT[1]},
    }
    payload = json.dumps(data).replace("</", "<\\/")
    today = datetime.date.today().isoformat()

    out_html = (TEMPLATE
                .replace("__DATA__", payload)
                .replace("__VAULT__", html.escape(root.name))
                .replace("__NODES__", str(len(node_list)))
                .replace("__EDGES__", str(len(edge_list)))
                .replace("__DATE__", today))

    outp = Path(args.out)
    if not outp.is_absolute():
        outp = root / outp
    outp.write_text(out_html, encoding="utf-8")
    print(f"Wrote {outp}  ({len(node_list)} nodes, {len(edge_list)} edges)")
    if args.open:
        import webbrowser
        webbrowser.open(outp.as_uri())
    return 0


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__VAULT__ -- knowledge graph</title>
<style>
  :root{--bg:#0f1420;--panel:#171d2b;--edge:#33405c;--text:#dfe6f2;--muted:#8a94a6;}
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;background:var(--bg);color:var(--text);
    font:14px/1.4 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;overflow:hidden}
  #wrap{position:fixed;inset:0}
  canvas{display:block;cursor:grab}
  canvas.drag{cursor:grabbing}
  #hud{position:fixed;top:12px;left:12px;right:12px;display:flex;gap:10px;
    align-items:center;pointer-events:none;flex-wrap:wrap}
  #hud>*{pointer-events:auto}
  h1{font-size:14px;margin:0;font-weight:600}
  h1 small{color:var(--muted);font-weight:400}
  #search{background:var(--panel);border:1px solid var(--edge);color:var(--text);
    border-radius:7px;padding:6px 10px;width:210px;outline:none}
  button{background:var(--panel);border:1px solid var(--edge);color:var(--text);
    border-radius:7px;padding:6px 11px;cursor:pointer}
  button:hover{border-color:#5b6b8f}
  #legend{position:fixed;bottom:12px;left:12px;background:rgba(23,29,43,.85);
    border:1px solid var(--edge);border-radius:8px;padding:8px 10px;display:flex;
    gap:14px;flex-wrap:wrap;backdrop-filter:blur(4px)}
  .lg{display:flex;align-items:center;gap:6px;color:var(--muted);cursor:pointer;user-select:none}
  .lg.off{opacity:.35;text-decoration:line-through}
  .dot{width:11px;height:11px;border-radius:50%}
  #tip{position:fixed;pointer-events:none;background:#0c101a;border:1px solid var(--edge);
    border-radius:7px;padding:7px 9px;max-width:280px;display:none;z-index:5;box-shadow:0 6px 22px rgba(0,0,0,.5)}
  #tip b{color:#fff}
  #tip .m{color:var(--muted);font-size:12px}
  #info{position:fixed;top:56px;right:12px;width:260px;background:rgba(23,29,43,.92);
    border:1px solid var(--edge);border-radius:9px;padding:12px 14px;display:none;
    max-height:70vh;overflow:auto;backdrop-filter:blur(4px)}
  #info h2{font-size:14px;margin:0 0 4px}
  #info .m{color:var(--muted);font-size:12px;margin-bottom:8px}
  #info a{color:#7db1ff;cursor:pointer;text-decoration:none;display:block;padding:2px 0}
  #info a:hover{text-decoration:underline}
  #foot{position:fixed;bottom:12px;right:12px;color:var(--muted);font-size:11px;text-align:right}
</style></head>
<body>
<div id="wrap"><canvas id="cv"></canvas></div>
<div id="hud">
  <h1>__VAULT__ <small>knowledge graph</small></h1>
  <input id="search" placeholder="Search pages...">
  <button id="fit">Fit</button>
  <button id="reheat">Reheat</button>
</div>
<div id="legend"></div>
<div id="tip"></div>
<div id="info"></div>
<div id="foot">Generated __DATE__ &middot; __NODES__ pages, __EDGES__ links &middot; self-contained (nothing leaves this file)</div>
<script>
const DATA = __DATA__;
const cv = document.getElementById('cv'), ctx = cv.getContext('2d');
const tip = document.getElementById('tip'), info = document.getElementById('info');
let dpr = Math.min(window.devicePixelRatio||1, 2), W=0, H=0;
function catColor(c){ return (DATA.cats[c]||DATA.defaultCat).color; }
function catLabel(c){ return (DATA.cats[c]||DATA.defaultCat).label; }

// ---- build graph ----
const N = DATA.nodes.map(n=>({...n, x:0,y:0,vx:0,vy:0}));
const byId = {}; N.forEach(n=>byId[n.id]=n);
const E = DATA.edges.filter(e=>byId[e.s]&&byId[e.t]);
const adj = {}; N.forEach(n=>adj[n.id]=new Set());
E.forEach(e=>{adj[e.s].add(e.t);adj[e.t].add(e.s);});
function radius(n){ return 4 + Math.sqrt(n.inbound)*2.4 + (n.central?3:0); }
// seed positions on a circle by category so it opens tidy
const cats = [...new Set(N.map(n=>n.cat))];
N.forEach((n,i)=>{ const a=i*2.399963; const r=40+ (i%17)*9;
  n.x=Math.cos(a)*r; n.y=Math.sin(a)*r; });

// ---- physics ----
const K=is_big()?34:44, SPRING=0.02, LINKLEN=48, GRAV=0.015, DAMP=0.86;
function is_big(){ return DATA.nodes.length>220; }
let dragging=null, pinned=null;
function step(){
  for(let i=0;i<N.length;i++){ const a=N[i]; let fx=0,fy=0;
    for(let j=0;j<N.length;j++){ if(i===j)continue; const b=N[j];
      let dx=a.x-b.x, dy=a.y-b.y; let d2=dx*dx+dy*dy||0.01; let d=Math.sqrt(d2);
      let f=(K*K)/d2; fx+=dx/d*f; fy+=dy/d*f; }
    fx-=a.x*GRAV; fy-=a.y*GRAV;
    a._fx=fx; a._fy=fy;
  }
  for(const e of E){ const a=byId[e.s], b=byId[e.t];
    let dx=b.x-a.x, dy=b.y-a.y; let d=Math.sqrt(dx*dx+dy*dy)||0.01;
    let f=(d-LINKLEN)*SPRING; let ux=dx/d*f, uy=dy/d*f;
    a._fx+=ux; a._fy+=uy; b._fx-=ux; b._fy-=uy;
  }
  for(const a of N){ if(a===dragging)continue;
    a.vx=(a.vx+a._fx)*DAMP; a.vy=(a.vy+a._fy)*DAMP;
    a.x+=a.vx; a.y+=a.vy;
  }
}
for(let i=0;i<260;i++) step();  // warm up before first paint

// ---- camera ----
let scale=1, tx=0, ty=0;
function fit(){
  let minx=1e9,miny=1e9,maxx=-1e9,maxy=-1e9;
  for(const n of N){ minx=Math.min(minx,n.x);miny=Math.min(miny,n.y);
    maxx=Math.max(maxx,n.x);maxy=Math.max(maxy,n.y); }
  const w=Math.max(maxx-minx,1), h=Math.max(maxy-miny,1);
  scale=Math.min(W/(w+120), H/(h+120))*0.92; scale=Math.max(0.05,Math.min(scale,2.5));
  tx=W/2-(minx+maxx)/2*scale; ty=H/2-(miny+maxy)/2*scale;
}
function sx(x){return x*scale+tx;} function sy(y){return y*scale+ty;}
function wx(x){return (x-tx)/scale;} function wy(y){return (y-ty)/scale;}

// ---- render ----
let hover=null, selected=null, query='';
const catOff = {};
function visible(n){
  if(catOff[n.cat]) return false;
  if(query && !(n.label.toLowerCase().includes(query)||n.id.toLowerCase().includes(query))) return false;
  return true;
}
function highlightSet(){
  const base = selected||hover; if(!base) return null;
  const s=new Set([base.id]); adj[base.id].forEach(x=>s.add(x)); return s;
}
function draw(){
  ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.clearRect(0,0,W,H);
  const hl=highlightSet();
  // edges
  ctx.lineWidth=1;
  for(const e of E){ const a=byId[e.s], b=byId[e.t];
    if(!visible(a)||!visible(b)) continue;
    const on = hl && (hl.has(a.id)&&hl.has(b.id));
    ctx.strokeStyle = on ? 'rgba(125,177,255,.7)' : (hl?'rgba(51,64,92,.25)':'rgba(51,64,92,.55)');
    ctx.beginPath(); ctx.moveTo(sx(a.x),sy(a.y)); ctx.lineTo(sx(b.x),sy(b.y)); ctx.stroke();
  }
  // nodes
  for(const n of N){ if(!visible(n))continue;
    const dim = hl && !hl.has(n.id);
    const r=radius(n)*Math.max(scale,.5)/Math.max(scale,.5); // r in world*scale below
    const R=radius(n)*Math.min(Math.max(scale,.55),1.6);
    ctx.globalAlpha = dim?0.25:1;
    ctx.beginPath(); ctx.arc(sx(n.x),sy(n.y),R,0,7); ctx.fillStyle=catColor(n.cat); ctx.fill();
    if(n.central){ ctx.lineWidth=2; ctx.strokeStyle='#fff'; ctx.stroke(); }
    ctx.globalAlpha=1;
    const showLabel = (R>7)||n===hover||n===selected||(query&&visible(n));
    if(showLabel && !dim){
      ctx.fillStyle = (n===selected||n===hover)?'#fff':'#c3ccdb';
      ctx.font = (n.central?'600 ':'')+ (12) +'px -apple-system,Segoe UI,sans-serif';
      ctx.fillText(n.label, sx(n.x)+R+3, sy(n.y)+4);
    }
  }
}
function loop(){ step(); draw(); requestAnimationFrame(loop); }

// ---- hit testing ----
function pick(mx,my){ let best=null,bd=1e9;
  for(const n of N){ if(!visible(n))continue;
    const dx=mx-sx(n.x), dy=my-sy(n.y); const d=dx*dx+dy*dy;
    const R=radius(n)*Math.min(Math.max(scale,.55),1.6)+4;
    if(d<R*R && d<bd){bd=d;best=n;} }
  return best;
}
// ---- interaction ----
let panning=false, lastx=0,lasty=0, moved=false;
cv.addEventListener('mousedown',e=>{ const n=pick(e.clientX,e.clientY); moved=false;
  if(n){ dragging=n; cv.classList.add('drag'); } else { panning=true; lastx=e.clientX;lasty=e.clientY; cv.classList.add('drag'); }
});
window.addEventListener('mousemove',e=>{
  if(dragging){ dragging.x=wx(e.clientX); dragging.y=wy(e.clientY); dragging.vx=dragging.vy=0; moved=true; return; }
  if(panning){ tx+=e.clientX-lastx; ty+=e.clientY-lasty; lastx=e.clientX;lasty=e.clientY; moved=true; return; }
  const n=pick(e.clientX,e.clientY); hover=n;
  if(n){ tip.style.display='block'; tip.style.left=(e.clientX+14)+'px'; tip.style.top=(e.clientY+14)+'px';
    tip.innerHTML='<b>'+esc(n.label)+'</b><div class="m">'+catLabel(n.cat)+
      ' &middot; '+n.inbound+' in / '+adj[n.id].size+' links'+
      (n.tags.length?'<br>'+n.tags.map(esc).join(', '):'')+'</div>';
    cv.style.cursor='pointer';
  } else { tip.style.display='none'; cv.style.cursor=''; }
});
window.addEventListener('mouseup',e=>{
  if(dragging&&!moved){ select(dragging); }
  else if(panning&&!moved){ select(null); }
  dragging=null; panning=false; cv.classList.remove('drag');
});
cv.addEventListener('wheel',e=>{ e.preventDefault();
  const f=Math.exp(-e.deltaY*0.0012); const mx=e.clientX,my=e.clientY;
  const bx=wx(mx),by=wy(my); scale=Math.max(0.04,Math.min(scale*f,4));
  tx=mx-bx*scale; ty=my-by*scale;
},{passive:false});
function select(n){ selected=n;
  if(!n){ info.style.display='none'; return; }
  const nb=[...adj[n.id]].map(id=>byId[id]).sort((a,b)=>b.inbound-a.inbound);
  info.style.display='block';
  info.innerHTML='<h2>'+esc(n.label)+'</h2><div class="m">'+catLabel(n.cat)+
    ' &middot; '+n.id+(n.tags.length?'<br>'+n.tags.map(esc).join(', '):'')+'</div>'+
    '<div class="m">'+nb.length+' connections</div>'+
    nb.map(x=>'<a data-id="'+esc(x.id)+'">'+esc(x.label)+'</a>').join('');
  info.querySelectorAll('a').forEach(a=>a.onclick=()=>{ const t=byId[a.dataset.id];
    if(t){ select(t); centerOn(t); } });
}
function centerOn(n){ tx=W/2-n.x*scale; ty=H/2-n.y*scale; }
function esc(s){ return (s+'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

// ---- controls ----
document.getElementById('search').addEventListener('input',e=>{ query=e.target.value.trim().toLowerCase(); });
document.getElementById('fit').onclick=fit;
document.getElementById('reheat').onclick=()=>{ N.forEach(n=>{n.vx=(Math.random()-.5)*6;n.vy=(Math.random()-.5)*6;}); };
// legend (click to toggle a category)
const lg=document.getElementById('legend');
const present=[...new Set(N.map(n=>n.cat))].sort();
present.forEach(c=>{ const d=document.createElement('div'); d.className='lg';
  d.innerHTML='<span class="dot" style="background:'+catColor(c)+'"></span>'+catLabel(c);
  d.onclick=()=>{ catOff[c]=!catOff[c]; d.classList.toggle('off',catOff[c]); };
  lg.appendChild(d);
});

// ---- resize ----
function resize(){ W=window.innerWidth; H=window.innerHeight;
  cv.width=W*dpr; cv.height=H*dpr; cv.style.width=W+'px'; cv.style.height=H+'px'; }
window.addEventListener('resize',()=>{resize();});
resize(); fit(); loop();
</script>
</body></html>
"""


if __name__ == "__main__":
    sys.exit(main())
