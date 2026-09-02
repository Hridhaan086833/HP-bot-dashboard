import os
import sqlite3
import time
import sys
import tracemalloc
from datetime import datetime, timezone
from aiohttp import web

try:
	import psutil  # type: ignore[import-not-found]
except ImportError:
	psutil = None

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ticket_bot.sqlite3")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

def get_db_connection():
	conn = sqlite3.connect(DB_PATH)
	conn.row_factory = sqlite3.Row
	return conn

def format_bytes(value: float) -> str:
	current = float(value)
	for unit in ("B", "KB", "MB", "GB"):
		if current < 1024 or unit == "GB":
			return f"{current:.2f} {unit}" if unit != "B" else f"{int(current)} {unit}"
		current /= 1024
	return f"{current:.2f} GB"

class DashboardServer:
	def __init__(self, bot=None, host="0.0.0.0", port=8000):
		self.bot = bot
		self.host = host
		self.port = int(os.getenv("DASHBOARD_PORT", str(port)))
		self.app = web.Application()
		self.setup_routes()
		self.runner = None

	def setup_routes(self):
		self.app.router.add_get("/api/stats", self.api_stats)
		self.app.router.add_get("/api/leaderboard", self.api_leaderboard)
		self.app.router.add_get("/api/tickets", self.api_tickets)
		self.app.router.add_get("/api/antinuke", self.api_antinuke)
		self.app.router.add_get("/api/deleted-logs", self.api_deleted_logs)
		self.app.router.add_static("/static", STATIC_DIR, name="static")
		self.app.router.add_get("/", self.index_handler)

	async def index_handler(self, request):
		return web.FileResponse(os.path.join(STATIC_DIR, "index.html"))

	async def api_stats(self, request):
		# Bot health & system info
		ping_ms = round(self.bot.latency * 1000, 2) if self.bot and hasattr(self.bot, "latency") else 0
		uptime_sec = 0
		if self.bot and hasattr(self.bot, "start_time"):
			uptime_sec = int((datetime.now(timezone.utc) - self.bot.start_time).total_seconds())
		elif hasattr(sys, "bot_start_time"):
			uptime_sec = int((datetime.now(timezone.utc) - getattr(sys, "bot_start_time")).total_seconds())

		if psutil:
			process = psutil.Process()
			rss_bytes = process.memory_info().rss
			peak_bytes = tracemalloc.get_traced_memory()[1]
		else:
			rss_bytes, peak_bytes = tracemalloc.get_traced_memory()

		guild_count = len(self.bot.guilds) if self.bot and hasattr(self.bot, "guilds") else 0
		member_count = sum(g.member_count or 0 for g in self.bot.guilds) if self.bot and hasattr(self.bot, "guilds") else 0

		# Database stats
		db_stats = {
			"users_count": 0,
			"total_balance": 0,
			"tickets_count": 0,
			"suggestions_count": 0,
			"giveaways_count": 0,
		}
		if os.path.exists(DB_PATH):
			try:
				conn = get_db_connection()
				cur = conn.cursor()
				cur.execute("SELECT COUNT(*), COALESCE(SUM(balance), 0) FROM users")
				row = cur.fetchone()
				if row:
					db_stats["users_count"] = row[0]
					db_stats["total_balance"] = row[1]
				cur.execute("SELECT COUNT(*) FROM tickets")
				db_stats["tickets_count"] = cur.fetchone()[0]
				cur.execute("SELECT COUNT(*) FROM suggestions")
				db_stats["suggestions_count"] = cur.fetchone()[0]
				cur.execute("SELECT COUNT(*) FROM giveaways WHERE status='active'")
				db_stats["giveaways_count"] = cur.fetchone()[0]
				conn.close()
			except Exception as e:
				print(f"Error reading DB stats: {e}")

		data = {
			"status": "online" if self.bot and self.bot.is_ready() else "standalone / starting",
			"ping_ms": ping_ms,
			"uptime_seconds": uptime_sec,
			"memory_rss": format_bytes(rss_bytes),
			"memory_peak": format_bytes(peak_bytes),
			"guilds": guild_count,
			"members": member_count,
			"db_stats": db_stats,
			"python_version": sys.version.split()[0],
			"timestamp": datetime.now(timezone.utc).isoformat()
		}
		return web.json_response(data)

	async def api_leaderboard(self, request):
		items = []
		if os.path.exists(DB_PATH):
			try:
				conn = get_db_connection()
				cur = conn.cursor()
				cur.execute("SELECT id, balance FROM users ORDER BY balance DESC LIMIT 10")
				for row in cur.fetchall():
					user_id = row["id"]
					balance = row["balance"]
					# try to get display name from bot if available
					user_name = f"User {user_id}"
					if self.bot and hasattr(self.bot, "get_user"):
						u = self.bot.get_user(user_id)
						if u:
							user_name = u.name
					items.append({"id": user_id, "name": user_name, "balance": balance})
				conn.close()
			except Exception as e:
				print(f"Error fetching leaderboard: {e}")
		return web.json_response({"leaderboard": items})

	async def api_tickets(self, request):
		tickets = []
		if os.path.exists(DB_PATH):
			try:
				conn = get_db_connection()
				cur = conn.cursor()
				cur.execute("SELECT guild_id, user_id, category, channel_id, created_at, claimed_by, claimed_at FROM tickets ORDER BY created_at DESC LIMIT 20")
				for row in cur.fetchall():
					user_id = row["user_id"]
					claimed_by = row["claimed_by"]
					user_name = f"User {user_id}"
					claimed_name = f"User {claimed_by}" if claimed_by else "Unclaimed"
					if self.bot and hasattr(self.bot, "get_user"):
						u = self.bot.get_user(user_id)
						if u: user_name = u.name
						if claimed_by:
							c = self.bot.get_user(claimed_by)
							if c: claimed_name = c.name
					tickets.append({
						"guild_id": row["guild_id"],
						"user_id": user_id,
						"user_name": user_name,
						"category": row["category"],
						"channel_id": row["channel_id"],
						"created_at": row["created_at"],
						"claimed_by": claimed_by,
						"claimed_name": claimed_name,
						"claimed_at": row["claimed_at"]
					})
				conn.close()
			except Exception as e:
				print(f"Error fetching tickets: {e}")
		return web.json_response({"tickets": tickets})

	async def api_antinuke(self, request):
		config_list = []
		if os.path.exists(DB_PATH):
			try:
				conn = get_db_connection()
				cur = conn.cursor()
				cur.execute("SELECT guild_id, enabled, lockdown, punishment, time_window FROM antinuke_config")
				for row in cur.fetchall():
					guild_id = row["guild_id"]
					cur2 = conn.cursor()
					cur2.execute("SELECT module, enabled FROM antinuke_modules WHERE guild_id=?", (guild_id,))
					modules = {r["module"]: bool(r["enabled"]) for r in cur2.fetchall()}
					cur2.execute("SELECT module, max_actions FROM antinuke_limits WHERE guild_id=?", (guild_id,))
					limits = {r["module"]: r["max_actions"] for r in cur2.fetchall()}
					config_list.append({
						"guild_id": guild_id,
						"enabled": bool(row["enabled"]),
						"lockdown": bool(row["lockdown"]),
						"punishment": row["punishment"],
						"time_window": row["time_window"],
						"modules": modules,
						"limits": limits
					})
				conn.close()
			except Exception as e:
				print(f"Error fetching antinuke config: {e}")
		return web.json_response({"antinuke": config_list})

	async def api_deleted_logs(self, request):
		logs = []
		if os.path.exists(DB_PATH):
			try:
				conn = get_db_connection()
				cur = conn.cursor()
				cur.execute("SELECT message_id, guild_id, channel_id, author_id, author_name, content, attachments, deleted_at, reason FROM deleted_messages ORDER BY deleted_at DESC LIMIT 20")
				for row in cur.fetchall():
					logs.append({
						"message_id": row["message_id"],
						"guild_id": row["guild_id"],
						"channel_id": row["channel_id"],
						"author_id": row["author_id"],
						"author_name": row["author_name"],
						"content": row["content"],
						"attachments": row["attachments"],
						"deleted_at": row["deleted_at"],
						"reason": row["reason"]
					})
				conn.close()
			except Exception as e:
				print(f"Error fetching deleted logs: {e}")
		return web.json_response({"logs": logs})

	async def start(self):
		os.makedirs(STATIC_DIR, exist_ok=True)
		self.runner = web.AppRunner(self.app)
		await self.runner.setup()
		site = web.TCPSite(self.runner, self.host, self.port)
		await site.start()
		print(f"🌐 3D Design Dashboard running at http://{self.host}:{self.port}")

	async def stop(self):
		if self.runner:
			await self.runner.cleanup()

async def run_standalone_dashboard(port=8000):
	server = DashboardServer(port=port)
	await server.start()
	print(f"Server started on port {port}. Press Ctrl+C to stop.")
	while True:
		await asyncio.sleep(3600)

if __name__ == "__main__":
	import asyncio
	try:
		asyncio.run(run_standalone_dashboard())
	except KeyboardInterrupt:
		pass
