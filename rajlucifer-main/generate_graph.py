#!/usr/bin/env python3
"""
Pulls the last N days of real GitHub contribution counts for GH_LOGIN via the
GraphQL API and regenerates an animated, self-drawing SVG contribution graph.

Env vars required:
  GH_TOKEN   - a token with access to read the GraphQL API (the default
               GITHUB_ACTIONS token works fine, since contribution calendars
               are public data)
  GH_LOGIN   - the GitHub username whose contributions to chart
               (defaults to the repo owner if unset, inside Actions)

Optional:
  DAYS       - how many trailing days to show (default 31)
  OUT_PATH   - output svg path (default contribution_graph.svg)
  TITLE      - graph title (default "<login>'s contribution graph")
"""

import os
import sys
import math
import json
from datetime import datetime, timedelta, timezone

import urllib.request
import urllib.error

GRAPHQL_URL = "https://api.github.com/graphql"

QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        weeks {
          contributionDays {
            date
            contributionCount
          }
        }
      }
    }
  }
}
"""


def fetch_contributions(login, token, days):
    to_dt = datetime.now(timezone.utc)
    from_dt = to_dt - timedelta(days=days - 1)
    # GitHub wants the 'from' at start of day, 'to' at end of day, ISO 8601
    from_iso = from_dt.strftime("%Y-%m-%dT00:00:00Z")
    to_iso = to_dt.strftime("%Y-%m-%dT23:59:59Z")

    payload = json.dumps({
        "query": QUERY,
        "variables": {"login": login, "from": from_iso, "to": to_iso},
    }).encode("utf-8")

    req = urllib.request.Request(
        GRAPHQL_URL,
        data=payload,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "live-graph-action",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"GitHub API error {e.code}: {e.read().decode('utf-8')}", file=sys.stderr)
        raise

    if "errors" in body:
        print(f"GraphQL errors: {body['errors']}", file=sys.stderr)
        raise SystemExit(1)

    weeks = body["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    days_list = []
    for week in weeks:
        for day in week["contributionDays"]:
            days_list.append((day["date"], day["contributionCount"]))

    days_list.sort(key=lambda d: d[0])
    days_list = days_list[-days:]
    return days_list


def build_svg(days_list, title):
    # Format labels as "Day DD" (e.g. "Mon 14") for richer x-axis display
    labels = []
    for d, _ in days_list:
        dt = datetime.strptime(d, "%Y-%m-%d")
        labels.append(f"{dt.strftime('%a')} {dt.day}")

    values = [v for _, v in days_list]
    N = len(values)

    ymax_data = max(values) if values else 0
    YMAX = max(5, math.ceil(ymax_data / 5) * 5) if ymax_data > 0 else 5

    X0, X1 = 90, 1460
    Y0, Y1 = 90, 470

    def xpos(i):
        return X0 + (X1 - X0) * i / (N - 1) if N > 1 else (X0 + X1) / 2

    def ypos(v):
        return Y1 - (Y1 - Y0) * (v / YMAX)

    points = [(xpos(i), ypos(values[i])) for i in range(N)]

    dists = []
    for i in range(1, N):
        x0, y0 = points[i - 1]
        x1, y1 = points[i]
        dists.append(math.hypot(x1 - x0, y1 - y0))
    total = sum(dists) or 1
    cum = [0]
    c = 0
    for d in dists:
        c += d
        cum.append(c)
    fracs = [c / total for c in cum]

    path_d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in points)

    DRAW_END = 0.55
    HOLD_END = 0.92
    RESET = 0.921

    def circle_anim(appear_frac):
        appear = round(appear_frac * DRAW_END, 4)
        appear2 = round(min(appear + 0.002, DRAW_END), 4)
        return f"0;{appear};{appear2};{HOLD_END};{RESET};1"

    # Build tooltip data points (show date + contribution count on hover)
    circles_svg = ""
    for i, (x, y) in enumerate(points):
        kt = circle_anim(fracs[i])
        date_str = days_list[i][0]
        count = values[i]
        label = labels[i]
        # Outer glow circle
        circles_svg += f'''
  <g>
    <title>{date_str}: {count} contribution{"s" if count != 1 else ""}</title>
    <circle cx="{x:.1f}" cy="{y:.1f}" r="12" fill="#a855f7" opacity="0">
      <animate attributeName="opacity" values="0;0;0.18;0.18;0;0" keyTimes="{kt}" dur="7s" repeatCount="indefinite"/>
    </circle>
    <circle cx="{x:.1f}" cy="{y:.1f}" r="6" fill="#a855f7" stroke="#0b1220" stroke-width="1.5" opacity="0">
      <animate attributeName="opacity" values="0;0;1;1;0;0" keyTimes="{kt}" dur="7s" repeatCount="indefinite"/>
    </circle>
    <text x="{x:.1f}" y="{y - 18:.1f}" text-anchor="middle" font-family="Fira Code, monospace" font-size="13"
          fill="#a855f7" opacity="0" font-weight="600">
      {count}
      <animate attributeName="opacity" values="0;0;1;1;0;0" keyTimes="{kt}" dur="7s" repeatCount="indefinite"/>
    </text>
  </g>'''

    grid_svg = ""
    # Horizontal grid lines & y-axis labels
    for v in range(0, YMAX + 1, max(1, YMAX // 10)):
        y = ypos(v)
        grid_svg += f'<line x1="{X0}" y1="{y:.1f}" x2="{X1}" y2="{y:.1f}" stroke="#1f5c56" stroke-width="1" stroke-dasharray="4 5" opacity="0.55"/>\n'
        grid_svg += f'<text x="{X0-18}" y="{y+5:.1f}" text-anchor="end" font-family="Fira Code, monospace" font-size="16" fill="#22d3ee">{v}</text>\n'

    # Vertical grid lines & x-axis labels (day + date)
    # Only render every Nth label to avoid crowding when DAYS is large
    label_step = max(1, N // 15)
    for i, x in enumerate([p[0] for p in points]):
        grid_svg += f'<line x1="{x:.1f}" y1="{Y0}" x2="{x:.1f}" y2="{Y1}" stroke="#1f5c56" stroke-width="1" stroke-dasharray="4 5" opacity="0.35"/>\n'
        if i % label_step == 0 or i == N - 1:
            # Two-line label: abbreviated weekday + day number
            parts = labels[i].split(" ")
            weekday = parts[0] if len(parts) > 0 else ""
            daynum = parts[1] if len(parts) > 1 else labels[i]
            grid_svg += f'<text x="{x:.1f}" y="{Y1+22}" text-anchor="middle" font-family="Fira Code, monospace" font-size="13" fill="#22d3ee" opacity="0.7">{weekday}</text>\n'
            grid_svg += f'<text x="{x:.1f}" y="{Y1+38}" text-anchor="middle" font-family="Fira Code, monospace" font-size="14" fill="#22d3ee" font-weight="600">{daynum}</text>\n'

    svg = f'''<svg viewBox="0 0 1500 580" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
      <feGaussianBlur stdDeviation="5" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <filter id="glow-soft" x="-30%" y="-30%" width="160%" height="160%">
      <feGaussianBlur stdDeviation="2.5" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <linearGradient id="bgGrad" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#0b1220"/>
      <stop offset="100%" stop-color="#0d1a2e"/>
    </linearGradient>
  </defs>

  <!-- Background -->
  <rect x="0" y="0" width="1500" height="580" rx="16" fill="url(#bgGrad)"/>
  <rect x="1" y="1" width="1498" height="578" rx="15" fill="none" stroke="#1f3a5f" stroke-width="1.5" opacity="0.6"/>

  <!-- Title -->
  <text x="750" y="50" text-anchor="middle" font-family="Fira Code, monospace" font-size="28" font-weight="700" fill="#22d3ee" filter="url(#glow-soft)">{title}</text>

  <!-- Sub-label: last updated date -->
  <text x="1450" y="78" text-anchor="end" font-family="Fira Code, monospace" font-size="13" fill="#22d3ee" opacity="0.45">auto-updated every 6 h</text>

  <!-- Grid + axes -->
  {grid_svg}

  <!-- Axis labels -->
  <text x="30" y="280" text-anchor="middle" font-family="Fira Code, monospace" font-size="16" fill="#22d3ee"
        transform="rotate(-90 30 280)">Contributions / day</text>
  <text x="775" y="548" text-anchor="middle" font-family="Fira Code, monospace" font-size="17" fill="#22d3ee">Date</text>

  <!-- Glow area under the line (gradient fill) -->
  <defs>
    <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#00eaff" stop-opacity="0.18"/>
      <stop offset="100%" stop-color="#00eaff" stop-opacity="0.01"/>
    </linearGradient>
  </defs>
  <path d="{path_d} L {points[-1][0]:.1f},{Y1} L {points[0][0]:.1f},{Y1} Z"
        fill="url(#areaGrad)" opacity="0.5"/>

  <!-- Animated line -->
  <path d="{path_d}" fill="none" stroke="#00eaff" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"
        stroke-dasharray="{round(total,1)}" filter="url(#glow)">
    <animate attributeName="stroke-dashoffset"
      values="{round(total,1)};0;0;{round(total,1)};{round(total,1)}"
      keyTimes="0;{DRAW_END};{HOLD_END};{RESET};1"
      dur="7s" repeatCount="indefinite"/>
  </path>

  <!-- Per-day dots + count labels -->
  {circles_svg}

  <!-- Comet dot riding the tip -->
  <g>
    <circle r="9" fill="#ffffff" filter="url(#glow)">
      <animate attributeName="opacity" values="1;1;0;0;1" keyTimes="0;{DRAW_END};{HOLD_END};{RESET};1" dur="7s" repeatCount="indefinite"/>
      <animateMotion dur="7s" repeatCount="indefinite"
        keyPoints="0;1;1;1;0" keyTimes="0;{DRAW_END};{HOLD_END};{RESET};1" calcMode="linear"
        path="{path_d}"/>
    </circle>
  </g>
</svg>'''
    return svg


def main():
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GH_TOKEN (or GITHUB_TOKEN) env var is required", file=sys.stderr)
        sys.exit(1)

    login = os.environ.get("GH_LOGIN") or os.environ.get("GITHUB_REPOSITORY_OWNER")
    if not login:
        print("GH_LOGIN env var is required (or run inside GitHub Actions)", file=sys.stderr)
        sys.exit(1)

    days = int(os.environ.get("DAYS", "31"))
    out_path = os.environ.get("OUT_PATH", "contribution_graph.svg")
    title = os.environ.get("TITLE", f"{login}'s contribution graph")

    days_list = fetch_contributions(login, token, days)
    if not days_list:
        print("No contribution data returned", file=sys.stderr)
        sys.exit(1)

    svg = build_svg(days_list, title)
    with open(out_path, "w") as f:
        f.write(svg)

    print(f"Wrote {out_path} ({len(days_list)} days, login={login})")


if __name__ == "__main__":
    main()
