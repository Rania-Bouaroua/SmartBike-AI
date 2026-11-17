"""Render assets/logo*.svg to PNG (used by the report, README and Streamlit). Requires: pip install resvg-py"""
import base64
import pathlib

import resvg_py

A = pathlib.Path(__file__).resolve().parents[1] / "assets"
icon_b64 = base64.b64encode((A / "logo.svg").read_bytes()).decode()
full = (A / "logo_full.svg").read_text(encoding="utf-8").replace('href="logo.svg"',
                                                                   f'href="data:image/svg+xml;base64,{icon_b64}"')
for name, svg, w in [("logo.png", (A / "logo.svg").read_text(encoding="utf-8"), 512),
                     ("logo_full.png", full, 1460),
                     ("logo_full_dark.png", full.replace('fill="#0F172A"', 'fill="#F8FAFC"')
                      .replace('fill="#64748B"', 'fill="#94A3B8"'), 1460)]:
    png = resvg_py.svg_to_bytes(svg_string=svg, width=w * 2, font_dirs=["C:/Windows/Fonts"])
    (A / name).write_bytes(bytes(png))
    print("wrote", name)
