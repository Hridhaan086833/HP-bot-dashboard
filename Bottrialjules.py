import os
import io
import re
import sqlite3
import ast
import operator
import asyncio
import random
import string
import time
import sys
import tracemalloc
from urllib.parse import urlparse
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

try:
	import psutil  # type: ignore[import-not-found]
except ImportError:
	psutil = None

import discord  # type: ignore[import-not-found]
from discord.ext import commands
from discord import app_commands

try:
	import google.generativeai as genai  # type: ignore[import-not-found]
except ImportError:
	genai = None

try:
	import aiohttp  # type: ignore[import-not-found]
except ImportError:
	aiohttp = None

try:
	from dotenv import load_dotenv  # type: ignore[import-not-found]
except ImportError:
	def load_dotenv(dotenv_path=None, override=False, *args, **kwargs):
		path = dotenv_path or ".env"
		try:
			with open(path, encoding="utf-8") as env_file:
				for raw_line in env_file:
					line = raw_line.strip()
					if not line or line.startswith("#") or "=" not in line:
						continue
					key, value = line.split("=", 1)
					key = key.strip()
					value = value.strip().strip("'\"")
					if key and (override or key not in os.environ):
						os.environ[key] = value
		except OSError:
			return False
		return True

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), override=False)


def env_int(name):
	try:
		return int(os.getenv(name, "0"))
	except ValueError:
		return 0


TOKEN = os.getenv("DISCORD_TOKEN", "")
SUPPORT_ROLE_ID = env_int("SUPPORT_ROLE_ID")
TICKET_CATEGORY_ID = env_int("TICKET_CATEGORY_ID")
SUGGESTION_CHANNEL_ID = env_int("SUGGESTION_CHANNEL_ID")
CONFESSION_REVIEW_CHANNEL_ID = env_int("CONFESSION_REVIEW_CHANNEL_ID")
CONFESSION_CHANNEL_ID = env_int("CONFESSION_CHANNEL_ID")
COUNTING_CHANNEL_ID = env_int("COUNTING_CHANNEL_ID")
MEDIA_CHANNEL_IDS = {env_int(value) for value in os.getenv("MEDIA_CHANNEL_IDS", "").split(",") if value.strip().isdigit()}
SAFE_DOMAINS = {value.strip().lower() for value in os.getenv("SAFE_DOMAINS", "").split(",") if value.strip()}
BLOCKED_DOMAINS = {value.strip().lower() for value in os.getenv("BLOCKED_DOMAINS", "").split(",") if value.strip()}
GOOGLE_SAFE_BROWSING_KEY = os.getenv("GOOGLE_SAFE_BROWSING_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "gemini-3.6-flash")
XP_PER_MESSAGE = max(1, env_int("XP_PER_MESSAGE") or 10)
WELCOME_BANNER_URL = os.getenv("WELCOME_BANNER_URL", "https://images.unsplash.com/photo-1511497584788-876760111969?auto=format&fit=crop&w=1200&q=80")
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ticket_bot.sqlite3")
MEDIA_LINK_HOSTS = {"youtube.com", "youtu.be", "imgur.com", "i.imgur.com", "tenor.com", "media.tenor.com"}
GAME_COMMANDS = {"tic-tac-toe", "rps", "roulette", "trivia", "guess", "hangman", "connect-four", "wordle", "slot", "coinflip", "roll", "blackjack", "unscramble", "emoji-quiz", "truth-or-dare", "high-low", "minefield", "pokemon-guess", "math-race", "explore"}
HANGMAN_WORDS = ["python", "discord", "support", "diamond", "server", "ticket", "portal", "forest", "rocket", "copper"]
WORDLE_WORDS = ["apple", "beach", "crown", "dream", "earth", "flame", "grape", "honey", "ivory", "jelly", "knight", "laser", "mango", "noble", "ocean", "pearl", "queen", "raven", "stone", "torch", "unity", "vivid", "whale", "xenon", "yacht", "zebra"]
ANTINUKE_MODULES = ("channel_delete", "role_delete", "channel_update", "role_update", "guild_update", "member_ban", "member_kick")
ANTI_NUKE_LOCKDOWNS: set[int] = set()

CATEGORIES = {
	"store": ("Store / Purchase Rank", "Minecraft IGN and proof or order links."),
	"minecraft": ("Minecraft Issue / Bug", "Minecraft IGN, server version, and a detailed issue."),
	"technical": ("Technical Support", "A detailed description and any relevant links."),
	"discord": ("Discord Support", "A detailed description and screenshots or links."),
	"report": ("Report Player / Appeal", "Minecraft IGN, reported player, and evidence links."),
	"vip": ("VIP Support", "Minecraft IGN and a detailed VIP-related request."),
}

try:
	from dashboard_server import DashboardServer
except ImportError:
	DashboardServer = None

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)
GIVEAWAY_TASKS: dict[int, asyncio.Task] = {}
BOT_START_TIME = datetime.now(timezone.utc)
bot.start_time = BOT_START_TIME
DASHBOARD_INSTANCE = None
tracemalloc.start()


def format_bytes(value: int | float) -> str:
	current = float(value)
	for unit in ("B", "KB", "MB", "GB"):
		if current < 1024 or unit == "GB":
			return f"{current:.2f} {unit}" if unit != "B" else f"{int(current)} {unit}"
		current /= 1024
	return f"{current:.2f} GB"


def db(query, args=(), fetch=False, many=False):
	with sqlite3.connect(DB) as con:
		cur = con.cursor()
		if many:
			cur.executemany(query, args)
		else:
			cur.execute(query, args)
		rows = cur.fetchall() if fetch else None
		con.commit()
		return rows


def init_db():
	db("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, balance INTEGER NOT NULL DEFAULT 100)")
	db("CREATE TABLE IF NOT EXISTS items (name TEXT PRIMARY KEY, price INTEGER NOT NULL, description TEXT NOT NULL)")
	db("""CREATE TABLE IF NOT EXISTS tickets (
		guild_id INTEGER NOT NULL,
		user_id INTEGER NOT NULL,
		category TEXT NOT NULL,
		channel_id INTEGER NOT NULL,
		created_at TEXT NOT NULL,
		claimed_by INTEGER,
		claimed_at TEXT,
		PRIMARY KEY (guild_id, user_id, category)
	)""")
	try:
		db("ALTER TABLE tickets ADD COLUMN claimed_by INTEGER")
	except sqlite3.OperationalError:
		pass
	try:
		db("ALTER TABLE tickets ADD COLUMN claimed_at TEXT")
	except sqlite3.OperationalError:
		pass
	db("CREATE TABLE IF NOT EXISTS ticket_log_config (guild_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL)")
	db("CREATE TABLE IF NOT EXISTS deletion_log_config (guild_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL)")
	db("CREATE TABLE IF NOT EXISTS deleted_messages (message_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, author_id INTEGER NOT NULL, author_name TEXT NOT NULL, content TEXT NOT NULL, attachments TEXT NOT NULL, deleted_at TEXT NOT NULL, reason TEXT NOT NULL)")
	db("CREATE TABLE IF NOT EXISTS moderation_config (guild_id INTEGER PRIMARY KEY, role_id INTEGER NOT NULL)")
	db("CREATE TABLE IF NOT EXISTS antinuke_config (guild_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0, lockdown INTEGER NOT NULL DEFAULT 0, punishment TEXT NOT NULL DEFAULT 'quarantine', quarantine_role_id INTEGER, log_channel_id INTEGER, time_window INTEGER NOT NULL DEFAULT 10)")
	db("CREATE TABLE IF NOT EXISTS antinuke_modules (guild_id INTEGER NOT NULL, module TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(guild_id, module))")
	db("CREATE TABLE IF NOT EXISTS antinuke_limits (guild_id INTEGER NOT NULL, module TEXT NOT NULL, max_actions INTEGER NOT NULL DEFAULT 3, PRIMARY KEY(guild_id, module))")
	db("CREATE TABLE IF NOT EXISTS antinuke_whitelist (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, PRIMARY KEY(guild_id, user_id))")
	db("CREATE TABLE IF NOT EXISTS antinuke_events (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, module TEXT NOT NULL, created_at TEXT NOT NULL)")
	db("CREATE TABLE IF NOT EXISTS giveaways (message_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, prize TEXT NOT NULL, winners INTEGER NOT NULL, end_at TEXT NOT NULL, created_by INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'active')")
	db("CREATE TABLE IF NOT EXISTS giveaway_entries (message_id INTEGER NOT NULL, user_id INTEGER NOT NULL, PRIMARY KEY(message_id, user_id))")
	db("CREATE TABLE IF NOT EXISTS suggestions (message_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL, author_id INTEGER NOT NULL, content TEXT NOT NULL, upvotes INTEGER NOT NULL DEFAULT 0, downvotes INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending')")
	db("CREATE TABLE IF NOT EXISTS suggestion_votes (message_id INTEGER NOT NULL, user_id INTEGER NOT NULL, vote INTEGER NOT NULL, PRIMARY KEY(message_id, user_id))")
	db("CREATE TABLE IF NOT EXISTS suggestion_config (guild_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL)")
	db("CREATE TABLE IF NOT EXISTS game_config (guild_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL)")
	db("CREATE TABLE IF NOT EXISTS member_xp (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, xp INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(guild_id, user_id))")
	db("CREATE TABLE IF NOT EXISTS counting (guild_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL, last_number INTEGER NOT NULL DEFAULT 0, last_user_id INTEGER)")
	db("CREATE TABLE IF NOT EXISTS confessions (message_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending')")
	db("CREATE TABLE IF NOT EXISTS media_only_channels (guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(guild_id, channel_id))")
	db("CREATE TABLE IF NOT EXISTS media_link_roles (guild_id INTEGER PRIMARY KEY, role_id INTEGER NOT NULL)")
	db("CREATE TABLE IF NOT EXISTS welcome_config (guild_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL, message TEXT NOT NULL DEFAULT 'Welcome {user} to {server}! We hope you enjoy your time here.')")
	if not db("SELECT name FROM items", fetch=True):
		db("INSERT INTO items VALUES (?, ?, ?)", [
			("VIP", 500, "VIP server role"),
			("Custom Role", 1000, "A custom color role"),
			("Mystery Box", 250, "A surprise reward"),
		], many=True)


def balance(user_id):
	db("INSERT OR IGNORE INTO users(id) VALUES (?)", (user_id,))
	return db("SELECT balance FROM users WHERE id=?", (user_id,), True)[0][0] # type: ignore


ANTINUKE_MODULES = ("channel_delete", "role_delete", "channel_update", "role_update", "guild_update", "member_ban", "member_kick")


def ensure_antinuke(guild_id):
	db("INSERT OR IGNORE INTO antinuke_config(guild_id) VALUES (?)", (guild_id,))
	for module in ANTINUKE_MODULES:
		db("INSERT OR IGNORE INTO antinuke_modules(guild_id, module) VALUES (?, ?)", (guild_id, module))
		db("INSERT OR IGNORE INTO antinuke_limits(guild_id, module) VALUES (?, ?)", (guild_id, module))
	db("UPDATE antinuke_limits SET max_actions=1 WHERE guild_id=? AND max_actions=3", (guild_id,))


def get_antinuke_config(guild_id):
	ensure_antinuke(guild_id)
	rows = db("SELECT enabled, lockdown, punishment, quarantine_role_id, log_channel_id, time_window FROM antinuke_config WHERE guild_id=?", (guild_id,), True)
	return rows[0] if rows else (0, 0, "quarantine", None, None, 10)


async def set_antinuke_lockdown(guild: discord.Guild, locked: bool):
	if locked and guild.id in ANTI_NUKE_LOCKDOWNS:
		return
	if locked:
		ANTI_NUKE_LOCKDOWNS.add(guild.id)
	else:
		ANTI_NUKE_LOCKDOWNS.discard(guild.id)

	async def update_channel(channel: discord.TextChannel):
		overwrite = channel.overwrites_for(guild.default_role)
		if locked:
			overwrite.send_messages = False
			overwrite.create_public_threads = False
			overwrite.create_private_threads = False
			overwrite.send_messages_in_threads = False
			overwrite.manage_channels = False
			overwrite.manage_webhooks = False
		else:
			overwrite.send_messages = None
			overwrite.create_public_threads = None
			overwrite.create_private_threads = None
			overwrite.send_messages_in_threads = None
			overwrite.manage_channels = None
			overwrite.manage_webhooks = None
		try:
			await channel.set_permissions(guild.default_role, overwrite=overwrite, reason="Anti-nuke lockdown")
		except discord.DiscordException:
			pass

	await asyncio.gather(*(update_channel(channel) for channel in guild.text_channels))


antinuke = app_commands.Group(name="antinuke", description="Configure anti-nuke protection")
bot.tree.add_command(antinuke)
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
	if isinstance(error, app_commands.errors.MissingPermissions):
		message = "You do not have permission to use this command."
	else:
		cause = getattr(error, "original", error)
		print(f"Application command error: {cause!r}")
		message = "The command could not be completed. Check the bot console for details."
	if interaction.response.is_done():
		await interaction.followup.send(message, ephemeral=True)
	else:
		await interaction.response.send_message(message, ephemeral=True)


async def game_channel_check(interaction: discord.Interaction):
	if interaction.guild is None:
		return True
	row = db("SELECT channel_id FROM game_config WHERE guild_id=?", (interaction.guild.id,), True)
	if not row or row[0][0] == interaction.channel_id:
		return True
	await interaction.response.send_message("Games can only be used in the configured game channel.", ephemeral=True)
	return False


def ticket_name(user: discord.abc.User, category: str):
	username = re.sub(r"[^a-z0-9-]", "-", user.name.lower()).strip("-")[:24] or str(user.id)
	return f"ticket-{username}-{category}"


def format_hangman_state(word: str, guessed: set[str]) -> str:
	return " ".join(letter if letter in guessed else "_" for letter in word)


def format_wordle_feedback(secret: str, guess: str) -> str:
	feedback = []
	for index, letter in enumerate(guess):
		if letter == secret[index]:
			feedback.append("🟩")
		elif letter in secret:
			feedback.append("🟨")
		else:
			feedback.append("⬜")
	return " ".join(feedback)


class TicketModal(discord.ui.Modal):
	def __init__(self, category):
		super().__init__(title=f"{CATEGORIES[category][0]} details", timeout=300)
		self.category = category
		self.minecraft_ign = discord.ui.TextInput(label="Minecraft IGN", placeholder="Your in-game name", required=category in {"store", "minecraft", "report", "vip"}, max_length=32)
		self.details = discord.ui.TextInput(label="Describe the issue", style=discord.TextStyle.paragraph, placeholder=CATEGORIES[category][1], max_length=2000)
		self.links = discord.ui.TextInput(label="Proof or relevant links", required=False, max_length=1000)
		self.add_item(self.minecraft_ign)
		self.add_item(self.details)
		self.add_item(self.links)

	async def on_submit(self, interaction: discord.Interaction):
		await create_ticket(interaction, self.category, self.minecraft_ign.value, self.details.value, self.links.value)

	async def on_error(self, interaction: discord.Interaction, error):
		print(f"Ticket modal error: {error!r}")
		message = "The ticket form could not be submitted. Please try again."
		if interaction.response.is_done():
			await interaction.followup.send(message, ephemeral=True)
		else:
			await interaction.response.send_message(message, ephemeral=True)


async def create_ticket(interaction, category, minecraft_ign, details, links):
	guild = interaction.guild
	if guild is None:
		return await interaction.response.send_message("Tickets can only be opened in a server.", ephemeral=True)
	await interaction.response.defer(ephemeral=True)
	row = db("SELECT channel_id FROM tickets WHERE guild_id=? AND user_id=? AND category=?", (guild.id, interaction.user.id, category), True)
	if row:
		channel = guild.get_channel(row[0][0])
		if channel:
			return await interaction.followup.send(f"You already have {channel.mention} for this category.", ephemeral=True)
		db("DELETE FROM tickets WHERE guild_id=? AND user_id=? AND category=?", (guild.id, interaction.user.id, category))
	overwrites = {
		guild.default_role: discord.PermissionOverwrite(view_channel=False),
		interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
		guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
	}
	if SUPPORT_ROLE_ID and (role := guild.get_role(SUPPORT_ROLE_ID)):
		overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
	category_channel = guild.get_channel(TICKET_CATEGORY_ID) if TICKET_CATEGORY_ID else None
	channel: Optional[discord.TextChannel] = None
	try:
		channel = await guild.create_text_channel(ticket_name(interaction.user, category), category=category_channel if isinstance(category_channel, discord.CategoryChannel) else None, overwrites=overwrites, reason="Ticket created")
		db("INSERT INTO tickets(guild_id, user_id, category, channel_id, created_at) VALUES (?, ?, ?, ?, ?)", (guild.id, interaction.user.id, category, channel.id, datetime.now(timezone.utc).isoformat())) # type: ignore
		embed = discord.Embed(
			title=f"🎫 {CATEGORIES[category][0]}",
			description="A staff member will review this ticket soon. Please keep this thread focused on the issue below.",
			color=discord.Color.blurple(),
		)
		embed.add_field(name="Opened by", value=interaction.user.mention, inline=True)
		embed.add_field(name="Category", value=CATEGORIES[category][0], inline=True)
		embed.add_field(name="Minecraft IGN", value=minecraft_ign or "Not provided", inline=True)
		embed.add_field(name="Details", value=details[:1024] if len(details) > 1024 else details, inline=False)
		if links:
			embed.add_field(name="Proof / Links", value=links[:1024] if len(links) > 1024 else links, inline=False)
		embed.set_footer(text="Use the buttons below to claim or close this ticket once it is resolved.")
		await channel.send(f"{interaction.user.mention} {f'<@&{SUPPORT_ROLE_ID}>' if SUPPORT_ROLE_ID else ''}", embed=embed, view=CloseTicketView()) # type: ignore
		await interaction.followup.send(embed=discord.Embed(title="✅ Ticket created", description=f"Your ticket is ready in {channel.mention}.", color=discord.Color.green()), ephemeral=True) # type: ignore
	except (discord.HTTPException, sqlite3.Error):
		if channel:
			await channel.delete(reason="Ticket setup failed")
		await interaction.followup.send("I could not create that ticket. Check my channel permissions and try again.", ephemeral=True)

async def log_ticket(channel: discord.TextChannel, closed_by: discord.abc.User):
	if channel.guild is None:
		return
	log_row = db("SELECT channel_id FROM ticket_log_config WHERE guild_id=?", (channel.guild.id,), True)
	ticket_row = db("SELECT user_id, category, created_at, claimed_by, claimed_at FROM tickets WHERE channel_id=?", (channel.id,), True)
	if not log_row or not ticket_row:
		return
	log_channel = channel.guild.get_channel(log_row[0][0])
	if not isinstance(log_channel, discord.TextChannel):
		return
	creator_id, category, created_at, claimed_by, claimed_at = ticket_row[0]
	creator = channel.guild.get_member(creator_id)
	creator_display = creator.mention if creator else f"<@{creator_id}>"
	claimant = channel.guild.get_member(claimed_by) if claimed_by else None
	claimant_display = claimant.mention if claimant else (f"<@{claimed_by}>" if claimed_by else "Not claimed")
	transcript_header = [
		"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
		f"📌 Ticket transcript: #{channel.name}",
		f"👤 Created by: {creator_display} ({creator_id})",
		f"🎫 Category: {category}",
		f"🔒 Closed by: {closed_by} ({closed_by.id})",
		f"⏰ Closed at: {datetime.now(timezone.utc).isoformat()}",
		"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
		"",
	]
	lines = transcript_header.copy()
	try:
		first_message = None
		async for message in channel.history(limit=None, oldest_first=True):
			if first_message is None:
				first_message = message
			content = message.content or "[no text]"
			attachments = " ".join(attachment.url for attachment in message.attachments)
			if attachments:
				content = f"{content}\n📎 Attachments: {attachments}"
			lines.append(f"🕒 [{message.created_at.isoformat()}] 👤 {message.author} ({message.author.id}): {content}")
		transcript = "\n".join(lines)
		if len(transcript.encode("utf-8")) > 7_000_000:
			transcript = transcript.encode("utf-8")[:7_000_000].decode("utf-8", errors="ignore") + "\n[Transcript truncated]"
		file = discord.File(io.BytesIO(transcript.encode("utf-8")), filename=f"{channel.name}-transcript.txt")
		details = "Not provided"
		minecraft_ign = "Not provided"
		if first_message and first_message.embeds:
			for field in first_message.embeds[0].fields:
				if field.name == "Details":
					details = field.value
				elif field.name == "Minecraft IGN":
					minecraft_ign = field.value
		embed = discord.Embed(title="🧾 Ticket closed", description=f"Ticket **#{channel.name}** has been archived and logged for review.", color=discord.Color.red())
		embed.add_field(name="👤 Created by", value=f"{creator_display}\nID: `{creator_id}`", inline=True)
		embed.add_field(name="🎫 Category", value=category.replace("_", " ").title(), inline=True)
		embed.add_field(name="🧩 Minecraft IGN", value=minecraft_ign, inline=True)
		embed.add_field(name="📝 Reason / Details", value=details[:1024], inline=False) # type: ignore
		embed.add_field(name="📎 Claimed by", value=claimant_display, inline=True)
		embed.add_field(name="⏱️ Claimed at", value=claimed_at or "Not claimed", inline=True)
		embed.add_field(name="🔒 Closed by", value=f"{closed_by.mention}\nID: `{closed_by.id}`", inline=True)
		embed.add_field(name="🗓️ Created at", value=created_at, inline=True)
		embed.add_field(name="🕰️ Closed at", value=datetime.now(timezone.utc).isoformat(), inline=True)
		embed.set_footer(text="📄 Full ticket conversation attached as a transcript")
		await log_channel.send(embed=embed, file=file)
	except (discord.HTTPException, discord.Forbidden) as error:
		print(f"Ticket log failed for {channel.name}: {error!r}")


class TicketView(discord.ui.View):
	def __init__(self):
		super().__init__(timeout=None)

	@discord.ui.select(placeholder="Choose a support category...", custom_id="ticket:category", options=[discord.SelectOption(label=label, value=value, description=description) for value, (label, description) in CATEGORIES.items()])
	async def select_category(self, interaction: discord.Interaction, select: discord.ui.Select):
		await interaction.response.send_modal(TicketModal(select.values[0]))

	@discord.ui.button(label="Open Store Ticket", emoji="🛒", style=discord.ButtonStyle.green, custom_id="ticket:store")
	async def store_ticket(self, interaction, button):
		await interaction.response.send_modal(TicketModal("store"))

	@discord.ui.button(label="Open Support Ticket", emoji="🎫", style=discord.ButtonStyle.blurple, custom_id="ticket:support")
	async def support_ticket(self, interaction, button):
		await interaction.response.send_message(view=CategoryButtonView(), ephemeral=True)


class CategoryButtonView(discord.ui.View):
	def __init__(self):
		super().__init__(timeout=120)
		for key, (label, _) in CATEGORIES.items():
			button = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary, custom_id=f"ticket:category:{key}")
			button.callback = self.make_callback(key)
			self.add_item(button)

	def make_callback(self, category):
		async def callback(interaction):
			await interaction.response.send_modal(TicketModal(category))
		return callback


class SuggestionView(discord.ui.View):
	def __init__(self):
		super().__init__(timeout=None)

	async def vote(self, interaction, value):
		row = db("SELECT upvotes, downvotes FROM suggestions WHERE message_id=?", (interaction.message.id,), True)
		if not row:
			return await interaction.response.send_message("Suggestion not found.", ephemeral=True)
		old = db("SELECT vote FROM suggestion_votes WHERE message_id=? AND user_id=?", (interaction.message.id, interaction.user.id), True)
		if old and old[0][0] == value:
			return await interaction.response.send_message("You already selected that vote.", ephemeral=True)
		if old:
			db("UPDATE suggestion_votes SET vote=? WHERE message_id=? AND user_id=?", (value, interaction.message.id, interaction.user.id))
			db("UPDATE suggestions SET upvotes=upvotes+?, downvotes=downvotes+? WHERE message_id=?", (-1 if value == -1 else 1, 1 if value == -1 else -1, interaction.message.id))
		else:
			db("INSERT INTO suggestion_votes VALUES (?, ?, ?)", (interaction.message.id, interaction.user.id, value))
			db("UPDATE suggestions SET upvotes=upvotes+?, downvotes=downvotes+? WHERE message_id=?", (1 if value == 1 else 0, 1 if value == -1 else 0, interaction.message.id))
		counts = db("SELECT upvotes, downvotes FROM suggestions WHERE message_id=?", (interaction.message.id,), True)[0] # type: ignore
		embed = interaction.message.embeds[0]
		if embed.fields:
			embed.set_field_at(0, name="Community interest", value=f"👍 {counts[0]} | 👎 {counts[1]}", inline=False)
		else:
			embed.add_field(name="Community interest", value=f"👍 {counts[0]} | 👎 {counts[1]}", inline=False)
		await interaction.message.edit(embed=embed)
		await interaction.response.send_message("Vote recorded.", ephemeral=True)

	@discord.ui.button(label="Upvote", emoji="👍", style=discord.ButtonStyle.green, custom_id="suggestion:up")
	async def upvote(self, interaction, button):
		await self.vote(interaction, 1)

	@discord.ui.button(label="Downvote", emoji="👎", style=discord.ButtonStyle.red, custom_id="suggestion:down")
	async def downvote(self, interaction, button):
		await self.vote(interaction, -1)

	async def moderate(self, interaction, approved):
		if not interaction.user.guild_permissions.manage_guild:
			return await interaction.response.send_message("Staff only.", ephemeral=True)
		row = db("SELECT author_id FROM suggestions WHERE message_id=? AND status='pending'", (interaction.message.id,), True)
		if not row:
			return await interaction.response.send_message("This suggestion was already reviewed.", ephemeral=True)
		status = "approved" if approved else "rejected"
		db("UPDATE suggestions SET status=? WHERE message_id=?", (status, interaction.message.id))
		embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed(title="Community suggestion")
		embed.color = discord.Color.green() if approved else discord.Color.red()
		embed.set_footer(text=f"Suggestion {status} by {interaction.user.display_name}")
		await interaction.message.edit(embed=embed, view=None)
		user = interaction.guild.get_member(row[0][0])
		if user:
			try:
				await user.send(f"Your suggestion in **{interaction.guild.name}** was {status}.")
			except discord.HTTPException:
				pass
		await interaction.response.send_message(f"Suggestion {status}.", ephemeral=True)

	@discord.ui.button(label="Approve", style=discord.ButtonStyle.secondary, custom_id="suggestion:approve", row=1)
	async def approve(self, interaction, button):
		await self.moderate(interaction, True)

	@discord.ui.button(label="Reject", style=discord.ButtonStyle.secondary, custom_id="suggestion:reject", row=1)
	async def reject(self, interaction, button):
		await self.moderate(interaction, False)


class ConfessionModal(discord.ui.Modal, title="Anonymous confession"):
	content = discord.ui.TextInput(label="Confession", style=discord.TextStyle.paragraph, max_length=2000)

	async def on_submit(self, interaction):
		if interaction.guild is None:
			return await interaction.response.send_message("Confessions can only be submitted in a server.", ephemeral=True)
		channel = interaction.guild.get_channel(CONFESSION_REVIEW_CHANNEL_ID)
		if not channel:
			return await interaction.response.send_message("Confession review is not configured.", ephemeral=True)
		message = await channel.send(embed=discord.Embed(title="Confession pending review", description=self.content.value, color=discord.Color.orange()), view=ConfessionView())
		db("INSERT INTO confessions VALUES (?, ?, ?, 'pending')", (message.id, interaction.guild.id, self.content.value))
		await interaction.response.send_message("Your confession was sent to staff for review.", ephemeral=True)


class ConfessionView(discord.ui.View):
	def __init__(self):
		super().__init__(timeout=None)

	async def review(self, interaction, approved):
		if not interaction.user.guild_permissions.manage_guild:
			return await interaction.response.send_message("Staff only.", ephemeral=True)
		row = db("SELECT content FROM confessions WHERE message_id=? AND status='pending'", (interaction.message.id,), True)
		if not row:
			return await interaction.response.send_message("This confession was already reviewed.", ephemeral=True)
		channel = interaction.guild.get_channel(CONFESSION_CHANNEL_ID) if approved and interaction.guild else None
		if approved and not channel:
			return await interaction.response.send_message("The public confessions channel is not configured.", ephemeral=True)
		status = "approved" if approved else "rejected"
		db("UPDATE confessions SET status=? WHERE message_id=?", (status, interaction.message.id))
		if approved:
			assert channel is not None
			await channel.send(embed=discord.Embed(title="Anonymous confession", description=row[0][0], color=discord.Color.blurple()))
		await interaction.message.edit(view=None)
		await interaction.response.send_message(f"Confession {status}.", ephemeral=True)

	@discord.ui.button(label="Approve", style=discord.ButtonStyle.green, custom_id="confession:approve")
	async def approve(self, interaction, button):
		await self.review(interaction, True)

	@discord.ui.button(label="Reject", style=discord.ButtonStyle.red, custom_id="confession:reject")
	async def reject(self, interaction, button):
		await self.review(interaction, False)


class RoleView(discord.ui.View):
	def __init__(self):
		super().__init__(timeout=None)
		options = []
		for item in os.getenv("REACTION_ROLES", "").split(","):
			if ":" in item:
				label, role_id = item.split(":", 1)
				options.append(discord.SelectOption(label=label.strip(), value=role_id.strip()))
		if options:
			select = discord.ui.Select(placeholder="Choose a role...", options=options[:25], custom_id="roles:select")
			select.callback = self.role_callback
			self.add_item(select)

	async def role_callback(self, interaction):
		if interaction.guild is None:
			return await interaction.response.send_message("Roles can only be changed in a server.", ephemeral=True)
		select = interaction.data.get("values", [""])[0]
		try:
			role_id = int(select)
		except (TypeError, ValueError):
			return await interaction.response.send_message("That role selection is invalid.", ephemeral=True)
		role = interaction.guild.get_role(role_id)
		if not role or role.is_default() or interaction.guild.me is None or role >= interaction.guild.me.top_role:
			return await interaction.response.send_message("That role is unavailable.", ephemeral=True)
		if role in interaction.user.roles:
			await interaction.user.remove_roles(role)
			message = f"Removed {role.name}."
		else:
			await interaction.user.add_roles(role)
			message = f"Added {role.name}."
		await interaction.response.send_message(message, ephemeral=True)


class ModerationRoleView(discord.ui.View):
	def __init__(self, role_id: int):
		super().__init__(timeout=None)
		self.role_id = role_id
		self.selected_member_id: Optional[int] = None
		select = discord.ui.UserSelect(placeholder="Select a member to manage...", custom_id=f"moderation:member:{role_id}", min_values=1, max_values=1)
		select.callback = self.select_member
		self.add_item(select)

	async def interaction_check(self, interaction: discord.Interaction):
		if interaction.guild is None or not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.administrator:
			await interaction.response.send_message("Only administrators can manage this moderation role.", ephemeral=True)
			return False
		return True

	async def select_member(self, interaction: discord.Interaction):
		values = interaction.data.get("values", []) if interaction.data else []
		self.selected_member_id = int(values[0]) if values else None
		guild = interaction.guild
		member = guild.get_member(self.selected_member_id) if guild and self.selected_member_id else None
		if member is None:
			return await interaction.response.send_message("That member is unavailable.", ephemeral=True)
		await interaction.response.send_message(f"Selected {member.mention}. Choose Grant or Revoke.", ephemeral=True)

	async def update_role(self, interaction: discord.Interaction, add: bool):
		if self.selected_member_id is None or interaction.guild is None:
			return await interaction.response.send_message("Select a member first.", ephemeral=True)
		member = interaction.guild.get_member(self.selected_member_id)
		role = interaction.guild.get_role(self.role_id)
		if member is None or role is None:
			return await interaction.response.send_message("The member or moderation role is unavailable.", ephemeral=True)
		bot_member = interaction.guild.me
		if bot_member is None or member == bot_member or role >= bot_member.top_role:
			return await interaction.response.send_message("That moderation role cannot be managed.", ephemeral=True)
		try:
			if add:
				await member.add_roles(role, reason=f"Moderation role granted by {interaction.user}")
				message = f"Granted {role.mention} to {member.mention}."
			else:
				await member.remove_roles(role, reason=f"Moderation role revoked by {interaction.user}")
				message = f"Revoked {role.mention} from {member.mention}."
		except discord.HTTPException:
			return await interaction.response.send_message("I could not update that member's role.", ephemeral=True)
		await interaction.response.send_message(message, ephemeral=True)

	@discord.ui.button(label="Grant", style=discord.ButtonStyle.green, custom_id="moderation:grant")
	async def grant(self, interaction: discord.Interaction, button: discord.ui.Button):
		await self.update_role(interaction, True)

	@discord.ui.button(label="Revoke", style=discord.ButtonStyle.red, custom_id="moderation:revoke")
	async def revoke(self, interaction: discord.Interaction, button: discord.ui.Button):
		await self.update_role(interaction, False)


class CloseTicketView(discord.ui.View):
	def __init__(self):
		super().__init__(timeout=None)

	@discord.ui.button(label="Claim Ticket", emoji="🙋", style=discord.ButtonStyle.blurple, custom_id="ticket:claim")
	async def claim_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
		if interaction.guild is None or not isinstance(interaction.user, discord.Member):
			return await interaction.response.send_message("Only server staff can claim tickets.", ephemeral=True)
		support_role = interaction.guild.get_role(SUPPORT_ROLE_ID) if SUPPORT_ROLE_ID else None
		is_staff = interaction.user.guild_permissions.manage_guild or (support_role is not None and support_role in interaction.user.roles)
		if not is_staff:
			return await interaction.response.send_message("Only ticket staff can claim this ticket.", ephemeral=True)
		channel = interaction.channel
		if not isinstance(channel, discord.TextChannel) or not channel.name.startswith("ticket-"):
			return await interaction.response.send_message("This is not a ticket channel.", ephemeral=True)
		claimed_at = datetime.now(timezone.utc).isoformat()
		updated = db("UPDATE tickets SET claimed_by=?, claimed_at=? WHERE channel_id=? AND claimed_by IS NULL RETURNING claimed_by", (interaction.user.id, claimed_at, channel.id), fetch=True)
		if not updated:
			row = db("SELECT claimed_by FROM tickets WHERE channel_id=?", (channel.id,), True)
			claimant = interaction.guild.get_member(row[0][0]) if row and row[0][0] else None
			name = claimant.mention if claimant else "another staff member"
			return await interaction.response.send_message(f"This ticket has already been claimed by {name}.", ephemeral=True)
		embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed(title="Ticket") # type: ignore
		embed.add_field(name="Claimed by", value=f"{interaction.user.mention}\n<t:{int(datetime.fromisoformat(claimed_at).timestamp())}:R>", inline=True)
		await interaction.message.edit(embed=embed, view=self) # type: ignore
		await interaction.response.send_message(f"{interaction.user.mention} claimed this ticket and will handle the response.")

	@discord.ui.button(label="Close Ticket", emoji="🔒", style=discord.ButtonStyle.red, custom_id="ticket:close")
	async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
		channel = interaction.channel
		if not isinstance(channel, discord.TextChannel) or not channel.name.startswith("ticket-"):
			return await interaction.response.send_message("This is not a ticket channel.", ephemeral=True)
		await log_ticket(channel, interaction.user)
		db("DELETE FROM tickets WHERE channel_id=?", (channel.id,))
		await interaction.response.send_message("Closing ticket in 5 seconds...")
		await asyncio.sleep(5)
		await channel.delete(reason=f"Closed by {interaction.user}")


class ShopView(discord.ui.View):
	@discord.ui.button(label="Shop", emoji="🛒", style=discord.ButtonStyle.blurple)
	async def shop(self, interaction: discord.Interaction, button: discord.ui.Button):
		rows = db("SELECT name, price, description FROM items", fetch=True)
		embed = discord.Embed(title="🛒 Shop", color=discord.Color.blurple())
		embed.description = "\n".join(f"**{n}** — {p} coins\n{d}\n`!buy {n}`" for n, p, d in rows) # type: ignore
		await interaction.response.send_message(embed=embed, ephemeral=True)


class GiveawayView(discord.ui.View):
	def __init__(self):
		super().__init__(timeout=None)

	@discord.ui.button(label="Enter Giveaway", emoji="🎉", style=discord.ButtonStyle.green, custom_id="giveaway:enter")
	async def enter(self, interaction: discord.Interaction, button: discord.ui.Button):
		if interaction.guild is None or interaction.message is None:
			return await interaction.response.send_message("Giveaways can only be entered in a server.", ephemeral=True)
		row = db("SELECT status, end_at FROM giveaways WHERE message_id=?", (interaction.message.id,), True)
		if not row or row[0][0] != "active":
			return await interaction.response.send_message("This giveaway has ended.", ephemeral=True)
		if datetime.fromisoformat(row[0][1]) <= datetime.now(timezone.utc):
			await finish_giveaway(interaction.message.id)
			return await interaction.response.send_message("This giveaway has ended.", ephemeral=True)
		added = db("INSERT OR IGNORE INTO giveaway_entries(message_id, user_id) VALUES (?, ?) RETURNING user_id", (interaction.message.id, interaction.user.id), fetch=True)
		if not added:
			return await interaction.response.send_message("You are already entered in this giveaway.", ephemeral=True)
		count_row = db("SELECT COUNT(*) FROM giveaway_entries WHERE message_id=?", (interaction.message.id,), True)
		count = count_row[0][0] if count_row else 0
		embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed(title="Giveaway")
		for field in embed.fields:
			if field.name == "Entries":
				embed.set_field_at(embed.fields.index(field), name="Entries", value=f"{count:,}", inline=True)
				break
		await interaction.message.edit(embed=embed)
		await interaction.response.send_message("You are entered. Good luck!", ephemeral=True)


async def finish_giveaway(message_id):
	row = db("SELECT guild_id, channel_id, prize, winners, created_by, status FROM giveaways WHERE message_id=?", (message_id,), True)
	if not row or row[0][5] != "active":
		return
	guild_id, channel_id, prize, winner_count, created_by, _ = row[0]
	entries = db("SELECT user_id FROM giveaway_entries WHERE message_id=?", (message_id,), True) or []
	winner_ids = [entry[0] for entry in random.sample(entries, min(winner_count, len(entries)))] if entries else []
	db("UPDATE giveaways SET status='ended' WHERE message_id=?", (message_id,))
	guild = bot.get_guild(guild_id)
	channel = guild.get_channel(channel_id) if guild else None
	if not isinstance(channel, discord.TextChannel):
		return
	try:
		message = await channel.fetch_message(message_id)
		mentions = ", ".join(f"<@{user_id}>" for user_id in winner_ids) or "No valid entries"
		creator = guild.get_member(created_by) if guild else None
		creator_display = creator.mention if creator else f"<@{created_by}>"
		embed = discord.Embed(
			title="🎉 Giveaway Complete",
			description=f"## {prize}\n\n{'Congratulations to the winner(s)!' if winner_ids else 'There were no eligible entries.'}",
			color=discord.Color.green() if winner_ids else discord.Color.dark_grey(),
			timestamp=datetime.now(timezone.utc),
		)
		embed.add_field(name="Winner(s)", value=mentions, inline=False)
		embed.add_field(name="Prize", value=prize, inline=True)
		embed.add_field(name="Total entries", value=f"{len(entries):,}", inline=True)
		embed.add_field(name="Created by", value=creator_display, inline=True)
		embed.set_footer(text="Giveaway ended")
		await message.edit(embed=embed, view=None)
		await channel.send(f"🎉 **Giveaway complete!** {mentions} won **{prize}**.")
	except (discord.HTTPException, discord.NotFound):
		pass


def schedule_giveaway(message_id, end_at):
	if message_id in GIVEAWAY_TASKS and not GIVEAWAY_TASKS[message_id].done():
		return
	delay = max(0, (datetime.fromisoformat(end_at) - datetime.now(timezone.utc)).total_seconds())
	GIVEAWAY_TASKS[message_id] = asyncio.create_task(asyncio.sleep(delay))
	GIVEAWAY_TASKS[message_id].add_done_callback(lambda _: asyncio.create_task(finish_giveaway(message_id)))


@bot.event
async def on_ready():
	global DASHBOARD_INSTANCE
	init_db()
	if DASHBOARD_INSTANCE is None and DashboardServer is not None and os.getenv("DASHBOARD_ENABLED", "1").lower() in ("1", "true", "yes"):
		try:
			DASHBOARD_INSTANCE = DashboardServer(bot=bot)
			asyncio.create_task(DASHBOARD_INSTANCE.start())
		except Exception as err:
			print(f"Failed to start dashboard server: {err!r}")
	if not getattr(bot, "_persistent_views_added", False):
		bot.add_view(TicketView())
		bot.add_view(CloseTicketView())
		bot.add_view(SuggestionView())
		bot.add_view(ConfessionView())
		bot.add_view(GiveawayView())
		for role_id, in (db("SELECT role_id FROM moderation_config", fetch=True) or []):
			bot.add_view(ModerationRoleView(role_id))
		if os.getenv("REACTION_ROLES"):
			bot.add_view(RoleView())
		bot._persistent_views_added = True # type: ignore
	if not getattr(bot, "_commands_synced", False):
		for guild in bot.guilds:
			bot.tree.clear_commands(guild=guild)
			await bot.tree.sync(guild=guild)
		await bot.tree.sync()
		bot._commands_synced = True # type: ignore
	for message_id, end_at in (db("SELECT message_id, end_at FROM giveaways WHERE status='active'", fetch=True) or []):
		schedule_giveaway(message_id, end_at)
	print(f"Logged in as {bot.user}")


@bot.tree.command(name="setup-ticket", description="Post the support ticket panel")
@app_commands.checks.has_permissions(manage_guild=True)
async def setup_ticket(interaction: discord.Interaction):
	embed = discord.Embed(
		title="🎫 Support Tickets",
		description="Choose a category below. A short form will collect the details before your private ticket opens.",
		color=discord.Color.green(),
	)
	await interaction.response.send_message(embed=embed, view=TicketView())


@setup_ticket.error
async def setup_ticket_error(interaction: discord.Interaction, error):
	message = "You do not have permission to use this command." if isinstance(error, app_commands.errors.MissingPermissions) else "The ticket panel could not be posted. Check the bot console."
	if interaction.response.is_done():
		await interaction.followup.send(message, ephemeral=True)
	else:
		await interaction.response.send_message(message, ephemeral=True)


@bot.tree.command(name="setup-ticket-log", description="Set the channel where closed ticket transcripts are stored")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(channel="Text channel for closed ticket transcripts")
async def setup_ticket_log(interaction: discord.Interaction, channel: discord.TextChannel):
	if interaction.guild is None:
		return await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
	bot_member = interaction.guild.me
	if bot_member is None:
		return await interaction.response.send_message("I could not verify my permissions in that channel.", ephemeral=True)
	permissions = channel.permissions_for(bot_member)
	if not permissions.send_messages or not permissions.embed_links or not permissions.attach_files:
		return await interaction.response.send_message("I need Send Messages, Embed Links, and Attach Files permission in that channel.", ephemeral=True)
	db("INSERT INTO ticket_log_config(guild_id, channel_id) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id", (interaction.guild.id, channel.id))
	await interaction.response.send_message(f"Closed ticket transcripts will now be stored in {channel.mention}.", ephemeral=True)


@bot.tree.command(name="setup-deletion-logs", description="Set the channel where deleted messages are archived")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(channel="Text channel for deleted-message logs")
async def setup_deletion_logs(interaction: discord.Interaction, channel: discord.TextChannel):
	if interaction.guild is None or interaction.guild.me is None:
		return await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
	permissions = channel.permissions_for(interaction.guild.me)
	if not permissions.send_messages or not permissions.embed_links:
		return await interaction.response.send_message("I need Send Messages and Embed Links permission in that channel.", ephemeral=True)
	db("INSERT INTO deletion_log_config(guild_id, channel_id) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id", (interaction.guild.id, channel.id))
	await interaction.response.send_message(f"Deleted-message logs will now be sent to {channel.mention}.", ephemeral=True)


@bot.tree.command(name="deleted-logs", description="View recently deleted messages")
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.describe(limit="Number of recent deleted messages to display")
async def deleted_logs(interaction: discord.Interaction, limit: app_commands.Range[int, 1, 10] = 10):
	if interaction.guild is None:
		return await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
	rows = db("SELECT channel_id, author_id, author_name, content, attachments, deleted_at, reason FROM deleted_messages WHERE guild_id=? ORDER BY deleted_at DESC LIMIT ?", (interaction.guild.id, limit), True) or []
	if not rows:
		return await interaction.response.send_message("No deleted-message logs are available.", ephemeral=True)
	embed = discord.Embed(title="🗑️ Deleted Message Logs", description=f"Showing the {len(rows)} most recent deleted messages.", color=discord.Color.red())
	for index, (channel_id, author_id, author_name, content, attachments, deleted_at, reason) in enumerate(rows, 1):
		channel = interaction.guild.get_channel(channel_id)
		location = channel.mention if channel else f"<#{channel_id}>"
		value = f"**Author:** {author_name} (<@{author_id}>)\n**Channel:** {location}\n**Reason:** {reason}\n**Deleted:** {deleted_at}\n**Content:** {(content or '[no text]')[:700]}"
		if attachments != "None":
			value += f"\n**Attachments:** {attachments[:300]}"
		embed.add_field(name=f"{index}. Message `{author_id}`", value=value[:1024], inline=False)
	await interaction.response.send_message(embed=embed, ephemeral=True)


@antinuke.command(name="enable", description="Enable anti-nuke protection")
@app_commands.checks.has_permissions(administrator=True)
async def antinuke_enable(interaction: discord.Interaction):
	ensure_antinuke(interaction.guild.id) # type: ignore
	db("UPDATE antinuke_config SET enabled=1 WHERE guild_id=?", (interaction.guild.id,)) # type: ignore
	await interaction.response.send_message("Anti-nuke protection enabled.", ephemeral=True)


@antinuke.command(name="disable", description="Disable anti-nuke protection")
@app_commands.checks.has_permissions(administrator=True)
async def antinuke_disable(interaction: discord.Interaction):
	ensure_antinuke(interaction.guild.id) # type: ignore
	db("UPDATE antinuke_config SET enabled=0 WHERE guild_id=?", (interaction.guild.id,)) # type: ignore
	await interaction.response.send_message("Anti-nuke protection disabled.", ephemeral=True)


@antinuke.command(name="guard", description="Enable or disable a protection module")
@app_commands.checks.has_permissions(administrator=True)
async def antinuke_guard(interaction: discord.Interaction, module: Literal["channel_delete", "role_delete", "channel_update", "role_update", "guild_update", "member_ban", "member_kick"], status: Literal["enable", "disable"]):
	ensure_antinuke(interaction.guild.id) # type: ignore
	db("UPDATE antinuke_modules SET enabled=? WHERE guild_id=? AND module=?", (status == "enable", interaction.guild.id, module)) # type: ignore
	await interaction.response.send_message(f"Guard `{module}` {status}d.", ephemeral=True)


@antinuke.command(name="limits", description="Set a module action limit")
@app_commands.checks.has_permissions(administrator=True)
async def antinuke_limits(interaction: discord.Interaction, module: Literal["channel_delete", "role_delete", "channel_update", "role_update", "guild_update", "member_ban", "member_kick"], limit: app_commands.Range[int, 1, 100]):
	ensure_antinuke(interaction.guild.id) # type: ignore
	db("UPDATE antinuke_limits SET max_actions=? WHERE guild_id=? AND module=?", (limit, interaction.guild.id, module)) # type: ignore
	await interaction.response.send_message(f"Limit for `{module}` set to {limit} actions.", ephemeral=True)


@antinuke.command(name="lockdown", description="Enable or disable lockdown mode")
@app_commands.checks.has_permissions(administrator=True)
async def antinuke_lockdown(interaction: discord.Interaction, status: Literal["enable", "disable"]):
	await interaction.response.defer(ephemeral=True)
	ensure_antinuke(interaction.guild.id) # type: ignore
	db("UPDATE antinuke_config SET lockdown=? WHERE guild_id=?", (status == "enable", interaction.guild.id)) # type: ignore
	await set_antinuke_lockdown(interaction.guild, status == "enable") # type: ignore
	await interaction.followup.send(f"Anti-nuke lockdown {status}d.", ephemeral=True)


@antinuke.command(name="punishment", description="Choose the response to a detected attack")
@app_commands.checks.has_permissions(administrator=True)
async def antinuke_punishment(interaction: discord.Interaction, action: Literal["ban", "kick", "quarantine", "none"]):
	ensure_antinuke(interaction.guild.id) # type: ignore
	db("UPDATE antinuke_config SET punishment=? WHERE guild_id=?", (action, interaction.guild.id)) # type: ignore
	await interaction.response.send_message(f"Anti-nuke punishment set to `{action}`.", ephemeral=True)


@antinuke.command(name="quarantinerole", description="Set the quarantine role")
@app_commands.checks.has_permissions(administrator=True)
async def antinuke_quarantine_role(interaction: discord.Interaction, role: discord.Role):
	if interaction.guild is None or interaction.guild.me is None or role >= interaction.guild.me.top_role:
		return await interaction.response.send_message("That role cannot be managed by the bot.", ephemeral=True)
	ensure_antinuke(interaction.guild.id)
	db("UPDATE antinuke_config SET quarantine_role_id=? WHERE guild_id=?", (role.id, interaction.guild.id))
	await interaction.response.send_message(f"Quarantine role set to {role.mention}.", ephemeral=True)


@antinuke.command(name="recover", description="Clear events and disable lockdown")
@app_commands.checks.has_permissions(administrator=True)
async def antinuke_recover(interaction: discord.Interaction):
	await interaction.response.defer(ephemeral=True)
	ensure_antinuke(interaction.guild.id) # type: ignore
	db("UPDATE antinuke_config SET lockdown=0 WHERE guild_id=?", (interaction.guild.id,)) # type: ignore
	db("DELETE FROM antinuke_events WHERE guild_id=?", (interaction.guild.id,)) # type: ignore
	await set_antinuke_lockdown(interaction.guild, False) # type: ignore
	await interaction.followup.send("Anti-nuke recovery complete; lockdown disabled.", ephemeral=True)


@antinuke.command(name="reset", description="Reset anti-nuke settings")
@app_commands.checks.has_permissions(administrator=True)
async def antinuke_reset(interaction: discord.Interaction):
	ensure_antinuke(interaction.guild.id) # type: ignore
	db("UPDATE antinuke_config SET enabled=0, lockdown=0, punishment='quarantine', quarantine_role_id=NULL, log_channel_id=NULL, time_window=10 WHERE guild_id=?", (interaction.guild.id,)) # type: ignore
	db("UPDATE antinuke_modules SET enabled=1 WHERE guild_id=?", (interaction.guild.id,)) # type: ignore
	db("UPDATE antinuke_limits SET max_actions=3 WHERE guild_id=?", (interaction.guild.id,)) # type: ignore
	db("DELETE FROM antinuke_whitelist WHERE guild_id=?", (interaction.guild.id,)) # type: ignore
	await interaction.response.send_message("Anti-nuke settings reset and disabled.", ephemeral=True)


@antinuke.command(name="status", description="Show anti-nuke status")
@app_commands.checks.has_permissions(administrator=True)
async def antinuke_status(interaction: discord.Interaction):
	config = get_antinuke_config(interaction.guild.id) # type: ignore
	modules = db("SELECT module, enabled FROM antinuke_modules WHERE guild_id=?", (interaction.guild.id,), True) or [] # type: ignore
	limits = db("SELECT module, max_actions FROM antinuke_limits WHERE guild_id=?", (interaction.guild.id,), True) or [] # type: ignore
	limit_map = dict(limits)
	module_text = "\n".join(f"`{module}`: {'on' if enabled else 'off'} | limit {limit_map.get(module, 3)}" for module, enabled in modules) or "No modules configured"
	embed = discord.Embed(title="Anti-nuke status", color=discord.Color.green() if config[0] else discord.Color.red())
	embed.add_field(name="Protection", value="Enabled" if config[0] else "Disabled", inline=True)
	embed.add_field(name="Lockdown", value="Enabled" if config[1] else "Disabled", inline=True)
	embed.add_field(name="Punishment", value=config[2], inline=True)
	embed.add_field(name="Time window", value=f"{config[5]} seconds", inline=True)
	embed.add_field(name="Modules", value=module_text, inline=False)
	await interaction.response.send_message(embed=embed, ephemeral=True)


@antinuke.command(name="timewindow", description="Set the detection time window")
@app_commands.checks.has_permissions(administrator=True)
async def antinuke_time_window(interaction: discord.Interaction, seconds: app_commands.Range[int, 1, 3600]):
	ensure_antinuke(interaction.guild.id) # type: ignore
	db("UPDATE antinuke_config SET time_window=? WHERE guild_id=?", (seconds, interaction.guild.id)) # type: ignore
	await interaction.response.send_message(f"Anti-nuke time window set to {seconds} seconds.", ephemeral=True)


@antinuke.command(name="whitelist", description="Add or remove a user from the whitelist")
@app_commands.checks.has_permissions(administrator=True)
async def antinuke_whitelist(interaction: discord.Interaction, member: discord.Member, action: Literal["add", "remove"]):
	if action == "add":
		db("INSERT OR IGNORE INTO antinuke_whitelist(guild_id, user_id) VALUES (?, ?)", (interaction.guild.id, member.id)) # type: ignore
	else:
		db("DELETE FROM antinuke_whitelist WHERE guild_id=? AND user_id=?", (interaction.guild.id, member.id)) # type: ignore
	await interaction.response.send_message(f"{member.mention} {('added to' if action == 'add' else 'removed from')} the whitelist.", ephemeral=True)


@antinuke.command(name="setlogs", description="Set the anti-nuke log channel")
@app_commands.checks.has_permissions(administrator=True)
async def antinuke_set_logs(interaction: discord.Interaction, channel: discord.TextChannel):
	if interaction.guild is None or interaction.guild.me is None or not channel.permissions_for(interaction.guild.me).send_messages:
		return await interaction.response.send_message("I cannot send messages in that channel.", ephemeral=True)
	ensure_antinuke(interaction.guild.id)
	db("UPDATE antinuke_config SET log_channel_id=? WHERE guild_id=?", (channel.id, interaction.guild.id))
	await interaction.response.send_message(f"Anti-nuke logs will be sent to {channel.mention}.", ephemeral=True)


@bot.tree.command(name="giveaway", description="Create a timed giveaway")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(prize="What the winner will receive", duration="Giveaway duration in minutes", winners="Number of winners")
async def giveaway(interaction: discord.Interaction, prize: str, duration: app_commands.Range[int, 1, 10080], winners: app_commands.Range[int, 1, 20] = 1):
	if interaction.guild is None:
		return await interaction.response.send_message("Giveaways can only be created in a server.", ephemeral=True)
	end_at = datetime.now(timezone.utc) + timedelta(minutes=duration)
	embed = discord.Embed(title="🎉 Giveaway", description=f"## {prize}\n\nClick the button below to enter for a chance to win!", color=discord.Color.gold())
	embed.add_field(name="Ends", value=f"<t:{int(end_at.timestamp())}:F>\n<t:{int(end_at.timestamp())}:R>", inline=True)
	embed.add_field(name="Winners", value=f"{winners:,}", inline=True)
	embed.add_field(name="Entries", value="0", inline=True)
	embed.add_field(name="Hosted by", value=interaction.user.mention, inline=False)
	embed.set_footer(text=f"Started by {interaction.user.display_name}")
	await interaction.response.send_message(embed=embed, view=GiveawayView())
	message = await interaction.original_response()
	db("INSERT INTO giveaways(message_id, guild_id, channel_id, prize, winners, end_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?)", (message.id, interaction.guild.id, message.channel.id, prize, winners, end_at.isoformat(), interaction.user.id))
	schedule_giveaway(message.id, end_at.isoformat())


@bot.tree.command(name="audit", description="Scan roles and bots for common security risks")
@app_commands.checks.has_permissions(administrator=True)
async def audit(interaction: discord.Interaction):
	await interaction.response.defer(ephemeral=True)
	risks = []
	for role in interaction.guild.roles: # type: ignore
		if role.permissions.administrator:
			owner = "@everyone" if role.is_default() else role.mention
			risks.append(f"Administrator permission: {owner}")
		elif role.permissions.manage_guild or role.permissions.manage_channels or role.permissions.manage_roles:
			risks.append(f"Elevated management permission: {role.mention}")
	for member in interaction.guild.members: # type: ignore
		if member.bot and not getattr(member.public_flags, "verified_bot", False):
			risks.append(f"Bot not marked verified: {member.mention} ({member.id})")
	description = "\n".join(f"• {risk}" for risk in risks)[:3900] if risks else "No common role or bot risks detected."
	embed = discord.Embed(title="Server security audit", description=description, color=discord.Color.orange() if risks else discord.Color.green())
	embed.set_footer(text="Review findings manually before changing permissions.")
	await interaction.followup.send(embed=embed, ephemeral=True)


def moderation_target_allowed(interaction: discord.Interaction, target: discord.Member):
	if interaction.guild is None or interaction.guild.me is None:
		return False
	return target != interaction.user and target != interaction.guild.me and target.top_role < interaction.guild.me.top_role


@bot.tree.command(name="kick", description="Kick a member from the server")
@app_commands.checks.has_permissions(kick_members=True)
@app_commands.describe(member="Member to kick", reason="Reason for the kick")
async def kick(interaction: discord.Interaction, member: discord.Member, reason: Optional[str] = None):
	if not moderation_target_allowed(interaction, member):
		return await interaction.response.send_message("You cannot moderate that member because of role hierarchy.", ephemeral=True)
	try:
		await member.kick(reason=reason or f"Kicked by {interaction.user}")
	except discord.Forbidden:
		return await interaction.response.send_message("I do not have permission to kick that member.", ephemeral=True)
	await interaction.response.send_message(f"Kicked **{member}**. Reason: {reason or 'No reason provided.'}")


@bot.tree.command(name="ban", description="Ban a member from the server")
@app_commands.checks.has_permissions(ban_members=True)
@app_commands.describe(member="Member to ban", reason="Reason for the ban", delete_days="Days of messages to delete")
async def ban(interaction: discord.Interaction, member: discord.Member, reason: Optional[str] = None, delete_days: app_commands.Range[int, 0, 7] = 0):
	if not moderation_target_allowed(interaction, member):
		return await interaction.response.send_message("You cannot moderate that member because of role hierarchy.", ephemeral=True)
	try:
		await member.ban(reason=reason or f"Banned by {interaction.user}", delete_message_days=delete_days)
	except discord.Forbidden:
		return await interaction.response.send_message("I do not have permission to ban that member.", ephemeral=True)
	await interaction.response.send_message(f"Banned **{member}**. Reason: {reason or 'No reason provided.'}")


@bot.tree.command(name="mute", description="Timeout a member")
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.describe(member="Member to mute", duration="Timeout duration in minutes", reason="Reason for the mute")
async def mute(interaction: discord.Interaction, member: discord.Member, duration: app_commands.Range[int, 1, 40320], reason: Optional[str] = None):
	if not moderation_target_allowed(interaction, member):
		return await interaction.response.send_message("You cannot moderate that member because of role hierarchy.", ephemeral=True)
	try:
		await member.timeout(timedelta(minutes=duration), reason=reason or f"Muted by {interaction.user}")
	except discord.Forbidden:
		return await interaction.response.send_message("I do not have permission to mute that member.", ephemeral=True)
	await interaction.response.send_message(f"Muted **{member}** for **{duration} minutes**. Reason: {reason or 'No reason provided.'}")


@bot.tree.command(name="unmute", description="Remove a member's timeout")
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.describe(member="Member to unmute", reason="Reason for removing the mute")
async def unmute(interaction: discord.Interaction, member: discord.Member, reason: Optional[str] = None):
	if not moderation_target_allowed(interaction, member):
		return await interaction.response.send_message("You cannot moderate that member because of role hierarchy.", ephemeral=True)
	try:
		await member.timeout(None, reason=reason or f"Unmuted by {interaction.user}")
	except discord.Forbidden:
		return await interaction.response.send_message("I do not have permission to unmute that member.", ephemeral=True)
	await interaction.response.send_message(f"Removed the timeout from **{member}**.")


@bot.tree.command(name="unban", description="Unban a user by ID")
@app_commands.checks.has_permissions(ban_members=True)
@app_commands.describe(user_id="The banned user's Discord ID", reason="Reason for the unban")
async def unban(interaction: discord.Interaction, user_id: str, reason: Optional[str] = None):
	if interaction.guild is None:
		return await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
	try:
		user = await bot.fetch_user(int(user_id))
		await interaction.guild.unban(user, reason=reason or f"Unbanned by {interaction.user}")
	except (ValueError, discord.NotFound):
		return await interaction.response.send_message("That user ID is invalid or the user is not banned.", ephemeral=True)
	except discord.Forbidden:
		return await interaction.response.send_message("I do not have permission to unban users.", ephemeral=True)
	await interaction.response.send_message(f"Unbanned **{user}**.")


@bot.tree.command(name="setup-moderation-role", description="Create and configure the moderation role panel")
@app_commands.checks.has_permissions(administrator=True)
async def setup_moderation_role(interaction: discord.Interaction):
	if interaction.guild is None:
		return await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
	permissions = discord.Permissions(kick_members=True, ban_members=True, moderate_members=True, manage_messages=True)
	config = db("SELECT role_id FROM moderation_config WHERE guild_id=?", (interaction.guild.id,), True)
	role = interaction.guild.get_role(config[0][0]) if config else None
	try:
		if role is None:
			role = await interaction.guild.create_role(name="Server Moderator", permissions=permissions, reason=f"Created by {interaction.user}")
		else:
			await role.edit(permissions=permissions, reason=f"Updated by {interaction.user}")
	except discord.Forbidden:
		return await interaction.response.send_message("I need Manage Roles permission to create or update the moderation role.", ephemeral=True)
	db("INSERT INTO moderation_config(guild_id, role_id) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET role_id=excluded.role_id", (interaction.guild.id, role.id))
	view = ModerationRoleView(role.id)
	embed = discord.Embed(title="🛡️ Moderation Role Manager", description=f"Select a member, then grant or revoke {role.mention}.\nThis role provides Kick Members, Ban Members, Moderate Members, and Manage Messages permissions.", color=discord.Color.orange())
	embed.set_footer(text="Only administrators can use this panel.")
	await interaction.response.send_message(embed=embed, view=view)


@bot.tree.command(name="mediaonly", description="Enable or disable media-only enforcement")
@app_commands.checks.has_permissions(manage_channels=True)
async def mediaonly(interaction: discord.Interaction, channel: discord.TextChannel, status: Literal["enable", "disable"]):
	enabled = 1 if status == "enable" else 0
	db("INSERT INTO media_only_channels(guild_id, channel_id, enabled) VALUES (?, ?, ?) ON CONFLICT(guild_id, channel_id) DO UPDATE SET enabled=excluded.enabled", (interaction.guild.id, channel.id, enabled)) # type: ignore
	await interaction.response.send_message(f"Media-only mode {status}d for {channel.mention}.", ephemeral=True)




class SuggestionCommandModal(discord.ui.Modal, title="New suggestion"):
	def __init__(self, channel: Optional[discord.TextChannel] = None):
		super().__init__()
		self.channel = channel

	content = discord.ui.TextInput(label="Suggestion", style=discord.TextStyle.paragraph, max_length=2000)

	async def on_submit(self, interaction):
		if interaction.guild is None:
			return await interaction.response.send_message("Suggestions can only be submitted in a server.", ephemeral=True)
		channel = self.channel
		if channel is None:
			configured_row = db("SELECT channel_id FROM suggestion_config WHERE guild_id=?", (interaction.guild.id,), True)
			configured_id = configured_row[0][0] if configured_row else SUGGESTION_CHANNEL_ID
			configured_channel = interaction.guild.get_channel(configured_id) if configured_id else None
			channel = configured_channel if isinstance(configured_channel, discord.TextChannel) else None
		if channel is None and isinstance(interaction.channel, discord.TextChannel):
			channel = interaction.channel
		if channel is None:
			return await interaction.response.send_message("The suggestion channel is not configured.", ephemeral=True)
		embed = discord.Embed(title="Community suggestion", description=self.content.value, color=discord.Color.blurple())
		embed.set_footer(text=f"Suggested by {interaction.user}")
		message = await channel.send(embed=embed, view=SuggestionView())
		db("INSERT INTO suggestions(message_id, guild_id, author_id, content) VALUES (?, ?, ?, ?)", (message.id, interaction.guild.id, interaction.user.id, self.content.value))
		await interaction.response.send_message(f"Suggestion posted in {channel.mention}.", ephemeral=True)


@bot.tree.command(name="setup-suggestion-channel", description="Set the channel where suggestions are posted")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(channel="Text channel to receive suggestions")
async def setup_suggestion_channel(interaction: discord.Interaction, channel: discord.TextChannel):
	if interaction.guild is None:
		return await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
	bot_member = interaction.guild.me
	if bot_member is None:
		return await interaction.response.send_message("I could not verify my permissions in that channel.", ephemeral=True)
	permissions = channel.permissions_for(bot_member)
	if not permissions.send_messages or not permissions.embed_links:
		return await interaction.response.send_message("I need Send Messages and Embed Links permission in that channel.", ephemeral=True)
	db("INSERT INTO suggestion_config(guild_id, channel_id) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id", (interaction.guild.id, channel.id))
	await interaction.response.send_message(f"Suggestions will now be posted in {channel.mention}.", ephemeral=True)


@bot.tree.command(name="setup-game-channel", description="Set the channel where games can be played")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(channel="Text channel where game commands are allowed")
async def setup_game_channel(interaction: discord.Interaction, channel: discord.TextChannel):
	if interaction.guild is None:
		return await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
	bot_member = interaction.guild.me
	if bot_member is None:
		return await interaction.response.send_message("I could not verify my permissions in that channel.", ephemeral=True)
	permissions = channel.permissions_for(bot_member)
	if not permissions.send_messages:
		return await interaction.response.send_message("I need Send Messages permission in that channel.", ephemeral=True)
	db("INSERT INTO game_config(guild_id, channel_id) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id", (interaction.guild.id, channel.id))
	await interaction.response.send_message(f"Games can now only be used in {channel.mention}.", ephemeral=True)


@bot.tree.command(name="suggest", description="Submit a community suggestion")
@app_commands.describe(channel="Optional destination channel; administrators can choose any text channel")
async def suggest(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
	if interaction.guild is None:
		return await interaction.response.send_message("Suggestions can only be submitted in a server.", ephemeral=True)
	if channel is not None and not interaction.user.guild_permissions.administrator: # type: ignore
		return await interaction.response.send_message("Only administrators can choose a suggestion channel.", ephemeral=True)
	await interaction.response.send_modal(SuggestionCommandModal(channel))


@bot.tree.command(name="confess", description="Submit an anonymous confession for staff review")
async def confess(interaction: discord.Interaction):
	await interaction.response.send_modal(ConfessionModal())


def rank_roles(guild):
	roles = []
	for item in os.getenv("RANK_ROLES", "").split(","):
		if ":" in item:
			level, role_id = item.split(":", 1)
			if level.isdigit() and (role := guild.get_role(env_int_value(role_id))):
				roles.append((int(level), role))
	return sorted(roles)


def env_int_value(value):
	try:
		return int(value)
	except ValueError:
		return 0


async def award_xp(member, amount=XP_PER_MESSAGE):
	db("INSERT OR IGNORE INTO member_xp VALUES (?, ?, 0)", (member.guild.id, member.id))
	db("UPDATE member_xp SET xp=xp+? WHERE guild_id=? AND user_id=?", (amount, member.guild.id, member.id))
	points = db("SELECT xp FROM member_xp WHERE guild_id=? AND user_id=?", (member.guild.id, member.id), True)[0][0] # type: ignore
	level = points // 100
	for minimum, role in rank_roles(member.guild):
		if level >= minimum and role not in member.roles:
			try:
				await member.add_roles(role, reason="Level reward")
			except discord.HTTPException:
				pass
	return points


@bot.tree.command(name="rank", description="Show your server activity rank")
async def rank(interaction: discord.Interaction):
	row = db("SELECT xp FROM member_xp WHERE guild_id=? AND user_id=?", (interaction.guild.id, interaction.user.id), True) # type: ignore
	points = row[0][0] if row else 0
	level = points // 100
	embed = discord.Embed(title=f"{interaction.user.display_name}'s rank", color=discord.Color.gold())
	embed.set_thumbnail(url=interaction.user.display_avatar.url)
	embed.add_field(name="Level", value=str(level))
	embed.add_field(name="XP", value=f"{points % 100}/100 toward level {level + 1}")
	await interaction.response.send_message(embed=embed)


@bot.tree.command(name="setup-reaction-roles", description="Post the self-assignable role panel")
@app_commands.checks.has_permissions(manage_guild=True)
async def setup_reaction_roles(interaction: discord.Interaction):
	if not os.getenv("REACTION_ROLES"):
		return await interaction.response.send_message("Set REACTION_ROLES first.", ephemeral=True)
	view = RoleView()
	if not view.children:
		return await interaction.response.send_message("REACTION_ROLES has no valid role entries.", ephemeral=True)
	await interaction.response.send_message("Choose your roles below.", view=view)


@bot.tree.command(name="ask", description="Ask the AI assistant a question")
async def ask(interaction: discord.Interaction, question: str):
	if genai is None:
		return await interaction.response.send_message("The Gemini package is missing. Install it with `pip install -r requirements.txt`.", ephemeral=True)
	if not GEMINI_API_KEY or GEMINI_API_KEY.lower() in {"your_gemini_api_key", "your-gemini-api-key"}:
		return await interaction.response.send_message("AI chat is not configured. Add GEMINI_API_KEY to the bot's .env file and restart the bot.", ephemeral=True)
	await interaction.response.defer()
	try:
		genai.configure(api_key=os.getenv("GEMINI_API_KEY")) # type: ignore
		model = genai.GenerativeModel(AI_MODEL) # type: ignore
		result = await asyncio.to_thread(model.generate_content, question)
		await interaction.followup.send(result.text[:2000])
	except Exception as error:
		print(f"Gemini request failed: {error!r}")
		await interaction.followup.send("Gemini could not answer right now. Check the bot console and Gemini API key.")


async def _run_health_report(interaction: discord.Interaction):
	loop_start = time.perf_counter()
	await asyncio.sleep(0.02)
	loop_delay_ms = (time.perf_counter() - loop_start) * 1000

	if psutil is not None:
		process = psutil.Process()
		mem_used = process.memory_info().rss
		mem_peak = tracemalloc.get_traced_memory()[1]
		memory_line = f"RSS: {format_bytes(mem_used)} | Python peak: {format_bytes(mem_peak)}"
	else:
		current_mem, peak_mem = tracemalloc.get_traced_memory()
		memory_line = f"Python memory: {format_bytes(current_mem)} | Peak: {format_bytes(peak_mem)}"

	uptime = datetime.now(timezone.utc) - BOT_START_TIME
	hours, remainder = divmod(int(uptime.total_seconds()), 3600)
	minutes, seconds = divmod(remainder, 60)

	embed = discord.Embed(title="🤖 Bot Health", color=discord.Color.blurple())
	embed.add_field(name="📡 Websocket Ping", value=f"{bot.latency * 1000:.0f} ms", inline=True)
	embed.add_field(name="⚡ Event Loop Delay", value=f"{loop_delay_ms:.2f} ms", inline=True)
	embed.add_field(name="🧠 Memory", value=memory_line, inline=False)
	embed.add_field(name="⏱️ Uptime", value=f"{hours}h {minutes}m {seconds}s", inline=True)
	embed.add_field(name="🖥️ Guilds", value=str(len(bot.guilds)), inline=True)
	embed.add_field(name="👥 Members", value=str(sum(g.member_count or 0 for g in bot.guilds)), inline=True)
	embed.add_field(name="🧩 Version", value=f"discord.py {discord.__version__}", inline=True)
	embed.add_field(name="🐍 Python", value=sys.version.split()[0], inline=True)
	embed.add_field(name="🕒 Local Time", value=datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC%z"), inline=False)
	await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="health", description="Inspect bot uptime, latency, memory, and runtime status")
async def health(interaction: discord.Interaction):
	await _run_health_report(interaction)


@bot.tree.command(name="status", description="Alias for bot health and uptime status")
async def status(interaction: discord.Interaction):
	await _run_health_report(interaction)


@bot.command(name="health")
async def health_text(ctx):
	await ctx.send("Use `/health` or `/status` for the bot status check.")


@bot.tree.command(name="help", description="Show the bot commands and how to use them")
@app_commands.describe(category="Show one command category or everything")
async def help_command(interaction: discord.Interaction, category: Literal["all", "support", "moderation", "economy", "games", "setup", "antinuke", "status"] = "all"):
	embed = discord.Embed(
		title="Bot Help",
		description="Use the commands below to get started. Choose a category to focus the list.",
		color=discord.Color.blurple(),
	)
	sections = {
		"support": ("Support", "`/suggest` Submit a suggestion\n`/confess` Send an anonymous confession\n`/ask question:<text>` Ask the AI assistant\n`/health` or `/status` Check bot latency, memory, and uptime"),
		"moderation": ("Moderation", "`/kick member:<member>` Kick a member\n`/ban member:<member>` Ban a member\n`/mute member:<member> duration:<minutes>` Timeout a member\n`/unmute member:<member>` Remove a timeout\n`/unban user_id:<id>` Unban a user\n`/deleted-logs limit:<number>` Review deleted messages\n`/setup-moderation-role` Create a moderator role and admin control panel"),
		"antinuke": ("Anti-nuke (Administrators)", "`/antinuke enable|disable` Turn protection on or off\n`/antinuke guard module status` Toggle a guard module\n`/antinuke limits module limit` Set action limits\n`/antinuke lockdown status` Toggle lockdown\n`/antinuke punishment action` Set ban, kick, quarantine, or none\n`/antinuke quarantinerole role` Set the quarantine role\n`/antinuke recover` Clear events and unlock\n`/antinuke reset` Restore defaults\n`/antinuke status` View current settings\n`/antinuke timewindow seconds` Set detection window\n`/antinuke whitelist member action` Manage whitelist\n`/antinuke setlogs channel` Set the log channel"),
		"economy": ("Coins and Shop", "`/leaderboard` See the top 10 coin holders\n`/shop` View useful rewards\n`/buy item:<name>` Spend coins on an item\n`/rank` View your activity XP and level"),
		"games": ("Games", "`/tic-tac-toe opponent:<member>` Challenge a member\n`/rps opponent:<member>` Play Rock Paper Scissors\n`/pokemon-guess` Guess a Pokemon\n`/trivia` Answer a quiz\n`/slot bet:<amount>` Spin the slots\n`/blackjack` Play blackjack\n`/minefield` Clear the minefield\n`/coinflip choice:<heads|tails> bet:<amount>` Bet on a coin flip\n`/roll sides:<number>` Roll a die\n`/guess` Guess a number\n`/hangman` Start hangman\n`/wordle` Start Wordle\n`/unscramble` Solve a scrambled word\n`/emoji-quiz` Guess the movie\n`/math-race` Solve a math problem\n`/high-low guess:<high|low>` Guess the next card\n`/truth-or-dare` Get a prompt\n`/explore` Explore a dungeon"),
		"setup": ("Server Setup (Administrators)", "`/giveaway prize:<text> duration:<minutes> winners:<number>` Create a giveaway\n`/setup-game-channel channel:<channel>` Restrict games to one channel\n`/setup-suggestion-channel channel:<channel>` Choose the suggestion channel\n`/setup-ticket-log channel:<channel>` Store closed ticket transcripts\n`/setup-deletion-logs channel:<channel>` Archive deleted messages\n`/setup-ticket` Post the ticket panel; staff can claim tickets with the Claim button\n`/setup-reaction-roles` Post the role panel\n`/mediaonly channel:<channel> status:<enable|disable>` Toggle media-only mode\n`/audit` Scan common server risks"),
		"status": ("System Status", "`/health` or `/status` Inspect bot latency, memory, uptime, and current runtime stats"),
	}
	selected_sections = sections.values() if category == "all" else [sections[category]]
	for name, value in selected_sections:
		embed.add_field(name=name, value=value, inline=False)
	embed.set_footer(text="Games follow the server's configured game channel when one is set.")
	await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.command()
@commands.has_permissions(manage_guild=True)
async def ticketpanel(ctx):
	embed = discord.Embed(title="🎫 Support Tickets", description="Press the button below to open a private support ticket.", color=discord.Color.green())
	await ctx.send(embed=embed, view=TicketView())


@bot.command()
async def balance_cmd(ctx):
	await ctx.send(f"{ctx.author.mention}, you have **{balance(ctx.author.id)} coins**.")


@bot.tree.command(name="leaderboard", description="Show the top 10 members with the most coins")
async def leaderboard(interaction: discord.Interaction):
	if interaction.guild is None:
		return await interaction.response.send_message("The leaderboard can only be viewed in a server.", ephemeral=True)
	members = [member for member in interaction.guild.members if not member.bot]
	ranked = sorted(((balance(member.id), member) for member in members), key=lambda entry: entry[0], reverse=True)[:10]
	if not ranked:
		return await interaction.response.send_message("No member coin data is available yet.", ephemeral=True)
	lines = [f"**{position}.** {member.mention} — **{coins:,} coins**" for position, (coins, member) in enumerate(ranked, 1)]
	embed = discord.Embed(title=f"{interaction.guild.name} Coin Leaderboard", description="\n".join(lines), color=discord.Color.gold())
	embed.set_footer(text="Top 10 members")
	await interaction.response.send_message(embed=embed)


@bot.tree.command(name="shop", description="View items available for your coins")
async def shop_slash(interaction: discord.Interaction):
	rows = db("SELECT name, price, description FROM items ORDER BY price", fetch=True)
	if not rows:
		return await interaction.response.send_message("The shop is empty.", ephemeral=True)
	embed = discord.Embed(title="Coin Shop", description="Use `/buy` with an item name to purchase something useful.", color=discord.Color.blurple())
	embed.add_field(name="Your balance", value=f"**{balance(interaction.user.id):,} coins**", inline=False)
	for name, price, description in rows:
		embed.add_field(name=f"{name} — {price:,} coins", value=description, inline=False)
	await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="buy", description="Spend coins on an item from the shop")
@app_commands.describe(item="The exact item name to purchase")
async def buy_slash(interaction: discord.Interaction, item: str):
	row = db("SELECT name, price, description FROM items WHERE lower(name)=lower(?)", (item.strip(),), True)
	if not row:
		return await interaction.response.send_message("That item does not exist. Use `/shop` to see available items.", ephemeral=True)
	name, price, description = row[0]
	current_balance = balance(interaction.user.id)
	if current_balance < price:
		return await interaction.response.send_message(f"You need {price - current_balance:,} more coins to buy **{name}**.", ephemeral=True)
	db("UPDATE users SET balance=balance-? WHERE id=?", (price, interaction.user.id))
	await interaction.response.send_message(f"You bought **{name}** for **{price:,} coins**. {description} Your balance is now **{current_balance - price:,} coins**.")


@bot.command()
async def shop(ctx):
	rows = db("SELECT name, price, description FROM items", fetch=True)
	embed = discord.Embed(title="🛒 Shop", color=discord.Color.blurple())
	embed.description = "\n".join(f"**{n}** — {p} coins\n{d}\n`!buy {n}`" for n, p, d in rows) # type: ignore
	await ctx.send(embed=embed, view=ShopView())


@bot.command()
async def buy(ctx, *, item: str):
	row = db("SELECT name, price FROM items WHERE lower(name)=lower(?)", (item,), True)
	if not row:
		return await ctx.send("That item does not exist. Use `!shop`.")
	name, price = row[0]
	if balance(ctx.author.id) < price:
		return await ctx.send(f"You need {price - balance(ctx.author.id)} more coins.")
	db("UPDATE users SET balance=balance-? WHERE id=?", (price, ctx.author.id))
	await ctx.send(f"✅ {ctx.author.mention} bought **{name}** for {price} coins!")


@bot.command()
@commands.cooldown(1, 86400, commands.BucketType.user)
async def daily(ctx):
	balance(ctx.author.id)
	db("UPDATE users SET balance=balance+500 WHERE id=?", (ctx.author.id,))
	await ctx.send(f"🎁 {ctx.author.mention} received 500 daily coins!")


def calculate_count(text):
	try:
		node = ast.parse(text, mode="eval").body
		operators = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
		def evaluate(value):
			if isinstance(value, ast.Constant) and isinstance(value.value, (int, float)):
				return value.value
			if isinstance(value, ast.BinOp) and type(value.op) in operators:
				left, right = evaluate(value.left), evaluate(value.right)
				if abs(right) > 1000000 or abs(left) > 1000000:
					raise ValueError
				return operators[type(value.op)](left, right)
			raise ValueError
		result = evaluate(node)
		return int(result) if int(result) == result else None
	except (ValueError, TypeError, ZeroDivisionError, SyntaxError):
		return None


URL_PATTERN = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)


async def record_deleted_message(message: discord.Message, reason: str):
	if message.guild is None or message.author.bot:
		return
	attachments = " ".join(attachment.url for attachment in message.attachments) or "None"
	deleted_at = datetime.now(timezone.utc).isoformat()
	db("INSERT OR IGNORE INTO deleted_messages(message_id, guild_id, channel_id, author_id, author_name, content, attachments, deleted_at, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (message.id, message.guild.id, message.channel.id, message.author.id, str(message.author), message.content or "[no text]", attachments, deleted_at, reason))
	row = db("SELECT channel_id FROM deletion_log_config WHERE guild_id=?", (message.guild.id,), True)
	log_channel = message.guild.get_channel(row[0][0]) if row else None
	if not isinstance(log_channel, discord.TextChannel):
		return
	channel_name = message.channel.mention if isinstance(message.channel, discord.TextChannel) else f"channel {message.channel.id}"
	embed = discord.Embed(title="🗑️ Message deleted", description=f"A message was deleted in {channel_name}.", color=discord.Color.red(), timestamp=datetime.now(timezone.utc))
	embed.add_field(name="Author", value=f"{message.author.mention}\n`{message.author.id}`", inline=True)
	embed.add_field(name="Reason", value=reason, inline=True)
	embed.add_field(name="Content", value=(message.content or "[no text]")[:1024], inline=False)
	if attachments != "None":
		embed.add_field(name="Attachments", value=attachments[:1024], inline=False)
	try:
		await log_channel.send(embed=embed)
	except (discord.Forbidden, discord.HTTPException):
		pass


async def malicious_url(url):
	host = (urlparse(url).hostname or "").lower().rstrip(".")
	if not host or any(host == domain or host.endswith(f".{domain}") for domain in SAFE_DOMAINS):
		return False
	if any(host == domain or host.endswith(f".{domain}") for domain in BLOCKED_DOMAINS):
		return True
	if not GOOGLE_SAFE_BROWSING_KEY or not aiohttp:
		return False
	payload = {"client": {"clientId": "discord-support-bot", "clientVersion": "1.0"}, "threatInfo": {"threatTypes": ["MALWARE", "SOCIAL_ENGINEERING", "UNWANTED_SOFTWARE"], "platformTypes": ["ANY_PLATFORM"], "threatEntryTypes": ["URL"], "threatEntries": [{"url": url}]}}
	try:
		endpoint = f"https://safebrowsing.googleapis.com/v4/threatMatches:find?key={GOOGLE_SAFE_BROWSING_KEY}"
		timeout = aiohttp.ClientTimeout(total=4)
		async with aiohttp.ClientSession(timeout=timeout) as session:
			async with session.post(endpoint, json=payload) as response:
				return response.status == 200 and bool((await response.json()).get("matches"))
	except (aiohttp.ClientError, ValueError, TimeoutError):
		return False


async def scan_message_links(message):
	for url in URL_PATTERN.findall(message.content):
		if await malicious_url(url):
			await record_deleted_message(message, "Potentially unsafe link")
			await message.delete()
			try:
				await message.channel.send(f"{message.author.mention}, that link was blocked as potentially unsafe.", delete_after=8)
			except discord.HTTPException:
				pass
			return True
	return False


def has_media(message):
	if message.attachments:
		return True
	for url in URL_PATTERN.findall(message.content):
		host = (urlparse(url).hostname or "").lower().rstrip(".")
		if any(host == domain or host.endswith(f".{domain}") for domain in MEDIA_LINK_HOSTS):
			return True
	return False


def media_link_category(url: str):
	host = (urlparse(url).hostname or "").lower().rstrip(".")
	lower = url.lower()
	path = urlparse(url).path.lower()
	query = urlparse(url).query.lower()
	if any(ext in path for ext in (".gif", ".gifv")) or "format=gif" in query:
		return "gif"
	if any(ext in path for ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp")) or any(format_name in query for format_name in ("format=png", "format=jpg", "format=jpeg", "format=webp")):
		return "photo"
	if any(ext in path for ext in (".mp4", ".mov", ".webm", ".m4v", ".avi", ".mkv")) or any(format_name in query for format_name in ("format=mp4", "format=webm")):
		return "video"
	if any(host == domain or host.endswith(f".{domain}") for domain in ("tenor.com", "media.tenor.com", "giphy.com", "i.giphy.com", "redgifs.com")):
		return "gif"
	if any(host == domain or host.endswith(f".{domain}") for domain in ("youtube.com", "youtu.be", "vimeo.com", "tiktok.com", "twitter.com", "x.com", "streamable.com", "clips.twitch.tv")):
		return "video"
	if any(host == domain or host.endswith(f".{domain}") for domain in ("imgur.com", "i.imgur.com", "images.unsplash.com", "cdn.discordapp.com", "media.discordapp.net")):
		return "photo"
	return None


def configured_media_role(guild: discord.Guild):
	row = db("SELECT role_id FROM media_link_roles WHERE guild_id=?", (guild.id,), True)
	return guild.get_role(row[0][0]) if row else None


def get_welcome_config(guild_id: int):
	row = db("SELECT channel_id, message FROM welcome_config WHERE guild_id=?", (guild_id,), True)
	if not row:
		return None
	channel_id, message = row[0]
	return {"channel_id": channel_id, "message": message}


def has_unauthorized_media_link(message):
	if message.attachments:
		return False
	role = configured_media_role(message.guild)
	if role is None:
		return False
	if not isinstance(message.author, discord.Member):
		return False
	if message.author.get_role(role.id) is not None:
		return False
	return any(media_link_category(url) for url in URL_PATTERN.findall(message.content))


def media_only_enabled(guild_id, channel_id):
	if channel_id in MEDIA_CHANNEL_IDS:
		return True
	row = db("SELECT enabled FROM media_only_channels WHERE guild_id=? AND channel_id=?", (guild_id, channel_id), True)
	return bool(row and row[0][0])


async def warn_media_only(message):
	warning = "This channel is media-only. Please post an image, video, file, or supported media link."
	try:
		await message.author.send(f"{warning} Your message in **{message.guild.name}** was removed.")
	except discord.HTTPException:
		try:
			await message.channel.send(f"{message.author.mention}, {warning}", delete_after=8)
		except discord.Forbidden:
			pass


@bot.tree.command(name="media-role", description="Choose the role allowed to post GIF, video, and photo links")
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.describe(role="Only members with this role may post media links")
async def media_role(interaction: discord.Interaction, role: discord.Role):
	if interaction.guild is None:
		return await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
	if role.is_default():
		return await interaction.response.send_message("Choose a role other than @everyone.", ephemeral=True)
	db("INSERT INTO media_link_roles(guild_id, role_id) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET role_id=excluded.role_id", (interaction.guild.id, role.id))
	await interaction.response.send_message(f"Only members with {role.mention} can now post GIF, video, and photo links.", ephemeral=True)


async def _set_welcome_config(interaction: discord.Interaction, channel: discord.TextChannel, message: str):
	if interaction.guild is None:
		return await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
	if not channel.permissions_for(interaction.guild.me).send_messages or not channel.permissions_for(interaction.guild.me).embed_links:
		return await interaction.response.send_message("I need Send Messages and Embed Links permission in that channel.", ephemeral=True)
	if not message.strip():
		return await interaction.response.send_message("Please provide a valid welcome message.", ephemeral=True)
	db("INSERT INTO welcome_config(guild_id, channel_id, message) VALUES (?, ?, ?) ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id, message=excluded.message", (interaction.guild.id, channel.id, message.strip()))
	await interaction.response.send_message(f"Welcome channel set to {channel.mention}. New members will be greeted there.", ephemeral=True)


@bot.tree.command(name="setup-welcome", description="Set the welcome channel and greeting message for new members")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(channel="Channel where welcome messages will be sent", message="Custom welcome message with {user}, {username}, {server}, and {member_count}")
async def setup_welcome(interaction: discord.Interaction, channel: discord.TextChannel, message: str = "Welcome {user} to {server}! We hope you enjoy your time here."):
	await _set_welcome_config(interaction, channel, message)


@bot.tree.command(name="setup-welcome-channel", description="Set the welcome channel and greeting message for new members")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(channel="Channel where welcome messages will be sent", message="Custom welcome message with {user}, {username}, {server}, and {member_count}")
async def setup_welcome_channel(interaction: discord.Interaction, channel: discord.TextChannel, message: str = "Welcome {user} to {server}! We hope you enjoy your time here."):
	await _set_welcome_config(interaction, channel, message)


role_group = app_commands.Group(name="role", description="Manage roles for members")
bot.tree.add_command(role_group)


@role_group.command(name="add", description="Give a role to a user")
@app_commands.checks.has_permissions(manage_roles=True)
@app_commands.describe(role="The role to assign", user="User to give the role to")
async def role_add(interaction: discord.Interaction, role: discord.Role, user: discord.Member):
	if interaction.guild is None or interaction.guild.me is None:
		return await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
	if role.is_default():
		return await interaction.response.send_message("Cannot assign the @everyone role.", ephemeral=True)
	if role >= interaction.guild.me.top_role or user == interaction.guild.me:
		return await interaction.response.send_message("I cannot manage that role or user because of role hierarchy.", ephemeral=True)
	if role in user.roles:
		return await interaction.response.send_message(f"{user.mention} already has {role.mention}.", ephemeral=True)
	try:
		await user.add_roles(role, reason=f"Role granted by {interaction.user}")
	except discord.Forbidden:
		return await interaction.response.send_message("I need Manage Roles permission and a role hierarchy above the role.", ephemeral=True)
	await interaction.response.send_message(f"Added {role.mention} to {user.mention}.", ephemeral=True)


@role_group.command(name="remove", description="Remove a role from a user")
@app_commands.checks.has_permissions(manage_roles=True)
@app_commands.describe(role="The role to remove", user="User to remove the role from")
async def role_remove(interaction: discord.Interaction, role: discord.Role, user: discord.Member):
	if interaction.guild is None or interaction.guild.me is None:
		return await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
	if role.is_default():
		return await interaction.response.send_message("Cannot remove the @everyone role.", ephemeral=True)
	if role >= interaction.guild.me.top_role or user == interaction.guild.me:
		return await interaction.response.send_message("I cannot manage that role or user because of role hierarchy.", ephemeral=True)
	if role not in user.roles:
		return await interaction.response.send_message(f"{user.mention} does not have {role.mention}.", ephemeral=True)
	try:
		await user.remove_roles(role, reason=f"Role removed by {interaction.user}")
	except discord.Forbidden:
		return await interaction.response.send_message("I need Manage Roles permission and a role hierarchy above the role.", ephemeral=True)
	await interaction.response.send_message(f"Removed {role.mention} from {user.mention}.", ephemeral=True)


class TicTacToeView(discord.ui.View):
	def __init__(self, first: discord.Member, second: discord.Member):
		super().__init__(timeout=180)
		self.players = [first, second]
		self.board = [" "] * 9
		self.turn = 0
		self.message: Optional[discord.Message] = None
		for index in range(9):
			button = discord.ui.Button(label="·", style=discord.ButtonStyle.secondary, row=index // 3, custom_id=f"ttt:{index}")
			button.callback = self.make_callback(index)
			self.add_item(button)

	def board_text(self):
		lines = []
		for row in range(3):
			cells = " | ".join(self.board[row * 3 + i] for i in range(3))
			lines.append(f"{cells}")
		return "\n".join(lines)

	def make_callback(self, index):
		async def callback(interaction):
			if interaction.user != self.players[self.turn]:
				return await interaction.response.send_message("Wait for your turn.", ephemeral=True)
			if self.board[index] != " ":
				return await interaction.response.send_message("That square is occupied.", ephemeral=True)
			self.board[index] = "X" if self.turn == 0 else "O"
			button = self.children[index]
			button.label = self.board[index]
			button.style = discord.ButtonStyle.success if self.board[index] == "X" else discord.ButtonStyle.danger
			button.disabled = True
			winner = self.winner()
			if winner or " " not in self.board:
				for item in self.children:
					item.disabled = True # type: ignore
				if winner:
					winner_member = self.players[0 if winner == "X" else 1]
					loser_member = self.players[1 if winner == "X" else 0]
					result = f"🏆 {winner_member.mention} wins Tic-Tac-Toe! {loser_member.mention}, better luck next time."
				else:
					result = f"🤝 Draw! {self.players[0].mention} and {self.players[1].mention} tied."
				return await interaction.response.edit_message(content=f"{result}\n\nBoard:\n{self.board_text()}", view=self)
			self.turn = 1 - self.turn
			await interaction.response.edit_message(content=f"🎮 {self.players[0].mention} (X) vs {self.players[1].mention} (O)\nBoard:\n{self.board_text()}\n\nTurn: {self.players[self.turn].mention}", view=self)
		return callback

	async def on_timeout(self):
		for item in self.children:
			item.disabled = True # type: ignore
		if self.message:
			await self.message.edit(content=f"⌛ Tic-Tac-Toe expired. {self.players[0].mention} and {self.players[1].mention}, start a new game to play again.", view=self)

	def winner(self):
		for line in ((0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)):
			if self.board[line[0]] != " " and len({self.board[i] for i in line}) == 1:
				return self.board[line[0]]
		return None


class ConnectFourView(discord.ui.View):
	def __init__(self, first: discord.Member, second: discord.Member):
		super().__init__(timeout=180)
		self.players = [first, second]
		self.board = [[" "] * 5 for _ in range(5)]
		self.turn = 0
		self.message: Optional[discord.Message] = None
		for row in range(5):
			for col in range(5):
				button = discord.ui.Button(label="⚪", style=discord.ButtonStyle.secondary, row=row, custom_id=f"cf:{row}:{col}")
				button.callback = self.make_callback(row, col)
				self.add_item(button)

	def board_text(self):
		header = "  1  2  3  4  5"
		rows = [header]
		for row in self.board:
			rows.append(" ".join(cell if cell != " " else "⚪" for cell in row))
		return "\n".join(rows)

	def next_open_slot(self, col):
		for row in range(4, -1, -1):
			if self.board[row][col] == " ":
				return row
		return None

	def winner(self, symbol):
		for row in range(5):
			for col in range(5):
				if self.board[row][col] != symbol:
					continue
				for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
					count = 1
					for step in range(1, 4):
						nr = row + dr * step
						nc = col + dc * step
						if 0 <= nr < 5 and 0 <= nc < 5 and self.board[nr][nc] == symbol:
							count += 1
						else:
							break
					if count >= 4:
						return True
		return False

	def make_callback(self, row, col):
		async def callback(interaction):
			if interaction.user != self.players[self.turn]:
				return await interaction.response.send_message("Wait for your turn.", ephemeral=True)
			lowest_row = self.next_open_slot(col)
			if lowest_row is None:
				return await interaction.response.send_message("That column is full. Pick another open column.", ephemeral=True)
			if row != lowest_row:
				return await interaction.response.send_message("Click any open space in the column to drop your token to the lowest available slot.", ephemeral=True)
			symbol = "🔴" if self.turn == 0 else "🟡"
			self.board[lowest_row][col] = symbol
			button = self.children[lowest_row * 5 + col]
			button.label = symbol
			button.style = discord.ButtonStyle.red if self.turn == 0 else discord.ButtonStyle.blurple
			button.disabled = True
			if self.winner(symbol):
				for item in self.children:
					item.disabled = True # type: ignore
				winner_member = self.players[self.turn]
				loser_member = self.players[1 - self.turn]
				return await interaction.response.edit_message(content=f"🏆 {winner_member.mention} wins Connect Four! {loser_member.mention}, better luck next time.\n\n{self.board_text()}", view=self)
			if all(cell != " " for row_cells in self.board for cell in row_cells):
				for item in self.children:
					item.disabled = True # type: ignore
				return await interaction.response.edit_message(content=f"🤝 Draw! {self.players[0].mention} and {self.players[1].mention} tied on the 5x5 board.\n\n{self.board_text()}", view=self)
			self.turn = 1 - self.turn
			await interaction.response.edit_message(content=f"🎮 {self.players[0].mention} 🔴 vs {self.players[1].mention} 🟡\n{self.board_text()}\n\nTurn: {self.players[self.turn].mention}\nTip: click any open slot in a column and it will drop to the lowest available spot.", view=self)
		return callback

	async def on_timeout(self):
		for item in self.children:
			item.disabled = True # type: ignore
		if self.message:
			await self.message.edit(content=f"⌛ Connect Four expired. {self.players[0].mention} and {self.players[1].mention}, start a new game to play again.", view=self)


class GameChallengeView(discord.ui.View):
	def __init__(self, challenger, opponent, game):
		super().__init__(timeout=60)
		self.challenger = challenger
		self.opponent = opponent
		self.game = game

	async def interaction_check(self, interaction: discord.Interaction):
		if interaction.user != self.opponent:
			await interaction.response.send_message("Only the challenged player can respond.", ephemeral=True)
			return False
		return True

	@discord.ui.button(label="Accept", style=discord.ButtonStyle.green)
	async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
		self.stop()
		for item in self.children:
			item.disabled = True # type: ignore
		if self.game == "tic-tac-toe":
			game_view = TicTacToeView(self.challenger, self.opponent)
			await interaction.response.edit_message(content=f"🎮 {self.challenger.mention} (X) vs {self.opponent.mention} (O)\nTurn: {self.challenger.mention}", view=game_view)
			game_view.message = await interaction.original_response()
		elif self.game == "connect-four":
			game_view = ConnectFourView(self.challenger, self.opponent)
			await interaction.response.edit_message(content=f"🎮 {self.challenger.mention} 🔴 vs {self.opponent.mention} 🟡\n{game_view.board_text()}\n\nTurn: {self.challenger.mention}\nTip: click any open square in a column to drop the token to the lowest available spot.", view=game_view)
			game_view.message = await interaction.original_response()
		else:
			game_view = RPSView(self.challenger, self.opponent)
			await interaction.response.edit_message(content=f"{self.opponent.mention} accepted! Choose your move.", view=game_view)

	@discord.ui.button(label="Deny", style=discord.ButtonStyle.red)
	async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
		self.stop()
		for item in self.children:
			item.disabled = True # type: ignore
		await interaction.response.edit_message(content=f"{self.opponent.mention} declined the challenge from {self.challenger.mention}.", view=self)


class RPSView(discord.ui.View):
	def __init__(self, challenger, opponent):
		super().__init__(timeout=60)
		self.challenger, self.opponent, self.moves = challenger, opponent, {}
		for label, move in (("Rock", "rock"), ("Paper", "paper"), ("Scissors", "scissors")):
			button = discord.ui.Button(label=label, style=discord.ButtonStyle.primary, custom_id=f"rps:{move}")
			button.callback = self.make_callback(move)
			self.add_item(button)

	def make_callback(self, move):
		async def callback(interaction):
			await self.choose(interaction, move)
		return callback

	async def choose(self, interaction, move):
		if interaction.user not in (self.challenger, self.opponent):
			return await interaction.response.send_message("This game is not for you.", ephemeral=True)
		self.moves[interaction.user.id] = move
		if self.opponent == bot.user:
			self.moves[self.opponent.id] = random.choice(("rock", "paper", "scissors"))
		await interaction.response.send_message("Move locked in.", ephemeral=True)
		if len(self.moves) == 2:
			first, second = self.moves[self.challenger.id], self.moves[self.opponent.id]
			winner = self.challenger if first != second and (first, second) in (("rock", "scissors"), ("scissors", "paper"), ("paper", "rock")) else self.opponent if first != second else None
			for item in self.children:
				item.disabled = True # type: ignore
			loser = self.opponent if winner == self.challenger else self.challenger if winner else None
			result = f"🏆 {winner.mention} wins! {loser.mention}, better luck next time." if winner and loser else f"🤝 Draw! {self.challenger.mention} and {self.opponent.mention} tied."
			await interaction.message.edit(content=result, view=self)

class BlackjackView(discord.ui.View):
	def __init__(self, interaction):
		super().__init__(timeout=120)
		self.user = interaction.user
		self.hand = [self._draw_card(), self._draw_card()]
		self.dealer = [self._draw_card(), self._draw_card()]
		self.game_over = False

	@staticmethod
	def _draw_card():
		return random.randint(1, 11)

	@staticmethod
	def hand_total(cards):
		total = sum(cards)
		aces = cards.count(11)
		while total > 21 and aces:
			total -= 10
			aces -= 1
		return total

	def total(self, cards=None):
		cards = self.hand if cards is None else cards
		return self.hand_total(cards)

	def dealer_visible_total(self):
		if self.game_over:
			return self.total(self.dealer)
		return self.total([self.dealer[0]])

	def describe_state(self):
		if self.game_over:
			return f"Your hand: {self.hand} ({self.total(self.hand)})\nDealer hand: {self.dealer} ({self.total(self.dealer)})"
		return f"Your hand: {self.hand} ({self.total(self.hand)})\nDealer hand: [{self.dealer[0]}, ?] ({self.dealer[0]})"

	async def finish_round(self, interaction, result_text):
		self.game_over = True
		for item in self.children:
			item.disabled = True # type: ignore
		await interaction.response.edit_message(content=f"{self.describe_state()}\n\n{result_text}", view=self)

	@discord.ui.button(label="Hit", style=discord.ButtonStyle.green)
	async def hit(self, interaction, button):
		if interaction.user != self.user:
			return await interaction.response.send_message("This hand is not yours.", ephemeral=True)
		if self.game_over:
			return await interaction.response.send_message("This round is already over. Start a new game to play again.", ephemeral=True)
		self.hand.append(self._draw_card())
		player_total = self.total(self.hand)
		if player_total > 21:
			return await self.finish_round(interaction, f"💥 Bust! {self.user.mention} went over 21. Dealer wins this round.")
		if player_total == 21:
			return await self.finish_round(interaction, f"🎯 Blackjack! {self.user.mention} reached exactly 21 and locked in the win.")
		await interaction.response.edit_message(content=f"{self.describe_state()}\n\n🃏 You drew another card. Choose to hit again or stand.", view=self)

	@discord.ui.button(label="Stand", style=discord.ButtonStyle.red)
	async def stand(self, interaction, button):
		if interaction.user != self.user:
			return await interaction.response.send_message("This hand is not yours.", ephemeral=True)
		if self.game_over:
			return await interaction.response.send_message("This round is already over. Start a new game to play again.", ephemeral=True)
		dealer_total = self.total(self.dealer)
		while dealer_total < 17:
			self.dealer.append(self._draw_card())
			dealer_total = self.total(self.dealer)
		player_total = self.total(self.hand)
		if dealer_total > 21:
			result = f"🏆 Dealer busts! {self.user.mention} wins this round."
		elif player_total > dealer_total:
			result = f"🏆 {self.user.mention} wins! Your total {player_total} beats the dealer's {dealer_total}."
		elif player_total == dealer_total:
			result = f"🤝 Push! {self.user.mention} and the dealer both landed on {player_total}."
		else:
			result = f"💸 Dealer wins. {self.user.mention}, better luck next time. Dealer scored {dealer_total} to your {player_total}."
		await self.finish_round(interaction, result)


class MinefieldView(discord.ui.View):
	def __init__(self, user):
		super().__init__(timeout=120)
		self.user, self.mines, self.safe = user, set(random.sample(range(16), 4)), 0
		for index in range(16):
			button = discord.ui.Button(label="?", style=discord.ButtonStyle.secondary, row=index // 4)
			button.callback = self.make_callback(index)
			self.add_item(button)

	def make_callback(self, index):
		async def callback(interaction):
			if interaction.user != self.user:
				return await interaction.response.send_message("This minefield is not yours.", ephemeral=True)
			button = self.children[index]
			button.disabled = True
			if index in self.mines:
				for item in self.children:
					item.disabled = True # type: ignore
				return await interaction.response.edit_message(content=f"💥 {self.user.mention} hit a mine and lost!", view=self)
			self.safe += 1
			button.label = "💎"
			if self.safe == 12:
				for item in self.children:
					item.disabled = True # type: ignore
				return await interaction.response.edit_message(content=f"🏆 {self.user.mention} cleared the minefield and won!", view=self)
			await interaction.response.edit_message(content=f"{self.user.mention} safe tiles: {self.safe}/12", view=self)
		return callback


def change_balance(user_id, amount):
	balance(user_id)
	db("UPDATE users SET balance=balance+? WHERE id=?", (amount, user_id))


@bot.tree.command(name="tic-tac-toe", description="Play interactive Tic-Tac-Toe")
@app_commands.check(game_channel_check)
async def tic_tac_toe(interaction: discord.Interaction, opponent: Optional[discord.Member] = None):
	if opponent is None or opponent == interaction.user or opponent.bot:
		return await interaction.response.send_message("Choose another human player.", ephemeral=True)
	view = GameChallengeView(interaction.user, opponent, "tic-tac-toe")
	await interaction.response.send_message(f"{opponent.mention}, {interaction.user.mention} has challenged you to Tic-Tac-Toe!", view=view)


@bot.tree.command(name="rps", description="Play Rock Paper Scissors")
@app_commands.check(game_channel_check)
async def rps(interaction: discord.Interaction, opponent: Optional[discord.Member] = None):
	if opponent is None:
		if bot.user is None:
			return await interaction.response.send_message("The bot is not ready yet.", ephemeral=True)
		target: discord.abc.User = bot.user
	else:
		target = opponent
	if target == interaction.user:
		return await interaction.response.send_message("Choose another player.", ephemeral=True)
	if target == bot.user:
		return await interaction.response.send_message("Choose privately. The bot chose after you.", view=RPSView(interaction.user, target))
	view = GameChallengeView(interaction.user, target, "rps")
	await interaction.response.send_message(f"{target.mention}, {interaction.user.mention} has challenged you to Rock Paper Scissors!", view=view)


@bot.tree.command(name="roulette", description="Play a harmless Russian Roulette round")
@app_commands.check(game_channel_check)
async def roulette(interaction: discord.Interaction):
	if random.randrange(6) == 0:
		await interaction.response.send_message(f"💥 {interaction.user.mention} lost Russian Roulette and was timed out for one minute.")
		try:
			await interaction.user.timeout(datetime.now(timezone.utc) + __import__("datetime").timedelta(minutes=1), reason="Russian Roulette game") # type: ignore
		except discord.HTTPException:
			pass
	else:
		await interaction.response.send_message(f"✅ {interaction.user.mention} won Russian Roulette and is safe!")


@bot.tree.command(name="trivia", description="Answer a random quiz question")
@app_commands.check(game_channel_check)
async def trivia(interaction: discord.Interaction):
	questions = [("What planet is known as the Red Planet?", ["Mars", "Venus", "Jupiter", "Saturn"], "Mars"), ("What is H2O?", ["Oxygen", "Water", "Hydrogen", "Salt"], "Water")]
	question, answers, correct = random.choice(questions)
	view = discord.ui.View(timeout=30)
	for answer in answers:
		button = discord.ui.Button(label=answer, style=discord.ButtonStyle.primary)
		async def callback(button_interaction, selected=answer):
			if selected == correct:
				change_balance(button_interaction.user.id, 25)
				result = f"🏆 Correct! {button_interaction.user.mention} wins and earns 25 coins."
			else:
				result = f"❌ {button_interaction.user.mention} lost this round. The answer was **{correct}**."
			await button_interaction.response.edit_message(content=result, view=None)
		button.callback = callback # type: ignore
		view.add_item(button)
	await interaction.response.send_message(f"**{question}**", view=view)


async def timed_guess(interaction, title, answer):
	await interaction.response.send_message(f"{title}\nFirst correct answer wins. You have 30 seconds.")
		
	try:
		message = await bot.wait_for("message", timeout=30, check=lambda item: item.channel.id == interaction.channel.id and not item.author.bot and item.content.lower().strip() == answer.lower())
		change_balance(message.author.id, 20)
		await interaction.channel.send(f"🏆 {message.author.mention} wins 20 coins!")
	except asyncio.TimeoutError:
		await interaction.channel.send(f"⌛ Time's up. No winner this round. The answer was **{answer}**.")


@bot.tree.command(name="guess", description="Guess a number from 1 to 100")
@app_commands.check(game_channel_check)
async def guess(interaction: discord.Interaction):
	await interaction.response.send_message("Guess a number from 1 to 100 in this channel. You have 60 seconds.")
	secret = random.randint(1, 100)
	end = asyncio.get_running_loop().time() + 60
	while asyncio.get_running_loop().time() < end:
		try:
			message = await bot.wait_for("message", timeout=max(0.1, end - asyncio.get_running_loop().time()), check=lambda item: item.channel.id == interaction.channel.id and item.content.isdigit()) # type: ignore
		except asyncio.TimeoutError:
			return await interaction.channel.send(f"⌛ Time's up. No winner this round. The number was {secret}.") # type: ignore
		value = int(message.content)
		if value == secret:
			change_balance(message.author.id, 30)
			return await interaction.channel.send(f"🏆 {message.author.mention} guessed it and wins 30 coins!") # type: ignore
		await message.reply("Higher!" if value < secret else "Lower!", delete_after=5)


@bot.tree.command(name="hangman", description="Start a hangman word game")
@app_commands.check(game_channel_check)
async def hangman(interaction: discord.Interaction):
	word = random.choice(HANGMAN_WORDS)
	guessed: set[str] = set()
	lives = 6
	message = await interaction.response.send_message(embed=discord.Embed(title="🎯 Hangman", description=f"**Word:** {format_hangman_state(word, guessed)}\n**Lives left:** {lives}\n**Guessed:** none", color=discord.Color.blurple()))

	while lives > 0:
		if not interaction.channel:
			return
		channel_id = interaction.channel.id
		try:
			incoming = await bot.wait_for("message", timeout=60, check=lambda item: bool(item.channel and item.channel.id == channel_id and not item.author.bot and item.content and item.content.lower().strip().isalpha() and len(item.content.strip()) == 1))
		except asyncio.TimeoutError:
			await interaction.followup.send(f"⏰ Hangman timed out. The word was **{word.upper()}**.")
			return

		guess = incoming.content.lower().strip()
		if guess in guessed:
			await incoming.reply("That letter was already guessed. Try another one.", delete_after=3)
			continue
		guessed.add(guess)
		if guess in word:
			masked = format_hangman_state(word, guessed)
			if "_" not in masked:
				await interaction.followup.send(f"🏆 {incoming.author.mention} solved the word **{word.upper()}** and won the game!")
				return
			await interaction.followup.send(embed=discord.Embed(title="🎯 Hangman", description=f"**Word:** {masked}\n**Lives left:** {lives}\n**Guessed:** {', '.join(sorted(guessed))}", color=discord.Color.green()))
		else:
			lives -= 1
			masked = format_hangman_state(word, guessed)
			if lives == 0:
				await interaction.followup.send(f"💥 {incoming.author.mention} ran out of lives. The word was **{word.upper()}**.")
				return
			await interaction.followup.send(embed=discord.Embed(title="🎯 Hangman", description=f"**Word:** {masked}\n**Lives left:** {lives}\n**Guessed:** {', '.join(sorted(guessed))}", color=discord.Color.orange()))


@bot.tree.command(name="connect-four", description="Play Connect Four on a 5x5 board")
@app_commands.check(game_channel_check)
async def connect_four(interaction: discord.Interaction, opponent: Optional[discord.Member] = None):
	if opponent is None or opponent == interaction.user or opponent.bot:
		return await interaction.response.send_message("Choose another human player to start a 5x5 Connect Four match.", ephemeral=True)
	view = GameChallengeView(interaction.user, opponent, "connect-four")
	await interaction.response.send_message(f"{opponent.mention}, {interaction.user.mention} has challenged you to Connect Four on a 5x5 board!\nFirst to connect 4 in a row wins.", view=view)


@bot.tree.command(name="wordle", description="Play a five-letter Wordle round")
@app_commands.check(game_channel_check)
async def wordle(interaction: discord.Interaction):
	word = random.choice(WORDLE_WORDS)
	attempts = 0
	await interaction.response.send_message(embed=discord.Embed(title="🟩 Wordle", description="Guess a five-letter word in chat. You have six tries.", color=discord.Color.blue()))

	while attempts < 6:
		if not interaction.channel:
			return
		channel_id = interaction.channel.id
		try:
			incoming = await bot.wait_for("message", timeout=60, check=lambda item: bool(item.channel and item.channel.id == channel_id and not item.author.bot and item.content and item.content.lower().strip().isalpha() and len(item.content.strip()) == 5))
		except asyncio.TimeoutError:
			await interaction.followup.send(f"⏰ Wordle timed out. The word was **{word.upper()}**.")
			return

		guess = incoming.content.lower().strip()
		attempts += 1
		feedback = format_wordle_feedback(word, guess)
		await interaction.followup.send(f"{incoming.author.mention} guess {attempts}/6: **{guess.upper()}**\n{feedback}")
		if guess == word:
			await interaction.followup.send(f"🏆 {incoming.author.mention} solved the Wordle! The word was **{word.upper()}**.")
			return
		if attempts == 6:
			await interaction.followup.send(f"❌ The Wordle is over. The word was **{word.upper()}**.")
			return


@bot.tree.command(name="slot", description="Spin the coin slots")
@app_commands.check(game_channel_check)
async def slot(interaction: discord.Interaction, bet: app_commands.Range[int, 1, 1000] = 10):
	starting_balance = balance(interaction.user.id)
	if starting_balance < bet:
		return await interaction.response.send_message("You cannot cover that bet.", ephemeral=True)
	icons = ["🍒", "🍋", "🔔", "💎"]
	result = [random.choice(icons) for _ in range(3)]
	win = bet * (10 if len(set(result)) == 1 else 2 if len(set(result)) == 2 else 0)
	change_balance(interaction.user.id, win - bet)
	ending_balance = starting_balance + win - bet
	jackpot = len(set(result)) == 1
	if jackpot:
		status = "JACKPOT!"
		color = discord.Color.gold()
	elif win:
		status = "WIN!"
		color = discord.Color.green()
	else:
		status = "No match"
		color = discord.Color.red()
	embed = discord.Embed(title="🎰 Coin Slots", description=f"{interaction.user.mention}\n# | {result[0]} | {result[1]} | {result[2]} |\n\n**{status}**", color=color)
	embed.add_field(name="Bet", value=f"{bet:,} coins", inline=True)
	embed.add_field(name="Payout", value=f"{win:,} coins", inline=True)
	embed.add_field(name="Balance", value=f"{ending_balance:,} coins", inline=True)
	embed.set_footer(text="Three matching symbols = 10x payout • Two matching symbols = 2x payout")
	await interaction.response.send_message(embed=embed)


@bot.tree.command(name="coinflip", description="Flip a coin")
@app_commands.check(game_channel_check)
@app_commands.describe(choice="Choose heads or tails", bet="Amount of coins to bet")
async def coinflip(interaction: discord.Interaction, choice: Literal["heads", "tails"], bet: app_commands.Range[int, 1, 1000] = 10):
	starting_balance = balance(interaction.user.id)
	if starting_balance < bet:
		return await interaction.response.send_message(f"You need {bet - starting_balance:,} more coins to place that bet.", ephemeral=True)
	result = random.choice(("heads", "tails"))
	won = choice == result
	payout = bet * 2 if won else 0
	change_balance(interaction.user.id, payout - bet)
	ending_balance = starting_balance + payout - bet
	embed = discord.Embed(
		title="🪙 Coin Flip",
		description=f"{interaction.user.mention}\nYour choice: **{choice.title()}**\nThe coin landed on: **{result.title()}**\n\n**{'You win!' if won else 'You lose!'}**",
		color=discord.Color.green() if won else discord.Color.red(),
	)
	embed.add_field(name="Bet", value=f"{bet:,} coins", inline=True)
	embed.add_field(name="Payout", value=f"{payout:,} coins", inline=True)
	embed.add_field(name="Balance", value=f"{ending_balance:,} coins", inline=True)
	embed.set_footer(text="Correct guesses pay 2x your bet")
	await interaction.response.send_message(embed=embed)


@bot.tree.command(name="roll", description="Roll a die")
@app_commands.check(game_channel_check)
async def roll(interaction: discord.Interaction, sides: app_commands.Range[int, 2, 100] = 6):
	await interaction.response.send_message(f"🎲 {interaction.user.mention} rolled **{random.randint(1, sides)}** (d{sides}).")


@bot.tree.command(name="blackjack", description="Play blackjack against the dealer")
@app_commands.check(game_channel_check)
async def blackjack(interaction: discord.Interaction):
	view = BlackjackView(interaction)
	initial_text = (
		"🃏 Blackjack rules: draw cards to reach 21 without going over. "
		"Hit for another card, or Stand to keep your total. The dealer must hit until 17."
	)
	await interaction.response.send_message(f"{initial_text}\n\n{interaction.user.mention}\n{view.describe_state()}", view=view)


@bot.tree.command(name="unscramble", description="Solve a scrambled word")
@app_commands.check(game_channel_check)
async def unscramble(interaction: discord.Interaction):
	word = random.choice(["python", "server", "button", "channel"])
	await timed_guess(interaction, f"Unscramble: **{' '.join(random.sample(list(word), len(word)))}**", word)


@bot.tree.command(name="emoji-quiz", description="Guess the movie from emojis")
@app_commands.check(game_channel_check)
async def emoji_quiz(interaction: discord.Interaction):
	await timed_guess(interaction, "Emoji quiz: 🦁 👑", "the lion king")


@bot.tree.command(name="truth-or-dare", description="Get a truth or dare prompt")
@app_commands.check(game_channel_check)
async def truth_or_dare(interaction: discord.Interaction):
	await interaction.response.send_message(random.choice(["Truth: What skill would you like to learn?", "Dare: Send a wholesome compliment to someone here."]))


@bot.tree.command(name="high-low", description="Guess whether the next card is higher or lower")
@app_commands.check(game_channel_check)
async def high_low(interaction: discord.Interaction, guess: str):
	first, second = random.randint(1, 13), random.randint(1, 13)
	correct = (guess.lower() == "high" and second > first) or (guess.lower() == "low" and second < first)
	await interaction.response.send_message(f"{interaction.user.mention}, card {first}, then {second}: {'🏆 Correct!' if correct else '❌ Wrong.'}")


@bot.tree.command(name="minefield", description="Clear a clickable minefield")
@app_commands.check(game_channel_check)
async def minefield(interaction: discord.Interaction):
	await interaction.response.send_message(f"{interaction.user.mention}, clear the field without hitting a mine.", view=MinefieldView(interaction.user))


@bot.tree.command(name="pokemon-guess", description="Guess the Pokemon from a hint")
@app_commands.check(game_channel_check)
async def pokemon_guess(interaction: discord.Interaction):
	questions = [
		("It is a yellow electric mouse.", "pikachu"),
		("It evolves from Charmander and has flames on its tail.", "charizard"),
		("It is a small blue water Pokemon that can hide in its shell.", "squirtle"),
		("It is a pink Pokemon known for singing opponents to sleep.", "jigglypuff"),
		("It is a sleepy Pokemon often found blocking paths.", "snorlax"),
		("It is a fox-like fire Pokemon with nine tails when fully evolved.", "ninetales"),
		("It is a ghost and poison Pokemon shaped like a ball with a mischievous grin.", "gengar"),
		("It is a small green Pokemon that evolves into Ivysaur.", "bulbasaur"),
		("It is a rare blue dragon Pokemon known for its powerful water attacks.", "gyarados"),
		("It is a yellow Pokemon with long ears and a lightning-shaped tail.", "pikachu"),
		("It is a psychic Pokemon with spoon-shaped weapons.", "alakazam"),
		("It is a fire Pokemon that resembles a pony.", "ponyta"),
		("It is a rock and ground Pokemon that looks like a boulder with arms.", "geodude"),
		("It is a water Pokemon that looks like a starfish.", "staryu"),
		("It is a butterfly-like bug and flying Pokemon with colorful wings.", "butterfree"),
		("It is a small electric Pokemon shaped like a mouse and has red cheeks.", "pichu"),
		("It is a fighting Pokemon famous for its spinning kicks.", "hitmonlee"),
		("It is an Eevee evolution with a blue body and water-based powers.", "vaporeon"),
		("It is an Eevee evolution with a black body and yellow rings.", "umbreon"),
		("It is a legendary ice Pokemon that resembles a large bird.", "articuno"),
	]
	hint, answer = random.choice(questions)
	await timed_guess(interaction, f"Who's that Pokemon? Hint: {hint}", answer)


@bot.tree.command(name="math-race", description="Solve a fast math problem")
@app_commands.check(game_channel_check)
async def math_race(interaction: discord.Interaction):
	left, right = random.randint(2, 20), random.randint(2, 20)
	await timed_guess(interaction, f"Math race: **{left} * {right}**", str(left * right))


@bot.tree.command(name="explore", description="Explore a text RPG dungeon")
@app_commands.check(game_channel_check)
@app_commands.checks.cooldown(1, 3600.0, key=lambda interaction: (interaction.guild_id, interaction.user.id))
async def explore(interaction: discord.Interaction):
	monster = random.choice(["slime", "skeleton", "cave bat"])
	reward = random.randint(20, 80)
	change_balance(interaction.user.id, reward)
	await interaction.response.send_message(f"🏆 {interaction.user.mention} defeated a **{monster}** and found **{reward} coins**.")


async def antinuke_guard_event(guild: discord.Guild, module: str, target_id: int):
	config = get_antinuke_config(guild.id)
	if not config[0]:
		return
	module_row = db("SELECT enabled FROM antinuke_modules WHERE guild_id=? AND module=?", (guild.id, module), True)
	if not module_row or not module_row[0][0]:
		return
	audit_action = {
		"channel_delete": discord.AuditLogAction.channel_delete,
		"role_delete": discord.AuditLogAction.role_delete,
		"channel_update": discord.AuditLogAction.channel_update,
		"role_update": discord.AuditLogAction.role_update,
		"guild_update": discord.AuditLogAction.guild_update,
		"member_ban": discord.AuditLogAction.ban,
		"member_kick": discord.AuditLogAction.kick,
	}[module]
	actor = None
	try:
		async for entry in guild.audit_logs(limit=5, action=audit_action):
			if entry.target and getattr(entry.target, "id", None) == target_id and (datetime.now(timezone.utc) - entry.created_at).total_seconds() <= 15:
				actor = entry.user
				break
	except (discord.Forbidden, discord.HTTPException):
		return
	if actor is None or (bot.user is not None and actor.id == bot.user.id):
		return
	member = guild.get_member(actor.id)
	if member is not None and not member.bot and (member.guild_permissions.administrator or member == guild.owner):
		return
	whitelisted = db("SELECT 1 FROM antinuke_whitelist WHERE guild_id=? AND user_id=?", (guild.id, actor.id), True)
	if whitelisted and not (member is not None and member.bot):
		return
	now = datetime.now(timezone.utc)
	db("INSERT INTO antinuke_events VALUES (?, ?, ?, ?)", (guild.id, actor.id, module, now.isoformat()))
	window = max(1, int(config[5]))
	cutoff = (now - timedelta(seconds=window)).isoformat()
	db("DELETE FROM antinuke_events WHERE created_at < ?", (cutoff,))
	limit_row = db("SELECT max_actions FROM antinuke_limits WHERE guild_id=? AND module=?", (guild.id, module), True)
	limit = min(limit_row[0][0], 1) if limit_row else 1
	count_row = db("SELECT COUNT(*) FROM antinuke_events WHERE guild_id=? AND user_id=? AND module=? AND created_at>=?", (guild.id, actor.id, module, cutoff), True)
	count = count_row[0][0] if count_row else 0
	log_channel = guild.get_channel(config[4]) if config[4] is not None else None
	if count < limit:
		if isinstance(log_channel, discord.TextChannel):
			event_embed = discord.Embed(title="Anti-nuke event detected", description=f"A `{module}` action was detected.", color=discord.Color.orange())
			event_embed.add_field(name="Actor", value=f"{actor.mention} (`{actor.id}`)", inline=True)
			event_embed.add_field(name="Activity", value=f"{count}/{limit} actions in {window}s", inline=True)
			event_embed.add_field(name="Status", value="Monitoring", inline=True)
			try:
				await log_channel.send(embed=event_embed)
			except discord.DiscordException:
				pass
		return
	if member is not None and member.bot:
		db("UPDATE antinuke_config SET lockdown=1 WHERE guild_id=?", (guild.id,))
		await set_antinuke_lockdown(guild, True)
		try:
			await member.edit(roles=[], reason=f"Anti-nuke: contain bot after {module}")
		except discord.HTTPException:
			pass
	if isinstance(member, discord.Member) and member != guild.owner and guild.me and member.top_role < guild.me.top_role:
		try:
			if config[2] == "ban":
				await member.ban(reason=f"Anti-nuke: {module} limit exceeded")
			elif config[2] == "kick":
				await member.kick(reason=f"Anti-nuke: {module} limit exceeded")
			elif config[2] == "quarantine" and config[3] is not None:
				role = guild.get_role(config[3])
				if role:
					await member.add_roles(role, reason=f"Anti-nuke: {module} limit exceeded")
				else:
					await member.kick(reason=f"Anti-nuke: {module} quarantine role unavailable")
			elif config[2] == "quarantine":
				await member.kick(reason=f"Anti-nuke: {module} quarantine role not configured")
		except discord.HTTPException:
			pass
	if config[1] and guild.id not in ANTI_NUKE_LOCKDOWNS:
		await set_antinuke_lockdown(guild, True)
	if config[4] is not None:
		if isinstance(log_channel, discord.TextChannel):
			embed = discord.Embed(title="Anti-nuke action", description=f"Detected repeated `{module}` actions.", color=discord.Color.red())
			embed.add_field(name="Actor", value=f"{actor.mention} (`{actor.id}`)", inline=True)
			embed.add_field(name="Count", value=f"{count} in {window}s", inline=True)
			embed.add_field(name="Punishment", value=config[2], inline=True)
			await log_channel.send(embed=embed)


@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
	await antinuke_guard_event(channel.guild, "channel_delete", channel.id)


@bot.event
async def on_guild_role_delete(role: discord.Role):
	await antinuke_guard_event(role.guild, "role_delete", role.id)


@bot.event
async def on_guild_channel_update(before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
	await antinuke_guard_event(after.guild, "channel_update", after.id)


@bot.event
async def on_guild_role_update(before: discord.Role, after: discord.Role):
	await antinuke_guard_event(after.guild, "role_update", after.id)


@bot.event
async def on_guild_update(before: discord.Guild, after: discord.Guild):
	await antinuke_guard_event(after, "guild_update", after.id)


@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
	await antinuke_guard_event(guild, "member_ban", user.id)


@bot.event
async def on_member_remove(member: discord.Member):
	await antinuke_guard_event(member.guild, "member_kick", member.id)


@bot.event
async def on_message_delete(message: discord.Message):
	await record_deleted_message(message, "Deleted manually or by a moderator")


@bot.event
async def on_message(message):
	if message.author.bot or not message.guild:
		return await bot.process_commands(message)
	if await scan_message_links(message):
		return
	if has_unauthorized_media_link(message):
		await record_deleted_message(message, "Media link requires configured role")
		await message.delete()
		try:
			await message.channel.send(f"{message.author.mention}, you do not have media-link access for that GIF, video, or photo link.", delete_after=8)
		except discord.HTTPException:
			pass
		return
	if media_only_enabled(message.guild.id, message.channel.id) and not has_media(message):
		await record_deleted_message(message, "Media-only channel")
		await message.delete()
		await warn_media_only(message)
		return
	if COUNTING_CHANNEL_ID and message.channel.id == COUNTING_CHANNEL_ID:
		row = db("SELECT last_number, last_user_id FROM counting WHERE guild_id=?", (message.guild.id,), True)
		last_number, last_user = row[0] if row else (0, None)
		value = calculate_count(message.content.strip())
		if value != last_number + 1 or last_user == message.author.id:
			await record_deleted_message(message, "Counting channel violation")
			await message.delete()
			return
		db("INSERT INTO counting(guild_id, channel_id, last_number, last_user_id) VALUES (?, ?, ?, ?) ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id, last_number=excluded.last_number, last_user_id=excluded.last_user_id", (message.guild.id, message.channel.id, value, message.author.id))
	await award_xp(message.author)
	await bot.process_commands(message)


@bot.event
async def on_member_join(member: discord.Member):
	config = get_welcome_config(member.guild.id)
	if not config:
		return
	channel = member.guild.get_channel(config["channel_id"])
	if not isinstance(channel, discord.TextChannel):
		return
	message = config["message"].format(
		user=member.mention,
		tusername=member.name,
		username=member.name,
		server=member.guild.name,
		member_count=member.guild.member_count,
	)
	custom_message = message
	if len(custom_message) > 180:
		custom_message = custom_message[:177].rstrip() + "..."
	accent = 0x7C5CFF
	embed = discord.Embed(
		title=f"✨ Welcome to {member.guild.name}!",
		description=f"{custom_message}\n\n**Member #{member.guild.member_count}** has joined the community.",
		color=discord.Color(accent),
		timestamp=datetime.now(timezone.utc),
	)
	embed.set_author(name="New arrival", icon_url=member.display_avatar.url)
	embed.set_thumbnail(url=member.display_avatar.url)
	embed.set_image(url=WELCOME_BANNER_URL)
	embed.add_field(name="Status", value="🎉 Ready to start the adventure!", inline=True)
	embed.add_field(name="Role", value="Member", inline=True)
	embed.add_field(name="Server", value=member.guild.name, inline=True)
	embed.set_footer(text=f"{member.name} joined the server • {datetime.now(timezone.utc).strftime('%b %d, %Y')}", icon_url=member.display_avatar.url)
	embed.colour = discord.Color(accent)
	try:
		await channel.send(embed=embed)
	except discord.HTTPException:
		pass


@bot.event
async def on_command_error(ctx, error):
	if isinstance(error, commands.CommandOnCooldown):
		await ctx.send(f"Try again in {error.retry_after / 3600:.1f} hours.")
	elif isinstance(error, commands.MissingPermissions):
		await ctx.send("You do not have permission to use that command.")
	elif isinstance(error, commands.CommandNotFound):
		return
	else:
		await ctx.send("Something went wrong. Check the bot console.")
		raise error


if not TOKEN:
	raise RuntimeError("Set the DISCORD_TOKEN environment variable first.")
bot.run(TOKEN)
