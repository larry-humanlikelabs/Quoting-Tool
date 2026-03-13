#!/bin/bash
export PATH="/home/appuser/.local/bin:$PATH"
python3 -m streamlit run app.py --server.port=5173 --server.address=0.0.0.0 --server.headless=true
