import os
import sqlite3
import asyncio
import discord
from discord import app_commands
from discord.ext import commands, tasks
from googleapiclient.discovery import build

# --- CONFIGURATION ---
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
MASTER_PASSWORD = os.getenv("MASTER_PASSWORD", "Pubstomped")

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect("data.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            channel_id TEXT PRIMARY KEY
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

def get_db():
    return sqlite3.connect("data.db")

# --- BOT SETUP ---
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)

# Track authenticated users in memory per session
authenticated_users = set()

# --- HELPER FUNCTIONS ---
def get_yt_stats(channel_id):
    """Fetches real-time statistics for a specific YouTube channel ID."""
    try:
        request = youtube.channels().list(
            part="snippet,statistics",
            id=channel_id
        )
        response = request.execute()
        if not response.get("items"):
            return None
        item = response["items"][0]
        return {
            "title": item["snippet"]["title"],
            "custom_url": item["snippet"].get("customUrl", ""),
            "thumbnail": item["snippet"]["thumbnails"]["high"]["url"],
            "subscribers": int(item["statistics"]["subscriberCount"]),
            "views": int(item["statistics"]["viewCount"]),
            "videos": int(item["statistics"]["videoCount"])
        }
    except Exception as e:
        print(f"YouTube API Error: {e}")
        return None

def create_stats_embed(stats):
    """Generates a clean Discord Embed for channel statistics."""
    url = f"https://www.youtube.com/{stats['custom_url']}" if stats['custom_url'] else "https://www.youtube.com"
    embed = discord.Embed(
        title=f"📊 {stats['title']}",
        url=url,
        color=discord.Color.from_rgb(255, 0, 0)  # YouTube Red
    )
    embed.set_thumbnail(url=stats["thumbnail"])
    embed.add_field(name="👥 Subscribers", value=f"**{stats['subscribers']:,}**", inline=True)
    embed.add_field(name="👁️ Total Views", value=f"**{stats['views']:,}**", inline=True)
    embed.add_field(name="🎬 Uploads", value=f"**{stats['videos']:,}**", inline=True)
    embed.set_footer(
        text="YouTube Real-Time Tracker • Railway Hosted",
        icon_url="https://www.youtube.com/s/desktop/d743f786/img/favicon.ico"
    )
    return embed

def check_auth(interaction: discord.Interaction) -> bool:
    return interaction.user.id in authenticated_users

# --- COMMANDS ---

@bot.tree.command(name="master_auth", description="Authenticate with password to unlock administrative access.")
@app_commands.describe(password="Master password")
async def master_auth(interaction: discord.Interaction, password: str):
    if password == MASTER_PASSWORD:
        authenticated_users.add(interaction.user.id)
        await interaction.response.send_message("✅ Access granted. You can now use administrative bot commands.", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Incorrect password. Access denied.", ephemeral=True)

@bot.tree.command(name="add_channel", description="Add a YouTube channel ID to track.")
@app_commands.describe(channel_id="The YouTube Channel ID (e.g. UC...)")
async def add_channel(interaction: discord.Interaction, channel_id: str):
    if not check_auth(interaction):
        return await interaction.response.send_message("🔒 Unauthorized! Authenticate with `/master_auth` first.", ephemeral=True)
    
    stats = get_yt_stats(channel_id)
    if not stats:
        return await interaction.response.send_message("❌ Invalid YouTube Channel ID or channel not found.", ephemeral=True)
    
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO channels (channel_id) VALUES (?)", (channel_id,))
        conn.commit()
        await interaction.response.send_message(f"✅ Successfully added **{stats['title']}** (`{channel_id}`) to tracking list.", ephemeral=True)
    except sqlite3.IntegrityError:
        await interaction.response.send_message("⚠️ Channel is already being tracked.", ephemeral=True)
    finally:
        conn.close()

@bot.tree.command(name="delete_channel", description="Remove a YouTube channel from tracking.")
@app_commands.describe(channel_id="The YouTube Channel ID to remove")
async def delete_channel(interaction: discord.Interaction, channel_id: str):
    if not check_auth(interaction):
        return await interaction.response.send_message("🔒 Unauthorized! Authenticate with `/master_auth` first.", ephemeral=True)

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM channels WHERE channel_id = ?", (channel_id,))
    if cursor.rowcount > 0:
        conn.commit()
        await interaction.response.send_message(f"🗑️ Removed channel `{channel_id}` from tracking list.", ephemeral=True)
    else:
        await interaction.response.send_message("⚠️ Channel ID not found in database.", ephemeral=True)
    conn.close()

@bot.tree.command(name="set_channel", description="Set the Discord channel for background announcements.")
@app_commands.describe(target_channel="Discord Text Channel for automatic updates")
async def set_channel(interaction: discord.Interaction, target_channel: discord.TextChannel):
    if not check_auth(interaction):
        return await interaction.response.send_message("🔒 Unauthorized! Authenticate with `/master_auth` first.", ephemeral=True)

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('announcement_channel', ?)", (str(target_channel.id),))
    conn.commit()
    conn.close()
    
    await interaction.response.send_message(f"📢 Target announcement channel set to {target_channel.mention}.", ephemeral=True)

@bot.tree.command(name="updates", description="Set periodic update interval (4 or 8 hours).")
@app_commands.describe(interval="Select update interval in hours")
@app_commands.choices(interval=[
    app_commands.Choice(name="Every 4 hours", value=4),
    app_commands.Choice(name="Every 8 hours", value=8)
])
async def updates(interaction: discord.Interaction, interval: app_commands.Choice[int]):
    if not check_auth(interaction):
        return await interaction.response.send_message("🔒 Unauthorized! Authenticate with `/master_auth` first.", ephemeral=True)

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('update_interval', ?)", (str(interval.value),))
    conn.commit()
    conn.close()

    background_updates.change_interval(hours=interval.value)
    await interaction.response.send_message(f"⏱️ Periodic updates scheduled every **{interval.value} hours**.", ephemeral=True)

@bot.tree.command(name="force", description="Fetch and display stats for all tracked channels immediately in real time.")
async def force(interaction: discord.Interaction):
    await interaction.response.defer()
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT channel_id FROM channels")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return await interaction.followup.send("⚠️ No YouTube channels configured. Add one using `/add_channel`.")

    embeds = []
    for row in rows:
        stats = get_yt_stats(row[0])
        if stats:
            embeds.append(create_stats_embed(stats))

    if embeds:
        await interaction.followup.send(embeds=embeds)
    else:
        await interaction.followup.send("❌ Failed to fetch channel statistics.")

# --- BACKGROUND TASK ---
@tasks.loop(hours=4)
async def background_updates():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT value FROM config WHERE key = 'announcement_channel'")
    target_row = cursor.fetchone()
    if not target_row:
        conn.close()
        return
    
    target_channel_id = int(target_row[0])
    channel = bot.get_channel(target_channel_id)
    if not channel:
        conn.close()
        return

    cursor.execute("SELECT channel_id FROM channels")
    rows = cursor.fetchall()
    conn.close()

    for row in rows:
        stats = get_yt_stats(row[0])
        if stats:
            embed = create_stats_embed(stats)
            await channel.send(embed=embed)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    await bot.tree.sync()
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM config WHERE key = 'update_interval'")
    row = cursor.fetchone()
    conn.close()
    
    if row:
        hours = int(row[0])
        background_updates.change_interval(hours=hours)

    if not background_updates.is_running():
        background_updates.start()

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
