# webpage.py
import os
from datetime import datetime
from flask import Flask, request, render_template_string, jsonify, Response, abort
import pandas as pd
from sqlalchemy import create_engine, text

app = Flask(__name__)

# =========================
# DB ENGINE (your DSN)
# =========================
DATABASE_DSN = (
    "postgresql+psycopg://postgres.avcznjglmqhmzqtsrlfg:Czheyuan0227@"
    "aws-0-us-east-2.pooler.supabase.com:6543/postgres?sslmode=require"
)

engine = create_engine(DATABASE_DSN, pool_pre_ping=True)

# =========================
# Data cache
# =========================
SO_INV: pd.DataFrame | None = None   # from public.wo_structured
NAV: pd.DataFrame | None = None      # from public."NT Shipping Schedule"
_LAST_LOAD_ERR: str | None = None
_LAST_LOADED_AT: datetime | None = None

def _safe_date_col(df: pd.DataFrame, col: str):
    if col in df.columns:
        df[col] = pd.to_datetime(df[col], errors="coerce")

def _read_table(schema: str, table: str) -> pd.DataFrame:
    """
    Read with explicit quoting so names with spaces work.
    """
    sql = f'SELECT * FROM "{schema}"."{table}"'
    return pd.read_sql_query(text(sql), con=engine)

def _load_from_db(force: bool = False):
    """
    Load SO_INV and NAV from Postgres into memory.
    """
    global SO_INV, NAV, _LAST_LOAD_ERR, _LAST_LOADED_AT
    try:
        so = _read_table("public", "wo_structured")
        nav = _read_table("public", "NT Shipping Schedule")

        # Light coercions
        for c in ("Ship Date", "Order Date"):
            _safe_date_col(so, c)
            _safe_date_col(nav, c)

        SO_INV = so
        NAV = nav
        _LAST_LOAD_ERR = None
        _LAST_LOADED_AT = datetime.now()
    except Exception as e:
        SO_INV = None
        NAV = None
        _LAST_LOAD_ERR = f"DB load error: {e}"

# initial load
_load_from_db(force=True)

def _ensure_loaded():
    if SO_INV is None or NAV is None:
        _load_from_db(force=True)

def _to_date_str(s: pd.Series, fmt="%Y-%m-%d") -> pd.Series:
    s = pd.to_datetime(s, errors="coerce")
    return s.apply(lambda x: x.strftime(fmt) if pd.notnull(x) else "")

def lookup_on_po_by_item(item: str) -> int | None:
    """Return first non-null numeric 'On PO' value from SO_INV filtered by Item."""
    df = SO_INV[SO_INV["Item"] == item]
    if "On PO" not in df.columns:
        return None
    s = pd.to_numeric(df["On PO"], errors="coerce").dropna()
    return int(s.iloc[0]) if not s.empty else None


# =========================
# Routes
# =========================
@app.route("/", methods=["GET"])
def index():
    if request.args.get("reload") == "1":
        _load_from_db(force=True)

    _ensure_loaded()
    if _LAST_LOAD_ERR:
        return render_template_string(ERR_TPL, error=_LAST_LOAD_ERR), 503

    so_num = (request.args.get("so") or request.args.get("qb") or "").strip()
    rows = None
    all_cols = []
    count = 0

    # The exact headers you render in the template
    required_headers = [
        "Order Date","Name","P. O. #","QB Num","Item","Qty(-)","Available",
        "Available + Pre-installed PO","On Hand","On Sales Order","On PO",
        "Assigned Q'ty","On Hand - WIP","Available + On PO","Sales/Week",
        "Recommended Restock Qty","Component_Status","Ship Date"
    ]

    if so_num:
        mask = SO_INV["QB Num"].astype(str) == so_num
        rows = SO_INV.loc[mask].copy()
        count = len(rows)

        # Derive "On Hand - WIP" from "In Stock(Inventory)" if needed
        if "On Hand - WIP" not in rows.columns and "In Stock(Inventory)" in rows.columns:
            rows["On Hand - WIP"] = rows["In Stock(Inventory)"]

        # Ensure all headers exist to avoid KeyError in Jinja
        for h in required_headers:
            if h not in rows.columns:
                rows[h] = ""

        # Format dates to strings
        for c in ("Ship Date", "Order Date"):
            if c in rows.columns:
                rows[c] = _to_date_str(rows[c])

        # Keep a copy of all original cols if you still need them elsewhere
        all_cols = list(rows.columns)

        # Make strings for safe rendering
        rows = rows.fillna("").astype(str)

    return render_template_string(
        INDEX_TPL,
        so_num=so_num,
        rows=None if rows is None else rows.to_dict(orient="records"),
        columns=all_cols,
        count=count,
        loaded_at=_LAST_LOADED_AT.strftime("%Y-%m-%d %H:%M:%S") if _LAST_LOADED_AT else "—",
        summary=None,  # set/keep this until you wire qb_summary()
    )


@app.route("/api/reload", methods=["POST"])
def api_reload():
    _load_from_db(force=True)
    if _LAST_LOAD_ERR:
        return jsonify({"ok": False, "error": _LAST_LOAD_ERR}), 500
    return jsonify({"ok": True, "loaded_at": _LAST_LOADED_AT.isoformat()})

@app.route("/so_lines")
def so_lines():
    _ensure_loaded()
    if _LAST_LOAD_ERR:
        return render_template_string(ERR_TPL, error=_LAST_LOAD_ERR), 503

    item = (request.args.get("item") or "").strip()
    if not item:
        abort(400, "Missing item")

    need_cols = ["Name", "QB Num", "Item", "Qty(-)", "Ship Date", "Picked"]
    g = SO_INV[SO_INV["Item"] == item].copy()
    for c in need_cols:
        if c not in g.columns:
            g[c] = ""
    if "Ship Date" in g.columns:
        g["Ship Date"] = _to_date_str(g["Ship Date"])

    on_po_val = lookup_on_po_by_item(item)

    return render_template_string(
        SUBPAGE_TPL,
        title=f"On Sales Order — {item}",
        columns=need_cols,
        rows=g[need_cols].fillna("").astype(str).to_dict(orient="records"),
        extra_note="Source: public.wo_structured",
        on_po=on_po_val,
    )


@app.route("/po_lines")
def po_lines():
    _ensure_loaded()
    if _LAST_LOAD_ERR:
        return render_template_string(ERR_TPL, error=_LAST_LOAD_ERR), 503

    item = (request.args.get("item") or "").strip()
    if not item:
        abort(400, "Missing item")

    if "Item" not in NAV.columns:
        return render_template_string(ERR_TPL, error="NAV table missing 'Item' column."), 500

    g = NAV[NAV["Item"] == item].copy()
    for dc in ("Ship Date", "Order Date", "ETA"):
        if dc in g.columns:
            g[dc] = _to_date_str(g[dc])

    cols = list(g.columns) if not g.empty else list(NAV.columns)
    g = g.fillna("").astype(str)

    on_po_val = lookup_on_po_by_item(item)

    return render_template_string(
        SUBPAGE_TPL,
        title=f"On PO — {item}",
        columns=cols,
        rows=g[cols].to_dict(orient="records") if not g.empty else [],
        extra_note='Source: public."NT Shipping Schedule"',
        on_po=on_po_val,
    )


# =========================
# Templates
# =========================
ERR_TPL = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Data Error</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    :root{ --ink:#1f2937; --muted:#64748b; }
    body{ padding:28px; background:#f7fafc; color:var(--ink); }
    .card-lite{ border-radius:14px; box-shadow:0 10px 22px rgba(0,0,0,.06); }
  </style>
</head>
<body>
  <div class="container">
    <div class="card-lite bg-white p-4">
      <div class="alert alert-danger m-0">
        <div class="fw-bold fs-5">Load Error</div>
        <div class="mt-2">{{ error }}</div>
      </div>
    </div>
  </div>
</body>
</html>
"""

INDEX_TPL = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>LT Check — DB</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    :root{
      --ink:#0f172a; --muted:#6b7280; --bg:#f7fafc;
      --ok-bg:#e7f8ed; --ok-fg:#137a2a;
      --warn-bg:#ffecec; --warn-fg:#a61b1b;
      --hdr:#f8fafc;
    }
    html,body{ background:var(--bg); color:var(--ink); }
    body{ padding:28px; }
    .card-lite{ border-radius:14px; box-shadow:0 10px 22px rgba(0,0,0,.06); }
    .muted{ color:var(--muted); }
    .nowrap{ white-space:nowrap; }
    .clicky a{text-decoration:none}
    .clicky a:hover{text-decoration:underline}
    .table td, .table th{ vertical-align:middle; }
    /* zebra + hover */
    .table tbody tr:nth-child(odd){ background:#fcfcfe; }
    .table tbody tr:hover{ background:#eef6ff; }
    /* sticky headers */
    .table-responsive{ max-height:70vh; overflow:auto; }
    .table thead th{ position:sticky; top:0; z-index:2; background:var(--hdr); }
    /* numeric alignment */
    .num{ text-align:right; font-variant-numeric: tabular-nums; }
    .neg{ color:var(--warn-fg); background: #fff6f6; }
    .zero{ color:var(--muted); }
    /* badges */
    .badge-pill{ display:inline-block; padding:.25rem .6rem; border-radius:999px; font-weight:600; }
    .badge-ok{ background:var(--ok-bg); color:var(--ok-fg); }
    .badge-warn{ background:var(--warn-bg); color:var(--warn-fg); }
    /* emphasis */
    .blue-cell{ color:#0d6efd; font-weight:600; }
  </style>
</head>
<body>
  <div class="d-flex justify-content-between align-items-center mb-3">
    <h4 class="m-0">LT Check — From DB</h4>
    <div class="text-muted small">Loaded: {{ loaded_at }}</div>
  </div>

  <form class="row gy-2 gx-2 mb-4" method="get">
    <div class="col-md-6 col-sm-10">
      <input class="form-control form-control-lg" style="height:60px;font-size:1.1rem"
             name="so" placeholder="Enter SO / QB Num (e.g., SO-20251368)" value="{{ so_num or '' }}">
    </div>
    <div class="col-auto">
      <button class="btn btn-primary btn-lg px-4" style="height:60px">Search</button>
    </div>
    <div class="col-auto">
      <a class="btn btn-outline-secondary btn-lg" style="height:60px" href="/?reload=1">Reload</a>
    </div>
  </form>

  {# ===== Summary card (qb_summary) ===== #}
  {% if summary %}
  <div class="card-lite bg-white mb-4">
    <div class="card-header fw-bold">QB {{ summary.qb_num }}</div>
    <div class="card-body">
      <div class="row g-3">
        <div class="col-md-4">
          <div><b>Site</b>: {{ summary.site }}</div>
          <div><b>Ship Date</b>: {{ summary.ship_date or "-" }}</div>
          <div><b>Qty Needed</b>: {{ "%.0f"|format(summary.need_qty|float) }}</div>
        </div>

        <div class="col-12">
          <h6 class="mb-2 mt-2">All Items in {{ summary.qb_num }}</h6>
          <div class="table-responsive">
            <table class="table table-sm table-hover align-middle">
              <thead>
                <tr class="text-uppercase small text-muted">
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
                  <a href="#" class="assign-link"
                     data-item="{{ r.item|e }}"
                     data-site="{{ r.site|e }}"
                     data-need="{{ (r.qty_minus|float) }}">
                     {{ r.item }}
                  </a>
                </td>
                <td class="nowrap">{{ r.site }}</td>
                <td class="nowrap">{{ r.ship_date or '' }}</td>
                <td class="num">{{ "%.0f"|format(r.qty_minus|float) }}</td>
                <td class="num">{{ "%.0f"|format(r.qty_plus|float) }}</td>
                <td class="num bg-warning-subtle fw-bold">{{ "%.0f"|format(r.projected|float) }}</td>
                <td class="num {% if (r.on_hand|float) == 0 %}text-danger fw-bold{% endif %}">
                  {{ "%.0f"|format(r.on_hand|float) }}
                </td>
                <td class="num">{{ "%.0f"|format(r.on_sale|float) }}</td>
                <td class="num {% if (r.available|float) < 0 %}text-danger fw-bold{% endif %}">
                  <b>{{ "%.0f"|format(r.available|float) }}</b>
                </td>
                <td class="num">{{ "%.0f"|format(r.on_po|float) }}</td>
                <td class="nowrap">{{ r.po_num or '' }}</td>
                <td class="nowrap">{{ r.remark or '' }}</td>
                <td class="nowrap">{{ r.name or '' }}</td>
              </tr>
              {% endfor %}
              </tbody>
            </table>
          </div>
          <div id="assign-panel" class="mt-3"></div>
        </div>
      </div>
    </div>
  </div>
  {% endif %}

  {% set headers = [
    "Order Date","Name","P. O. #","QB Num","Item","Qty(-)","Available",
    "Available + Pre-installed PO","On Hand","On Sales Order","On PO",
    "Assigned Q'ty","On Hand - WIP","Available + On PO","Sales/Week",
    "Recommended Restock Qty","Component_Status","Ship Date"
  ] %}
  {% set numeric_cols = [
    "Qty(-)","Available","Available + Pre-installed PO","On Hand",
    "On Sales Order","On PO","Assigned Q'ty","On Hand - WIP",
    "Available + On PO","Sales/Week","Recommended Restock Qty"
  ] %}

  {% if so_num and rows %}
  <div class="card-lite bg-white">
    <div class="card-header fw-bold">
      SO / QB Num: {{ so_num }} &nbsp; <span class="text-muted">Rows: {{ count }}</span>
    </div>
    <div class="card-body">
      <div class="table-responsive">
        <table class="table table-sm table-bordered table-hover align-middle">
          <thead class="table-light text-uppercase small text-muted">
            <tr>
              {% for h in headers %}
                <th class="{{ 'text-end' if h in numeric_cols else '' }}">{{ h }}</th>
              {% endfor %}
            </tr>
          </thead>
          <tbody>
            {% for r in rows %}
              {% set status_badge = 'badge-ok' if r.get('Component_Status') == 'Available' else 'badge-warn' %}
              <tr>
              {% for h in headers %}
                {% if h == 'On Sales Order' %}
                  <td class="num clicky">
                    <a href="/so_lines?item={{ r.get('Item','') | urlencode }}">{{ r.get(h,'') }}</a>
                  </td>
                {% elif h == 'On PO' %}
                  <td class="num clicky">
                    <a href="/po_lines?item={{ r.get('Item','') | urlencode }}">{{ r.get(h,'') }}</a>
                  </td>
                {% elif h == 'On Hand - WIP' %}
                  <td class="num blue-cell">{{ r.get('On Hand - WIP', '') }}</td>
                {% elif h == 'Component_Status' %}
                  <td>
                    <span class="badge-pill {{ status_badge }}">{{ r.get(h,'') }}</span>
                  </td>
                {% elif h in numeric_cols %}
                  {% set v = r.get(h,'') %}
                  <td class="num {% if v is number and v < 0 %}neg{% elif v == 0 %}zero{% endif %}">
                    {{ v }}
                  </td>
                {% else %}
                  <td class="{{ 'nowrap' if h in ['Order Date','Ship Date','QB Num','P. O. #'] else '' }}">
                    {{ r.get(h,'') }}
                  </td>
                {% endif %}
              {% endfor %}
              </tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
      <div class="mt-2 text-muted small">Tip: Click “On Sales Order” or “On PO” to drill down by Item. Headers stay visible when you scroll.</div>
    </div>
  </div>
  {% elif so_num %}
  <div class="alert alert-warning mt-3">No rows found for "{{ so_num }}".</div>
  {% endif %}

  <script>
  // Quick drill-down buttons from summary card
  document.addEventListener('click', function (e) {
    const el = e.target.closest('.assign-link');
    if (!el) return;
    e.preventDefault();
    const item = el.dataset.item || '';
    const panel = document.getElementById('assign-panel');
    const safe = item.replace(/</g,'&lt;').replace(/>/g,'&gt;');
    panel.innerHTML = '<div class="alert alert-info">Quick links for <b>' + safe + '</b>…</div>' +
      '<div class="mt-2">' +
      '<a class="btn btn-sm btn-outline-primary me-2" href="/so_lines?item=' + encodeURIComponent(item) + '">View On Sales Order</a>' +
      '<a class="btn btn-sm btn-outline-secondary" href="/po_lines?item=' + encodeURIComponent(item) + '">View On PO</a>' +
      '</div>';
  });
  </script>
</body>
</html>
"""

SUBPAGE_TPL = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>{{ title }}</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    :root{ --hdr:#f8fafc; }
    body{ padding:28px; background:#f7fafc; }
    .table td, .table th{ vertical-align:middle; }
    .card-lite{ border-radius:14px; box-shadow:0 10px 22px rgba(0,0,0,.06); }
    .pill{
      display:inline-block; padding:.2rem .65rem; border-radius:999px;
      background:#eef4ff; border:1px solid #d9e4ff; font-weight:600; margin-left:.5rem;
    }
    .table-responsive{ max-height:70vh; overflow:auto; }
    .table thead th{ position:sticky; top:0; z-index:2; background:var(--hdr); }
  </style>
</head>
<body>
  <div class="d-flex justify-content-between align-items-center mb-3">
    <h5 class="m-0">{{ title }}</h5>
    <a class="btn btn-sm btn-outline-secondary" href="/">Back</a>
  </div>

  <div class="card-lite bg-white">
    <div class="card-header fw-bold d-flex align-items-center justify-content-between">
      <span>{{ title }}</span>
      {% if on_po is not none %}
        <span class="pill">On PO: {{ on_po }}</span>
      {% endif %}
    </div>
    <div class="card-body">
      <div class="table-responsive">
        <table class="table table-sm table-bordered table-hover align-middle">
          <thead class="table-light text-uppercase small text-muted">
            <tr>
              {% for c in columns %}
                <th>{{ c }}</th>
              {% endfor %}
            </tr>
          </thead>
          <tbody>
            {% if rows %}
              {% for r in rows %}
                <tr>
                  {% for c in columns %}
                    <td>{{ r[c] }}</td>
                  {% endfor %}
                </tr>
              {% endfor %}
            {% else %}
              <tr><td colspan="{{ columns|length }}" class="text-center text-muted">No data</td></tr>
            {% endif %}
          </tbody>
        </table>
      </div>
      <div class="text-muted small">{{ extra_note }}</div>
    </div>
  </div>
</body>
</html>
"""


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5002)

