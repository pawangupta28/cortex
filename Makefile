.PHONY: install init test eval gate serve mcp
install:  ; pip install -r requirements.txt
init:     ; python -m cortex.cli init --synthetic
test:     ; python -m pytest -q
eval:     ; python -m cortex.cli eval
gate:     ; python -m cortex.cli eval --gate
serve:    ; python -m cortex.cli serve
mcp:      ; python -m cortex.cli mcp
