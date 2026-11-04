"""Build the GitHub Pages site (landing page + guides) into ``_site/``.

    git clone -b main https://github.com/SimulaMet/Verdi.git main   # project source
    pip install markdown
    python build.py                 # writes _site/
    python -m http.server -d _site  # preview

Lives on the ``website`` branch so ``main`` holds no site code. The guides are
rendered from ``main``'s ``docs/*.md`` (path from VERDI_SRC, default ./main),
so the site never drifts from the project. Every page gets a canonical URL, description, Open Graph tags and
structured data; ``sitemap.xml`` and ``robots.txt`` let search engines find
all of it.
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
from datetime import date
from pathlib import Path

import markdown
import tomllib

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("VERDI_SRC", HERE / "main")).resolve()  # main checkout
OUT = HERE / "_site"
SITE = "https://simulamet.github.io/Verdi/"
REPO = "https://github.com/SimulaMet/Verdi"
PYPI = "https://pypi.org/project/verdi-agents/"
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]

# docs/<file>.md -> /<slug>.html, with a search-friendly title and description.
GUIDES = [
    ("SWEEPS.md", "sweeps", "Sweeps: compare LLMs and settings across seeds",
     "Rerun one agent-society scenario across models, governance settings and seeds "
     "with one YAML file. Plan and cost first, resume, spreadsheet and comparison page."),
    ("SCENARIOS.md", "scenarios", "Scenario reference",
     "Every key of a Verdi scenario YAML: model endpoint, agents, resources, shocks, "
     "constitution, economy, delivery tasks, interviews and probe schedule."),
    ("RESEARCHER_GUIDE.md", "researcher-guide", "Researcher guide: paired institutional study",
     "Run a fixed-vs-revisable institutions experiment on LLM agent societies with one "
     "config file and read the comparative results."),
    ("INSTITUTIONAL_RECOVERY.md", "institutional-recovery",
     "Institutional recovery experiments",
     "How Verdi measures whether societies of LLM agents recover from shocks under fixed "
     "or amendable constitutions, using objective delivery tasks."),
]

CSS = """
:root{--bg:#fbfbfd;--surface:#fff;--fg:#1d1d1f;--muted:#5f6368;--line:#e4e4e9;
--accent:#2f5bd3;--accent-soft:#eef2fd;--code:#f4f4f7;--radius:14px}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0f1115;
--surface:#161a20;--fg:#ecedf0;--muted:#a1a6b0;--line:#272c35;--accent:#8aa8ff;
--accent-soft:#1a2236;--code:#1b2027}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.65 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:1040px;margin:0 auto;padding:0 20px}
header.top{border-bottom:1px solid var(--line);background:var(--surface)}
header.top .wrap{display:flex;align-items:center;gap:20px;height:60px}
.brand{font-weight:700;font-size:18px;color:var(--fg)}
nav{display:flex;gap:18px;font-size:15px;white-space:nowrap;overflow-x:auto;
scrollbar-width:none}nav a{color:var(--muted)}
nav a:hover{color:var(--fg);text-decoration:none}
.spacer{flex:1}
.hero{padding:72px 0 40px}.hero h1{font-size:clamp(34px,6vw,54px);line-height:1.1;
margin:0 0 16px;letter-spacing:-.02em}.hero p.lead{font-size:clamp(17px,2.4vw,21px);
color:var(--muted);max-width:720px;margin:0 0 28px}
.cta{display:flex;gap:12px;flex-wrap:wrap}
.btn{display:inline-block;padding:11px 18px;border-radius:10px;font-weight:600;
border:1px solid var(--line);color:var(--fg);background:var(--surface)}
.btn.primary{background:var(--accent);color:#fff;border-color:var(--accent)}
.btn:hover{text-decoration:none;filter:brightness(1.05)}
pre,code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:14px}
pre{background:var(--code);border:1px solid var(--line);border-radius:var(--radius);
padding:16px 18px;overflow-x:auto;line-height:1.55}
:not(pre)>code{background:var(--code);padding:2px 6px;border-radius:6px}
section{padding:36px 0}section h2{font-size:28px;margin:0 0 8px;letter-spacing:-.01em}
section p.sub{color:var(--muted);margin:0 0 24px;max-width:720px}
.grid{display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(240px,1fr))}
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);
padding:20px}.card h3{margin:0 0 6px;font-size:17px}.card p{margin:0;color:var(--muted);
font-size:15px}
.steps{counter-reset:s;display:grid;gap:16px;
grid-template-columns:repeat(auto-fit,minmax(280px,1fr))}
.steps .card h3::before{counter-increment:s;content:counter(s);display:inline-grid;
place-items:center;width:26px;height:26px;margin-right:8px;border-radius:50%;
background:var(--accent-soft);color:var(--accent);font-size:14px}
article.doc{max-width:820px;padding:40px 0 64px}
article.doc h1{font-size:clamp(28px,4.5vw,38px);line-height:1.2;letter-spacing:-.01em}
article.doc h2{margin-top:2em;padding-top:.4em;border-top:1px solid var(--line)}
article.doc table{border-collapse:collapse;display:block;overflow-x:auto;margin:1em 0}
article.doc th,article.doc td{border:1px solid var(--line);padding:6px 10px;
text-align:left;vertical-align:top}article.doc th{background:var(--code)}
.crumbs{font-size:14px;color:var(--muted);margin-bottom:8px}
footer{border-top:1px solid var(--line);padding:28px 0 40px;color:var(--muted);font-size:14px}
footer .wrap{display:flex;gap:16px;flex-wrap:wrap;justify-content:space-between}
@media (max-width:760px){nav.ext,.spacer{display:none}.hero{padding:44px 0 24px}}
"""


def page(*, title: str, description: str, path: str, body: str,
         jsonld: dict | None = None) -> str:
    url = SITE + path
    nav = "".join(f'<a href="{s}.html">{t.split(":")[0]}</a>'
                  for _, s, t, _ in GUIDES[:2])
    ld = f'<script type="application/ld+json">{json.dumps(jsonld)}</script>' if jsonld else ""
    e = html.escape
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(description)}">
<link rel="canonical" href="{url}">
<meta property="og:type" content="website"><meta property="og:site_name" content="Verdi">
<meta property="og:title" content="{e(title)}"><meta property="og:description" content="{e(description)}">
<meta property="og:url" content="{url}">
<meta name="twitter:card" content="summary"><meta name="twitter:title" content="{e(title)}">
<meta name="twitter:description" content="{e(description)}">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='7' fill='%232f5bd3'/><path d='M8 9l8 15 8-15' stroke='white' stroke-width='3.2' fill='none' stroke-linecap='round' stroke-linejoin='round'/></svg>">
<style>{CSS}</style>{ld}
</head><body>
<header class="top"><div class="wrap">
<a class="brand" href="./">Verdi</a>
<nav><a href="./#install">Install</a>{nav}<a href="researcher-guide.html">Guide</a></nav>
<span class="spacer"></span>
<nav class="ext"><a href="{PYPI}">PyPI</a><a href="{REPO}">GitHub</a></nav>
</div></header>
<main>{body}</main>
<footer><div class="wrap">
<span>Verdi {VERSION} · MIT license · by Sushant Gautam, SimulaMet</span>
<span><a href="{REPO}">GitHub</a> · <a href="{PYPI}">PyPI</a> ·
<a href="{REPO}/issues">Issues</a></span>
</div></footer>
</body></html>
"""


def landing() -> str:
    feats = [
        ("Auditable by construction", "Every action, vote, law, sanction and appeal is written "
         "to an append-only event log and replayed to an identical state hash."),
        ("Declared vs observed values", "Agents state their values; Verdi measures what they "
         "actually do. The gap over time is the measurement."),
        ("Self-governing societies", "Universal laws plus a constitution agents can amend in "
         "assemblies: quorum, thresholds, sanctions, appeals, restoration."),
        ("Shocks and recovery", "Deterministic exogenous shocks, a constrained economy and "
         "objective delivery tasks show whether a society recovers."),
        ("Compare models with sweeps", "Vary the model, quorum or shock size in one YAML file; "
         "every combination runs on the same seeds, in parallel, resumable."),
        ("One-click reports", "Each run renders a self-contained report.html; each sweep a "
         "comparison page and a spreadsheet."),
    ]
    cards = "".join(f'<div class="card"><h3>{t}</h3><p>{d}</p></div>' for t, d in feats)
    body = f"""
<div class="wrap">
<div class="hero">
<h1>Verdi: an auditable testbed for societies of LLM agents</h1>
<p class="lead">Run a society of language-model agents over time, let them govern
themselves, hit them with shocks, and measure how their values and institutions
drift and recover. Reproducible, seeded, and fully logged.</p>
<div class="cta"><a class="btn primary" href="#install">Get started</a>
<a class="btn" href="sweeps.html">Compare models</a>
<a class="btn" href="{REPO}">View on GitHub</a></div>
</div>

<section id="install"><h2>Install in one line</h2>
<p class="sub">No virtual environment needed: <a href="https://docs.astral.sh/uv/">uv</a>
fetches Verdi on first use. Works with any OpenAI-compatible endpoint (vLLM, Ollama,
hosted APIs).</p>
<pre><code>uvx verdi-agents init my-study          # example scenarios, sweeps, .env template
cd my-study                             # add your key to .env, endpoint to examples/starter.yaml
uvx verdi-agents run examples/starter.yaml
open runs/starter/report.html</code></pre>
<p>Or <code>pip install verdi-agents</code>, then run <code>verdi-agents</code> directly.</p>
</section>

<section><h2>What Verdi measures</h2>
<p class="sub">Built for research on AI safety, alignment and governance in multi-agent
LLM systems.</p>
<div class="grid">{cards}</div></section>

<section><h2>How it works</h2>
<div class="steps">
<div class="card"><h3>Describe a society</h3><p>One YAML file: the model endpoint,
agents with roles and declared values, resources, laws, constitution, shocks and
when to interview them. See the <a href="scenarios.html">scenario reference</a>.</p></div>
<div class="card"><h3>Run it</h3><p><code>verdi-agents run scenario.yaml</code> drives every
agent through every tick, validates each action against the law, and writes the
full event ledger.</p></div>
<div class="card"><h3>Compare</h3><p><code>verdi-agents sweep</code> reruns the scenario
across models and settings and summarizes the outcomes per condition.
See <a href="sweeps.html">sweeps</a>.</p></div>
</div></section>

<section><h2>Compare LLMs on the same society</h2>
<p class="sub">Change only the model; everything else stays identical.</p>
<pre><code>name: model_comparison
scenario: examples/full.yaml
vary:
  model.name: [model-a, model-b]
parallel_runs: 2</code></pre>
<pre><code>uvx verdi-agents sweep examples/sweeps/model_comparison.yaml --dry-run   # plan + cost
uvx verdi-agents sweep examples/sweeps/model_comparison.yaml             # run, resumable</code></pre>
</section>

<section><h2>Guides</h2><div class="grid">
{"".join(f'<a class="card" href="{s}.html"><h3>{html.escape(t)}</h3><p>{html.escape(d)}</p></a>' for _, s, t, d in GUIDES)}
</div></section>
</div>"""
    jsonld = {
        "@context": "https://schema.org", "@type": "SoftwareSourceCode",
        "name": "Verdi", "alternateName": "verdi-agents",
        "description": DESCRIPTION, "url": SITE, "codeRepository": REPO,
        "programmingLanguage": "Python", "license": "https://opensource.org/licenses/MIT",
        "version": VERSION, "author": {"@type": "Person", "name": "Sushant Gautam"},
        "sourceOrganization": {"@type": "Organization", "name": "SimulaMet"},
        "keywords": "LLM agents, multi-agent simulation, AI safety, AI governance, "
                    "value drift, institutional design, agent-based modeling",
        "runtimePlatform": "Python 3.10+", "downloadUrl": PYPI,
    }
    return page(title="Verdi: auditable testbed for societies of LLM agents",
                description=DESCRIPTION, path="", body=body, jsonld=jsonld)


DESCRIPTION = ("Open-source Python testbed for societies of LLM agents: self-governance, "
               "sanctions, shocks and recovery, with auditable logs, reproducible runs and "
               "model comparison sweeps.")


def _fix_links(text: str) -> str:
    slugs = {f: s for f, s, _, _ in GUIDES}

    def repl(m: re.Match) -> str:
        target = m.group(1)
        name = target.split("#")[0]
        anchor = target[len(name):]
        if name in slugs:
            return f"]({slugs[name]}.html{anchor})"
        if name.startswith("../"):
            kind = "tree" if name.endswith("/") else "blob"
            return f"]({REPO}/{kind}/main/{name[3:]}{anchor})"
        if name.endswith(".md") and "://" not in name:
            return f"]({REPO}/blob/main/docs/{name}{anchor})"
        return m.group(0)

    return re.sub(r"\]\(([^)\s]+)\)", repl, text)


def guide(src: str, slug: str, title: str, description: str) -> str:
    text = _fix_links((ROOT / "docs" / src).read_text(encoding="utf-8"))
    body_html = markdown.markdown(text, extensions=["tables", "fenced_code", "toc", "sane_lists"])
    body = (f'<div class="wrap"><article class="doc"><div class="crumbs">'
            f'<a href="./">Verdi</a> / Guides</div>{body_html}'
            f'<p><a href="{REPO}/edit/main/docs/{src}">Edit this page on GitHub</a></p>'
            f'</article></div>')
    jsonld = {"@context": "https://schema.org", "@type": "TechArticle", "headline": title,
              "description": description, "url": SITE + f"{slug}.html",
              "isPartOf": {"@type": "WebSite", "name": "Verdi", "url": SITE}}
    return page(title=f"{title} | Verdi", description=description,
                path=f"{slug}.html", body=body, jsonld=jsonld)


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    (OUT / "index.html").write_text(landing(), encoding="utf-8")
    urls = [SITE]
    for src, slug, title, desc in GUIDES:
        (OUT / f"{slug}.html").write_text(guide(src, slug, title, desc), encoding="utf-8")
        urls.append(SITE + f"{slug}.html")
    today = date.today().isoformat()
    (OUT / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"  <url><loc>{u}</loc><lastmod>{today}</lastmod></url>\n" for u in urls)
        + "</urlset>\n", encoding="utf-8")
    (OUT / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {SITE}sitemap.xml\n",
                                    encoding="utf-8")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    print(f"Built {len(urls)} pages into {OUT}")


if __name__ == "__main__":
    main()
