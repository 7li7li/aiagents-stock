import json
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from typing import Dict, Optional

import config
from ai_agents import StockAnalysisAgents
from database import db
from stock_data import StockDataFetcher


class AnalysisJobManager:
    """SQLite-backed background analysis job manager."""

    def __init__(self, db_path="stock_analysis.db"):
        self.db_path = db_path
        self._threads = {}
        self._lock = threading.Lock()
        self.init_database()

    def _connect(self):
        return sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)

    def init_database(self):
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS analysis_jobs (
                job_id TEXT PRIMARY KEY,
                job_type TEXT NOT NULL,
                status TEXT NOT NULL,
                symbol TEXT,
                period TEXT,
                params_json TEXT,
                progress INTEGER DEFAULT 0,
                current_stage TEXT,
                stream_json TEXT,
                result_json TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
        conn.close()

    def create_single_stock_job(self, symbol: str, period: str, enabled_analysts: Dict, selected_model: str) -> str:
        job_id = uuid.uuid4().hex
        now = datetime.now().isoformat()
        params = {
            "enabled_analysts": enabled_analysts,
            "selected_model": selected_model,
        }

        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO analysis_jobs
            (job_id, job_type, status, symbol, period, params_json, progress, current_stage, stream_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                "single_stock",
                "pending",
                symbol,
                period,
                json.dumps(params, ensure_ascii=False, default=str),
                0,
                "等待开始",
                json.dumps({}, ensure_ascii=False),
                now,
                now,
            ),
        )
        conn.commit()
        conn.close()

        thread = threading.Thread(target=self._run_single_stock_job, args=(job_id,), daemon=True)
        with self._lock:
            self._threads[job_id] = thread
        thread.start()
        return job_id

    def get_job(self, job_id: str) -> Optional[Dict]:
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM analysis_jobs WHERE job_id = ?", (job_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None

        return {
            "job_id": row[0],
            "job_type": row[1],
            "status": row[2],
            "symbol": row[3],
            "period": row[4],
            "params": json.loads(row[5]) if row[5] else {},
            "progress": row[6] or 0,
            "current_stage": row[7] or "",
            "stream": json.loads(row[8]) if row[8] else {},
            "result": json.loads(row[9]) if row[9] else None,
            "error": row[10],
            "created_at": row[11],
            "updated_at": row[12],
        }

    def get_latest_active_job(self) -> Optional[Dict]:
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT job_id FROM analysis_jobs
            WHERE status IN ('pending', 'running')
            ORDER BY updated_at DESC
            LIMIT 1
            """
        )
        row = cursor.fetchone()
        conn.close()
        return self.get_job(row[0]) if row else None

    def _update_job(self, job_id: str, **fields):
        if not fields:
            return
        fields["updated_at"] = datetime.now().isoformat()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        values = list(fields.values()) + [job_id]
        conn = self._connect()
        cursor = conn.cursor()
        cursor.execute(f"UPDATE analysis_jobs SET {assignments} WHERE job_id = ?", values)
        conn.commit()
        conn.close()

    def _run_single_stock_job(self, job_id: str):
        stream_sections = {}
        stream_dirty = {"chars": 0, "last_flush": time.time()}

        def flush_stream(force=False):
            if not force and stream_dirty["chars"] < 120 and time.time() - stream_dirty["last_flush"] < 1.0:
                return
            self._update_job(job_id, stream_json=json.dumps(stream_sections, ensure_ascii=False, default=str))
            stream_dirty["chars"] = 0
            stream_dirty["last_flush"] = time.time()

        def append_stream_chunk(label: str, chunk: str):
            stream_sections[label] = stream_sections.get(label, "") + chunk
            if len(stream_sections[label]) > 12000:
                stream_sections[label] = stream_sections[label][-12000:]
            stream_dirty["chars"] += len(chunk)
            flush_stream()

        try:
            job = self.get_job(job_id)
            if not job:
                return
            symbol = job["symbol"]
            period = job["period"]
            params = job["params"]
            enabled_analysts = params.get("enabled_analysts") or {}
            selected_model = params.get("selected_model") or config.DEFAULT_MODEL_NAME

            self._update_job(job_id, status="running", progress=5, current_stage="获取股票数据")

            fetcher = StockDataFetcher()
            stock_info = fetcher.get_stock_info(symbol)
            if "error" in stock_info:
                raise RuntimeError(stock_info["error"])

            stock_data = fetcher.get_stock_data(symbol, period)
            if stock_data is None or (isinstance(stock_data, dict) and "error" in stock_data):
                raise RuntimeError("无法获取股票历史数据")

            stock_data_with_indicators = fetcher.calculate_technical_indicators(stock_data)
            indicators = fetcher.get_latest_indicators(stock_data_with_indicators)

            self._update_job(job_id, progress=20, current_stage="获取财务与补充数据")
            financial_data = fetcher.get_financial_data(symbol)

            quarterly_data = None
            if enabled_analysts.get("fundamental", True) and fetcher._is_chinese_stock(symbol):
                try:
                    from quarterly_report_data import QuarterlyReportDataFetcher

                    quarterly_data = QuarterlyReportDataFetcher().get_quarterly_reports(symbol)
                except Exception:
                    quarterly_data = None

            fund_flow_data = None
            if enabled_analysts.get("fund_flow", True) and fetcher._is_chinese_stock(symbol):
                try:
                    from fund_flow_akshare import FundFlowAkshareDataFetcher

                    fund_flow_data = FundFlowAkshareDataFetcher().get_fund_flow_data(symbol)
                except Exception:
                    fund_flow_data = None

            sentiment_data = None
            if enabled_analysts.get("sentiment", False) and fetcher._is_chinese_stock(symbol):
                try:
                    from market_sentiment_data import MarketSentimentDataFetcher

                    sentiment_data = MarketSentimentDataFetcher().get_market_sentiment_data(symbol, stock_data_with_indicators)
                except Exception:
                    sentiment_data = None

            news_data = None
            if enabled_analysts.get("news", False) and fetcher._is_chinese_stock(symbol):
                try:
                    from qstock_news_data import QStockNewsDataFetcher

                    news_data = QStockNewsDataFetcher().get_stock_news(symbol)
                except Exception:
                    news_data = None

            risk_data = None
            if enabled_analysts.get("risk", True) and fetcher._is_chinese_stock(symbol):
                try:
                    risk_data = fetcher.get_risk_data(symbol)
                except Exception:
                    risk_data = None

            self._update_job(job_id, progress=35, current_stage="AI分析师团队分析")
            agents = StockAnalysisAgents(model=selected_model, stream_callback=append_stream_chunk)
            agents_results = agents.run_multi_agent_analysis(
                stock_info,
                stock_data_with_indicators,
                indicators,
                financial_data,
                fund_flow_data,
                sentiment_data,
                news_data,
                quarterly_data,
                risk_data,
                enabled_analysts=enabled_analysts,
            )
            flush_stream(force=True)

            self._update_job(job_id, progress=75, current_stage="团队讨论")
            discussion_result = agents.conduct_team_discussion(agents_results, stock_info)
            flush_stream(force=True)

            self._update_job(job_id, progress=88, current_stage="最终投资决策")
            final_decision = agents.make_final_decision(discussion_result, stock_info, indicators)
            flush_stream(force=True)

            saved_to_db = False
            db_error = None
            try:
                db.save_analysis(
                    symbol=stock_info.get("symbol", ""),
                    stock_name=stock_info.get("name", ""),
                    period=period,
                    stock_info=stock_info,
                    agents_results=agents_results,
                    discussion_result=discussion_result,
                    final_decision=final_decision,
                )
                saved_to_db = True
            except Exception as exc:
                db_error = str(exc)

            result = {
                "symbol": symbol,
                "success": True,
                "stock_info": stock_info,
                "indicators": indicators,
                "agents_results": agents_results,
                "discussion_result": discussion_result,
                "final_decision": final_decision,
                "saved_to_db": saved_to_db,
                "db_error": db_error,
            }
            self._update_job(
                job_id,
                status="success",
                progress=100,
                current_stage="分析完成",
                result_json=json.dumps(result, ensure_ascii=False, default=str),
                stream_json=json.dumps(stream_sections, ensure_ascii=False, default=str),
            )
        except Exception as exc:
            self._update_job(
                job_id,
                status="failed",
                progress=100,
                current_stage="分析失败",
                error=str(exc),
                stream_json=json.dumps(stream_sections, ensure_ascii=False, default=str),
            )


analysis_job_manager = AnalysisJobManager()
