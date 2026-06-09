"""
dashboard.py
Reads output/screened_tenders.xlsx produced by screening_pipeline.py
and generates a self-contained HTML dashboard at output/dashboard.html.

Usage:
    python src/dashboard.py
"""

import pandas as pd
import json
import sys
from pathlib import Path
from datetime import datetime

EXCEL_PATH = Path("output/screened_tenders.xlsx")
OUTPUT_PATH = Path("output/dashboard.html")


def load_data():
    if not EXCEL_PATH.exists():
        print("[Error] output/screened_tenders.xlsx not found.")
        print("        Run python src/screening_pipeline.py first.")
        sys.exit(1)

    high   = pd.read_excel(EXCEL_PATH, sheet_name="High Priority")
    medium = pd.read_excel(EXCEL_PATH, sheet_name="Medium Priority")
    low    = pd.read_excel(EXCEL_PATH, sheet_name="Low Priority")
    all_df = pd.read_excel(EXCEL_PATH, sheet_name="All Scored")

    high["tier"]   = "High"
    medium["tier"] = "Medium"
    low["tier"]    = "Low"
    full = pd.concat([high, medium, low], ignore_index=True)
    return full, all_df


def build_chart_data(full, all_df):
    portal_counts = all_df["portal"].value_counts()
    portals = portal_counts.index.tolist()
    portal_vals = portal_counts.values.tolist()

    tier_counts = full["tier"].value_counts()
    high_n  = int(tier_counts.get("High", 0))
    med_n   = int(tier_counts.get("Medium", 0))
    low_n   = int(tier_counts.get("Low", 0))
    total_raw = len(all_df)
    total_scored = len(full)
    filtered_out = total_raw - total_scored

    top5 = full.nlargest(5, "composite_score")[
        ["tender_id","title","portal","composite_score","days_to_deadline","tier"]
    ].to_dict("records")
    for t in top5:
        t["composite_score"] = round(float(t["composite_score"]), 3)
        t["days_to_deadline"] = int(t["days_to_deadline"])
        t["title"] = str(t["title"])[:55] + ("…" if len(str(t["title"])) > 55 else "")

    cat_scores = full.groupby("category")["composite_score"].mean().sort_values()
    categories = cat_scores.index.tolist()
    cat_vals   = [round(float(v), 3) for v in cat_scores.values.tolist()]

    score_bins  = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    score_labels = ["0–0.1","0.1–0.2","0.2–0.3","0.3–0.4","0.4–0.5",
                    "0.5–0.6","0.6–0.7","0.7–0.8","0.8–0.9","0.9–1.0"]
    hist_counts = pd.cut(full["composite_score"], bins=score_bins,
                         labels=score_labels, include_lowest=True).value_counts().sort_index()
    hist_vals = hist_counts.values.tolist()

    top_score = round(float(full["composite_score"].max()), 3)

    return {
        "total_raw":     total_raw,
        "total_scored":  total_scored,
        "filtered_out":  filtered_out,
        "high_n":        high_n,
        "med_n":         med_n,
        "low_n":         low_n,
        "top_score":     top_score,
        "portals":       portals,
        "portal_vals":   portal_vals,
        "top5":          top5,
        "categories":    categories,
        "cat_vals":      cat_vals,
        "score_labels":  score_labels,
        "hist_vals":     hist_vals,
        "run_date":      datetime.now().strftime("%d %b %Y, %H:%M"),
    }


def render_html(d):
    top5_rows = ""
    for t in d["top5"]:
        tier = t["tier"]
        badge_cls = {"High": "ba-high", "Medium": "ba-med", "Low": "ba-low"}[tier]
        bar_color = {"High": "#639922", "Medium": "#BA7517", "Low": "#888780"}[tier]
        bar_w = round(t["composite_score"] * 80)
        top5_rows += f"""
        <tr>
          <td style="font-family:monospace;font-size:12px;color:#888;">{t['tender_id']}</td>
          <td>{t['title']}</td>
          <td style="color:#888;">{t['portal']}</td>
          <td>
            <span style="font-weight:500;margin-right:8px;">{t['composite_score']:.3f}</span>
            <span style="display:inline-block;background:#eee;border-radius:3px;height:6px;width:80px;vertical-align:middle;">
              <span style="display:block;width:{bar_w}px;height:6px;background:{bar_color};border-radius:3px;"></span>
            </span>
          </td>
          <td>{t['days_to_deadline']}d</td>
          <td><span class="badge {badge_cls}">{tier}</span></td>
        </tr>"""

    portals_js  = json.dumps(d["portals"])
    pvals_js    = json.dumps(d["portal_vals"])
    cats_js     = json.dumps(d["categories"])
    cvals_js    = json.dumps(d["cat_vals"])
    slabels_js  = json.dumps(d["score_labels"])
    hvals_js    = json.dumps(d["hist_vals"])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bid Alert — Dashboard</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
          background: #f5f5f3; color: #1a1a1a; font-size: 14px; line-height: 1.6; }}
  .wrap {{ max-width: 960px; margin: 0 auto; padding: 2rem 1.5rem; }}
  .header {{ display: flex; justify-content: space-between; align-items: baseline;
             border-bottom: 1px solid #e0e0e0; padding-bottom: 1rem; margin-bottom: 1.5rem; }}
  .header h1 {{ font-size: 18px; font-weight: 500; }}
  .header h1 span {{ color: #888; font-weight: 400; }}
  .run-info {{ font-size: 12px; color: #888; }}
  .metrics {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 1.5rem; }}
  .metric {{ background: #fff; border: 1px solid #e8e8e8; border-radius: 10px; padding: 14px 16px; }}
  .metric-label {{ font-size: 11px; color: #888; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 6px; }}
  .metric-value {{ font-size: 28px; font-weight: 500; line-height: 1; }}
  .metric-sub {{ font-size: 11px; color: #888; margin-top: 4px; }}
  .metric-value.green {{ color: #3B6D11; }}
  .metric-value.blue  {{ color: #185FA5; }}
  .funnel {{ background: #fff; border: 1px solid #e8e8e8; border-radius: 10px;
             padding: 16px 20px; margin-bottom: 1.5rem; }}
  .funnel-title {{ font-size: 11px; font-weight: 500; color: #888; text-transform: uppercase;
                   letter-spacing: 0.05em; margin-bottom: 14px; }}
  .funnel-steps {{ display: flex; align-items: center; gap: 4px; }}
  .funnel-step {{ flex: 1; border-radius: 8px; padding: 12px 10px; text-align: center; }}
  .funnel-num {{ font-size: 24px; font-weight: 500; }}
  .funnel-lbl {{ font-size: 11px; margin-top: 2px; }}
  .funnel-arrow {{ color: #ccc; font-size: 20px; flex-shrink: 0; }}
  .f-blue  {{ background: #E6F1FB; }}  .f-blue .funnel-num  {{ color: #0C447C; }} .f-blue .funnel-lbl  {{ color: #378ADD; }}
  .f-amber {{ background: #FAEEDA; }}  .f-amber .funnel-num {{ color: #633806; }} .f-amber .funnel-lbl {{ color: #BA7517; }}
  .f-light {{ background: #EAF3DE; }}  .f-light .funnel-num {{ color: #3B6D11; }} .f-light .funnel-lbl {{ color: #639922; }}
  .f-green {{ background: #EAF3DE; border: 2px solid #97C459; }}
             .f-green .funnel-num {{ color: #173404; }} .f-green .funnel-lbl {{ color: #3B6D11; }}
  .charts {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 1.5rem; }}
  .chart-box {{ background: #fff; border: 1px solid #e8e8e8; border-radius: 10px; padding: 16px; }}
  .chart-title {{ font-size: 11px; font-weight: 500; color: #888; text-transform: uppercase;
                  letter-spacing: 0.05em; margin-bottom: 12px; }}
  .legend {{ display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 10px; }}
  .legend-item {{ display: flex; align-items: center; gap: 5px; font-size: 11px; color: #888; }}
  .legend-dot {{ width: 8px; height: 8px; border-radius: 2px; flex-shrink: 0; }}
  .chart-full {{ background: #fff; border: 1px solid #e8e8e8; border-radius: 10px;
                 padding: 16px; margin-bottom: 1.5rem; }}
  .table-box {{ background: #fff; border: 1px solid #e8e8e8; border-radius: 10px;
                padding: 16px; margin-bottom: 1.5rem; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ text-align: left; font-size: 11px; font-weight: 500; color: #888;
        text-transform: uppercase; letter-spacing: 0.05em;
        padding: 0 10px 10px 0; border-bottom: 1px solid #eee; }}
  td {{ padding: 10px 10px 10px 0; border-bottom: 1px solid #f0f0f0; vertical-align: middle; }}
  tr:last-child td {{ border-bottom: none; }}
  .badge {{ display: inline-block; font-size: 11px; font-weight: 500; padding: 3px 8px; border-radius: 4px; }}
  .ba-high {{ background: #EAF3DE; color: #27500A; }}
  .ba-med  {{ background: #FAEEDA; color: #633806; }}
  .ba-low  {{ background: #F1EFE8; color: #5F5E5A; }}
  .scoring-note {{ background: #fff; border: 1px solid #e8e8e8; border-radius: 10px;
                   padding: 16px 20px; }}
  .scoring-note h3 {{ font-size: 11px; font-weight: 500; color: #888; text-transform: uppercase;
                      letter-spacing: 0.05em; margin-bottom: 12px; }}
  .weight-row {{ display: flex; align-items: center; gap: 10px; margin-bottom: 8px; font-size: 13px; }}
  .weight-label {{ width: 160px; flex-shrink: 0; }}
  .weight-bar-bg {{ flex: 1; background: #f0f0f0; border-radius: 3px; height: 8px; }}
  .weight-bar {{ height: 8px; border-radius: 3px; }}
  .weight-pct {{ width: 36px; text-align: right; font-size: 12px; color: #888; flex-shrink: 0; }}
</style>
</head>
<body>
<div class="wrap">

  <div class="header">
    <h1>Bid Alert <span>/ tender screening dashboard</span></h1>
    <span class="run-info">Run {d["run_date"]} &nbsp;·&nbsp; 5 portals</span>
  </div>

  <div class="metrics">
    <div class="metric">
      <div class="metric-label">Tenders ingested</div>
      <div class="metric-value blue">{d["total_raw"]}</div>
      <div class="metric-sub">across {len(d["portals"])} portals</div>
    </div>
    <div class="metric">
      <div class="metric-label">Passed eligibility</div>
      <div class="metric-value">{d["total_scored"]}</div>
      <div class="metric-sub" style="color:#3B6D11;">{d["filtered_out"]} filtered out</div>
    </div>
    <div class="metric">
      <div class="metric-label">High priority</div>
      <div class="metric-value green">{d["high_n"]}</div>
      <div class="metric-sub">score ≥ 0.70</div>
    </div>
    <div class="metric">
      <div class="metric-label">Top score</div>
      <div class="metric-value">{d["top_score"]}</div>
      <div class="metric-sub">this run</div>
    </div>
  </div>

  <div class="funnel">
    <div class="funnel-title">pipeline funnel</div>
    <div class="funnel-steps">
      <div class="funnel-step f-blue">
        <div class="funnel-num">{d["total_raw"]}</div>
        <div class="funnel-lbl">raw tenders</div>
      </div>
      <div class="funnel-arrow">›</div>
      <div class="funnel-step f-amber">
        <div class="funnel-num">{d["filtered_out"]}</div>
        <div class="funnel-lbl">removed by filter</div>
      </div>
      <div class="funnel-arrow">›</div>
      <div class="funnel-step f-light">
        <div class="funnel-num">{d["total_scored"]}</div>
        <div class="funnel-lbl">eligible tenders</div>
      </div>
      <div class="funnel-arrow">›</div>
      <div class="funnel-step f-green">
        <div class="funnel-num">{d["high_n"]}</div>
        <div class="funnel-lbl">high priority</div>
      </div>
    </div>
  </div>

  <div class="charts">
    <div class="chart-box">
      <div class="chart-title">tenders by portal</div>
      <div class="legend" id="portalLegend"></div>
      <div style="position:relative;height:180px;">
        <canvas id="portalChart" role="img" aria-label="Bar chart of tenders by portal"></canvas>
      </div>
    </div>
    <div class="chart-box">
      <div class="chart-title">priority breakdown</div>
      <div class="legend">
        <span class="legend-item"><span class="legend-dot" style="background:#639922;"></span>High ({d["high_n"]})</span>
        <span class="legend-item"><span class="legend-dot" style="background:#BA7517;"></span>Medium ({d["med_n"]})</span>
        <span class="legend-item"><span class="legend-dot" style="background:#888780;"></span>Low ({d["low_n"]})</span>
      </div>
      <div style="position:relative;height:180px;">
        <canvas id="scoreChart" role="img" aria-label="Donut chart of priority tiers"></canvas>
      </div>
    </div>
  </div>

  <div class="charts">
    <div class="chart-box">
      <div class="chart-title">score distribution</div>
      <div style="position:relative;height:180px;">
        <canvas id="histChart" role="img" aria-label="Histogram of composite scores"></canvas>
      </div>
    </div>
    <div class="chart-box">
      <div class="chart-title">avg score by category</div>
      <div style="position:relative;height:180px;">
        <canvas id="catChart" role="img" aria-label="Average score per tender category"></canvas>
      </div>
    </div>
  </div>

  <div class="table-box">
    <div class="chart-title">top 5 tenders this run</div>
    <table>
      <thead>
        <tr>
          <th>Tender ID</th>
          <th>Description</th>
          <th>Portal</th>
          <th>Score</th>
          <th>Days left</th>
          <th>Priority</th>
        </tr>
      </thead>
      <tbody>{top5_rows}</tbody>
    </table>
  </div>

  <div class="scoring-note">
    <h3>scoring model weights</h3>
    <div class="weight-row"><span class="weight-label">Category match</span>
      <div class="weight-bar-bg"><div class="weight-bar" style="width:86%;background:#378ADD;"></div></div>
      <span class="weight-pct">30%</span></div>
    <div class="weight-row"><span class="weight-label">Value sweet spot</span>
      <div class="weight-bar-bg"><div class="weight-bar" style="width:57%;background:#1D9E75;"></div></div>
      <span class="weight-pct">20%</span></div>
    <div class="weight-row"><span class="weight-label">State preference</span>
      <div class="weight-bar-bg"><div class="weight-bar" style="width:57%;background:#7F77DD;"></div></div>
      <span class="weight-pct">20%</span></div>
    <div class="weight-row"><span class="weight-label">MSME advantage</span>
      <div class="weight-bar-bg"><div class="weight-bar" style="width:43%;background:#D85A30;"></div></div>
      <span class="weight-pct">15%</span></div>
    <div class="weight-row"><span class="weight-label">Deadline comfort</span>
      <div class="weight-bar-bg"><div class="weight-bar" style="width:43%;background:#888780;"></div></div>
      <span class="weight-pct">15%</span></div>
  </div>

</div>

<script>
const portals   = {portals_js};
const pVals     = {pvals_js};
const pColors   = ['#378ADD','#1D9E75','#D85A30','#7F77DD','#888780','#D4537E','#BA7517'];

const legend = document.getElementById('portalLegend');
portals.forEach((p, i) => {{
  legend.innerHTML += `<span class="legend-item">
    <span class="legend-dot" style="background:${{pColors[i % pColors.length]}};"></span>${{p}}
  </span>`;
}});

new Chart(document.getElementById('portalChart'), {{
  type: 'bar',
  data: {{ labels: portals, datasets: [{{ data: pVals, backgroundColor: pColors, borderRadius: 4, borderSkipped: false }}] }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }}, tooltip: {{ callbacks: {{ label: c => ` ${{c.raw}} tenders` }} }} }},
    scales: {{ x: {{ grid: {{ display: false }}, ticks: {{ font: {{ size: 11 }} }} }},
               y: {{ grid: {{ color: 'rgba(0,0,0,0.05)' }}, ticks: {{ font: {{ size: 11 }} }}, beginAtZero: true }} }}
  }}
}});

new Chart(document.getElementById('scoreChart'), {{
  type: 'doughnut',
  data: {{ labels: ['High','Medium','Low'],
           datasets: [{{ data: [{d["high_n"]},{d["med_n"]},{d["low_n"]}],
                        backgroundColor: ['#639922','#BA7517','#888780'],
                        borderWidth: 0, hoverOffset: 4 }}] }},
  options: {{ responsive: true, maintainAspectRatio: false, cutout: '65%',
              plugins: {{ legend: {{ display: false }}, tooltip: {{ callbacks: {{ label: c => ` ${{c.raw}} tenders` }} }} }} }}
}});

new Chart(document.getElementById('histChart'), {{
  type: 'bar',
  data: {{ labels: {slabels_js},
           datasets: [{{ data: {hvals_js}, backgroundColor: '#378ADD', borderRadius: 3, borderSkipped: false }}] }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }}, tooltip: {{ callbacks: {{ label: c => ` ${{c.raw}} tenders` }} }} }},
    scales: {{ x: {{ grid: {{ display: false }}, ticks: {{ font: {{ size: 10 }}, maxRotation: 45 }} }},
               y: {{ grid: {{ color: 'rgba(0,0,0,0.05)' }}, ticks: {{ font: {{ size: 11 }} }}, beginAtZero: true }} }}
  }}
}});

new Chart(document.getElementById('catChart'), {{
  type: 'bar',
  data: {{ labels: {cats_js},
           datasets: [{{ data: {cvals_js}, backgroundColor: '#7F77DD', borderRadius: 3, borderSkipped: false }}] }},
  options: {{
    indexAxis: 'y',
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }}, tooltip: {{ callbacks: {{ label: c => ` avg ${{c.raw}}` }} }} }},
    scales: {{ x: {{ grid: {{ color: 'rgba(0,0,0,0.05)' }}, ticks: {{ font: {{ size: 11 }} }}, min: 0, max: 1 }},
               y: {{ grid: {{ display: false }}, ticks: {{ font: {{ size: 11 }} }} }} }}
  }}
}});
</script>
</body>
</html>"""


def main():
    print("[Dashboard] Loading data from output/screened_tenders.xlsx ...")
    full, all_df = load_data()
    d = build_chart_data(full, all_df)
    html = render_html(d)
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"[Dashboard] Saved → {OUTPUT_PATH}")
    print(f"            Open in browser: file://{OUTPUT_PATH.resolve()}")


if __name__ == "__main__":
    main()
