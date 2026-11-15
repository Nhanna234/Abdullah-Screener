"""Streamlit host for the prebuilt EMA Reclaim Lab React dashboard."""
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from dashboard import chart, dashboard_config, export_report, run

DIST = Path(__file__).resolve().parent / "frontend" / "dist"
HANDLERS = {"run": run, "export": export_report, "chart": chart}

st.set_page_config(page_title="EMA Reclaim Lab", layout="wide", page_icon="📈")
if not (DIST / "index.html").is_file():
    st.error("The React build is missing. Run npm ci and npm run build in frontend.")
    st.stop()

st.markdown(
    """
    <style>
    header[data-testid="stHeader"] { display: none; }
    .stMainBlockContainer { max-width: none; padding: 0; }
    .stMainBlockContainer iframe { display: block; }
    </style>
    """,
    unsafe_allow_html=True,
)
dashboard = components.declare_component("ema_dashboard", path=str(DIST))
response = st.session_state.get("dashboard_response")
request = dashboard(config=dashboard_config(), response=response, key="ema_dashboard")
if isinstance(request, dict) and request.get("id") and (not response or request["id"] != response["id"]):
    try:
        handler = HANDLERS.get(request.get("path"))
        if handler is None:
            raise ValueError("Unsupported dashboard request.")
        response = {"id": request["id"], "result": handler(request.get("body") or {})}
    except Exception as error:
        response = {"id": request["id"], "error": str(error)}
    st.session_state["dashboard_response"] = response
    st.rerun()
