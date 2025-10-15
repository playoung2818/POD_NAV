import pandas as pd
from flask import Flask, request, render_template_string, jsonify
from datetime import datetime
import os
import pandas as pd

def to_date_str(s: pd.Series, fmt="%m-%d-%Y") -> pd.Series:
    """Coerce to datetime, then format; leave blanks for NaT."""
    s = pd.to_datetime(s, errors="coerce")
    return s.apply(lambda x: x.strftime(fmt) if pd.notnull(x) else "")



EXCEL_PATH = r"20251002_LT.xlsx"  # adjust path as needed
SHEET_NAME = "check"                        # sheet is lowercase

APP_TITLE = f"LT Check — From {os.path.basename(EXCEL_PATH)}"

app = Flask(__name__)

def load_check():
    df = pd.read_excel(EXCEL_PATH, sheet_name=SHEET_NAME, engine="openpyxl")
    # normalize columns (keep your original header casing if you prefer)
    rename = {
        "Ship Date": "ship_date",
        "QB Num": "qb_num",
        "P. O. #": "po_num",
        "Qty(-)": "qty_minus",
        "Qty(+)": "qty_plus",
        "Item": "item",
        "Inventory Site": "site",
        "projected": "projected",
        "On Hand": "on_hand",
        "On Sales Order": "on_sale",
        "Available": "available",
        "On PO": "on_po",
        "Name": "name",
        "Remark": "remark",
    }
    for k, v in rename.items():
        if k in df.columns:
            df.rename(columns={k: v}, inplace=True)

    # types
    if "ship_date" in df.columns:
        df["ship_date"] = pd.to_datetime(df["ship_date"], errors="coerce")
    for c in ["qty_minus","qty_plus","projected","on_hand","on_sale","available","on_po"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

    # net delta (PO positive, SO negative)
    df["delta"] = df.get("qty_plus", 0) - df.get("qty_minus", 0)
    return df

CHECK = load_check()

def qb_summary(qb_num: str):
    rows = CHECK[CHECK["qb_num"] == qb_num].copy()
    if rows.empty:
        return None

    # sort for stable display
    rows = rows.sort_values(["item", "ship_date"], na_position="last")

    # Format a per-line view for the table
    def _fmt_date(x):
        try:
            return "" if pd.isna(x) else str(pd.to_datetime(x).date())
        except Exception:
            return ""

    line_cols = [
        "item","site","ship_date","qty_minus","qty_plus",
        "projected","on_hand","on_sale","available","on_po","po_num","remark","name"
    ]
    for c in line_cols:
        if c not in rows.columns:
            rows[c] = None

    lines = []
    for r in rows.itertuples(index=False):
        lines.append({
            "item": r.item,
            "site": r.site,
            "ship_date": _fmt_date(r.ship_date),
            "qty_minus": float(r.qty_minus or 0),
            "qty_plus": float(r.qty_plus or 0),
            "projected": float(r.projected or 0),
            "on_hand": float(r.on_hand or 0),
            "on_sale": float(r.on_sale or 0),
            "available": float(r.available or 0),
            "on_po": float(r.on_po or 0),
            "po_num": r.po_num,
            "remark": r.remark,
            "name": r.name,
        })

    # Totals across the QB
    tot = {
        "projected": float(rows["projected"].sum()),
        "on_hand": float(rows["on_hand"].sum()),
        "on_sale": float(rows["on_sale"].sum()),
        "available": float(rows["available"].sum()),
        "on_po": float(rows["on_po"].sum()),
    }

    # Use the first line as the "main" line for the assign-date helper
    main = rows.sort_values("ship_date", na_position="first").iloc[0]
    need_qty = float(main.get("qty_minus", 0))
    return {
        "qb_num": qb_num,
        "item": main.get("item", ""),
        "site": main.get("site", ""),
        "ship_date": (None if pd.isna(main.get("ship_date")) else str(pd.to_datetime(main.get("ship_date")).date())),
        "need_qty": need_qty,
        **tot,
        "lines": lines,  # <<< new: all items/rows under this SO
    }


def earliest_assign_date(item: str, need_qty: float, site: str | None):
    df = CHECK[(CHECK["item"] == item)].copy()
    if site:
        df = df[df["site"] == site]

    # starting on-hand (use latest nonzero snapshot if present; else 0)
    start_on_hand = 0.0
    snap = df.loc[df["on_hand"] > 0, "on_hand"]
    if not snap.empty:
        start_on_hand = float(snap.iloc[-1])

    tl = (df.groupby("ship_date", dropna=False)["delta"]
            .sum()
            .sort_index()
            .reset_index()
            .rename(columns={"delta": "net_change"}))

    today = pd.to_datetime(datetime.today().date())
    tl["ship_date"] = tl["ship_date"].fillna(today)
    tl["cum_available"] = start_on_hand + tl["net_change"].cumsum()

    meet = tl[tl["cum_available"] >= need_qty]
    date = None if meet.empty else meet.iloc[0]["ship_date"]
    return {
        "date": (None if date is None else str(pd.to_datetime(date).date())),
        "start_on_hand": start_on_hand,
        "timeline": tl
    }

@app.route("/", methods=["GET"])
def index():
    qb = request.args.get("qb", "").strip()
    summary = qb_summary(qb) if qb else None
    assign = None
    if summary and (summary["available"] < 0 or summary["need_qty"] > summary["on_hand"]):
        assign = earliest_assign_date(summary["item"], max(summary["need_qty"], 1), summary["site"])
    return render_template_string(TPL, qb=qb, summary=summary, assign=assign, app_title=APP_TITLE)

@app.route("/api/qb/<qb_num>")
def api_qb(qb_num):
    s = qb_summary(qb_num)
    if not s:
        return jsonify({"error": "QB not found"}), 404
    resp = {"summary": s}
    if s["available"] < 0 or s["need_qty"] > s["on_hand"]:
        a = earliest_assign_date(s["item"], max(s["need_qty"], 1), s["site"])
        tl = a["timeline"].copy()
        tl["ship_date"] = tl["ship_date"].astype(str)
        resp["assign"] = {
            "earliest_date": a["date"],
            "start_on_hand": a["start_on_hand"],
            "timeline": tl.head(50).to_dict(orient="records")
        }
    return jsonify(resp)

@app.route("/api/assign", methods=["GET"])
def api_assign():
    item = (request.args.get("item") or "").strip()
    site = (request.args.get("site") or "").strip() or None
    try:
        need_qty = float(request.args.get("need_qty") or 0)
    except ValueError:
        need_qty = 0

    if not item:
        return jsonify({"error": "Missing item"}), 400
    if need_qty <= 0:
        return jsonify({"error": "Need qty must be > 0"}), 400

    a = earliest_assign_date(item, need_qty, site)
    tl = a["timeline"].copy()
    tl["ship_date"] = tl["ship_date"].astype(str)

    return jsonify({
        "item": item,
        "site": site,
        "need_qty": need_qty,
        "earliest_date": (None if a["date"] is None else str(a["date"])),
        "start_on_hand": a["start_on_hand"],
        "timeline": tl.head(50).to_dict(orient="records"),
    })

@app.route("/api/item_rows", methods=["GET"])
def api_item_rows():
    item = (request.args.get("item") or "").strip()
    if not item:
        return jsonify({"error": "Missing item"}), 400

    df = CHECK[CHECK["item"] == item].copy()

    # Detect canonical column names you use
    site_col = "site" if "site" in df.columns else "Inventory Site"
    date_col = "ship_date" if "ship_date" in df.columns else "Ship Date"

    # Ensure datetime dtype for sorting
    if date_col in df.columns:
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")

    cols = list(df.columns)
    groups = []
    total_count = 0

    for site_value, g in df.groupby(site_col, dropna=False):
        # --- Safe sorting: NA Order Date first, then Ship Date ascending ---
        if "Order Date" in g.columns:
            g["__is_order_na"] = g["Order Date"].isna() | (g["Order Date"] == "")
        else:
            g["__is_order_na"] = False

        if date_col in g.columns:
            g = g.sort_values(
                by=["__is_order_na", date_col],
                ascending=[False, True],   # False → NA first; True → date ascending
                na_position="last"
            ).copy()
        else:
            g = g.sort_values("__is_order_na", ascending=False).copy()

        total_count += len(g)

        # Format date columns safely
        for c in [date_col, "Order Date", "Ship Date"]:
            if c in g.columns:
                g[c] = to_date_str(g[c])  # use the safe helper from earlier

        g = g.fillna("")

        groups.append({
            "site": "" if pd.isna(site_value) else str(site_value),
            "count": int(len(g)),
            "columns": cols,
            "rows": g.to_dict(orient="records"),
        })


    groups.sort(key=lambda x: (x["site"] == "", x["site"]))  # optional stable order

    return jsonify({
        "item": item,
        "count": int(total_count),
        "groups": groups,
    })




TPL = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>LT Check - {{ app_title|e }}</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body{padding:24px}
    .neg{background:#ffeaea}
    .pos{background:#eaf9ef}
    .pill{display:inline-block;padding:.15rem .5rem;border-radius:999px}
    .assign-link{text-decoration:none}
    .assign-link:hover{text-decoration:underline}
  </style>
</head>
<body>
  <h3 class="mb-3 text-center">{{ app_title|e }}</h3>
    <form class="row gy-2 gx-2 mb-4 justify-content-center" method="get">
    <div class="col-md-6 col-sm-10">
        <input class="form-control form-control-lg"
            style="height:60px; font-size:1.2rem;"
            name="qb"
            placeholder="Enter QB Num (e.g., SO-20251368)"
            value="{{ qb }}">
    </div>
    <div class="col-auto">
        <button class="btn btn-lg btn-primary px-4" style="height:60px;">Search</button>
    </div>
    </form>


  {% if summary %}
  <!-- ===== Summary card ===== -->
  <div class="card mb-4">
    <div class="card-header fw-bold">QB {{ summary.qb_num }}</div>
    <div class="card-body">
      <div class="row">
        <!-- Left: key info -->
        <div class="col-md-4">
          <div><b>Site</b>: {{ summary.site }}</div>
          <div><b>Ship Date</b>: {{ summary.ship_date or "-" }}</div>
          <div><b>Qty Needed</b>: {{ "%.0f"|format(summary.need_qty|float) }}</div>
        </div>

        <!-- All items/rows under this SO -->
        <div class="col-12 mt-4">
          <h6 class="mb-2">All Items in {{ summary.qb_num }}</h6>
          <div class="table-responsive">
            <table class="table table-sm table-hover align-middle">
              <thead>
                <tr>
                  <th>Item</th>
                  <th>Site</th>
                  <th>Ship Date</th>
                  <th class="text-end">Qty(-)</th>
                  <th class="text-end">Qty(+)</th>
                  <th class="text-end">Projected</th>
                  <th class="text-end">On Hand</th>
                  <th class="text-end">On Sales Order</th>
                  <th class="text-end">Available</th>
                  <th class="text-end">On PO</th>
                  <th>PO #</th>
                  <th>Remark</th>
                  <th>Vendor/Name</th>
                </tr>
              </thead>
            <tbody>
            {% for r in summary.lines %}
            <tr class="{{ 'neg' if (r.available|float) < 0 else '' }}">
                <td>
                <a href="#"
                    class="assign-link"
                    data-item="{{ r.item|e }}"
                    data-site="{{ r.site|e }}"
                    data-need="{{ (r.qty_minus|float) }}">
                    {{ r.item }}
                </a>
                </td>
                <td>{{ r.site }}</td>
                <td>{{ r.ship_date or '' }}</td>
                <td class="text-end">{{ "%.0f"|format(r.qty_minus|float) }}</td>
                <td class="text-end">{{ "%.0f"|format(r.qty_plus|float) }}</td>

                <!-- Highlight Projected column -->
                <td class="text-end bg-warning-subtle fw-bold">
                {{ "%.0f"|format(r.projected|float) }}
                </td>

                <!-- On Hand: red when == 0 -->
                <td class="text-end {% if (r.on_hand|float) == 0 %}text-danger fw-bold{% endif %}">
                {{ "%.0f"|format(r.on_hand|float) }}
                </td>

                <td class="text-end">{{ "%.0f"|format(r.on_sale|float) }}</td>

                <!-- Available: red when < 0 -->
                <td class="text-end {% if (r.available|float) < 0 %}text-danger fw-bold{% endif %}">
                <b>{{ "%.0f"|format(r.available|float) }}</b>
                </td>

                <td class="text-end">{{ "%.0f"|format(r.on_po|float) }}</td>
                <td>{{ r.po_num or '' }}</td>
                <td>{{ r.remark or '' }}</td>
                <td>{{ r.name or '' }}</td>
            </tr>
            {% endfor %}
            </tbody>

            </table>
          </div>

          <!-- Dynamic result panel for per-line assignment -->
          <div id="assign-panel" class="mt-3"></div>
        </div>
        <!-- /All items -->
      </div>
    </div>
  </div>


  {% elif qb %}
  <!-- ===== Not found ===== -->
  <div class="alert alert-warning mt-3">QB "{{ qb }}" not found on sheet 'check'.</div>
  {% endif %}

  <!-- ===== JS: handle per-line assign lookup ===== -->
<script>
document.addEventListener('click', function (e) {
  const el = e.target.closest('.assign-link');
  if (!el) return;
  e.preventDefault();

  const item = el.dataset.item || '';
  const safeItem = item.replace(/</g,'&lt;').replace(/>/g,'&gt;');

  const panel = document.getElementById('assign-panel');
  panel.innerHTML = '<div class="alert alert-info">Loading timeline for <b>' + safeItem + '</b>…</div>';

  const url = '/api/item_rows?item=' + encodeURIComponent(item);

  fetch(url).then(r => r.json()).then(d => {
    if (d.error) {
      panel.innerHTML = '<div class="alert alert-danger">' + d.error + '</div>';
      return;
    }

    const groups = d.groups || [];
    if (!groups.length) {
      panel.innerHTML = '<div class="card mt-3"><div class="card-header fw-bold">Timeline — ' +
        safeItem + ' (All Sites)</div><div class="card-body">No data</div></div>';
      return;
    }

    // helpers
    const isProj = c => String(c).trim().toLowerCase() === 'projected';
    const isNumericCol = c => {
      const s = String(c).trim().toLowerCase();
      return ['qty(-)','qty(+)','projected','on hand','on sales order','available','on po','net change','cumulative available']
        .includes(s);
    };

    // Build a card per site
    const siteCards = groups.map(function(g) {
      const cols = g.columns || [];

      // THEAD with highlight on "projected"
      const thead = '<thead><tr>' + cols.map(c => {
        const safeC = String(c).replace(/</g,'&lt;').replace(/>/g,'&gt;');
        const classes = []
        if (isProj(c)) classes.push('hi-projected');
        if (isNumericCol(c)) classes.push('text-end');
        const clsAttr = classes.length ? ' class="' + classes.join(' ') + '"' : '';
        return '<th' + clsAttr + '>' + safeC + '</th>';
      }).join('') + '</tr></thead>';

      // TBODY with highlight on "projected" + right-align numeric columns
      const rows = (g.rows || []).map(function(row) {
        const tds = cols.map(function(c) {
          const v = (row[c] == null ? '' : String(row[c]));
          const classes = [];
          if (isProj(c)) classes.push('hi-projected','fw-bold');
          if (isNumericCol(c)) classes.push('text-end');
          const clsAttr = classes.length ? ' class="' + classes.join(' ') + '"' : '';
          return '<td' + clsAttr + '>' + v.replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</td>';
        }).join('');
        return '<tr>' + tds + '</tr>';
      }).join('');

      const body = rows || '<tr><td colspan="' + cols.length +
                   '" class="text-center text-muted">No rows</td></tr>';

      return '' +
      '<div class="card mt-3">' +
        '<div class="card-header fw-bold">Site: ' +
          (g.site ? g.site.replace(/</g,'&lt;').replace(/>/g,'&gt;') : '(None)') +
          ' &nbsp; <span class="text-muted fw-normal">Rows: ' + (g.count||0) + '</span>' +
        '</div>' +
        '<div class="card-body">' +
          '<div class="table-responsive">' +
            '<table class="table table-sm table-bordered table-hover align-middle">' +
              thead + '<tbody>' + body + '</tbody>' +
            '</table>' +
          '</div>' +
        '</div>' +
      '</div>';
    }).join('');

    panel.innerHTML = '' +
      '<div class="card mt-3">' +
        '<div class="card-header fw-bold">Timeline — ' + safeItem + ' (Grouped by Inventory Site, Ship Date ↑)</div>' +
        '<div class="card-body">' +
          '<div class="text-muted small mb-2">Total rows: ' + (d.count||0) + '</div>' +
          siteCards +
        '</div>' +
      '</div>';
  }).catch(function(err) {
    panel.innerHTML = '<div class="alert alert-danger">Error: ' + String(err) + '</div>';
  });
});
</script>


</body>
</html>
"""


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5002)