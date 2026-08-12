#!/usr/bin/env python3
"""Telegram Live Monitor — Unified Entrypoint"""
import os, sys, asyncio, threading, logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

def run_web():
    from web_dashboard import app
    port = int(os.getenv("PORT", "5000"))
    logger.info(f"🌐 Web Dashboard on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("Telegram Live Monitor v5 — Unified Server")
    logger.info("=" * 50)
    
    web_thread = threading.Thread(target=run_web, daemon=True)
    web_thread.start()
    logger.info("✅ Web thread started — starting bot...")
    
    import bot
    try:
        asyncio.run(bot.main())
    except RuntimeError as e:
        logger.error(f"Bot monitoring disabled: {e}")
        logger.info("Dashboard still running at port " + str(os.getenv("PORT", "5000")))
        # Keep main thread alive for Flask daemon
        import time
        while True:
            time.sleep(3600)
