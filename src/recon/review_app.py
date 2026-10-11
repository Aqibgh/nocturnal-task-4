"""Review page: shows only the items a person must decide, each next to the photo of that row.

streamlit run src/recon/review_app.py
"""

from __future__ import annotations

import json

import streamlit as st
from PIL import Image

from recon import config, pipeline

st.set_page_config(page_title="GRN review", page_icon="📋", layout="centered")
cfg = config.load()
period = st.sidebar.text_input("Month", cfg.period)
reviewer = st.sidebar.text_input("Your name (saved with every decision)")
st.sidebar.caption(f"Scenario time: {cfg.now:%a %d %b %Y %H:%M}")

st.title(f"Store reconciliation: items to check ({period})")
out = cfg.out_dir / period
review_file = out / "review.json"
if not review_file.exists():
    st.info("No run found for this month yet.")
    if st.button("Run reconciliation"):
        pipeline.run(cfg, period)
        st.rerun()
    st.stop()

items = json.loads(review_file.read_text(encoding="utf-8"))
done = pipeline.load_resolutions(cfg, period)

if not items:
    st.success("Nothing needs a person. Every remaining difference is in the report.")
else:
    st.write(f"{len(items)} item(s). Everything else was matched or explained automatically.")

for it in items:
    with st.container(border=True):
        st.subheader(f"GRN {it['grn']}: {it['field']}" if it["grn"] else it["row_id"])
        st.write(f"**Why:** {it['reason']}")
        if it.get("page"):
            full_img: Image.Image = Image.open(cfg.period_dir(period) / "register" / it["page"])
            img = full_img
            if it.get("y_top") is not None and it.get("y_bottom") is not None:
                h = img.height
                # Model coordinates are approximate: retain context and always offer the full page.
                top = max(0, int((it["y_top"] - 0.15) * h))
                bottom = min(h, int((it["y_bottom"] + 0.15) * h))
                img = img.crop((0, top, img.width, bottom))
            st.image(img, caption=f"{it['page']}, row {it['row_id'].rsplit('r', 1)[-1]}")
            with st.expander("Full register page"):
                st.image(full_img, caption=it["page"])
            st.write(f"**Model read:** {it['raw']}")
        if it.get("others"):
            st.write("**Other sources:** " + ", ".join(f"{k}: {v}" for k, v in it["others"].items()))
        value = st.text_input(
            "Correct value as written in the source", value=it.get("suggestion") or "", key=f"v-{it['item_id']}"
        )
        note = st.text_input("Note (optional)", key=f"n-{it['item_id']}")
        if st.button("Save decision", key=f"b-{it['item_id']}", disabled=not reviewer.strip() or not value.strip()):
            try:
                pipeline.save_resolution(cfg, period, it["item_id"], value.strip(), reviewer.strip(), note.strip())
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.success(f"Saved: {value}")
                st.rerun()

if done:
    st.divider()
    st.write(f"{len(done)} decision(s) saved for {period}.")
    with st.expander("Saved decisions"):
        st.json(done)
    if st.button("Re-run reconciliation with these decisions", type="primary"):
        res = pipeline.run(cfg, period)
        st.code(res.message)
        st.write(f"Proof: {'ties' if res.proof_ok else 'DOES NOT TIE'} · message {res.delivery}")
        st.rerun()

if not reviewer.strip():
    st.sidebar.warning("Enter your name to save decisions.")
