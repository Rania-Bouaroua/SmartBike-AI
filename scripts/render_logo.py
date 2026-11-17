"""Render assets/logo*.svg to PNG (used by the report, README and Streamlit). Requires: pip install resvg-py

Palette (same as the team portfolio): ink #0B1020 · accent #2563EB · sky #8DDCFF · green #22C55E · gold.
Font: Space Grotesk (SIL OFL, assets/fonts).
"""
import base64
import pathlib

import resvg_py

A = pathlib.Path(__file__).resolve().parents[1] / "assets"
FONTS = [str(A / "fonts"), "C:/Windows/Fonts"]
icon_b64 = base64.b64encode((A / "logo.svg").read_bytes()).decode()
full = (A / "logo_full.svg").read_text(encoding="utf-8").replace('href="logo.svg"',
                                                                   f'href="data:image/svg+xml;base64,{icon_b64}"')
dark = (full.replace('fill="#0B1020"', 'fill="#F8FAFC"').replace('fill="#526174"', 'fill="#94A3B8"')
        .replace('stroke="#2563EB"', 'stroke="#8DDCFF"').replace('<tspan fill="#2563EB">', '<tspan fill="#8DDCFF">'))
width = int(full.split('width="')[1].split('"')[0])
for name, svg, w in [("logo.png", (A / "logo.svg").read_text(encoding="utf-8"), 512),
                     ("logo_full.png", full, width), ("logo_full_dark.png", dark, width)]:
    png = resvg_py.svg_to_bytes(svg_string=svg, width=w * 2, font_dirs=FONTS)
    (A / name).write_bytes(bytes(png))
    print("wrote", name)
