# AGENTS.md

## Project Shape
- This is a single-root Python Streamlit app, not a packaged library; there is no `pyproject.toml`, task runner, CI workflow, or pytest suite in the repo.
- Main UI entrypoint is `app.py`; `run.py` starts Streamlit on `127.0.0.1:8503` and prints that URL.
- Feature modules are flat root-level files named by domain, for example `longhubang_*`, `sector_strategy_*`, `macro_*`, `news_flow_*`, `portfolio_*`, and `smart_monitor_*`.
- The checked-in `env/`, `.vs/`, `__pycache__/`, and root `*.db` files are local/runtime artifacts; do not inspect or edit them unless the task is explicitly about local state.

## Commands
- Install dependencies with `pip install -r requirements.txt`; Node.js 16+ is also needed because `pywencai` depends on it.
- Local run: `python run.py` or `streamlit run app.py --server.port 8503 --server.address 127.0.0.1`.
- Docker run: copy `.env.example` to `.env`, then `docker-compose up -d`; the compose file maps `8503:8503` and mounts `./data` plus `./.env`.
- Focused TDX verification: `python test_tdx_api.py` after setting `TDX_BASE_URL` and starting the external TDX API service.
- No repo test command is defined; for syntax-only verification use `python -m py_compile <changed .py files>`.

## Configuration Gotchas
- Runtime configuration is `.env` loaded by `config.py` with `load_dotenv(override=True)`, so `.env` overrides existing shell variables for modules that import `config`.
- Required AI config is `DEEPSEEK_API_KEY`; `DEEPSEEK_BASE_URL` and `DEFAULT_MODEL_NAME` support any OpenAI-compatible provider/model.
- Prefer `.env.example` over `env_example.txt`; `env_example.txt` contains older names such as `EMAIL_HOST`, `EMAIL_USER`, and `DINGTALK_WEBHOOK` that current code does not use.
- Current notification env names are `EMAIL_ENABLED`, `SMTP_SERVER`, `SMTP_PORT`, `EMAIL_FROM`, `EMAIL_PASSWORD`, `EMAIL_TO`, `WEBHOOK_ENABLED`, `WEBHOOK_TYPE`, `WEBHOOK_URL`, and `WEBHOOK_KEYWORD`.
- TDX is opt-in via `TDX_ENABLED=true`; `TDX_BASE_URL` defaults differ across files, so check the active `.env` before debugging TDX behavior.
- `app.py` clears HTTP/HTTPS proxy env vars at startup to keep AKShare and related data sources from using system proxies.

## Stock Analysis Convention
- For any feature that analyzes individual stocks, follow `docs/UNIFIED_ANALYSIS_SPEC.md`: call `app.analyze_single_stock_for_batch()` instead of directly orchestrating `StockDataFetcher` plus `StockAnalysisAgents`.
- Use the current final-decision fields: `rating`, `confidence_level`, `entry_range`, `take_profit`, `stop_loss`, `target_price`, and `advice`; avoid older names like `investment_rating`, `confidence`, and `entry_exit_positions`.
- Batch analysis intentionally caps parallel workers at 3 in `app.run_batch_analysis()` to reduce API rate-limit failures.

## Documentation Notes
- `docs/AGENTS.md` is an OpenSpec-managed stub, but `.gitignore` excludes `/openspec` and no `openspec/` directory is present in this checkout.
- The README is long and includes stale Docker snippets; executable sources currently show `Dockerfile` and `docker-compose.yml` use port `8503`.
