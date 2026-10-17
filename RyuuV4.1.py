import discord
from discord.ext import commands
import asyncio
import random
import os
import time
import json

TOKEN = os.getenv("DISCORD_TOKEN", "PUT_YOUR_TOKEN_HERE")
PREFIX = ","
SETTINGS_FILE = "settings.json"
START_TIME = time.monotonic()

def load_lines(path):
    if not path.endswith(".txt"):
        path += ".txt"
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return [l.strip() for l in f if l.strip()]
    except Exception as e:
        print(f"[load error {path}] {e}")
        return []

def normalize_filename(name, default):
    if not name:
        return default
    if not name.endswith(".txt"):
        name += ".txt"
    return name

DEFAULT_SETTINGS = {
    "global": {
        "case": "M",
        "format": "none",
        "format_chance": 100,
    },
}

def load_settings():
    if not os.path.exists(SETTINGS_FILE):
        return json.loads(json.dumps(DEFAULT_SETTINGS))
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for section, values in DEFAULT_SETTINGS.items():
            if section not in data:
                data[section] = values
            else:
                for k, v in values.items():
                    if k not in data[section]:
                        data[section][k] = v
        return data
    except Exception as e:
        print(f"[settings load error] {e}")
        return json.loads(json.dumps(DEFAULT_SETTINGS))

def save_settings():
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except Exception as e:
        print(f"[settings save error] {e}")

settings = load_settings()

# =========================================================
# ICON PATCH — makes Discord show the PC/desktop icon instead of web globe / phone
# Injects Windows client properties into the gateway IDENTIFY packet.
# =========================================================
try:
    import discord.gateway as _gw_icon
    import json as _json_icon

    _orig_gw_send = _gw_icon.DiscordWebSocket.send

    async def _patched_gw_send(self, data, **kwargs):
        try:
            if isinstance(data, str):
                parsed = _json_icon.loads(data)
                if parsed.get("op") == 2:  # IDENTIFY
                    d = parsed.get("d", {})
                    d["properties"] = {
                        "$os": "Windows",
                        "$browser": "Discord Client",
                        "$device": "",
                        "$system_locale": "en-US",
                        "$browser_user_agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "discord/1.0.9170 Chrome/128.0.6613.186 "
                            "Electron/32.2.7 Safari/537.36"
                        ),
                        "$browser_version": "32.2.7",
                        "$os_version": "10.0.19045",
                        "$referrer": "",
                        "$referring_domain": "",
                        "$referrer_current": "",
                        "$referring_domain_current": "",
                        "$release_channel": "stable",
                        "$client_build_number": 369571,
                        "$client_event_source": None,
                        "$design_id": 0,
                    }
                    parsed["d"] = d
                    data = _json_icon.dumps(parsed)
        except Exception:
            pass
        return await _orig_gw_send(self, data, **kwargs)

    _gw_icon.DiscordWebSocket.send = _patched_gw_send
except Exception:
    pass

bot = commands.Bot(command_prefix=PREFIX, self_bot=True, help_command=None)

class TaskManager:
    def __init__(self):
        self.tasks = {}
        self.states = {}

    def register(self, key, task):
        self.cancel(key)
        self.tasks[key] = task
        self.states[key] = "running"

    def cancel(self, key):
        t = self.tasks.get(key)
        if t and not t.done():
            t.cancel()
        self.tasks[key] = None
        self.states[key] = "stopped"

    def pause(self, key):
        if self.states.get(key) == "running":
            self.states[key] = "paused"
            return True
        return False

    def resume(self, key):
        if self.states.get(key) == "paused":
            self.states[key] = "running"
            return True
        return False

    def is_running(self, key):
        t = self.tasks.get(key)
        return t is not None and not t.done() and self.states.get(key) == "running"

    def is_paused(self, key):
        return self.states.get(key) == "paused"

    def status(self, key):
        return self.states.get(key, "stopped")

tm = TaskManager()

def apply_case(text, mode):
    mode = (mode or "M").upper()
    if mode == "U": return text.upper()
    if mode == "L": return text.lower()
    if mode == "M": return "".join(random.choice([c.upper(), c.lower()]) for c in text)
    return text

def apply_format(text, fmt):
    if fmt == "bold": return f"**{text}**"
    if fmt == "italic": return f"*{text}*"
    if fmt == "code": return f"`{text}`"
    if fmt == "codeblock": return f"```\n{text}\n```"
    if fmt == "header": return f"# {text}"
    if fmt == "quote": return f"> {text}"
    if fmt == "spoiler": return f"||{text}||"
    return text

def resolve_case(feature):
    override = settings.get(feature, {}).get("case", "global")
    if override == "global":
        return settings["global"]["case"]
    return override

def resolve_format(feature):
    override = settings.get(feature, {}).get("format", "global")
    if override == "global":
        return settings["global"]["format"]
    return override

def resolve_format_chance(feature):
    override = settings.get(feature, {}).get("format_chance", None)
    if override is None or override == "global":
        return settings["global"]["format_chance"]
    return override

async def safe_send(channel, content=None, **kwargs):
    try: return await channel.send(content, **kwargs)
    except Exception as e: print(f"[send fail] {e}")

async def safe_edit(channel, **kwargs):
    try: return await channel.edit(**kwargs)
    except Exception as e: print(f"[edit fail] {e}")

async def send_typing(channel, duration: float = 1.5):
    """Keeps the typing bubble alive for `duration` seconds.
    Discord clears it after ~10s or on next message send.
    Calling every 8s keeps it visible without spamming the API."""
    try:
        await channel.trigger_typing()
    except Exception as e:
        print(f"[typing fail] {e}")
        return
    # Discord's typing bubble lasts ~10s; refresh at 8s if duration > 8
    elapsed = 0.0
    while elapsed < duration:
        step = min(8.0, duration - elapsed)
        await asyncio.sleep(step)
        elapsed += step
        if elapsed < duration:
            try:
                await channel.trigger_typing()
            except Exception:
                pass

def cancel_all():
    for key in list(tm.tasks.keys()):
        tm.cancel(key)

def build_tag_prefix(feature):
    cfg = settings.get(feature, {})
    mode = cfg.get("tag_mode", "none")
    ids = cfg.get("tag_ids", [])
    if mode == "none" or not ids:
        return ""
    if mode == "single":
        return f"<@{ids[0]}> "
    return " ".join(f"<@{uid}>" for uid in ids) + " "

@bot.event
async def on_ready():
    print("Ryuu SelfBot")
    print(f"Logged in as {bot.user} ({bot.user.id})")
    print(f"Prefix: {bot.command_prefix}")

HELP_MAIN = """```
--- RyuuV4 Selfbot ---

`,help`           -> this menu
`,help <topic>`   -> sub commands for a feature
`,files`          -> list .txt files

─── SENDERS ────────────────────────────────
  ab     -> autobeef
  al     -> autoladder
  ak     -> autokill
  press  -> press engine + sets
  spam   -> spam (single msg loop)
  ap     -> autopaste
  apsess -> named autopaste sessions
  stam   -> stam (numbered)
  oneshot-> burst / ladder / pastewl
  wslots -> wordlist slots 1-5 per engine
  ac     -> autocount
  acmode -> autocount styles
  ackey  -> stop AC on keyword

─── CHAT TOOLS ─────────────────────────────
  mr     -> multi-react (self + per-user)
  rct    -> autoreact (single emoji)
  ar     -> autoreply
  afk    -> afk
  aafk   -> antiafk
  recite -> quote + insult loop
  trigger-> word → command
  tools  -> snipe / watch / ghost ping
  spy    -> spy / shush / kill
  pretype-> typing bubble per channel

─── GC / SERVER ────────────────────────────
  gc     -> gcname rotation
  kgc    -> killgc
  gcx    -> gclock / gcmode / agct
  agc    -> antigc
  srv    -> perms/roles/invites/emoji

─── FILES / DATA ───────────────────────────
  purge  -> delete your msgs
  arc    -> archive chat to html
  wl     -> wordlist tools
  imp    -> import .txt files
  scan   -> file & url scanner

─── TIME / NOTES / LOOKUP ──────────────────
  time   -> remind / schedule / todo / notes
  look   -> calc / weather / cur / crypto /
            define / wiki / urban / translate
  date / uptime / stats / logs

─── PRESENCE / MEDIA ───────────────────────
  st     -> status changer
  rpc    -> rich presence
  psp    -> ping / stream / prefix
  dl     -> video / audio downloader

─── INTEGRATIONS ───────────────────────────
  ai     -> AI chat + lore
  macro  -> countdown / vanity / snapshot
  hook   -> webhooks + embeds
  panel  -> HTTP panel + console
  prof   -> settings profiles ```
 ``` restart-> persistence / default ch

─── SETUP ──────────────────────────────────
  alias  -> command aliases
  global -> global case/format
  setchannel -> default channel
  files / prefix
  pace   -> rate limit + rename spacing

─── DIAGNOSTICS ────────────────────────────
  status / ping / uptime / stats / logs``` """
HELP_GLOBAL = """```
--- Global Settings ---

  ,case <u/l/m>            global case
  ,format <mode>           global format
  ,formatchance <0-100>    global format chance
  ,settings                show current global settings
  ,resetglobal             reset global to defaults
```"""
@bot.command(name="help", aliases=["h", "menu"])
async def cmd_help(ctx, topic: str = None):
    if not topic:
        for part in chunk_text(HELP_MAIN, 1900):
            await safe_send(ctx.channel, part)
            await asyncio.sleep(0.3)
    else:
        t = topic.lower()
        if t in ("ab", "autobeef", "beef"):
            await safe_send(ctx.channel, HELP_AB)
        elif t in ("al", "autoladder", "ladder"):
            await safe_send(ctx.channel, HELP_AL)
        elif t in ("ak", "autokill", "kill"):
            await safe_send(ctx.channel, HELP_AK)
        elif t in ("spam",):
            await safe_send(ctx.channel, HELP_SPAM)
        elif t in ("ap", "autopaste", "paste"):
            await safe_send(ctx.channel, HELP_AP)
        elif t in ("stam",):
            await safe_send(ctx.channel, HELP_STAM)
        elif t in ("ac", "autocount", "count"):
            await safe_send(ctx.channel, HELP_AC)
        elif t in ("gc", "gcname"):
            await safe_send(ctx.channel, HELP_GC)
        elif t in ("kgc", "killgc"):
            await safe_send(ctx.channel, HELP_KILLGC)
        elif t in ("agc", "antigc"):
            await safe_send(ctx.channel, HELP_ANTIGC)
        elif t in ("ar", "autoreply", "reply"):
            await safe_send(ctx.channel, HELP_REPLY)
        elif t in ("rct", "react", "autoreact"):
            await safe_send(ctx.channel, HELP_REACT)
        elif t in ("afk",):
            await safe_send(ctx.channel, HELP_AFK)
        elif t in ("aafk", "antiafk"):
            await safe_send(ctx.channel, HELP_ANTIAFK)
        elif t in ("alias", "aliases"):
            await safe_send(ctx.channel, HELP_ALIAS)
        elif t in ("psp", "ping", "stream", "prefix"):
            await safe_send(ctx.channel, HELP_PSP)
        elif t in ("st", "status"):
            await safe_send(ctx.channel, HELP_STATUS)
        elif t in ("global", "settings"):
            await safe_send(ctx.channel, HELP_GLOBAL)
        elif t in EXTRA_HELP_ALIASES:
            await safe_send(ctx.channel, EXTRA_HELP[EXTRA_HELP_ALIASES[t]])
        else:
            await safe_send(ctx.channel, f"Unknown topic: {topic}\nTry ,help for a list.")
    try: await ctx.message.delete()
    except: pass

@bot.command(name="case")
async def cmd_case(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Global case: {settings['global']['case']}")
    mode = mode.upper()
    if mode not in ("U", "L", "M"):
        return await safe_send(ctx.channel, "Use U, L, or M.")
    settings["global"]["case"] = mode
    save_settings()
    await safe_send(ctx.channel, f"Global case set to {mode}.")

@bot.command(name="format")
async def cmd_format(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Global format: {settings['global']['format']}")
    valid = ("none", "bold", "italic", "code", "codeblock", "header", "quote", "spoiler")
    if mode.lower() not in valid:
        return await safe_send(ctx.channel, f"Valid: {', '.join(valid)}")
    settings["global"]["format"] = mode.lower()
    save_settings()
    await safe_send(ctx.channel, f"Global format set to {mode.lower()}.")

@bot.command(name="formatchance")
async def cmd_formatchance(ctx, pct: int = None):
    if pct is None:
        return await safe_send(ctx.channel, f"Global format chance: {settings['global']['format_chance']}%")
    if not (0 <= pct <= 100):
        return await safe_send(ctx.channel, "Use 0-100.")
    settings["global"]["format_chance"] = pct
    save_settings()
    await safe_send(ctx.channel, f"Global format chance: {pct}%")

@bot.command(name="settings")
async def cmd_settings(ctx):
    g = settings["global"]
    msg = (
        f"```\n--- Global Settings ---\n"
        f"Case:          {g['case']}\n"
        f"Format:        {g['format']}\n"
        f"Format chance: {g['format_chance']}%\n```"
    )
    await safe_send(ctx.channel, msg)

@bot.command(name="resetglobal")
async def cmd_resetglobal(ctx):
    settings["global"] = json.loads(json.dumps(DEFAULT_SETTINGS["global"]))
    save_settings()
    await safe_send(ctx.channel, "Global settings reset.")

@bot.command(name="files")
async def cmd_files(ctx):
    txts = sorted([f for f in os.listdir(".") if f.endswith(".txt")])
    if not txts:
        return await safe_send(ctx.channel, "No .txt files in the bot folder.")
    await safe_send(ctx.channel, "Available .txt files:\n" + "\n".join(f"- {f}" for f in txts))

# =========================================================
# AUTOBEEF
# =========================================================
DEFAULT_SETTINGS["autobeef"] = {
    "file": "autobeef.txt",
    "delay": 1.2,
    "case": "global",
    "format": "global",
    "format_chance": 100,
    "bold_chance": 0,
    "header_chance": 0,
    "tag_mode": "none",
    "tag_ids": [],
    "burst": 1,
    "burst_gap": 0.3,
    "typing_indicator": True,
    "random_order": True,
    "loop": True,
}

# Merge into loaded settings so upgrades don't wipe it
if "autobeef" not in settings:
    settings["autobeef"] = json.loads(json.dumps(DEFAULT_SETTINGS["autobeef"]))
else:
    for k, v in DEFAULT_SETTINGS["autobeef"].items():
        if k not in settings["autobeef"]:
            settings["autobeef"][k] = v
save_settings()

HELP_AB = """```
--- Autobeef Sub-Commands ---

Control:
  ,ab [channel_id]         start
  ,abstop                  stop
  ,abpause / ,abresume     pause / resume
  ,abinfo                  show current settings
  ,abreset                 reset to defaults

File:
  ,abfile <name>           change wordlist
  ,abfiles                 list .txt files

Delay:
  ,abdelay <seconds>

Formatting:
  ,abcase <u/l/m/global>
  ,abformat <mode>
  ,abformatchance <0-100>
  ,abbold <0-100>
  ,abheader <0-100>

Tagging:
  ,abtag @user             add tag target
  ,abuntag @user           remove
  ,abcleartags             clear all
  ,abtagmode <none/single/multi>

Burst:
  ,abbust <n>
  ,abbustgap <seconds>

Other:
  ,abtyping on/off
  ,abshuffle on/off
  ,abloop on/off
```"""

async def autobeef_loop(channel, key="autobeef"):
    ab = settings["autobeef"]
    if not load_lines(ab["file"]):
        await safe_send(channel, f"[autobeef] {ab['file']} is empty or missing.")
        return
    while True:
        try:
            if tm.is_paused(key):
                await asyncio.sleep(0.5)
                continue
            lines = load_lines(ab["file"])
            if not lines:
                await asyncio.sleep(2)
                continue
            if ab["random_order"]:
                random.shuffle(lines)
            for raw in lines:
                while tm.is_paused(key):
                    await asyncio.sleep(0.3)
                text = apply_case(raw, resolve_case("autobeef"))
                fmt = resolve_format("autobeef")
                chance = resolve_format_chance("autobeef")
                if fmt != "none" and random.randint(1, 100) <= chance:
                    text = apply_format(text, fmt)
                bc = ab.get("bold_chance", 0)
                if bc > 0 and random.randint(1, 100) <= bc:
                    text = f"**{text}**"
                hc = ab.get("header_chance", 0)
                if hc > 0 and random.randint(1, 100) <= hc:
                    text = f"# {text}"
                text = tag_join(text, build_tag_prefix("autobeef"))
                if ab.get("typing_indicator", True):
                    await send_typing(channel)
                    await asyncio.sleep(random.uniform(0.3, 0.8))
                burst = max(1, int(ab.get("burst", 1)))
                for _ in range(burst):
                    await safe_send(channel, text)
                    if burst > 1:
                        await asyncio.sleep(ab.get("burst_gap", 0.3))
                await asyncio.sleep(ab.get("delay", 1.2))
            if not ab.get("loop", True):
                break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[autobeef error] {e}")
            await asyncio.sleep(3)

@bot.command(name="ab", aliases=["autobeef"])
async def cmd_ab(ctx, channel_id: int = None):
    if not channel_id:
        return await safe_send(ctx.channel, "Usage: ,ab <channel_id>")
    ch = bot.get_channel(channel_id)
    if not ch:
        return await safe_send(ctx.channel, "Channel not found.")
    tm.cancel("autobeef")
    task = asyncio.create_task(autobeef_loop(ch))
    tm.register("autobeef", task)
    await safe_send(ctx.channel, f"autobeef started in {channel_id} using {settings['autobeef']['file']}.")

@bot.command(name="abstop")
async def cmd_abstop(ctx):
    tm.cancel("autobeef")
    await safe_send(ctx.channel, "autobeef stopped.")

@bot.command(name="abpause")
async def cmd_abpause(ctx):
    if tm.pause("autobeef"):
        await safe_send(ctx.channel, "autobeef paused.")
    else:
        await safe_send(ctx.channel, "autobeef is not running.")

@bot.command(name="abresume")
async def cmd_abresume(ctx):
    if tm.resume("autobeef"):
        await safe_send(ctx.channel, "autobeef resumed.")
    else:
        await safe_send(ctx.channel, "autobeef is not paused.")

@bot.command(name="abinfo")
async def cmd_abinfo(ctx):
    ab = settings["autobeef"]
    tags = ab.get("tag_ids", [])
    tag_str = ", ".join(f"<@{u}>" for u in tags) if tags else "none"
    msg = (
        f"```\n--- Autobeef Settings ---\n"
        f"Status:        {tm.status('autobeef')}\n"
        f"File:          {ab['file']}\n"
        f"Delay:         {ab['delay']}s\n"
        f"Case:          {ab['case']}\n"
        f"Format:        {ab['format']} (chance {ab['format_chance']}%)\n"
        f"Bold chance:   {ab['bold_chance']}%\n"
        f"Header chance: {ab['header_chance']}%\n"
        f"Tag mode:      {ab['tag_mode']}\n"
        f"Tag list:      {tag_str}\n"
        f"Burst:         {ab['burst']} (gap {ab['burst_gap']}s)\n"
        f"Typing ind.:   {ab['typing_indicator']}\n"
        f"Random order:  {ab['random_order']}\n"
        f"Loop:          {ab['loop']}\n```"
    )
    await safe_send(ctx.channel, msg)

@bot.command(name="abreset")
async def cmd_abreset(ctx):
    settings["autobeef"] = json.loads(json.dumps(DEFAULT_SETTINGS["autobeef"]))
    save_settings()
    await safe_send(ctx.channel, "autobeef settings reset to defaults.")

@bot.command(name="abfile")
async def cmd_abfile(ctx, filename: str = None):
    if not filename:
        return await safe_send(ctx.channel, f"Current file: {settings['autobeef']['file']}")
    fname = normalize_filename(filename, "autobeef.txt")
    if not os.path.exists(fname):
        return await safe_send(ctx.channel, f"{fname} not found.")
    settings["autobeef"]["file"] = fname
    save_settings()
    await safe_send(ctx.channel, f"autobeef file set to {fname}.")

@bot.command(name="abfiles")
async def cmd_abfiles(ctx):
    txts = sorted([f for f in os.listdir(".") if f.endswith(".txt")])
    if not txts:
        return await safe_send(ctx.channel, "No .txt files.")
    await safe_send(ctx.channel, "Available:\n" + "\n".join(f"- {f}" for f in txts))

@bot.command(name="abdelay")
async def cmd_abdelay(ctx, seconds: float = None):
    if seconds is None:
        return await safe_send(ctx.channel, f"Current delay: {settings['autobeef']['delay']}s")
    if seconds < 0.1:
        return await safe_send(ctx.channel, "Min delay is 0.1s.")
    settings["autobeef"]["delay"] = seconds
    save_settings()
    await safe_send(ctx.channel, f"autobeef delay set to {seconds}s.")

@bot.command(name="abcase")
async def cmd_abcase(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Current case: {settings['autobeef']['case']}")
    mode = mode.lower()
    if mode not in ("u", "l", "m", "global"):
        return await safe_send(ctx.channel, "Use u, l, m, or global.")
    settings["autobeef"]["case"] = mode.upper() if mode != "global" else "global"
    save_settings()
    await safe_send(ctx.channel, f"autobeef case set to {settings['autobeef']['case']}.")

@bot.command(name="abformat")
async def cmd_abformat(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Current format: {settings['autobeef']['format']}")
    valid = ("none", "bold", "italic", "code", "codeblock", "header", "quote", "spoiler", "global")
    if mode.lower() not in valid:
        return await safe_send(ctx.channel, f"Valid: {', '.join(valid)}")
    settings["autobeef"]["format"] = mode.lower()
    save_settings()
    await safe_send(ctx.channel, f"autobeef format set to {mode.lower()}.")

@bot.command(name="abformatchance")
async def cmd_abformatchance(ctx, pct: int = None):
    if pct is None:
        return await safe_send(ctx.channel, f"Current format chance: {settings['autobeef']['format_chance']}%")
    if not (0 <= pct <= 100):
        return await safe_send(ctx.channel, "Use 0-100.")
    settings["autobeef"]["format_chance"] = pct
    save_settings()
    await safe_send(ctx.channel, f"autobeef format chance: {pct}%")

@bot.command(name="abbold")
async def cmd_abbold(ctx, pct: int = None):
    if pct is None:
        return await safe_send(ctx.channel, f"Current bold chance: {settings['autobeef']['bold_chance']}%")
    if not (0 <= pct <= 100):
        return await safe_send(ctx.channel, "Use 0-100.")
    settings["autobeef"]["bold_chance"] = pct
    save_settings()
    await safe_send(ctx.channel, f"autobeef bold chance: {pct}%")

@bot.command(name="abheader")
async def cmd_abheader(ctx, pct: int = None):
    if pct is None:
        return await safe_send(ctx.channel, f"Current header chance: {settings['autobeef']['header_chance']}%")
    if not (0 <= pct <= 100):
        return await safe_send(ctx.channel, "Use 0-100.")
    settings["autobeef"]["header_chance"] = pct
    save_settings()
    await safe_send(ctx.channel, f"autobeef header chance: {pct}%")

@bot.command(name="abtag")
async def cmd_abtag(ctx, target: str = None):
    if not target:
        return await safe_send(ctx.channel, "Usage: ,abtag @user OR ,abtag <user_id>")
    uid = None
    if ctx.message.mentions:
        uid = ctx.message.mentions[0].id
    else:
        try: uid = int(target.strip("<@!>"))
        except: return await safe_send(ctx.channel, "Invalid user.")
    ids = settings["autobeef"]["tag_ids"]
    if uid in ids:
        return await safe_send(ctx.channel, f"<@{uid}> already in tag list.")
    ids.append(uid)
    if settings["autobeef"]["tag_mode"] == "none":
        settings["autobeef"]["tag_mode"] = "single"
    save_settings()
    await safe_send(ctx.channel, f"Added <@{uid}>. Mode: {settings['autobeef']['tag_mode']}")

@bot.command(name="abuntag")
async def cmd_abuntag(ctx, target: str = None):
    if not target:
        return await safe_send(ctx.channel, "Usage: ,abuntag @user OR ,abuntag <user_id>")
    uid = None
    if ctx.message.mentions:
        uid = ctx.message.mentions[0].id
    else:
        try: uid = int(target.strip("<@!>"))
        except: return await safe_send(ctx.channel, "Invalid user.")
    ids = settings["autobeef"]["tag_ids"]
    if uid not in ids:
        return await safe_send(ctx.channel, "Not in tag list.")
    ids.remove(uid)
    if not ids:
        settings["autobeef"]["tag_mode"] = "none"
    save_settings()
    await safe_send(ctx.channel, f"Removed <@{uid}>. Remaining: {len(ids)}")

@bot.command(name="abcleartags")
async def cmd_abcleartags(ctx):
    settings["autobeef"]["tag_ids"] = []
    settings["autobeef"]["tag_mode"] = "none"
    save_settings()
    await safe_send(ctx.channel, "Tag list cleared.")

@bot.command(name="abtagmode")
async def cmd_abtagmode(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Current tag mode: {settings['autobeef']['tag_mode']}")
    if mode.lower() not in ("none", "single", "multi"):
        return await safe_send(ctx.channel, "Use none, single, or multi.")
    settings["autobeef"]["tag_mode"] = mode.lower()
    save_settings()
    await safe_send(ctx.channel, f"Tag mode: {mode.lower()}")

@bot.command(name="abbust")
async def cmd_abbust(ctx, n: int = None):
    if n is None:
        return await safe_send(ctx.channel, f"Current burst: {settings['autobeef']['burst']}")
    if not (1 <= n <= 50):
        return await safe_send(ctx.channel, "Use 1-50.")
    settings["autobeef"]["burst"] = n
    save_settings()
    await safe_send(ctx.channel, f"Burst set to {n}.")

@bot.command(name="abbustgap")
async def cmd_abbustgap(ctx, seconds: float = None):
    if seconds is None:
        return await safe_send(ctx.channel, f"Current burst gap: {settings['autobeef']['burst_gap']}s")
    if seconds < 0:
        return await safe_send(ctx.channel, "Must be >= 0.")
    settings["autobeef"]["burst_gap"] = seconds
    save_settings()
    await safe_send(ctx.channel, f"Burst gap: {seconds}s")

@bot.command(name="abtyping")
async def cmd_abtyping(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Typing indicator: {settings['autobeef']['typing_indicator']}")
    val = mode.lower() == "on"
    settings["autobeef"]["typing_indicator"] = val
    save_settings()
    await safe_send(ctx.channel, f"Typing indicator: {'ON' if val else 'OFF'}")

@bot.command(name="abshuffle")
async def cmd_abshuffle(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Random order: {settings['autobeef']['random_order']}")
    val = mode.lower() == "on"
    settings["autobeef"]["random_order"] = val
    save_settings()
    await safe_send(ctx.channel, f"Random order: {'ON' if val else 'OFF'}")

@bot.command(name="abloop")
async def cmd_abloop(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Loop: {settings['autobeef']['loop']}")
    val = mode.lower() == "on"
    settings["autobeef"]["loop"] = val
    save_settings()
    await safe_send(ctx.channel, f"Loop: {'ON' if val else 'OFF'}")

# =========================================================
# PING / STREAM / PREFIX
# =========================================================
DEFAULT_SETTINGS["stream"] = {
    "texts": [],
    "delay": 10.0,
}
if "stream" not in settings:
    settings["stream"] = json.loads(json.dumps(DEFAULT_SETTINGS["stream"]))
else:
    for k, v in DEFAULT_SETTINGS["stream"].items():
        if k not in settings["stream"]:
            settings["stream"][k] = v
save_settings()

HELP_PSP = """```
--- Ping / Stream / Prefix ---

Ping:
  ,ping                     show latency + uptime

Stream:
  ,stream <text>            set streaming status
  ,streamlist               show current stream texts
  ,streamadd <text>         add text to rotation
  ,streamremove <n>         remove by index
  ,streamclear              clear all texts
  ,streamstart              start rotation
  ,streamstop               stop rotation + clear status
  ,streamdelay <seconds>    delay between rotations

Prefix:
  ,prefix                   show current prefix
  ,prefix <new>             change prefix
```"""

state_helpers = {}  # extra state slot for stream task

@bot.command(name="ping")
async def cmd_ping(ctx):
    up = int(time.monotonic() - START_TIME)
    d, r = divmod(up, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    latency_ms = round(bot.latency * 1000) if bot.latency else "?"
    await safe_send(ctx.channel, f"Pong | {latency_ms}ms | Uptime {d}d {h}h {m}m {s}s")

@bot.command(name="stream")
async def cmd_stream(ctx, *, text: str = None):
    if not text:
        return await safe_send(ctx.channel, "Usage: ,stream <text>")
    try:
        activity = discord.Activity(
            type=discord.ActivityType.streaming,
            name=text,
            url="https://www.twitch.tv/twitch"
        )
        await bot.change_presence(activity=activity)
        await safe_send(ctx.channel, f"Streaming: {text}")
    except Exception as e:
        await safe_send(ctx.channel, f"Error: {e}")

@bot.command(name="streamlist")
async def cmd_streamlist(ctx):
    texts = settings["stream"]["texts"]
    if not texts:
        return await safe_send(ctx.channel, "No stream texts set.")
    delay = settings["stream"]["delay"]
    lines = [f"{i+1}. {t}" for i, t in enumerate(texts)]
    await safe_send(ctx.channel, f"Stream texts (delay {delay}s):\n" + "\n".join(lines))

@bot.command(name="streamadd")
async def cmd_streamadd(ctx, *, text: str = None):
    if not text:
        return await safe_send(ctx.channel, "Usage: ,streamadd <text>")
    settings["stream"]["texts"].append(text)
    save_settings()
    await safe_send(ctx.channel, f"Added stream text: {text}")

@bot.command(name="streamremove")
async def cmd_streamremove(ctx, n: int = None):
    if n is None:
        return await safe_send(ctx.channel, "Usage: ,streamremove <n>")
    texts = settings["stream"]["texts"]
    if not (1 <= n <= len(texts)):
        return await safe_send(ctx.channel, "Invalid index.")
    removed = texts.pop(n - 1)
    save_settings()
    await safe_send(ctx.channel, f"Removed: {removed}")

@bot.command(name="streamclear")
async def cmd_streamclear(ctx):
    settings["stream"]["texts"] = []
    save_settings()
    await safe_send(ctx.channel, "All stream texts cleared.")

@bot.command(name="streamdelay")
async def cmd_streamdelay(ctx, seconds: float = None):
    if seconds is None:
        return await safe_send(ctx.channel, f"Current delay: {settings['stream']['delay']}s")
    if seconds < 1:
        return await safe_send(ctx.channel, "Min delay is 1s.")
    settings["stream"]["delay"] = seconds
    save_settings()
    await safe_send(ctx.channel, f"Stream delay set to {seconds}s.")

async def stream_rotation_loop():
    while True:
        try:
            texts = settings["stream"]["texts"]
            if not texts:
                await asyncio.sleep(2)
                continue
            for t in texts:
                try:
                    await bot.change_presence(activity=discord.Streaming(name=t, url="https://twitch.tv/x"))
                except Exception as e:
                    print(f"[stream rot] {e}")
                await asyncio.sleep(settings["stream"]["delay"])
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[stream loop] {e}")
            await asyncio.sleep(3)

@bot.command(name="streamstart")
async def cmd_streamstart(ctx):
    texts = settings["stream"]["texts"]
    if not texts:
        return await safe_send(ctx.channel, "No stream texts set. Use ,streamadd <text>.")
    tm.cancel("stream_rotation")
    task = asyncio.create_task(stream_rotation_loop())
    tm.register("stream_rotation", task)
    await safe_send(ctx.channel, f"Stream rotation started ({len(texts)} text(s)).")

@bot.command(name="streamstop")
async def cmd_streamstop(ctx):
    tm.cancel("stream_rotation")
    try:
        await bot.change_presence(activity=None)
    except: pass
    await safe_send(ctx.channel, "Stream rotation stopped + status cleared.")

@bot.command(name="prefix")
async def cmd_prefix(ctx, new_prefix: str = None):
    if new_prefix is None:
        return await safe_send(ctx.channel, f"Current prefix: `{bot.command_prefix}`")
    bot.command_prefix = new_prefix
    await safe_send(ctx.channel, f"Prefix changed to `{new_prefix}`")

# =========================================================
# STATUS CHANGER (through file)
# =========================================================
DEFAULT_SETTINGS["status"] = {
    "file": "status.txt",
    "delay": 15.0,
    "activity_type": "playing",  # playing / watching / listening / streaming / competing
    "random_order": True,
    "loop": True,
}
if "status" not in settings:
    settings["status"] = json.loads(json.dumps(DEFAULT_SETTINGS["status"]))
else:
    for k, v in DEFAULT_SETTINGS["status"].items():
        if k not in settings["status"]:
            settings["status"][k] = v
save_settings()

HELP_STATUS = """```
--- Status Changer Sub-Commands ---

  ,status [channel_id]        start (reads from status.txt)
  ,statusstop                 stop
  ,statuspause / ,statusresume
  ,statusinfo                 show current settings
  ,statusreset                reset

File:
  ,statusfile <name>          change the text file
  ,statusfiles                list .txt files

Delay:
  ,statusdelay <seconds>

Type:
  ,statustype <playing|watching|listening|streaming|competing>

Other:
  ,statusshuffle on/off
  ,statusloop on/off
```"""

def make_activity(text, kind):
    try:
        if kind == "playing":
            return discord.Game(name=text)
        if kind == "watching":
            return discord.Activity(type=discord.ActivityType.watching, name=text)
        if kind == "listening":
            return discord.Activity(type=discord.ActivityType.listening, name=text)
        if kind == "competing":
            return discord.Activity(type=discord.ActivityType.competing, name=text)
        if kind == "streaming":
            return discord.Streaming(name=text, url="https://twitch.tv/x")
    except Exception as e:
        print(f"[status make] {e}")
    return discord.Game(name=text)

async def status_loop():
    st = settings["status"]
    while True:
        try:
            if tm.is_paused("status"):
                await asyncio.sleep(0.5)
                continue
            texts = load_lines(st["file"])
            if not texts:
                await asyncio.sleep(2)
                continue
            if st.get("random_order", True):
                random.shuffle(texts)
            for t in texts:
                while tm.is_paused("status"):
                    await asyncio.sleep(0.3)
                try:
                    await bot.change_presence(activity=make_activity(t, st.get("activity_type", "playing")))
                except Exception as e:
                    print(f"[status] {e}")
                await asyncio.sleep(st.get("delay", 15.0))
            if not st.get("loop", True):
                break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[status loop] {e}")
            await asyncio.sleep(3)

@bot.command(name="status")
async def cmd_status(ctx):
    fname = normalize_filename(settings["status"]["file"], "status.txt")
    if not os.path.exists(fname):
        return await safe_send(ctx.channel, f"{fname} not found. Create it with one status per line.")
    if not load_lines(fname):
        return await safe_send(ctx.channel, f"{fname} is empty.")
    tm.cancel("status")
    task = asyncio.create_task(status_loop())
    tm.register("status", task)
    await safe_send(ctx.channel, f"Status rotation started using {fname}.")

@bot.command(name="statusstop")
async def cmd_statusstop(ctx):
    tm.cancel("status")
    try:
        await bot.change_presence(activity=None)
    except: pass
    await safe_send(ctx.channel, "Status rotation stopped.")

@bot.command(name="statuspause")
async def cmd_statuspause(ctx):
    if tm.pause("status"):
        await safe_send(ctx.channel, "Status paused.")
    else:
        await safe_send(ctx.channel, "Status not running.")

@bot.command(name="statusresume")
async def cmd_statusresume(ctx):
    if tm.resume("status"):
        await safe_send(ctx.channel, "Status resumed.")
    else:
        await safe_send(ctx.channel, "Status not paused.")

@bot.command(name="statusinfo")
async def cmd_statusinfo(ctx):
    st = settings["status"]
    msg = (
        f"```\n--- Status Settings ---\n"
        f"Status:       {tm.status('status')}\n"
        f"File:         {st['file']}\n"
        f"Delay:        {st['delay']}s\n"
        f"Activity:     {st.get('activity_type', 'playing')}\n"
        f"Random order: {st.get('random_order', True)}\n"
        f"Loop:         {st.get('loop', True)}\n```"
    )
    await safe_send(ctx.channel, msg)

@bot.command(name="statusreset")
async def cmd_statusreset(ctx):
    settings["status"] = json.loads(json.dumps(DEFAULT_SETTINGS["status"]))
    save_settings()
    await safe_send(ctx.channel, "Status settings reset.")

@bot.command(name="statusfile")
async def cmd_statusfile(ctx, filename: str = None):
    if not filename:
        return await safe_send(ctx.channel, f"Current file: {settings['status']['file']}")
    fname = normalize_filename(filename, "status.txt")
    if not os.path.exists(fname):
        return await safe_send(ctx.channel, f"{fname} not found.")
    settings["status"]["file"] = fname
    save_settings()
    await safe_send(ctx.channel, f"Status file set to {fname}.")

@bot.command(name="statusfiles")
async def cmd_statusfiles(ctx):
    txts = sorted([f for f in os.listdir(".") if f.endswith(".txt")])
    if not txts:
        return await safe_send(ctx.channel, "No .txt files.")
    await safe_send(ctx.channel, "Available:\n" + "\n".join(f"- {f}" for f in txts))

@bot.command(name="statusdelay")
async def cmd_statusdelay(ctx, seconds: float = None):
    if seconds is None:
        return await safe_send(ctx.channel, f"Current delay: {settings['status']['delay']}s")
    if seconds < 1:
        return await safe_send(ctx.channel, "Min delay is 1s.")
    settings["status"]["delay"] = seconds
    save_settings()
    await safe_send(ctx.channel, f"Status delay set to {seconds}s.")

@bot.command(name="statustype")
async def cmd_statustype(ctx, kind: str = None):
    if kind is None:
        return await safe_send(ctx.channel, f"Current activity type: {settings['status'].get('activity_type')}")
    kind = kind.lower()
    valid = ("playing", "watching", "listening", "streaming", "competing")
    if kind not in valid:
        return await safe_send(ctx.channel, f"Valid: {', '.join(valid)}")
    settings["status"]["activity_type"] = kind
    save_settings()
    await safe_send(ctx.channel, f"Activity type: {kind}")

@bot.command(name="statusshuffle")
async def cmd_statusshuffle(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Random order: {settings['status'].get('random_order')}")
    val = mode.lower() == "on"
    settings["status"]["random_order"] = val
    save_settings()
    await safe_send(ctx.channel, f"Random order: {'ON' if val else 'OFF'}")

@bot.command(name="statusloop")
async def cmd_statusloop(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Loop: {settings['status'].get('loop')}")
    val = mode.lower() == "on"
    settings["status"]["loop"] = val
    save_settings()
    await safe_send(ctx.channel, f"Loop: {'ON' if val else 'OFF'}")

# =========================================================
# AUTOLADDER
# =========================================================
DEFAULT_SETTINGS["autoladder"] = {
    "file": "ladder.txt",
    "delay": 1.0,
    "case": "global",
    "format": "global",
    "format_chance": 100,
    "bold_chance": 0,
    "header_chance": 0,
    "tag_mode": "none",
    "tag_ids": [],
    "burst": 1,
    "burst_gap": 0.3,
    "typing_indicator": True,
    "random_order": True,
    "loop": True,
}

if "autoladder" not in settings:
    settings["autoladder"] = json.loads(json.dumps(DEFAULT_SETTINGS["autoladder"]))
else:
    for k, v in DEFAULT_SETTINGS["autoladder"].items():
        if k not in settings["autoladder"]:
            settings["autoladder"][k] = v
save_settings()

HELP_AL = """```
--- Autoladder Sub-Commands ---

Control:
  ,al [channel_id] [@user]  start (use ,altag first or add @user here)
  ,alstop                   stop
  ,alpause / ,alresume      pause / resume
  ,alinfo                   show current settings
  ,alreset                  reset to defaults

Target:
  ,altag @user              add target
  ,aluntag @user            remove target
  ,alcleartags              clear all
  ,altagmode <none/single/multi>

File:
  ,alfile <name>            change wordlist
  ,alfiles                  list .txt files

Delay:
  ,aldelay <seconds>

Formatting:
  ,alcase <u/l/m/global>
  ,alformat <mode>
  ,alformatchance <0-100>
  ,albold <0-100>
  ,alheader <0-100>

Burst:
  ,albust <n>
  ,albustgap <seconds>

Other:
  ,altyping on/off
  ,alshuffle on/off
  ,alloop on/off
```"""

async def autoladder_loop(channel, key="autoladder"):
    al = settings["autoladder"]
    if not load_lines(al["file"]):
        await safe_send(channel, f"[autoladder] {al['file']} is empty or missing.")
        return
    while True:
        try:
            if tm.is_paused(key):
                await asyncio.sleep(0.5)
                continue
            lines = load_lines(al["file"])
            if not lines:
                await asyncio.sleep(2)
                continue
            if al["random_order"]:
                random.shuffle(lines)
            for raw in lines:
                while tm.is_paused(key):
                    await asyncio.sleep(0.3)
                text = apply_case(raw, resolve_case("autoladder"))
                fmt = resolve_format("autoladder")
                chance = resolve_format_chance("autoladder")
                if fmt != "none" and random.randint(1, 100) <= chance:
                    text = apply_format(text, fmt)
                bc = al.get("bold_chance", 0)
                if bc > 0 and random.randint(1, 100) <= bc:
                    text = f"**{text}**"
                hc = al.get("header_chance", 0)
                if hc > 0 and random.randint(1, 100) <= hc:
                    text = f"# {text}"
                text = tag_join(text, build_tag_prefix("autoladder"))
                if al.get("typing_indicator", True):
                    await send_typing(channel)
                    await asyncio.sleep(random.uniform(0.3, 0.8))
                burst = max(1, int(al.get("burst", 1)))
                for _ in range(burst):
                    await safe_send(channel, text)
                    if burst > 1:
                        await asyncio.sleep(al.get("burst_gap", 0.3))
                await asyncio.sleep(al.get("delay", 1.0))
            if not al.get("loop", True):
                break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[autoladder error] {e}")
            await asyncio.sleep(3)

@bot.command(name="al", aliases=["autoladder"])
async def cmd_al(ctx, channel_id: int = None, member: discord.Member = None):
    if not channel_id:
        return await safe_send(ctx.channel, "Usage: ,al <channel_id> [@user]")
    ch = bot.get_channel(channel_id)
    if not ch:
        return await safe_send(ctx.channel, "Channel not found.")
    if member:
        ids = settings["autoladder"]["tag_ids"]
        if member.id not in ids:
            ids.append(member.id)
        settings["autoladder"]["tag_mode"] = "single"
        save_settings()
    tm.cancel("autoladder")
    task = asyncio.create_task(autoladder_loop(ch))
    tm.register("autoladder", task)
    await safe_send(ctx.channel, f"autoladder started in {channel_id} using {settings['autoladder']['file']}.")

@bot.command(name="alstop")
async def cmd_alstop(ctx):
    tm.cancel("autoladder")
    await safe_send(ctx.channel, "autoladder stopped.")

@bot.command(name="alpause")
async def cmd_alpause(ctx):
    if tm.pause("autoladder"):
        await safe_send(ctx.channel, "autoladder paused.")
    else:
        await safe_send(ctx.channel, "autoladder is not running.")

@bot.command(name="alresume")
async def cmd_alresume(ctx):
    if tm.resume("autoladder"):
        await safe_send(ctx.channel, "autoladder resumed.")
    else:
        await safe_send(ctx.channel, "autoladder is not paused.")

@bot.command(name="alinfo")
async def cmd_alinfo(ctx):
    al = settings["autoladder"]
    tags = al.get("tag_ids", [])
    tag_str = ", ".join(f"<@{u}>" for u in tags) if tags else "none"
    msg = (
        f"```\n--- Autoladder Settings ---\n"
        f"Status:        {tm.status('autoladder')}\n"
        f"File:          {al['file']}\n"
        f"Delay:         {al['delay']}s\n"
        f"Case:          {al['case']}\n"
        f"Format:        {al['format']} (chance {al['format_chance']}%)\n"
        f"Bold chance:   {al['bold_chance']}%\n"
        f"Header chance: {al['header_chance']}%\n"
        f"Tag mode:      {al['tag_mode']}\n"
        f"Tag list:      {tag_str}\n"
        f"Burst:         {al['burst']} (gap {al['burst_gap']}s)\n"
        f"Typing ind.:   {al['typing_indicator']}\n"
        f"Random order:  {al['random_order']}\n"
        f"Loop:          {al['loop']}\n```"
    )
    await safe_send(ctx.channel, msg)

@bot.command(name="alreset")
async def cmd_alreset(ctx):
    settings["autoladder"] = json.loads(json.dumps(DEFAULT_SETTINGS["autoladder"]))
    save_settings()
    await safe_send(ctx.channel, "autoladder settings reset to defaults.")

@bot.command(name="alfile")
async def cmd_alfile(ctx, filename: str = None):
    if not filename:
        return await safe_send(ctx.channel, f"Current file: {settings['autoladder']['file']}")
    fname = normalize_filename(filename, "ladder.txt")
    if not os.path.exists(fname):
        return await safe_send(ctx.channel, f"{fname} not found.")
    settings["autoladder"]["file"] = fname
    save_settings()
    await safe_send(ctx.channel, f"autoladder file set to {fname}.")

@bot.command(name="alfiles")
async def cmd_alfiles(ctx):
    txts = sorted([f for f in os.listdir(".") if f.endswith(".txt")])
    if not txts:
        return await safe_send(ctx.channel, "No .txt files.")
    await safe_send(ctx.channel, "Available:\n" + "\n".join(f"- {f}" for f in txts))

@bot.command(name="aldelay")
async def cmd_aldelay(ctx, seconds: float = None):
    if seconds is None:
        return await safe_send(ctx.channel, f"Current delay: {settings['autoladder']['delay']}s")
    if seconds < 0.1:
        return await safe_send(ctx.channel, "Min delay is 0.1s.")
    settings["autoladder"]["delay"] = seconds
    save_settings()
    await safe_send(ctx.channel, f"autoladder delay set to {seconds}s.")

@bot.command(name="alcase")
async def cmd_alcase(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Current case: {settings['autoladder']['case']}")
    mode = mode.lower()
    if mode not in ("u", "l", "m", "global"):
        return await safe_send(ctx.channel, "Use u, l, m, or global.")
    settings["autoladder"]["case"] = mode.upper() if mode != "global" else "global"
    save_settings()
    await safe_send(ctx.channel, f"autoladder case set to {settings['autoladder']['case']}.")

@bot.command(name="alformat")
async def cmd_alformat(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Current format: {settings['autoladder']['format']}")
    valid = ("none", "bold", "italic", "code", "codeblock", "header", "quote", "spoiler", "global")
    if mode.lower() not in valid:
        return await safe_send(ctx.channel, f"Valid: {', '.join(valid)}")
    settings["autoladder"]["format"] = mode.lower()
    save_settings()
    await safe_send(ctx.channel, f"autoladder format set to {mode.lower()}.")

@bot.command(name="alformatchance")
async def cmd_alformatchance(ctx, pct: int = None):
    if pct is None:
        return await safe_send(ctx.channel, f"Current format chance: {settings['autoladder']['format_chance']}%")
    if not (0 <= pct <= 100):
        return await safe_send(ctx.channel, "Use 0-100.")
    settings["autoladder"]["format_chance"] = pct
    save_settings()
    await safe_send(ctx.channel, f"autoladder format chance: {pct}%")

@bot.command(name="albold")
async def cmd_albold(ctx, pct: int = None):
    if pct is None:
        return await safe_send(ctx.channel, f"Current bold chance: {settings['autoladder']['bold_chance']}%")
    if not (0 <= pct <= 100):
        return await safe_send(ctx.channel, "Use 0-100.")
    settings["autoladder"]["bold_chance"] = pct
    save_settings()
    await safe_send(ctx.channel, f"autoladder bold chance: {pct}%")

@bot.command(name="alheader")
async def cmd_alheader(ctx, pct: int = None):
    if pct is None:
        return await safe_send(ctx.channel, f"Current header chance: {settings['autoladder']['header_chance']}%")
    if not (0 <= pct <= 100):
        return await safe_send(ctx.channel, "Use 0-100.")
    settings["autoladder"]["header_chance"] = pct
    save_settings()
    await safe_send(ctx.channel, f"autoladder header chance: {pct}%")

@bot.command(name="altag")
async def cmd_altag(ctx, target: str = None):
    if not target:
        return await safe_send(ctx.channel, "Usage: ,altag @user OR ,altag <user_id>")
    uid = None
    if ctx.message.mentions:
        uid = ctx.message.mentions[0].id
    else:
        try: uid = int(target.strip("<@!>"))
        except: return await safe_send(ctx.channel, "Invalid user.")
    ids = settings["autoladder"]["tag_ids"]
    if uid in ids:
        return await safe_send(ctx.channel, f"<@{uid}> already in tag list.")
    ids.append(uid)
    if settings["autoladder"]["tag_mode"] == "none":
        settings["autoladder"]["tag_mode"] = "single"
    save_settings()
    await safe_send(ctx.channel, f"Added <@{uid}>. Mode: {settings['autoladder']['tag_mode']}")

@bot.command(name="aluntag")
async def cmd_aluntag(ctx, target: str = None):
    if not target:
        return await safe_send(ctx.channel, "Usage: ,aluntag @user OR ,aluntag <user_id>")
    uid = None
    if ctx.message.mentions:
        uid = ctx.message.mentions[0].id
    else:
        try: uid = int(target.strip("<@!>"))
        except: return await safe_send(ctx.channel, "Invalid user.")
    ids = settings["autoladder"]["tag_ids"]
    if uid not in ids:
        return await safe_send(ctx.channel, "Not in tag list.")
    ids.remove(uid)
    if not ids:
        settings["autoladder"]["tag_mode"] = "none"
    save_settings()
    await safe_send(ctx.channel, f"Removed <@{uid}>. Remaining: {len(ids)}")

@bot.command(name="alcleartags")
async def cmd_alcleartags(ctx):
    settings["autoladder"]["tag_ids"] = []
    settings["autoladder"]["tag_mode"] = "none"
    save_settings()
    await safe_send(ctx.channel, "Tag list cleared.")

@bot.command(name="altagmode")
async def cmd_altagmode(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Current tag mode: {settings['autoladder']['tag_mode']}")
    if mode.lower() not in ("none", "single", "multi"):
        return await safe_send(ctx.channel, "Use none, single, or multi.")
    settings["autoladder"]["tag_mode"] = mode.lower()
    save_settings()
    await safe_send(ctx.channel, f"Tag mode: {mode.lower()}")

@bot.command(name="albust")
async def cmd_albust(ctx, n: int = None):
    if n is None:
        return await safe_send(ctx.channel, f"Current burst: {settings['autoladder']['burst']}")
    if not (1 <= n <= 50):
        return await safe_send(ctx.channel, "Use 1-50.")
    settings["autoladder"]["burst"] = n
    save_settings()
    await safe_send(ctx.channel, f"Burst set to {n}.")

@bot.command(name="albustgap")
async def cmd_albustgap(ctx, seconds: float = None):
    if seconds is None:
        return await safe_send(ctx.channel, f"Current burst gap: {settings['autoladder']['burst_gap']}s")
    if seconds < 0:
        return await safe_send(ctx.channel, "Must be >= 0.")
    settings["autoladder"]["burst_gap"] = seconds
    save_settings()
    await safe_send(ctx.channel, f"Burst gap: {seconds}s")

@bot.command(name="altyping")
async def cmd_altyping(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Typing indicator: {settings['autoladder']['typing_indicator']}")
    val = mode.lower() == "on"
    settings["autoladder"]["typing_indicator"] = val
    save_settings()
    await safe_send(ctx.channel, f"Typing indicator: {'ON' if val else 'OFF'}")

@bot.command(name="alshuffle")
async def cmd_alshuffle(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Random order: {settings['autoladder']['random_order']}")
    val = mode.lower() == "on"
    settings["autoladder"]["random_order"] = val
    save_settings()
    await safe_send(ctx.channel, f"Random order: {'ON' if val else 'OFF'}")

@bot.command(name="alloop")
async def cmd_alloop(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Loop: {settings['autoladder']['loop']}")
    val = mode.lower() == "on"
    settings["autoladder"]["loop"] = val
    save_settings()
    await safe_send(ctx.channel, f"Loop: {'ON' if val else 'OFF'}")

# =========================================================
# AUTOKILL
# =========================================================
DEFAULT_SETTINGS["autokill"] = {
    "file": "kill.txt",
    "delay": 0.5,
    "case": "global",
    "format": "global",
    "format_chance": 100,
    "bold_chance": 0,
    "header_chance": 0,
    "tag_mode": "none",
    "tag_ids": [],
    "burst": 1,
    "burst_gap": 0.3,
    "typing_indicator": False,
    "random_order": True,
    "loop": True,
}

if "autokill" not in settings:
    settings["autokill"] = json.loads(json.dumps(DEFAULT_SETTINGS["autokill"]))
else:
    for k, v in DEFAULT_SETTINGS["autokill"].items():
        if k not in settings["autokill"]:
            settings["autokill"][k] = v
save_settings()

HELP_AK = """```
--- Autokill Sub-Commands ---

Control:
  ,ak [channel_id] [@user]  start
  ,akstop                   stop
  ,akpause / ,akresume      pause / resume
  ,akinfo                   show current settings
  ,akreset                  reset to defaults

Target:
  ,aktag @user              add target
  ,akuntag @user            remove target
  ,akcleartags              clear all
  ,aktagmode <none/single/multi>

File:
  ,akfile <name>            change wordlist
  ,akfiles                  list .txt files

Delay:
  ,akdelay <seconds>

Formatting:
  ,akcase <u/l/m/global>
  ,akformat <mode>
  ,akformatchance <0-100>
  ,akbold <0-100>
  ,akheader <0-100>

Burst:
  ,akburst <n>
  ,akburstgap <seconds>

Other:
  ,aktyping on/off
  ,akshuffle on/off
  ,akloop on/off
```"""

async def autokill_loop(channel, key="autokill"):
    ak = settings["autokill"]
    if not load_lines(ak["file"]):
        await safe_send(channel, f"[autokill] {ak['file']} is empty or missing.")
        return
    while True:
        try:
            if tm.is_paused(key):
                await asyncio.sleep(0.5)
                continue
            lines = load_lines(ak["file"])
            if not lines:
                await asyncio.sleep(2)
                continue
            if ak["random_order"]:
                random.shuffle(lines)
            for raw in lines:
                while tm.is_paused(key):
                    await asyncio.sleep(0.3)
                text = apply_case(raw, resolve_case("autokill"))
                fmt = resolve_format("autokill")
                chance = resolve_format_chance("autokill")
                if fmt != "none" and random.randint(1, 100) <= chance:
                    text = apply_format(text, fmt)
                bc = ak.get("bold_chance", 0)
                if bc > 0 and random.randint(1, 100) <= bc:
                    text = f"**{text}**"
                hc = ak.get("header_chance", 0)
                if hc > 0 and random.randint(1, 100) <= hc:
                    text = f"# {text}"
                text = tag_join(text, build_tag_prefix("autokill"))
                if ak.get("typing_indicator", False):
                    await send_typing(channel)
                    await asyncio.sleep(random.uniform(0.2, 0.6))
                burst = max(1, int(ak.get("burst", 1)))
                for _ in range(burst):
                    await safe_send(channel, text)
                    if burst > 1:
                        await asyncio.sleep(ak.get("burst_gap", 0.3))
                await asyncio.sleep(ak.get("delay", 0.5))
            if not ak.get("loop", True):
                break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[autokill error] {e}")
            await asyncio.sleep(3)

@bot.command(name="ak", aliases=["autokill"])
async def cmd_ak(ctx, channel_id: int = None, member: discord.Member = None):
    if not channel_id:
        return await safe_send(ctx.channel, "Usage: ,ak <channel_id> [@user]")
    ch = bot.get_channel(channel_id)
    if not ch:
        return await safe_send(ctx.channel, "Channel not found.")
    if member:
        ids = settings["autokill"]["tag_ids"]
        if member.id not in ids:
            ids.append(member.id)
        settings["autokill"]["tag_mode"] = "single"
        save_settings()
    tm.cancel("autokill")
    task = asyncio.create_task(autokill_loop(ch))
    tm.register("autokill", task)
    await safe_send(ctx.channel, f"autokill started in {channel_id} using {settings['autokill']['file']}.")

@bot.command(name="akstop")
async def cmd_akstop(ctx):
    tm.cancel("autokill")
    await safe_send(ctx.channel, "autokill stopped.")

@bot.command(name="akpause")
async def cmd_akpause(ctx):
    if tm.pause("autokill"):
        await safe_send(ctx.channel, "autokill paused.")
    else:
        await safe_send(ctx.channel, "autokill is not running.")

@bot.command(name="akresume")
async def cmd_akresume(ctx):
    if tm.resume("autokill"):
        await safe_send(ctx.channel, "autokill resumed.")
    else:
        await safe_send(ctx.channel, "autokill is not paused.")

@bot.command(name="akinfo")
async def cmd_akinfo(ctx):
    ak = settings["autokill"]
    tags = ak.get("tag_ids", [])
    tag_str = ", ".join(f"<@{u}>" for u in tags) if tags else "none"
    msg = (
        f"```\n--- Autokill Settings ---\n"
        f"Status:        {tm.status('autokill')}\n"
        f"File:          {ak['file']}\n"
        f"Delay:         {ak['delay']}s\n"
        f"Case:          {ak['case']}\n"
        f"Format:        {ak['format']} (chance {ak['format_chance']}%)\n"
        f"Bold chance:   {ak['bold_chance']}%\n"
        f"Header chance: {ak['header_chance']}%\n"
        f"Tag mode:      {ak['tag_mode']}\n"
        f"Tag list:      {tag_str}\n"
        f"Burst:         {ak['burst']} (gap {ak['burst_gap']}s)\n"
        f"Typing ind.:   {ak['typing_indicator']}\n"
        f"Random order:  {ak['random_order']}\n"
        f"Loop:          {ak['loop']}\n```"
    )
    await safe_send(ctx.channel, msg)

@bot.command(name="akreset")
async def cmd_akreset(ctx):
    settings["autokill"] = json.loads(json.dumps(DEFAULT_SETTINGS["autokill"]))
    save_settings()
    await safe_send(ctx.channel, "autokill settings reset to defaults.")

@bot.command(name="akfile")
async def cmd_akfile(ctx, filename: str = None):
    if not filename:
        return await safe_send(ctx.channel, f"Current file: {settings['autokill']['file']}")
    fname = normalize_filename(filename, "kill.txt")
    if not os.path.exists(fname):
        return await safe_send(ctx.channel, f"{fname} not found.")
    settings["autokill"]["file"] = fname
    save_settings()
    await safe_send(ctx.channel, f"autokill file set to {fname}.")

@bot.command(name="akfiles")
async def cmd_akfiles(ctx):
    txts = sorted([f for f in os.listdir(".") if f.endswith(".txt")])
    if not txts:
        return await safe_send(ctx.channel, "No .txt files.")
    await safe_send(ctx.channel, "Available:\n" + "\n".join(f"- {f}" for f in txts))

@bot.command(name="akdelay")
async def cmd_akdelay(ctx, seconds: float = None):
    if seconds is None:
        return await safe_send(ctx.channel, f"Current delay: {settings['autokill']['delay']}s")
    if seconds < 0.1:
        return await safe_send(ctx.channel, "Min delay is 0.1s.")
    settings["autokill"]["delay"] = seconds
    save_settings()
    await safe_send(ctx.channel, f"autokill delay set to {seconds}s.")

@bot.command(name="akcase")
async def cmd_akcase(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Current case: {settings['autokill']['case']}")
    mode = mode.lower()
    if mode not in ("u", "l", "m", "global"):
        return await safe_send(ctx.channel, "Use u, l, m, or global.")
    settings["autokill"]["case"] = mode.upper() if mode != "global" else "global"
    save_settings()
    await safe_send(ctx.channel, f"autokill case set to {settings['autokill']['case']}.")

@bot.command(name="akformat")
async def cmd_akformat(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Current format: {settings['autokill']['format']}")
    valid = ("none", "bold", "italic", "code", "codeblock", "header", "quote", "spoiler", "global")
    if mode.lower() not in valid:
        return await safe_send(ctx.channel, f"Valid: {', '.join(valid)}")
    settings["autokill"]["format"] = mode.lower()
    save_settings()
    await safe_send(ctx.channel, f"autokill format set to {mode.lower()}.")

@bot.command(name="akformatchance")
async def cmd_akformatchance(ctx, pct: int = None):
    if pct is None:
        return await safe_send(ctx.channel, f"Current format chance: {settings['autokill']['format_chance']}%")
    if not (0 <= pct <= 100):
        return await safe_send(ctx.channel, "Use 0-100.")
    settings["autokill"]["format_chance"] = pct
    save_settings()
    await safe_send(ctx.channel, f"autokill format chance: {pct}%")

@bot.command(name="akbold")
async def cmd_akbold(ctx, pct: int = None):
    if pct is None:
        return await safe_send(ctx.channel, f"Current bold chance: {settings['autokill']['bold_chance']}%")
    if not (0 <= pct <= 100):
        return await safe_send(ctx.channel, "Use 0-100.")
    settings["autokill"]["bold_chance"] = pct
    save_settings()
    await safe_send(ctx.channel, f"autokill bold chance: {pct}%")

@bot.command(name="akheader")
async def cmd_akheader(ctx, pct: int = None):
    if pct is None:
        return await safe_send(ctx.channel, f"Current header chance: {settings['autokill']['header_chance']}%")
    if not (0 <= pct <= 100):
        return await safe_send(ctx.channel, "Use 0-100.")
    settings["autokill"]["header_chance"] = pct
    save_settings()
    await safe_send(ctx.channel, f"autokill header chance: {pct}%")

@bot.command(name="aktag")
async def cmd_aktag(ctx, target: str = None):
    if not target:
        return await safe_send(ctx.channel, "Usage: ,aktag @user OR ,aktag <user_id>")
    uid = None
    if ctx.message.mentions:
        uid = ctx.message.mentions[0].id
    else:
        try: uid = int(target.strip("<@!>"))
        except: return await safe_send(ctx.channel, "Invalid user.")
    ids = settings["autokill"]["tag_ids"]
    if uid in ids:
        return await safe_send(ctx.channel, f"<@{uid}> already in tag list.")
    ids.append(uid)
    if settings["autokill"]["tag_mode"] == "none":
        settings["autokill"]["tag_mode"] = "single"
    save_settings()
    await safe_send(ctx.channel, f"Added <@{uid}>. Mode: {settings['autokill']['tag_mode']}")

@bot.command(name="akuntag")
async def cmd_akuntag(ctx, target: str = None):
    if not target:
        return await safe_send(ctx.channel, "Usage: ,akuntag @user OR ,akuntag <user_id>")
    uid = None
    if ctx.message.mentions:
        uid = ctx.message.mentions[0].id
    else:
        try: uid = int(target.strip("<@!>"))
        except: return await safe_send(ctx.channel, "Invalid user.")
    ids = settings["autokill"]["tag_ids"]
    if uid not in ids:
        return await safe_send(ctx.channel, "Not in tag list.")
    ids.remove(uid)
    if not ids:
        settings["autokill"]["tag_mode"] = "none"
    save_settings()
    await safe_send(ctx.channel, f"Removed <@{uid}>. Remaining: {len(ids)}")

@bot.command(name="akcleartags")
async def cmd_akcleartags(ctx):
    settings["autokill"]["tag_ids"] = []
    settings["autokill"]["tag_mode"] = "none"
    save_settings()
    await safe_send(ctx.channel, "Tag list cleared.")

@bot.command(name="aktagmode")
async def cmd_aktagmode(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Current tag mode: {settings['autokill']['tag_mode']}")
    if mode.lower() not in ("none", "single", "multi"):
        return await safe_send(ctx.channel, "Use none, single, or multi.")
    settings["autokill"]["tag_mode"] = mode.lower()
    save_settings()
    await safe_send(ctx.channel, f"Tag mode: {mode.lower()}")

@bot.command(name="akburst")
async def cmd_akburst(ctx, n: int = None):
    if n is None:
        return await safe_send(ctx.channel, f"Current burst: {settings['autokill']['burst']}")
    if not (1 <= n <= 50):
        return await safe_send(ctx.channel, "Use 1-50.")
    settings["autokill"]["burst"] = n
    save_settings()
    await safe_send(ctx.channel, f"Burst set to {n}.")

@bot.command(name="akburstgap")
async def cmd_akburstgap(ctx, seconds: float = None):
    if seconds is None:
        return await safe_send(ctx.channel, f"Current burst gap: {settings['autokill']['burst_gap']}s")
    if seconds < 0:
        return await safe_send(ctx.channel, "Must be >= 0.")
    settings["autokill"]["burst_gap"] = seconds
    save_settings()
    await safe_send(ctx.channel, f"Burst gap: {seconds}s")

@bot.command(name="aktyping")
async def cmd_aktyping(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Typing indicator: {settings['autokill']['typing_indicator']}")
    val = mode.lower() == "on"
    settings["autokill"]["typing_indicator"] = val
    save_settings()
    await safe_send(ctx.channel, f"Typing indicator: {'ON' if val else 'OFF'}")

@bot.command(name="akshuffle")
async def cmd_akshuffle(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Random order: {settings['autokill']['random_order']}")
    val = mode.lower() == "on"
    settings["autokill"]["random_order"] = val
    save_settings()
    await safe_send(ctx.channel, f"Random order: {'ON' if val else 'OFF'}")

@bot.command(name="akloop")
async def cmd_akloop(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Loop: {settings['autokill']['loop']}")
    val = mode.lower() == "on"
    settings["autokill"]["loop"] = val
    save_settings()
    await safe_send(ctx.channel, f"Loop: {'ON' if val else 'OFF'}")

# =========================================================
# SPAM
# =========================================================
DEFAULT_SETTINGS["spam"] = {
    "message": "spam message here",
    "delay": 0.7,
    "case": "global",
    "format": "global",
    "format_chance": 100,
    "bold_chance": 0,
    "header_chance": 0,
    "typing_indicator": False,
    "burst": 1,
    "burst_gap": 0.3,
    "loop": True,
}

if "spam" not in settings:
    settings["spam"] = json.loads(json.dumps(DEFAULT_SETTINGS["spam"]))
else:
    for k, v in DEFAULT_SETTINGS["spam"].items():
        if k not in settings["spam"]:
            settings["spam"][k] = v
save_settings()

HELP_SPAM = """```
--- Spam Sub-Commands ---

Control:
  ,spam [channel_id]       start (uses saved message)
  ,spamstop                stop
  ,spampause / ,spamresume pause / resume
  ,spaminfo                show current settings
  ,spamreset               reset to defaults

Message:
  ,spammsg <text>          set the message to spam
  ,spamshow                show current spam message

Delay:
  ,spamdelay <seconds>

Formatting:
  ,spamcase <u/l/m/global>
  ,spamformat <mode>
  ,spamformatchance <0-100>
  ,spambold <0-100>
  ,spamheader <0-100>

Burst:
  ,spamburst <n>
  ,spamburstgap <seconds>

Other:
  ,spamtyping on/off
  ,spamloop on/off
```"""

async def spam_loop(channel, key="spam"):
    sp = settings["spam"]
    while True:
        try:
            if tm.is_paused(key):
                await asyncio.sleep(0.5)
                continue
            raw = sp.get("message", "")
            if not raw:
                await asyncio.sleep(2)
                continue
            text = apply_case(raw, resolve_case("spam"))
            fmt = resolve_format("spam")
            chance = resolve_format_chance("spam")
            if fmt != "none" and random.randint(1, 100) <= chance:
                text = apply_format(text, fmt)
            bc = sp.get("bold_chance", 0)
            if bc > 0 and random.randint(1, 100) <= bc:
                text = f"**{text}**"
            hc = sp.get("header_chance", 0)
            if hc > 0 and random.randint(1, 100) <= hc:
                text = f"# {text}"
            if sp.get("typing_indicator", False):
                await send_typing(channel)
                await asyncio.sleep(random.uniform(0.2, 0.5))
            burst = max(1, int(sp.get("burst", 1)))
            for _ in range(burst):
                await safe_send(channel, text)
                if burst > 1:
                    await asyncio.sleep(sp.get("burst_gap", 0.3))
            await asyncio.sleep(sp.get("delay", 0.7))
            if not sp.get("loop", True):
                break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[spam error] {e}")
            await asyncio.sleep(3)

@bot.command(name="spam")
async def cmd_spam(ctx, channel_id: int = None):
    if not channel_id:
        return await safe_send(ctx.channel, "Usage: ,spam <channel_id>")
    ch = bot.get_channel(channel_id)
    if not ch:
        return await safe_send(ctx.channel, "Channel not found.")
    tm.cancel("spam")
    task = asyncio.create_task(spam_loop(ch))
    tm.register("spam", task)
    await safe_send(ctx.channel, f"spam started in {channel_id}.")

@bot.command(name="spamstop")
async def cmd_spamstop(ctx):
    tm.cancel("spam")
    await safe_send(ctx.channel, "spam stopped.")

@bot.command(name="spampause")
async def cmd_spampause(ctx):
    if tm.pause("spam"):
        await safe_send(ctx.channel, "spam paused.")
    else:
        await safe_send(ctx.channel, "spam is not running.")

@bot.command(name="spamresume")
async def cmd_spamresume(ctx):
    if tm.resume("spam"):
        await safe_send(ctx.channel, "spam resumed.")
    else:
        await safe_send(ctx.channel, "spam is not paused.")

@bot.command(name="spaminfo")
async def cmd_spaminfo(ctx):
    sp = settings["spam"]
    msg = (
        f"```\n--- Spam Settings ---\n"
        f"Status:        {tm.status('spam')}\n"
        f"Message:       {sp['message']}\n"
        f"Delay:         {sp['delay']}s\n"
        f"Case:          {sp['case']}\n"
        f"Format:        {sp['format']} (chance {sp['format_chance']}%)\n"
        f"Bold chance:   {sp['bold_chance']}%\n"
        f"Header chance: {sp['header_chance']}%\n"
        f"Burst:         {sp['burst']} (gap {sp['burst_gap']}s)\n"
        f"Typing ind.:   {sp['typing_indicator']}\n"
        f"Loop:          {sp['loop']}\n```"
    )
    await safe_send(ctx.channel, msg)

@bot.command(name="spamreset")
async def cmd_spamreset(ctx):
    settings["spam"] = json.loads(json.dumps(DEFAULT_SETTINGS["spam"]))
    save_settings()
    await safe_send(ctx.channel, "spam settings reset to defaults.")

@bot.command(name="spammsg")
async def cmd_spammsg(ctx, *, text: str = None):
    if not text:
        return await safe_send(ctx.channel, "Usage: ,spammsg <text>")
    settings["spam"]["message"] = text
    save_settings()
    await safe_send(ctx.channel, f"spam message set to: {text}")

@bot.command(name="spamshow")
async def cmd_spamshow(ctx):
    await safe_send(ctx.channel, f"Current spam message: {settings['spam']['message']}")

@bot.command(name="spamdelay")
async def cmd_spamdelay(ctx, seconds: float = None):
    if seconds is None:
        return await safe_send(ctx.channel, f"Current delay: {settings['spam']['delay']}s")
    if seconds < 0.1:
        return await safe_send(ctx.channel, "Min delay is 0.1s.")
    settings["spam"]["delay"] = seconds
    save_settings()
    await safe_send(ctx.channel, f"spam delay set to {seconds}s.")

@bot.command(name="spamcase")
async def cmd_spamcase(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Current case: {settings['spam']['case']}")
    mode = mode.lower()
    if mode not in ("u", "l", "m", "global"):
        return await safe_send(ctx.channel, "Use u, l, m, or global.")
    settings["spam"]["case"] = mode.upper() if mode != "global" else "global"
    save_settings()
    await safe_send(ctx.channel, f"spam case set to {settings['spam']['case']}.")

@bot.command(name="spamformat")
async def cmd_spamformat(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Current format: {settings['spam']['format']}")
    valid = ("none", "bold", "italic", "code", "codeblock", "header", "quote", "spoiler", "global")
    if mode.lower() not in valid:
        return await safe_send(ctx.channel, f"Valid: {', '.join(valid)}")
    settings["spam"]["format"] = mode.lower()
    save_settings()
    await safe_send(ctx.channel, f"spam format set to {mode.lower()}.")

@bot.command(name="spamformatchance")
async def cmd_spamformatchance(ctx, pct: int = None):
    if pct is None:
        return await safe_send(ctx.channel, f"Current format chance: {settings['spam']['format_chance']}%")
    if not (0 <= pct <= 100):
        return await safe_send(ctx.channel, "Use 0-100.")
    settings["spam"]["format_chance"] = pct
    save_settings()
    await safe_send(ctx.channel, f"spam format chance: {pct}%")

@bot.command(name="spambold")
async def cmd_spambold(ctx, pct: int = None):
    if pct is None:
        return await safe_send(ctx.channel, f"Current bold chance: {settings['spam']['bold_chance']}%")
    if not (0 <= pct <= 100):
        return await safe_send(ctx.channel, "Use 0-100.")
    settings["spam"]["bold_chance"] = pct
    save_settings()
    await safe_send(ctx.channel, f"spam bold chance: {pct}%")

@bot.command(name="spamheader")
async def cmd_spamheader(ctx, pct: int = None):
    if pct is None:
        return await safe_send(ctx.channel, f"Current header chance: {settings['spam']['header_chance']}%")
    if not (0 <= pct <= 100):
        return await safe_send(ctx.channel, "Use 0-100.")
    settings["spam"]["header_chance"] = pct
    save_settings()
    await safe_send(ctx.channel, f"spam header chance: {pct}%")

@bot.command(name="spamburst")
async def cmd_spamburst(ctx, n: int = None):
    if n is None:
        return await safe_send(ctx.channel, f"Current burst: {settings['spam']['burst']}")
    if not (1 <= n <= 50):
        return await safe_send(ctx.channel, "Use 1-50.")
    settings["spam"]["burst"] = n
    save_settings()
    await safe_send(ctx.channel, f"Burst set to {n}.")

@bot.command(name="spamburstgap")
async def cmd_spamburstgap(ctx, seconds: float = None):
    if seconds is None:
        return await safe_send(ctx.channel, f"Current burst gap: {settings['spam']['burst_gap']}s")
    if seconds < 0:
        return await safe_send(ctx.channel, "Must be >= 0.")
    settings["spam"]["burst_gap"] = seconds
    save_settings()
    await safe_send(ctx.channel, f"Burst gap: {seconds}s")

@bot.command(name="spamtyping")
async def cmd_spamtyping(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Typing indicator: {settings['spam']['typing_indicator']}")
    val = mode.lower() == "on"
    settings["spam"]["typing_indicator"] = val
    save_settings()
    await safe_send(ctx.channel, f"Typing indicator: {'ON' if val else 'OFF'}")

@bot.command(name="spamloop")
async def cmd_spamloop(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Loop: {settings['spam']['loop']}")
    val = mode.lower() == "on"
    settings["spam"]["loop"] = val
    save_settings()
    await safe_send(ctx.channel, f"Loop: {'ON' if val else 'OFF'}")

# =========================================================
# AUTOPASTE
# =========================================================
DEFAULT_SETTINGS["autopaste"] = {
    "message": "paste message here",
    "delay": 1.2,
    "case": "global",
    "format": "global",
    "format_chance": 100,
    "bold_chance": 0,
    "header_chance": 0,
    "typing_indicator": False,
    "burst": 1,
    "burst_gap": 0.3,
    "loop": True,
}

if "autopaste" not in settings:
    settings["autopaste"] = json.loads(json.dumps(DEFAULT_SETTINGS["autopaste"]))
else:
    for k, v in DEFAULT_SETTINGS["autopaste"].items():
        if k not in settings["autopaste"]:
            settings["autopaste"][k] = v
save_settings()

HELP_AP = """```
--- Autopaste Sub-Commands ---

Control:
  ,ap [channel_id]         start (uses saved message)
  ,apstop                  stop
  ,appause / ,apresume     pause / resume
  ,apinfo                  show current settings
  ,apreset                 reset to defaults

Message:
  ,apmsg <text>            set the message to paste
  ,apshow                  show current paste message

Delay:
  ,apdelay <seconds>

Formatting:
  ,apcase <u/l/m/global>
  ,apformat <mode>
  ,apformatchance <0-100>
  ,apbold <0-100>
  ,apheader <0-100>

Burst:
  ,apburst <n>
  ,apburstgap <seconds>

Other:
  ,aptyping on/off
  ,aploop on/off
```"""

async def autopaste_loop(channel, key="autopaste"):
    ap = settings["autopaste"]
    while True:
        try:
            if tm.is_paused(key):
                await asyncio.sleep(0.5)
                continue
            raw = ap.get("message", "")
            if not raw:
                await asyncio.sleep(2)
                continue
            text = apply_case(raw, resolve_case("autopaste"))
            fmt = resolve_format("autopaste")
            chance = resolve_format_chance("autopaste")
            if fmt != "none" and random.randint(1, 100) <= chance:
                text = apply_format(text, fmt)
            bc = ap.get("bold_chance", 0)
            if bc > 0 and random.randint(1, 100) <= bc:
                text = f"**{text}**"
            hc = ap.get("header_chance", 0)
            if hc > 0 and random.randint(1, 100) <= hc:
                text = f"# {text}"
            if ap.get("typing_indicator", False):
                await send_typing(channel)
                await asyncio.sleep(random.uniform(0.2, 0.5))
            burst = max(1, int(ap.get("burst", 1)))
            for _ in range(burst):
                await safe_send(channel, text)
                if burst > 1:
                    await asyncio.sleep(ap.get("burst_gap", 0.3))
            await asyncio.sleep(ap.get("delay", 1.2))
            if not ap.get("loop", True):
                break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[autopaste error] {e}")
            await asyncio.sleep(3)

@bot.command(name="ap", aliases=["autopaste"])
async def cmd_ap(ctx, channel_id: int = None):
    if not channel_id:
        return await safe_send(ctx.channel, "Usage: ,ap <channel_id>")
    ch = bot.get_channel(channel_id)
    if not ch:
        return await safe_send(ctx.channel, "Channel not found.")
    tm.cancel("autopaste")
    task = asyncio.create_task(autopaste_loop(ch))
    tm.register("autopaste", task)
    await safe_send(ctx.channel, f"autopaste started in {channel_id}.")

@bot.command(name="apstop")
async def cmd_apstop(ctx):
    tm.cancel("autopaste")
    await safe_send(ctx.channel, "autopaste stopped.")

@bot.command(name="appause")
async def cmd_appause(ctx):
    if tm.pause("autopaste"):
        await safe_send(ctx.channel, "autopaste paused.")
    else:
        await safe_send(ctx.channel, "autopaste is not running.")

@bot.command(name="apresume")
async def cmd_apresume(ctx):
    if tm.resume("autopaste"):
        await safe_send(ctx.channel, "autopaste resumed.")
    else:
        await safe_send(ctx.channel, "autopaste is not paused.")

@bot.command(name="apinfo")
async def cmd_apinfo(ctx):
    ap = settings["autopaste"]
    msg = (
        f"```\n--- Autopaste Settings ---\n"
        f"Status:        {tm.status('autopaste')}\n"
        f"Message:       {ap['message']}\n"
        f"Delay:         {ap['delay']}s\n"
        f"Case:          {ap['case']}\n"
        f"Format:        {ap['format']} (chance {ap['format_chance']}%)\n"
        f"Bold chance:   {ap['bold_chance']}%\n"
        f"Header chance: {ap['header_chance']}%\n"
        f"Burst:         {ap['burst']} (gap {ap['burst_gap']}s)\n"
        f"Typing ind.:   {ap['typing_indicator']}\n"
        f"Loop:          {ap['loop']}\n```"
    )
    await safe_send(ctx.channel, msg)

@bot.command(name="apreset")
async def cmd_apreset(ctx):
    settings["autopaste"] = json.loads(json.dumps(DEFAULT_SETTINGS["autopaste"]))
    save_settings()
    await safe_send(ctx.channel, "autopaste settings reset to defaults.")

@bot.command(name="apmsg")
async def cmd_apmsg(ctx, *, text: str = None):
    if not text:
        return await safe_send(ctx.channel, "Usage: ,apmsg <text>")
    settings["autopaste"]["message"] = text
    save_settings()
    await safe_send(ctx.channel, f"autopaste message set to: {text}")

@bot.command(name="apshow")
async def cmd_apshow(ctx):
    await safe_send(ctx.channel, f"Current autopaste message: {settings['autopaste']['message']}")

@bot.command(name="apdelay")
async def cmd_apdelay(ctx, seconds: float = None):
    if seconds is None:
        return await safe_send(ctx.channel, f"Current delay: {settings['autopaste']['delay']}s")
    if seconds < 0.1:
        return await safe_send(ctx.channel, "Min delay is 0.1s.")
    settings["autopaste"]["delay"] = seconds
    save_settings()
    await safe_send(ctx.channel, f"autopaste delay set to {seconds}s.")

@bot.command(name="apcase")
async def cmd_apcase(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Current case: {settings['autopaste']['case']}")
    mode = mode.lower()
    if mode not in ("u", "l", "m", "global"):
        return await safe_send(ctx.channel, "Use u, l, m, or global.")
    settings["autopaste"]["case"] = mode.upper() if mode != "global" else "global"
    save_settings()
    await safe_send(ctx.channel, f"autopaste case set to {settings['autopaste']['case']}.")

@bot.command(name="apformat")
async def cmd_apformat(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Current format: {settings['autopaste']['format']}")
    valid = ("none", "bold", "italic", "code", "codeblock", "header", "quote", "spoiler", "global")
    if mode.lower() not in valid:
        return await safe_send(ctx.channel, f"Valid: {', '.join(valid)}")
    settings["autopaste"]["format"] = mode.lower()
    save_settings()
    await safe_send(ctx.channel, f"autopaste format set to {mode.lower()}.")

@bot.command(name="apformatchance")
async def cmd_apformatchance(ctx, pct: int = None):
    if pct is None:
        return await safe_send(ctx.channel, f"Current format chance: {settings['autopaste']['format_chance']}%")
    if not (0 <= pct <= 100):
        return await safe_send(ctx.channel, "Use 0-100.")
    settings["autopaste"]["format_chance"] = pct
    save_settings()
    await safe_send(ctx.channel, f"autopaste format chance: {pct}%")

@bot.command(name="apbold")
async def cmd_apbold(ctx, pct: int = None):
    if pct is None:
        return await safe_send(ctx.channel, f"Current bold chance: {settings['autopaste']['bold_chance']}%")
    if not (0 <= pct <= 100):
        return await safe_send(ctx.channel, "Use 0-100.")
    settings["autopaste"]["bold_chance"] = pct
    save_settings()
    await safe_send(ctx.channel, f"autopaste bold chance: {pct}%")

@bot.command(name="apheader")
async def cmd_apheader(ctx, pct: int = None):
    if pct is None:
        return await safe_send(ctx.channel, f"Current header chance: {settings['autopaste']['header_chance']}%")
    if not (0 <= pct <= 100):
        return await safe_send(ctx.channel, "Use 0-100.")
    settings["autopaste"]["header_chance"] = pct
    save_settings()
    await safe_send(ctx.channel, f"autopaste header chance: {pct}%")

@bot.command(name="apburst")
async def cmd_apburst(ctx, n: int = None):
    if n is None:
        return await safe_send(ctx.channel, f"Current burst: {settings['autopaste']['burst']}")
    if not (1 <= n <= 50):
        return await safe_send(ctx.channel, "Use 1-50.")
    settings["autopaste"]["burst"] = n
    save_settings()
    await safe_send(ctx.channel, f"Burst set to {n}.")

@bot.command(name="apburstgap")
async def cmd_apburstgap(ctx, seconds: float = None):
    if seconds is None:
        return await safe_send(ctx.channel, f"Current burst gap: {settings['autopaste']['burst_gap']}s")
    if seconds < 0:
        return await safe_send(ctx.channel, "Must be >= 0.")
    settings["autopaste"]["burst_gap"] = seconds
    save_settings()
    await safe_send(ctx.channel, f"Burst gap: {seconds}s")

@bot.command(name="aptyping")
async def cmd_aptyping(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Typing indicator: {settings['autopaste']['typing_indicator']}")
    val = mode.lower() == "on"
    settings["autopaste"]["typing_indicator"] = val
    save_settings()
    await safe_send(ctx.channel, f"Typing indicator: {'ON' if val else 'OFF'}")

@bot.command(name="aploop")
async def cmd_aploop(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Loop: {settings['autopaste']['loop']}")
    val = mode.lower() == "on"
    settings["autopaste"]["loop"] = val
    save_settings()
    await safe_send(ctx.channel, f"Loop: {'ON' if val else 'OFF'}")
# =========================================================
# STAM (spam with auto-incrementing counter)
# =========================================================
DEFAULT_SETTINGS["stam"] = {
    "message": "stam message here",
    "delay": 1.5,
    "case": "global",
    "format": "global",
    "format_chance": 100,
    "bold_chance": 0,
    "header_chance": 0,
    "typing_indicator": False,
    "counter_style": "parens",
    "start_at": 1,
    "loop": True,
}

if "stam" not in settings:
    settings["stam"] = json.loads(json.dumps(DEFAULT_SETTINGS["stam"]))
else:
    for k, v in DEFAULT_SETTINGS["stam"].items():
        if k not in settings["stam"]:
            settings["stam"][k] = v
save_settings()

HELP_STAM = """```
--- Stam Sub-Commands ---

Control:
  ,stam [channel_id]       start (uses saved message)
  ,stamstop                stop
  ,stampause / ,stamresume pause / resume
  ,staminfo                show current settings
  ,stamreset               reset to defaults

Message:
  ,stammsg <text>          set the message
  ,stamshow                show current message

Counter:
  ,stamstart <n>           starting number (default 1)
  ,stamstyle <style>       parens / bracket / dash / colon / none
  ,stamloop on/off         reset to start number when looped

Delay:
  ,stamdelay <seconds>

Formatting:
  ,stamcase <u/l/m/global>
  ,stamformat <mode>
  ,stamformatchance <0-100>
  ,stambold <0-100>
  ,stamheader <0-100>

Other:
  ,stamtyping on/off
```"""

def apply_counter_style(n, style):
    if style == "parens":  return f" ({n})"
    if style == "bracket": return f" [{n}]"
    if style == "dash":    return f" - {n}"
    if style == "colon":   return f": {n}"
    if style == "none":    return ""
    return f" ({n})"

async def stam_loop(channel, key="stam"):
    st = settings["stam"]
    while True:
        try:
            if tm.is_paused(key):
                await asyncio.sleep(0.5)
                continue
            raw = st.get("message", "")
            if not raw:
                await asyncio.sleep(2)
                continue
            text = apply_case(raw, resolve_case("stam"))
            text += apply_counter_style(st.get("counter", st.get("start_at", 1)), st.get("counter_style", "parens"))
            fmt = resolve_format("stam")
            chance = resolve_format_chance("stam")
            if fmt != "none" and random.randint(1, 100) <= chance:
                text = apply_format(text, fmt)
            bc = st.get("bold_chance", 0)
            if bc > 0 and random.randint(1, 100) <= bc:
                text = f"**{text}**"
            hc = st.get("header_chance", 0)
            if hc > 0 and random.randint(1, 100) <= hc:
                text = f"# {text}"
            if st.get("typing_indicator", False):
                await send_typing(channel)
                await asyncio.sleep(random.uniform(0.2, 0.5))
            await safe_send(channel, text)
            st["counter"] = st.get("counter", st.get("start_at", 1)) + 1
            save_settings()
            await asyncio.sleep(st.get("delay", 1.5))
            if not st.get("loop", True):
                break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[stam error] {e}")
            await asyncio.sleep(3)

@bot.command(name="stam")
async def cmd_stam(ctx, channel_id: int = None):
    if not channel_id:
        return await safe_send(ctx.channel, "Usage: ,stam <channel_id>")
    ch = bot.get_channel(channel_id)
    if not ch:
        return await safe_send(ctx.channel, "Channel not found.")
    settings["stam"]["counter"] = settings["stam"].get("start_at", 1)
    save_settings()
    tm.cancel("stam")
    task = asyncio.create_task(stam_loop(ch))
    tm.register("stam", task)
    await safe_send(ctx.channel, f"stam started in {channel_id} at counter {settings['stam']['counter']}.")

@bot.command(name="stamstop")
async def cmd_stamstop(ctx):
    tm.cancel("stam")
    await safe_send(ctx.channel, "stam stopped.")

@bot.command(name="stampause")
async def cmd_stampause(ctx):
    if tm.pause("stam"):
        await safe_send(ctx.channel, "stam paused.")
    else:
        await safe_send(ctx.channel, "stam is not running.")

@bot.command(name="stamresume")
async def cmd_stamresume(ctx):
    if tm.resume("stam"):
        await safe_send(ctx.channel, "stam resumed.")
    else:
        await safe_send(ctx.channel, "stam is not paused.")

@bot.command(name="staminfo")
async def cmd_staminfo(ctx):
    st = settings["stam"]
    current = st.get("counter", st.get("start_at", 1))
    msg = (
        f"```\n--- Stam Settings ---\n"
        f"Status:        {tm.status('stam')}\n"
        f"Message:       {st['message']}\n"
        f"Current num:   {current}\n"
        f"Start number:  {st.get('start_at', 1)}\n"
        f"Style:         {st.get('counter_style', 'parens')}\n"
        f"Delay:         {st['delay']}s\n"
        f"Case:          {st['case']}\n"
        f"Format:        {st['format']} (chance {st['format_chance']}%)\n"
        f"Bold chance:   {st['bold_chance']}%\n"
        f"Header chance: {st['header_chance']}%\n"
        f"Typing ind.:   {st['typing_indicator']}\n"
        f"Loop:          {st['loop']}\n```"
    )
    await safe_send(ctx.channel, msg)

@bot.command(name="stamreset")
async def cmd_stamreset(ctx):
    settings["stam"] = json.loads(json.dumps(DEFAULT_SETTINGS["stam"]))
    save_settings()
    await safe_send(ctx.channel, "stam settings reset to defaults.")

@bot.command(name="stammsg")
async def cmd_stammsg(ctx, *, text: str = None):
    if not text:
        return await safe_send(ctx.channel, "Usage: ,stammsg <text>")
    settings["stam"]["message"] = text
    save_settings()
    await safe_send(ctx.channel, f"stam message set to: {text}")

@bot.command(name="stamshow")
async def cmd_stamshow(ctx):
    await safe_send(ctx.channel, f"Current stam message: {settings['stam']['message']}")

@bot.command(name="stamstart")
async def cmd_stamstart(ctx, n: int = None):
    if n is None:
        return await safe_send(ctx.channel, f"Current start number: {settings['stam'].get('start_at', 1)}")
    if n < 0:
        return await safe_send(ctx.channel, "Must be >= 0.")
    settings["stam"]["start_at"] = n
    settings["stam"]["counter"] = n
    save_settings()
    await safe_send(ctx.channel, f"stam start number set to {n}.")

@bot.command(name="stamstyle")
async def cmd_stamstyle(ctx, style: str = None):
    if style is None:
        return await safe_send(ctx.channel, f"Current counter style: {settings['stam'].get('counter_style', 'parens')}")
    style = style.lower()
    valid = ("parens", "bracket", "dash", "colon", "none")
    if style not in valid:
        return await safe_send(ctx.channel, f"Valid: {', '.join(valid)}")
    settings["stam"]["counter_style"] = style
    save_settings()
    await safe_send(ctx.channel, f"Counter style: {style}")

@bot.command(name="stamdelay")
async def cmd_stamdelay(ctx, seconds: float = None):
    if seconds is None:
        return await safe_send(ctx.channel, f"Current delay: {settings['stam']['delay']}s")
    if seconds < 0.1:
        return await safe_send(ctx.channel, "Min delay is 0.1s.")
    settings["stam"]["delay"] = seconds
    save_settings()
    await safe_send(ctx.channel, f"stam delay set to {seconds}s.")

@bot.command(name="stamcase")
async def cmd_stamcase(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Current case: {settings['stam']['case']}")
    mode = mode.lower()
    if mode not in ("u", "l", "m", "global"):
        return await safe_send(ctx.channel, "Use u, l, m, or global.")
    settings["stam"]["case"] = mode.upper() if mode != "global" else "global"
    save_settings()
    await safe_send(ctx.channel, f"stam case set to {settings['stam']['case']}.")

@bot.command(name="stamformat")
async def cmd_stamformat(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Current format: {settings['stam']['format']}")
    valid = ("none", "bold", "italic", "code", "codeblock", "header", "quote", "spoiler", "global")
    if mode.lower() not in valid:
        return await safe_send(ctx.channel, f"Valid: {', '.join(valid)}")
    settings["stam"]["format"] = mode.lower()
    save_settings()
    await safe_send(ctx.channel, f"stam format set to {mode.lower()}.")

@bot.command(name="stamformatchance")
async def cmd_stamformatchance(ctx, pct: int = None):
    if pct is None:
        return await safe_send(ctx.channel, f"Current format chance: {settings['stam']['format_chance']}%")
    if not (0 <= pct <= 100):
        return await safe_send(ctx.channel, "Use 0-100.")
    settings["stam"]["format_chance"] = pct
    save_settings()
    await safe_send(ctx.channel, f"stam format chance: {pct}%")

@bot.command(name="stambold")
async def cmd_stambold(ctx, pct: int = None):
    if pct is None:
        return await safe_send(ctx.channel, f"Current bold chance: {settings['stam']['bold_chance']}%")
    if not (0 <= pct <= 100):
        return await safe_send(ctx.channel, "Use 0-100.")
    settings["stam"]["bold_chance"] = pct
    save_settings()
    await safe_send(ctx.channel, f"stam bold chance: {pct}%")

@bot.command(name="stamheader")
async def cmd_stamheader(ctx, pct: int = None):
    if pct is None:
        return await safe_send(ctx.channel, f"Current header chance: {settings['stam']['header_chance']}%")
    if not (0 <= pct <= 100):
        return await safe_send(ctx.channel, "Use 0-100.")
    settings["stam"]["header_chance"] = pct
    save_settings()
    await safe_send(ctx.channel, f"stam header chance: {pct}%")

@bot.command(name="stamtyping")
async def cmd_stamtyping(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Typing indicator: {settings['stam']['typing_indicator']}")
    val = mode.lower() == "on"
    settings["stam"]["typing_indicator"] = val
    save_settings()
    await safe_send(ctx.channel, f"Typing indicator: {'ON' if val else 'OFF'}")

@bot.command(name="stamloop")
async def cmd_stamloop(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Loop: {settings['stam']['loop']}")
    val = mode.lower() == "on"
    settings["stam"]["loop"] = val
    save_settings()
    await safe_send(ctx.channel, f"Loop: {'ON' if val else 'OFF'}")

# =========================================================
# AUTOCOUNT (count 1 → N in a channel)
# =========================================================
DEFAULT_SETTINGS["autocount"] = {
    "mode": "normal",
    "skip": False,
    "step": 1,
    "start_at": 1,
    "end_at": 10,
    "delay": 1.5,
    "prefix": "",
    "suffix": "",
    "case": "global",
    "format": "global",
    "format_chance": 100,
    "bold_chance": 0,
    "header_chance": 0,
    "typing_indicator": False,
    "loop": False,
}

if "autocount" not in settings:
    settings["autocount"] = json.loads(json.dumps(DEFAULT_SETTINGS["autocount"]))
else:
    for k, v in DEFAULT_SETTINGS["autocount"].items():
        if k not in settings["autocount"]:
            settings["autocount"][k] = v
save_settings()

HELP_AC = """```
--- Autocount Sub-Commands ---

Control:
  ,ac [channel_id]         start (uses saved start/end)
  ,acstop                  stop
  ,acpause / ,acresume     pause / resume
  ,acinfo                  show current settings
  ,acreset                 reset to defaults

Range:
  ,acstart <n>             set start number
  ,acend <n>               set end number

Style:
  ,acmode <mode>           number style (,acmodes)
  ,acstep <n>              count by n
  ,acskip on/off           skip a few numbers

Text:
  ,acprefix <text>         prefix each number (e.g. "count: ")
  ,acsuffix <text>         suffix each number (e.g. "!")

Delay:
  ,acdelay <seconds>

Formatting:
  ,accase <u/l/m/global>
  ,acformat <mode>
  ,acformatchance <0-100>
  ,acbold <0-100>
  ,acheader <0-100>

Other:
  ,actyping on/off
  ,acloop on/off           restart from start when hitting end
```"""

async def autocount_loop(channel, key="autocount"):
    ac = settings["autocount"]
    while True:
        try:
            if tm.is_paused(key):
                await asyncio.sleep(0.5)
                continue
            start = int(ac.get("start_at", 1))
            end = int(ac.get("end_at", 10))
            _st = max(1, int(ac.get("step", 1)))
            step = _st if end >= start else -_st
            for n in _ac_sequence(start, end, _st, ac.get("skip", False)):
                while tm.is_paused(key):
                    await asyncio.sleep(0.3)
                text = f"{ac.get('prefix', '')}{format_number(n, ac.get('mode', 'normal'))}{ac.get('suffix', '')}"
                text = apply_case(text, resolve_case("autocount"))
                fmt = resolve_format("autocount")
                chance = resolve_format_chance("autocount")
                if fmt != "none" and random.randint(1, 100) <= chance:
                    text = apply_format(text, fmt)
                bc = ac.get("bold_chance", 0)
                if bc > 0 and random.randint(1, 100) <= bc:
                    text = f"**{text}**"
                hc = ac.get("header_chance", 0)
                if hc > 0 and random.randint(1, 100) <= hc:
                    text = f"# {text}"
                if ac.get("typing_indicator", False):
                    await send_typing(channel)
                    await asyncio.sleep(random.uniform(0.2, 0.5))
                await safe_send(channel, text)
                await asyncio.sleep(ac.get("delay", 1.5))
            if not ac.get("loop", False):
                break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[autocount error] {e}")
            await asyncio.sleep(3)

@bot.command(name="ac", aliases=["autocount"])
async def cmd_ac(ctx, channel_id: int = None):
    if not channel_id:
        return await safe_send(ctx.channel, "Usage: ,ac <channel_id>")
    ch = bot.get_channel(channel_id)
    if not ch:
        return await safe_send(ctx.channel, "Channel not found.")
    tm.cancel("autocount")
    task = asyncio.create_task(autocount_loop(ch))
    tm.register("autocount", task)
    await safe_send(ctx.channel, f"autocount started in {channel_id} ({settings['autocount']['start_at']} → {settings['autocount']['end_at']}).")

@bot.command(name="acstop")
async def cmd_acstop(ctx):
    tm.cancel("autocount")
    await safe_send(ctx.channel, "autocount stopped.")

@bot.command(name="acpause")
async def cmd_acpause(ctx):
    if tm.pause("autocount"):
        await safe_send(ctx.channel, "autocount paused.")
    else:
        await safe_send(ctx.channel, "autocount is not running.")

@bot.command(name="acresume")
async def cmd_acresume(ctx):
    if tm.resume("autocount"):
        await safe_send(ctx.channel, "autocount resumed.")
    else:
        await safe_send(ctx.channel, "autocount is not paused.")

@bot.command(name="acinfo")
async def cmd_acinfo(ctx):
    ac = settings["autocount"]
    msg = (
        f"```\n--- Autocount Settings ---\n"
        f"Status:        {tm.status('autocount')}\n"
        f"Start:         {ac['start_at']}\n"
        f"End:           {ac['end_at']}\n"
        f"Prefix:        {ac['prefix']}\n"
        f"Suffix:        {ac['suffix']}\n"
        f"Delay:         {ac['delay']}s\n"
        f"Case:          {ac['case']}\n"
        f"Format:        {ac['format']} (chance {ac['format_chance']}%)\n"
        f"Bold chance:   {ac['bold_chance']}%\n"
        f"Header chance: {ac['header_chance']}%\n"
        f"Typing ind.:   {ac['typing_indicator']}\n"
        f"Loop:          {ac['loop']}\n```"
    )
    await safe_send(ctx.channel, msg)

@bot.command(name="acreset")
async def cmd_acreset(ctx):
    settings["autocount"] = json.loads(json.dumps(DEFAULT_SETTINGS["autocount"]))
    save_settings()
    await safe_send(ctx.channel, "autocount settings reset to defaults.")

@bot.command(name="acstart")
async def cmd_acstart(ctx, n: int = None):
    if n is None:
        return await safe_send(ctx.channel, f"Current start: {settings['autocount']['start_at']}")
    settings["autocount"]["start_at"] = n
    save_settings()
    await safe_send(ctx.channel, f"autocount start set to {n}.")

@bot.command(name="acend")
async def cmd_acend(ctx, n: int = None):
    if n is None:
        return await safe_send(ctx.channel, f"Current end: {settings['autocount']['end_at']}")
    settings["autocount"]["end_at"] = n
    save_settings()
    await safe_send(ctx.channel, f"autocount end set to {n}.")

@bot.command(name="acprefix")
async def cmd_acprefix(ctx, *, text: str = None):
    if text is None:
        return await safe_send(ctx.channel, f"Current prefix: {settings['autocount']['prefix']!r}")
    settings["autocount"]["prefix"] = text
    save_settings()
    await safe_send(ctx.channel, f"autocount prefix set to: {text!r}")

@bot.command(name="acsuffix")
async def cmd_acsuffix(ctx, *, text: str = None):
    if text is None:
        return await safe_send(ctx.channel, f"Current suffix: {settings['autocount']['suffix']!r}")
    settings["autocount"]["suffix"] = text
    save_settings()
    await safe_send(ctx.channel, f"autocount suffix set to: {text!r}")

@bot.command(name="acdelay")
async def cmd_acdelay(ctx, seconds: float = None):
    if seconds is None:
        return await safe_send(ctx.channel, f"Current delay: {settings['autocount']['delay']}s")
    if seconds < 0.1:
        return await safe_send(ctx.channel, "Min delay is 0.1s.")
    settings["autocount"]["delay"] = seconds
    save_settings()
    await safe_send(ctx.channel, f"autocount delay set to {seconds}s.")

@bot.command(name="accase")
async def cmd_accase(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Current case: {settings['autocount']['case']}")
    mode = mode.lower()
    if mode not in ("u", "l", "m", "global"):
        return await safe_send(ctx.channel, "Use u, l, m, or global.")
    settings["autocount"]["case"] = mode.upper() if mode != "global" else "global"
    save_settings()
    await safe_send(ctx.channel, f"autocount case set to {settings['autocount']['case']}.")

@bot.command(name="acformat")
async def cmd_acformat(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Current format: {settings['autocount']['format']}")
    valid = ("none", "bold", "italic", "code", "codeblock", "header", "quote", "spoiler", "global")
    if mode.lower() not in valid:
        return await safe_send(ctx.channel, f"Valid: {', '.join(valid)}")
    settings["autocount"]["format"] = mode.lower()
    save_settings()
    await safe_send(ctx.channel, f"autocount format set to {mode.lower()}.")

@bot.command(name="acformatchance")
async def cmd_acformatchance(ctx, pct: int = None):
    if pct is None:
        return await safe_send(ctx.channel, f"Current format chance: {settings['autocount']['format_chance']}%")
    if not (0 <= pct <= 100):
        return await safe_send(ctx.channel, "Use 0-100.")
    settings["autocount"]["format_chance"] = pct
    save_settings()
    await safe_send(ctx.channel, f"autocount format chance: {pct}%")

@bot.command(name="acbold")
async def cmd_acbold(ctx, pct: int = None):
    if pct is None:
        return await safe_send(ctx.channel, f"Current bold chance: {settings['autocount']['bold_chance']}%")
    if not (0 <= pct <= 100):
        return await safe_send(ctx.channel, "Use 0-100.")
    settings["autocount"]["bold_chance"] = pct
    save_settings()
    await safe_send(ctx.channel, f"autocount bold chance: {pct}%")

@bot.command(name="acheader")
async def cmd_acheader(ctx, pct: int = None):
    if pct is None:
        return await safe_send(ctx.channel, f"Current header chance: {settings['autocount']['header_chance']}%")
    if not (0 <= pct <= 100):
        return await safe_send(ctx.channel, "Use 0-100.")
    settings["autocount"]["header_chance"] = pct
    save_settings()
    await safe_send(ctx.channel, f"autocount header chance: {pct}%")

@bot.command(name="actyping")
async def cmd_actyping(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Typing indicator: {settings['autocount']['typing_indicator']}")
    val = mode.lower() == "on"
    settings["autocount"]["typing_indicator"] = val
    save_settings()
    await safe_send(ctx.channel, f"Typing indicator: {'ON' if val else 'OFF'}")

@bot.command(name="acloop")
async def cmd_acloop(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Loop: {settings['autocount']['loop']}")
    val = mode.lower() == "on"
    settings["autocount"]["loop"] = val
    save_settings()
    await safe_send(ctx.channel, f"Loop: {'ON' if val else 'OFF'}")


# =========================================================
# GCNAME (rotate group chat names)
# =========================================================
DEFAULT_SETTINGS["gcname"] = {
    "file": "gcname.txt",
    "delay": 2.0,
    "random_order": True,
    "loop": True,
}

if "gcname" not in settings:
    settings["gcname"] = json.loads(json.dumps(DEFAULT_SETTINGS["gcname"]))
else:
    for k, v in DEFAULT_SETTINGS["gcname"].items():
        if k not in settings["gcname"]:
            settings["gcname"][k] = v
save_settings()

HELP_GC = """```
--- Gcname Sub-Commands ---

Control:
  ,gcname [channel_id]     start rotating GC name
  ,gcnamestop              stop
  ,gcnamepause / ,gcnameresume
  ,gcnameinfo              show current settings
  ,gcnamereset             reset to defaults

File:
  ,gcnamefile <name>       change the names file
  ,gcnamefiles             list .txt files

Delay:
  ,gcnamedelay <seconds>

Other:
  ,gcnameshuffle on/off    random order
  ,gcnameloop on/off       restart file when exhausted
```"""

async def gcname_loop(channel, key="gcname"):
    gc = settings["gcname"]
    if not load_lines(gc["file"]):
        await safe_send(channel, f"[gcname] {gc['file']} is empty or missing.")
        return
    while True:
        try:
            if tm.is_paused(key):
                await asyncio.sleep(0.5)
                continue
            names = load_lines(gc["file"])
            if not names:
                await asyncio.sleep(2)
                continue
            if gc.get("random_order", True):
                random.shuffle(names)
            for name in names:
                while tm.is_paused(key):
                    await asyncio.sleep(0.3)
                await safe_edit(channel, name=name)
                await asyncio.sleep(gc.get("delay", 2.0))
            if not gc.get("loop", True):
                break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[gcname error] {e}")
            await asyncio.sleep(3)

@bot.command(name="gcname")
async def cmd_gcname(ctx, channel_id: int = None):
    if not channel_id:
        return await safe_send(ctx.channel, "Usage: ,gcname <channel_id>")
    ch = bot.get_channel(channel_id)
    if not ch:
        return await safe_send(ctx.channel, "Channel not found.")
    if not isinstance(ch, discord.GroupChannel):
        return await safe_send(ctx.channel, "Not a group channel.")
    tm.cancel("gcname")
    task = asyncio.create_task(gcname_loop(ch))
    tm.register("gcname", task)
    await safe_send(ctx.channel, f"gcname started in {channel_id} using {settings['gcname']['file']}.")

@bot.command(name="gcnamestop")
async def cmd_gcnamestop(ctx):
    tm.cancel("gcname")
    await safe_send(ctx.channel, "gcname stopped.")

@bot.command(name="gcnamepause")
async def cmd_gcnamepause(ctx):
    if tm.pause("gcname"):
        await safe_send(ctx.channel, "gcname paused.")
    else:
        await safe_send(ctx.channel, "gcname is not running.")

@bot.command(name="gcnameresume")
async def cmd_gcnameresume(ctx):
    if tm.resume("gcname"):
        await safe_send(ctx.channel, "gcname resumed.")
    else:
        await safe_send(ctx.channel, "gcname is not paused.")

@bot.command(name="gcnameinfo")
async def cmd_gcnameinfo(ctx):
    gc = settings["gcname"]
    msg = (
        f"```\n--- Gcname Settings ---\n"
        f"Status:        {tm.status('gcname')}\n"
        f"File:          {gc['file']}\n"
        f"Delay:         {gc['delay']}s\n"
        f"Random order:  {gc['random_order']}\n"
        f"Loop:          {gc['loop']}\n```"
    )
    await safe_send(ctx.channel, msg)

@bot.command(name="gcnamereset")
async def cmd_gcnamereset(ctx):
    settings["gcname"] = json.loads(json.dumps(DEFAULT_SETTINGS["gcname"]))
    save_settings()
    await safe_send(ctx.channel, "gcname settings reset.")

@bot.command(name="gcnamefile")
async def cmd_gcnamefile(ctx, filename: str = None):
    if not filename:
        return await safe_send(ctx.channel, f"Current file: {settings['gcname']['file']}")
    fname = normalize_filename(filename, "gcname.txt")
    if not os.path.exists(fname):
        return await safe_send(ctx.channel, f"{fname} not found.")
    settings["gcname"]["file"] = fname
    save_settings()
    await safe_send(ctx.channel, f"gcname file set to {fname}.")

@bot.command(name="gcnamefiles")
async def cmd_gcnamefiles(ctx):
    txts = sorted([f for f in os.listdir(".") if f.endswith(".txt")])
    if not txts:
        return await safe_send(ctx.channel, "No .txt files.")
    await safe_send(ctx.channel, "Available:\n" + "\n".join(f"- {f}" for f in txts))

@bot.command(name="gcnamedelay")
async def cmd_gcnamedelay(ctx, seconds: float = None):
    if seconds is None:
        return await safe_send(ctx.channel, f"Current delay: {settings['gcname']['delay']}s")
    if seconds < 0.5:
        return await safe_send(ctx.channel, "Min delay is 0.5s (Discord rate limits rename).")
    settings["gcname"]["delay"] = seconds
    save_settings()
    await safe_send(ctx.channel, f"gcname delay set to {seconds}s.")

@bot.command(name="gcnameshuffle")
async def cmd_gcnameshuffle(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Random order: {settings['gcname']['random_order']}")
    val = mode.lower() == "on"
    settings["gcname"]["random_order"] = val
    save_settings()
    await safe_send(ctx.channel, f"Random order: {'ON' if val else 'OFF'}")

@bot.command(name="gcnameloop")
async def cmd_gcnameloop(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Loop: {settings['gcname']['loop']}")
    val = mode.lower() == "on"
    settings["gcname"]["loop"] = val
    save_settings()
    await safe_send(ctx.channel, f"Loop: {'ON' if val else 'OFF'}")

# =========================================================
# KILLGC (rapid-fire rename a group chat)
# =========================================================
DEFAULT_SETTINGS["killgc"] = {
    "file": "gcname.txt",
    "delay": 1.0,
    "random_order": True,
    "loop": True,
}

if "killgc" not in settings:
    settings["killgc"] = json.loads(json.dumps(DEFAULT_SETTINGS["killgc"]))
else:
    for k, v in DEFAULT_SETTINGS["killgc"].items():
        if k not in settings["killgc"]:
            settings["killgc"][k] = v
save_settings()

HELP_KILLGC = """```
--- Killgc Sub-Commands ---

Control:
  ,killgc [channel_id]     start
  ,killgcstop              stop
  ,killgcpause / ,killgcresume
  ,killgcinfo              show current settings
  ,killgcreset             reset to defaults

File:
  ,killgcfile <name>       change the names file
  ,killgcfiles             list .txt files

Delay:
  ,killgcdelay <seconds>

Other:
  ,killgcshuffle on/off
  ,killgcloop on/off
```"""

async def killgc_loop(channel, key="killgc"):
    kg = settings["killgc"]
    if not load_lines(kg["file"]):
        await safe_send(channel, f"[killgc] {kg['file']} is empty or missing.")
        return
    while True:
        try:
            if tm.is_paused(key):
                await asyncio.sleep(0.5)
                continue
            names = load_lines(kg["file"])
            if not names:
                await asyncio.sleep(2)
                continue
            if kg.get("random_order", True):
                random.shuffle(names)
            for name in names:
                while tm.is_paused(key):
                    await asyncio.sleep(0.3)
                await safe_edit(channel, name=name)
                await asyncio.sleep(kg.get("delay", 1.0))
            if not kg.get("loop", True):
                break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[killgc error] {e}")
            await asyncio.sleep(3)

@bot.command(name="killgc")
async def cmd_killgc(ctx, channel_id: int = None):
    if not channel_id:
        return await safe_send(ctx.channel, "Usage: ,killgc <channel_id>")
    ch = bot.get_channel(channel_id)
    if not ch:
        return await safe_send(ctx.channel, "Channel not found.")
    if not isinstance(ch, discord.GroupChannel):
        return await safe_send(ctx.channel, "Not a group channel.")
    tm.cancel("killgc")
    task = asyncio.create_task(killgc_loop(ch))
    tm.register("killgc", task)
    await safe_send(ctx.channel, f"killgc started in {channel_id} using {settings['killgc']['file']}.")

@bot.command(name="killgcstop")
async def cmd_killgcstop(ctx):
    tm.cancel("killgc")
    await safe_send(ctx.channel, "killgc stopped.")

@bot.command(name="killgcpause")
async def cmd_killgcpause(ctx):
    if tm.pause("killgc"):
        await safe_send(ctx.channel, "killgc paused.")
    else:
        await safe_send(ctx.channel, "killgc is not running.")

@bot.command(name="killgcresume")
async def cmd_killgcresume(ctx):
    if tm.resume("killgc"):
        await safe_send(ctx.channel, "killgc resumed.")
    else:
        await safe_send(ctx.channel, "killgc is not paused.")

@bot.command(name="killgcinfo")
async def cmd_killgcinfo(ctx):
    kg = settings["killgc"]
    msg = (
        f"```\n--- Killgc Settings ---\n"
        f"Status:        {tm.status('killgc')}\n"
        f"File:          {kg['file']}\n"
        f"Delay:         {kg['delay']}s\n"
        f"Random order:  {kg['random_order']}\n"
        f"Loop:          {kg['loop']}\n```"
    )
    await safe_send(ctx.channel, msg)

@bot.command(name="killgcreset")
async def cmd_killgcreset(ctx):
    settings["killgc"] = json.loads(json.dumps(DEFAULT_SETTINGS["killgc"]))
    save_settings()
    await safe_send(ctx.channel, "killgc settings reset.")

@bot.command(name="killgcfile")
async def cmd_killgcfile(ctx, filename: str = None):
    if not filename:
        return await safe_send(ctx.channel, f"Current file: {settings['killgc']['file']}")
    fname = normalize_filename(filename, "gcname.txt")
    if not os.path.exists(fname):
        return await safe_send(ctx.channel, f"{fname} not found.")
    settings["killgc"]["file"] = fname
    save_settings()
    await safe_send(ctx.channel, f"killgc file set to {fname}.")

@bot.command(name="killgcfiles")
async def cmd_killgcfiles(ctx):
    txts = sorted([f for f in os.listdir(".") if f.endswith(".txt")])
    if not txts:
        return await safe_send(ctx.channel, "No .txt files.")
    await safe_send(ctx.channel, "Available:\n" + "\n".join(f"- {f}" for f in txts))

@bot.command(name="killgcdelay")
async def cmd_killgcdelay(ctx, seconds: float = None):
    if seconds is None:
        return await safe_send(ctx.channel, f"Current delay: {settings['killgc']['delay']}s")
    if seconds < 0.5:
        return await safe_send(ctx.channel, "Min delay is 0.5s (Discord rate limits rename).")
    settings["killgc"]["delay"] = seconds
    save_settings()
    await safe_send(ctx.channel, f"killgc delay set to {seconds}s.")

@bot.command(name="killgcshuffle")
async def cmd_killgcshuffle(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Random order: {settings['killgc']['random_order']}")
    val = mode.lower() == "on"
    settings["killgc"]["random_order"] = val
    save_settings()
    await safe_send(ctx.channel, f"Random order: {'ON' if val else 'OFF'}")

@bot.command(name="killgcloop")
async def cmd_killgcloop(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Loop: {settings['killgc']['loop']}")
    val = mode.lower() == "on"
    settings["killgc"]["loop"] = val
    save_settings()
    await safe_send(ctx.channel, f"Loop: {'ON' if val else 'OFF'}")

# =========================================================
# ANTIGC (auto-leave new group chats, optional message + rename)
# =========================================================
DEFAULT_SETTINGS["antigc"] = {
    "enabled": False,
    "message": "",
    "rename_to": "",
    "leave_delay": 1.2,
    "whitelist": [],
    "blacklist": [],
}

if "antigc" not in settings:
    settings["antigc"] = json.loads(json.dumps(DEFAULT_SETTINGS["antigc"]))
else:
    for k, v in DEFAULT_SETTINGS["antigc"].items():
        if k not in settings["antigc"]:
            settings["antigc"][k] = v
save_settings()

HELP_ANTIGC = """```
--- Antigc Sub-Commands ---

  ,antigc on               enable auto-leave
  ,antigc off              disable
  ,antigc status           show status
  ,antigc info             show all settings

Message:
  ,antigcmsg <text>        message sent before leaving (empty = none)
  ,antigcmsgclear          clear the message

Rename:
  ,antigcrename <name>     rename GC before leaving (empty = none)
  ,antigcrenameclear       clear the rename

Delay:
  ,antigcdelay <seconds>   delay between message and leave

Whitelist (users you NEVER want to trigger auto-leave):
  ,antigcwl @user          add user to whitelist
  ,antigcwlun @user        remove
  ,antigcwllist            show whitelist
  ,antigcwlclear           clear whitelist

Blacklist (users who ALWAYS trigger auto-leave):
  ,antigcbl @user          add user to blacklist
  ,antigcblun @user        remove
  ,antigcbllist            show blacklist
  ,antigcblclear           clear blacklist

  ,antigcreset             reset to defaults
```"""

async def antigc_handle(channel):
    ac = settings["antigc"]
    try:
        # Rename first if set
        if ac.get("rename_to"):
            try:
                await channel.edit(name=ac["rename_to"])
                await asyncio.sleep(ac.get("leave_delay", 1.2))
            except Exception as e:
                print(f"[antigc rename] {e}")

        # Send message if set
        if ac.get("message"):
            try:
                await channel.send(ac["message"])
                await asyncio.sleep(ac.get("leave_delay", 1.2))
            except Exception as e:
                print(f"[antigc msg] {e}")

        # Leave
        await channel.leave()
        print(f"[antigc] Left GC {channel.id}")

    except Exception as e:
        print(f"[antigc handle] {e}")

def antigc_should_leave(channel):
    """Check whitelist / blacklist and decide."""
    ac = settings["antigc"]
    recipients = getattr(channel, "recipients", []) or []
    recipient_ids = {u.id for u in recipients}
    if u_self_id in recipient_ids:
        recipient_ids.discard(u_self_id)

    whitelist = set(ac.get("whitelist", []))
    blacklist = set(ac.get("blacklist", []))

    # If anyone in blacklist is here → leave
    if blacklist & recipient_ids:
        return True

    # If everyone is whitelisted → don't leave
    if recipient_ids and recipient_ids.issubset(whitelist):
        return False

    # If any non-whitelisted user is here → leave
    if whitelist:
        # Some are whitelisted, some aren't
        non_whitelisted = recipient_ids - whitelist
        if non_whitelisted:
            return True
        return False

    # No rules → default leave
    return True

u_self_id = None

@bot.event
async def on_ready_antigc_hook():
    global u_self_id
    if bot.user:
        u_self_id = bot.user.id
        print(f"[antigc] hooked self id = {u_self_id}")

# Attach hook after bot is ready
@bot.event
async def on_private_channel_create(channel):
    if not settings["antigc"].get("enabled"):
        return
    if not isinstance(channel, discord.GroupChannel):
        return
    global u_self_id
    if u_self_id is None and bot.user:
        u_self_id = bot.user.id
    if antigc_should_leave(channel):
        await antigc_handle(channel)

@bot.command(name="antigc")
async def cmd_antigc(ctx, mode: str = None):
    ac = settings["antigc"]
    if mode is None or mode.lower() == "status":
        state = "ON" if ac.get("enabled") else "OFF"
        return await safe_send(ctx.channel, f"antigc: {state}")
    m = mode.lower()
    if m == "on":
        ac["enabled"] = True
        save_settings()
        await safe_send(ctx.channel, "antigc ON.")
    elif m == "off":
        ac["enabled"] = False
        save_settings()
        await safe_send(ctx.channel, "antigc OFF.")
    elif m == "info":
        wl = ac.get("whitelist", [])
        bl = ac.get("blacklist", [])
        wl_str = ", ".join(f"<@{u}>" for u in wl) if wl else "none"
        bl_str = ", ".join(f"<@{u}>" for u in bl) if bl else "none"
        msg = (
            f"```\n--- Antigc Settings ---\n"
            f"Enabled:     {ac.get('enabled')}\n"
            f"Message:     {ac.get('message') or '(none)'}\n"
            f"Rename to:   {ac.get('rename_to') or '(none)'}\n"
            f"Leave delay: {ac.get('leave_delay')}s\n"
            f"Whitelist:   {wl_str}\n"
            f"Blacklist:   {bl_str}\n```"
        )
        await safe_send(ctx.channel, msg)
    else:
        await safe_send(ctx.channel, "Use: ,antigc on | off | status | info")

@bot.command(name="antigcmsg")
async def cmd_antigcmsg(ctx, *, text: str = None):
    if text is None:
        return await safe_send(ctx.channel, f"Current message: {settings['antigc'].get('message') or '(none)'}")
    settings["antigc"]["message"] = text
    save_settings()
    await safe_send(ctx.channel, f"antigc message set to: {text}")

@bot.command(name="antigcmsgclear")
async def cmd_antigcmsgclear(ctx):
    settings["antigc"]["message"] = ""
    save_settings()
    await safe_send(ctx.channel, "antigc message cleared.")

@bot.command(name="antigcrename")
async def cmd_antigcrename(ctx, *, name: str = None):
    if name is None:
        return await safe_send(ctx.channel, f"Current rename: {settings['antigc'].get('rename_to') or '(none)'}")
    settings["antigc"]["rename_to"] = name
    save_settings()
    await safe_send(ctx.channel, f"antigc rename set to: {name}")

@bot.command(name="antigcrenameclear")
async def cmd_antigcrenameclear(ctx):
    settings["antigc"]["rename_to"] = ""
    save_settings()
    await safe_send(ctx.channel, "antigc rename cleared.")

@bot.command(name="antigcdelay")
async def cmd_antigcdelay(ctx, seconds: float = None):
    if seconds is None:
        return await safe_send(ctx.channel, f"Current delay: {settings['antigc']['leave_delay']}s")
    if seconds < 0:
        return await safe_send(ctx.channel, "Must be >= 0.")
    settings["antigc"]["leave_delay"] = seconds
    save_settings()
    await safe_send(ctx.channel, f"antigc leave delay set to {seconds}s.")

@bot.command(name="antigcwl")
async def cmd_antigcwl(ctx, target: str = None):
    if not target:
        return await safe_send(ctx.channel, "Usage: ,antigcwl @user")
    uid = ctx.message.mentions[0].id if ctx.message.mentions else None
    if uid is None:
        try: uid = int(target.strip("<@!>"))
        except: return await safe_send(ctx.channel, "Invalid user.")
    wl = settings["antigc"]["whitelist"]
    if uid in wl:
        return await safe_send(ctx.channel, "Already whitelisted.")
    wl.append(uid)
    save_settings()
    await safe_send(ctx.channel, f"Whitelisted <@{uid}>.")

@bot.command(name="antigcwlun")
async def cmd_antigcwlun(ctx, target: str = None):
    if not target:
        return await safe_send(ctx.channel, "Usage: ,antigcwlun @user")
    uid = ctx.message.mentions[0].id if ctx.message.mentions else None
    if uid is None:
        try: uid = int(target.strip("<@!>"))
        except: return await safe_send(ctx.channel, "Invalid user.")
    wl = settings["antigc"]["whitelist"]
    if uid not in wl:
        return await safe_send(ctx.channel, "Not whitelisted.")
    wl.remove(uid)
    save_settings()
    await safe_send(ctx.channel, f"Unwhitelisted <@{uid}>.")

@bot.command(name="antigcwllist")
async def cmd_antigcwllist(ctx):
    wl = settings["antigc"]["whitelist"]
    if not wl:
        return await safe_send(ctx.channel, "Whitelist is empty.")
    await safe_send(ctx.channel, "Whitelist:\n" + "\n".join(f"- <@{u}>" for u in wl))

@bot.command(name="antigcwlclear")
async def cmd_antigcwlclear(ctx):
    settings["antigc"]["whitelist"] = []
    save_settings()
    await safe_send(ctx.channel, "Whitelist cleared.")

@bot.command(name="antigcbl")
async def cmd_antigcbl(ctx, target: str = None):
    if not target:
        return await safe_send(ctx.channel, "Usage: ,antigcbl @user")
    uid = ctx.message.mentions[0].id if ctx.message.mentions else None
    if uid is None:
        try: uid = int(target.strip("<@!>"))
        except: return await safe_send(ctx.channel, "Invalid user.")
    bl = settings["antigc"]["blacklist"]
    if uid in bl:
        return await safe_send(ctx.channel, "Already blacklisted.")
    bl.append(uid)
    save_settings()
    await safe_send(ctx.channel, f"Blacklisted <@{uid}>.")

@bot.command(name="antigcblun")
async def cmd_antigcblun(ctx, target: str = None):
    if not target:
        return await safe_send(ctx.channel, "Usage: ,antigcblun @user")
    uid = ctx.message.mentions[0].id if ctx.message.mentions else None
    if uid is None:
        try: uid = int(target.strip("<@!>"))
        except: return await safe_send(ctx.channel, "Invalid user.")
    bl = settings["antigc"]["blacklist"]
    if uid not in bl:
        return await safe_send(ctx.channel, "Not blacklisted.")
    bl.remove(uid)
    save_settings()
    await safe_send(ctx.channel, f"Unblacklisted <@{uid}>.")

@bot.command(name="antigcbllist")
async def cmd_antigcbllist(ctx):
    bl = settings["antigc"]["blacklist"]
    if not bl:
        return await safe_send(ctx.channel, "Blacklist is empty.")
    await safe_send(ctx.channel, "Blacklist:\n" + "\n".join(f"- <@{u}>" for u in bl))

@bot.command(name="antigcblclear")
async def cmd_antigcblclear(ctx):
    settings["antigc"]["blacklist"] = []
    save_settings()
    await safe_send(ctx.channel, "Blacklist cleared.")

@bot.command(name="antigcreset")
async def cmd_antigcreset(ctx):
    settings["antigc"] = json.loads(json.dumps(DEFAULT_SETTINGS["antigc"]))
    save_settings()
    await safe_send(ctx.channel, "antigc settings reset.")

# =========================================================
# LEAVEALLGCS (bulk leave every group chat)
# =========================================================
@bot.command(name="leaveallgcs", aliases=["lag"])
async def cmd_leaveallgcs(ctx):
    gcs = [ch for ch in bot.private_channels if isinstance(ch, discord.GroupChannel)]
    if not gcs:
        return await safe_send(ctx.channel, "No group chats to leave.")
    await safe_send(ctx.channel, f"Leaving {len(gcs)} GC(s)...")
    count = 0
    failed = 0
    for ch in gcs:
        try:
            await ch.leave()
            count += 1
            await asyncio.sleep(0.9)
        except Exception as e:
            failed += 1
            print(f"[leaveallgcs] failed to leave {ch.id}: {e}")
    msg = f"Left {count} GC(s)."
    if failed:
        msg += f" {failed} failed."
    await safe_send(ctx.channel, msg)

@bot.command(name="gclist")
async def cmd_gclist(ctx):
    gcs = [ch for ch in bot.private_channels if isinstance(ch, discord.GroupChannel)]
    if not gcs:
        return await safe_send(ctx.channel, "No group chats found.")
    lines = []
    for ch in gcs:
        try:
            name = ch.name or "(no name)"
            lines.append(f"- {name} | id: {ch.id} | members: {len(ch.recipients) + 1}")
        except Exception:
            lines.append(f"- (unknown) | id: {ch.id}")
    header = f"Group chats ({len(gcs)}):\n"
    # Split into chunks of 15 to avoid message-length issues
    chunk_size = 15
    for i in range(0, len(lines), chunk_size):
        part = header if i == 0 else ""
        part += "\n".join(lines[i:i + chunk_size])
        await safe_send(ctx.channel, part)
        if i + chunk_size < len(lines):
            await asyncio.sleep(0.5)

# =========================================================
# AUTOREACT (react to self or specific users)
# =========================================================
DEFAULT_SETTINGS["autoreact"] = {
    "self_react_emoji": "",
    "targets": {},      # { user_id: "emoji" }
}

if "autoreact" not in settings:
    settings["autoreact"] = json.loads(json.dumps(DEFAULT_SETTINGS["autoreact"]))
else:
    for k, v in DEFAULT_SETTINGS["autoreact"].items():
        if k not in settings["autoreact"]:
            settings["autoreact"][k] = v
save_settings()

HELP_REACT = """```
--- Autoreact Sub-Commands ---

Self-react (bot reacts to its own messages):
  ,selfreact <emoji>       set emoji
  ,selfreactoff            turn off
  ,selfreactinfo           show current

Target-react (bot reacts to specific users' messages):
  ,react @user <emoji>     add target
  ,react <user_id> <emoji> add target by ID
  ,reactlist               show targets
  ,reactun @user           remove one target
  ,reactun <user_id>       remove by ID
  ,reactclear              remove all targets
  ,reactoff                turn off everything (self + targets)
```"""

@bot.command(name="selfreact")
async def cmd_selfreact(ctx, emoji: str = None):
    if not emoji:
        return await safe_send(ctx.channel, "Usage: ,selfreact <emoji>")
    settings["autoreact"]["self_react_emoji"] = emoji
    save_settings()
    await safe_send(ctx.channel, f"Self-react emoji set to: {emoji}")

@bot.command(name="selfreactoff")
async def cmd_selfreactoff(ctx):
    settings["autoreact"]["self_react_emoji"] = ""
    save_settings()
    await safe_send(ctx.channel, "Self-react off.")

@bot.command(name="selfreactinfo")
async def cmd_selfreactinfo(ctx):
    emo = settings["autoreact"].get("self_react_emoji", "")
    if not emo:
        return await safe_send(ctx.channel, "Self-react: off")
    await safe_send(ctx.channel, f"Self-react emoji: {emo}")

@bot.command(name="react")
async def cmd_react(ctx, user: str = None, emoji: str = None):
    if not user or not emoji:
        return await safe_send(ctx.channel, "Usage: ,react @user <emoji>  OR  ,react <user_id> <emoji>")
    uid = None
    if ctx.message.mentions:
        uid = ctx.message.mentions[0].id
    else:
        try: uid = int(user.strip("<@!>"))
        except: return await safe_send(ctx.channel, "Invalid user.")
    settings["autoreact"]["targets"][str(uid)] = emoji
    save_settings()
    await safe_send(ctx.channel, f"Added <@{uid}> → {emoji}")

@bot.command(name="reactun")
async def cmd_reactun(ctx, user: str = None):
    if not user:
        return await safe_send(ctx.channel, "Usage: ,reactun @user OR ,reactun <user_id>")
    uid = None
    if ctx.message.mentions:
        uid = ctx.message.mentions[0].id
    else:
        try: uid = int(user.strip("<@!>"))
        except: return await safe_send(ctx.channel, "Invalid user.")
    targets = settings["autoreact"]["targets"]
    if str(uid) not in targets:
        return await safe_send(ctx.channel, "Not in list.")
    del targets[str(uid)]
    save_settings()
    await safe_send(ctx.channel, f"Removed <@{uid}>.")

@bot.command(name="reactlist")
async def cmd_reactlist(ctx):
    targets = settings["autoreact"]["targets"]
    self_emo = settings["autoreact"].get("self_react_emoji", "")
    lines = []
    if self_emo:
        lines.append(f"SELF → {self_emo}")
    for uid, emo in targets.items():
        lines.append(f"<@{uid}> → {emo}")
    if not lines:
        return await safe_send(ctx.channel, "No reactions set.")
    await safe_send(ctx.channel, "Current reactions:\n" + "\n".join(lines))

@bot.command(name="reactclear")
async def cmd_reactclear(ctx):
    settings["autoreact"]["targets"] = {}
    save_settings()
    await safe_send(ctx.channel, "All target reacts cleared.")

@bot.command(name="reactoff")
async def cmd_reactoff(ctx):
    settings["autoreact"]["targets"] = {}
    settings["autoreact"]["self_react_emoji"] = ""
    save_settings()
    await safe_send(ctx.channel, "Autoreact fully cleared (self + targets).")

# Hook into on_message (append to existing handler via another listener is not possible,
# so we route through a helper that on_message checks)
async def autoreact_try(message):
    try:
        cfg = settings.get("autoreact", {})
        # Self-react
        if message.author.id == bot.user.id:
            emo = cfg.get("self_react_emoji", "")
            if emo:
                try: await message.add_reaction(emo)
                except Exception as e: print(f"[selfreact] {e}")
            return
        # Target react
        targets = cfg.get("targets", {})
        emo = targets.get(str(message.author.id))
        if emo:
            try: await message.add_reaction(emo)
            except Exception as e: print(f"[react] {e}")
    except Exception as e:
        print(f"[autoreact hook] {e}")

# =========================================================
# AUTOREPLY (auto-reply to specific users)
# =========================================================
DEFAULT_SETTINGS["autoreply"] = {
    "targets": {},   # { user_id: "reply text" }
}

if "autoreply" not in settings:
    settings["autoreply"] = json.loads(json.dumps(DEFAULT_SETTINGS["autoreply"]))
else:
    for k, v in DEFAULT_SETTINGS["autoreply"].items():
        if k not in settings["autoreply"]:
            settings["autoreply"][k] = v
save_settings()

HELP_REPLY = """```
--- Autoreply Sub-Commands ---

  ,ar @user <text>         add autoreply for user
  ,ar <user_id> <text>     add by ID
  ,arlist                  show all autoreplies
  ,arun @user              remove one
  ,arun <user_id>          remove by ID
  ,arclear                 remove all
```"""

@bot.command(name="ar", aliases=["autoreply"])
async def cmd_ar(ctx, user: str = None, *, text: str = None):
    if not user or not text:
        return await safe_send(ctx.channel, "Usage: ,ar @user <reply text>")
    uid = None
    if ctx.message.mentions:
        uid = ctx.message.mentions[0].id
    else:
        try: uid = int(user.strip("<@!>"))
        except: return await safe_send(ctx.channel, "Invalid user.")
    settings["autoreply"]["targets"][str(uid)] = text
    save_settings()
    await safe_send(ctx.channel, f"Autoreply set for <@{uid}>: {text}")

@bot.command(name="arun")
async def cmd_arun(ctx, user: str = None):
    if not user:
        return await safe_send(ctx.channel, "Usage: ,arun @user OR ,arun <user_id>")
    uid = None
    if ctx.message.mentions:
        uid = ctx.message.mentions[0].id
    else:
        try: uid = int(user.strip("<@!>"))
        except: return await safe_send(ctx.channel, "Invalid user.")
    targets = settings["autoreply"]["targets"]
    if str(uid) not in targets:
        return await safe_send(ctx.channel, "Not in list.")
    del targets[str(uid)]
    save_settings()
    await safe_send(ctx.channel, f"Removed <@{uid}>.")

@bot.command(name="arlist")
async def cmd_arlist(ctx):
    targets = settings["autoreply"]["targets"]
    if not targets:
        return await safe_send(ctx.channel, "No autoreplies set.")
    lines = [f"<@{uid}> → {txt}" for uid, txt in targets.items()]
    await safe_send(ctx.channel, "Autoreplies:\n" + "\n".join(lines))

@bot.command(name="arclear")
async def cmd_arclear(ctx):
    settings["autoreply"]["targets"] = {}
    save_settings()
    await safe_send(ctx.channel, "All autoreplies cleared.")

async def autoreply_try(message):
    try:
        if message.author.id == bot.user.id:
            return
        cfg = settings.get("autoreply", {})
        targets = cfg.get("targets", {})
        txt = targets.get(str(message.author.id))
        if txt:
            try:
                await message.reply(txt)
            except Exception as e:
                print(f"[autoreply] {e}")
    except Exception as e:
        print(f"[autoreply hook] {e}")

# =========================================================
# CENTRAL MESSAGE ROUTER
# =========================================================
@bot.event
async def on_message(message):
    # Ignore our own command processing pipeline still works
    try:
        await autoreact_try(message)
    except Exception as e:
        print(f"[router autoreact] {e}")

    try:
        await autoreply_try(message)
    except Exception as e:
        print(f"[router autoreply] {e}")

    # Import-file attachment catcher
    if await _try_import_attachment(message):
       return

    # Let command processing run
    await bot.process_commands(message)

# =========================================================
# AFK (manual AFK mode with auto-reply on mention)
# =========================================================
DEFAULT_SETTINGS["afk"] = {
    "enabled": False,
    "reply": "I'm AFK right now.",
    "started_at": 0,
}

if "afk" not in settings:
    settings["afk"] = json.loads(json.dumps(DEFAULT_SETTINGS["afk"]))
else:
    for k, v in DEFAULT_SETTINGS["afk"].items():
        if k not in settings["afk"]:
            settings["afk"][k] = v
save_settings()

HELP_AFK = """```
--- AFK Sub-Commands ---

  ,afk                     enable AFK
  ,afkoff                  disable AFK (shows duration)
  ,afkinfo                 show status
  ,afkreply <text>         set the auto-reply text
```"""

@bot.command(name="afk")
async def cmd_afk(ctx):
    if settings["afk"].get("enabled"):
        return await safe_send(ctx.channel, "Already AFK.")
    settings["afk"]["enabled"] = True
    settings["afk"]["started_at"] = time.time()
    save_settings()
    await safe_send(ctx.channel, "AFK ON.")

@bot.command(name="afkoff")
async def cmd_afkoff(ctx):
    if not settings["afk"].get("enabled"):
        return await safe_send(ctx.channel, "Not AFK.")
    dur = int(time.time() - settings["afk"].get("started_at", time.time()))
    settings["afk"]["enabled"] = False
    save_settings()
    h, r = divmod(dur, 3600)
    m, s = divmod(r, 60)
    await safe_send(ctx.channel, f"AFK off. Was away {h}h {m}m {s}s.")

@bot.command(name="afkinfo")
async def cmd_afkinfo(ctx):
    cfg = settings["afk"]
    state = "ON" if cfg.get("enabled") else "OFF"
    reply = cfg.get("reply", "")
    await safe_send(ctx.channel, f"AFK: {state}\nReply: {reply}")

@bot.command(name="afkreply")
async def cmd_afkreply(ctx, *, text: str = None):
    if not text:
        return await safe_send(ctx.channel, f"Current reply: {settings['afk'].get('reply')}")
    settings["afk"]["reply"] = text
    save_settings()
    await safe_send(ctx.channel, f"AFK reply set to: {text}")

# =========================================================
# ANTI-AFK (auto-say a reply when someone messages you)
# =========================================================
DEFAULT_SETTINGS["antiafk"] = {
    "enabled": False,
    "reply": "I'm here.",
    "mode": "mention",     # "mention" = only when pinged, "channel" = every msg in monitored channel
    "channel": None,
}

if "antiafk" not in settings:
    settings["antiafk"] = json.loads(json.dumps(DEFAULT_SETTINGS["antiafk"]))
else:
    for k, v in DEFAULT_SETTINGS["antiafk"].items():
        if k not in settings["antiafk"]:
            settings["antiafk"][k] = v
save_settings()

HELP_ANTIAFK = """```
--- Anti-AFK Sub-Commands ---

  ,antiafk on [channel_id]     enable (optional channel ID for channel mode)
  ,antiafk off                 disable
  ,antiafk info                show current settings
  ,antiafk mode <mention|channel>
  ,antiafk channel <id|clear>
  ,antiafk reply <text>
```"""

@bot.command(name="antiafk")
async def cmd_antiafk(ctx, mode: str = None, channel_id: int = None):
    cfg = settings["antiafk"]
    if mode is None or mode.lower() == "info":
        state = "ON" if cfg.get("enabled") else "OFF"
        ch = cfg.get("channel") or "none"
        msg = (
            f"```\n--- Anti-AFK ---\n"
            f"Status:  {state}\n"
            f"Mode:    {cfg.get('mode', 'mention')}\n"
            f"Channel: {ch}\n"
            f"Reply:   {cfg.get('reply')}\n```"
        )
        return await safe_send(ctx.channel, msg)

    m = mode.lower()
    if m == "on":
        cfg["enabled"] = True
        if channel_id is not None:
            cfg["channel"] = channel_id
            cfg["mode"] = "channel"
        save_settings()
        await safe_send(ctx.channel, "Anti-AFK ON.")
    elif m == "off":
        cfg["enabled"] = False
        save_settings()
        await safe_send(ctx.channel, "Anti-AFK OFF.")
    elif m == "mode":
        if channel_id is None:
            return await safe_send(ctx.channel, "Usage: ,antiafk mode <mention|channel>")
        # Actually mode string passed as channel_id here
        return await safe_send(ctx.channel, "Usage: ,antiafk mode mention  OR  ,antiafk mode channel")
    else:
        await safe_send(ctx.channel, "Use: ,antiafk on | off | info")

@bot.command(name="antiafkmode")
async def cmd_antiafkmode(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Current mode: {settings['antiafk'].get('mode')}")
    if mode.lower() not in ("mention", "channel"):
        return await safe_send(ctx.channel, "Use: mention or channel")
    settings["antiafk"]["mode"] = mode.lower()
    save_settings()
    await safe_send(ctx.channel, f"Anti-AFK mode: {mode.lower()}")

@bot.command(name="antiafkchannel")
async def cmd_antiafkchannel(ctx, channel_id: str = None):
    if channel_id is None:
        return await safe_send(ctx.channel, f"Current channel: {settings['antiafk'].get('channel')}")
    if channel_id.lower() == "clear":
        settings["antiafk"]["channel"] = None
        save_settings()
        return await safe_send(ctx.channel, "Anti-AFK channel cleared.")
    try:
        cid = int(channel_id)
    except ValueError:
        return await safe_send(ctx.channel, "Invalid channel ID.")
    settings["antiafk"]["channel"] = cid
    settings["antiafk"]["mode"] = "channel"
    save_settings()
    await safe_send(ctx.channel, f"Anti-AFK channel set to {cid}.")

@bot.command(name="antiafkreply")
async def cmd_antiafkreply(ctx, *, text: str = None):
    if not text:
        return await safe_send(ctx.channel, f"Current reply: {settings['antiafk'].get('reply')}")
    settings["antiafk"]["reply"] = text
    save_settings()
    await safe_send(ctx.channel, f"Anti-AFK reply set to: {text}")

async def afk_try(message):
    """Handle AFK auto-reply when we get pinged."""
    try:
        cfg = settings.get("afk", {})
        if not cfg.get("enabled"):
            return
        if message.author.id == bot.user.id:
            return
        if bot.user in message.mentions:
            try:
                await message.channel.send(cfg.get("reply", "I'm AFK right now."))
            except Exception as e:
                print(f"[afk reply] {e}")
    except Exception as e:
        print(f"[afk hook] {e}")

async def antiafk_try(message):
    try:
        cfg = settings.get("antiafk", {})
        if not cfg.get("enabled"):
            return
        if message.author.id == bot.user.id:
            return
        mode = cfg.get("mode", "mention")
        if mode == "mention":
            if bot.user in message.mentions:
                try:
                    await message.channel.send(cfg.get("reply", "I'm here."))
                except Exception as e:
                    print(f"[antiafk mention] {e}")
        elif mode == "channel":
            ch = cfg.get("channel")
            if ch is not None and message.channel.id == ch:
                try:
                    await message.channel.send(cfg.get("reply", "I'm here."))
                except Exception as e:
                    print(f"[antiafk channel] {e}")
    except Exception as e:
        print(f"[antiafk hook] {e}")

@bot.command(name="antiafkreset")
async def cmd_antiafkreset(ctx):
    settings["antiafk"] = json.loads(json.dumps(DEFAULT_SETTINGS["antiafk"]))
    save_settings()
    await safe_send(ctx.channel, "Anti-AFK settings reset.")

# =========================================================
# COMMAND ALIASES (user-defined shortcuts)
# =========================================================
DEFAULT_SETTINGS["aliases"] = {}   # { "short": "long" }

if "aliases" not in settings:
    settings["aliases"] = {}
save_settings()

HELP_ALIAS = """```
--- Command Aliases ---

  ,alias add <short> <long>   create alias (e.g. ,alias add ab autobeef)
  ,alias remove <short>       remove alias
  ,alias list                 show all aliases
  ,alias clear                remove all aliases

Example:
  ,alias add s spam
  then typing ,s 12345 runs ,spam 12345
```"""

@bot.command(name="alias")
async def cmd_alias(ctx, action: str = None, short: str = None, *, long: str = None):
    if action is None:
        return await safe_send(ctx.channel, HELP_ALIAS)

    a = action.lower()

    if a == "add":
        if not short or not long:
            return await safe_send(ctx.channel, "Usage: ,alias add <short> <long>")
        settings["aliases"][short.lower()] = long.lower()
        save_settings()
        await safe_send(ctx.channel, f"Alias added: ,{short} → ,{long}")

    elif a == "remove":
        if not short:
            return await safe_send(ctx.channel, "Usage: ,alias remove <short>")
        key = short.lower()
        if key not in settings["aliases"]:
            return await safe_send(ctx.channel, f"No alias named {key}.")
        del settings["aliases"][key]
        save_settings()
        await safe_send(ctx.channel, f"Alias removed: {key}")

    elif a == "list":
        al = settings["aliases"]
        if not al:
            return await safe_send(ctx.channel, "No aliases set.")
        lines = [f",{k} → ,{v}" for k, v in al.items()]
        await safe_send(ctx.channel, "Aliases:\n" + "\n".join(lines))

    elif a == "clear":
        settings["aliases"] = {}
        save_settings()
        await safe_send(ctx.channel, "All aliases cleared.")

    else:
        await safe_send(ctx.channel, HELP_ALIAS)

# =========================================================
# CENTRAL MESSAGE ROUTER (final version)
# =========================================================
@bot.event
async def on_message(message):
    # Alias resolver: rewrite short → long before processing
    try:
        if message.author.id == bot.user.id and message.content.startswith(PREFIX):
            al = settings.get("aliases", {})
            if al:
                body = message.content[len(PREFIX):]
                parts = body.split(maxsplit=1)
                if parts:
                    head = parts[0].lower()
                    if head in al:
                        rest = parts[1] if len(parts) > 1 else ""
                        new_cmd = f"{PREFIX}{al[head]}"
                        if rest:
                            new_cmd += f" {rest}"
                        try:
                            await message.edit(content=new_cmd)
                            message = await message.channel.fetch_message(message.id)
                        except Exception as e:
                            print(f"[alias edit] {e}")
    except Exception as e:
        print(f"[alias hook] {e}")

    # Pending .txt import upload
    try:
        if await _try_import_attachment(message):
            return
    except Exception as e:
        print(f"[router import] {e}")

    # Feature hooks
    try:
        await mreact_try(message)
    except Exception as e:
        print(f"[router mreact] {e}")

    try:
        await autoreact_try(message)
    except Exception as e:
        print(f"[router autoreact] {e}")

    try:
        await autoreply_try(message)
    except Exception as e:
        print(f"[router autoreply] {e}")

    try:
        await afk_try(message)
    except Exception as e:
        print(f"[router afk] {e}")

    try:
        await antiafk_try(message)
    except Exception as e:
        print(f"[router antiafk] {e}")

    # Command processing
    await bot.process_commands(message)

# =========================================================
# ADDED FEATURES: purge / archive / multi-react / autocount modes / import
# =========================================================
import re
import html as _html
import datetime as _dt

# ---------- pure helpers (no discord needed) ----------

_ONES = ["zero","one","two","three","four","five","six","seven","eight","nine",
         "ten","eleven","twelve","thirteen","fourteen","fifteen","sixteen",
         "seventeen","eighteen","nineteen"]
_TENS = ["","","twenty","thirty","forty","fifty","sixty","seventy","eighty","ninety"]
_SCALES = [(10**12,"trillion"),(10**9,"billion"),(10**6,"million"),(10**3,"thousand")]

def num_to_words(n):
    n = int(n)
    if n < 0:
        return "minus " + num_to_words(-n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        t, o = divmod(n, 10)
        return _TENS[t] + (f"-{_ONES[o]}" if o else "")
    if n < 1000:
        h, r = divmod(n, 100)
        return f"{_ONES[h]} hundred" + (f" {num_to_words(r)}" if r else "")
    for size, name in _SCALES:
        if n >= size:
            q, r = divmod(n, size)
            return f"{num_to_words(q)} {name}" + (f" {num_to_words(r)}" if r else "")
    return str(n)

def to_roman(n):
    n = int(n)
    if n <= 0 or n >= 4000:
        return str(n)
    out = ""
    for v, s in [(1000,"M"),(900,"CM"),(500,"D"),(400,"CD"),(100,"C"),(90,"XC"),
                 (50,"L"),(40,"XL"),(10,"X"),(9,"IX"),(5,"V"),(4,"IV"),(1,"I")]:
        while n >= v:
            out += s
            n -= v
    return out

def to_ordinal(n):
    n = int(n)
    if 10 <= abs(n) % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(abs(n) % 10, "th")
    return f"{n}{suf}"

def _shift_digits(s, base):
    return "".join(chr(base + int(c)) if c.isdigit() else c for c in s)

_SUP = str.maketrans("0123456789-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻")
_SUB = str.maketrans("0123456789-", "₀₁₂₃₄₅₆₇₈₉₋")

def _circled(n):
    n = int(n)
    if n == 0:
        return "⓪"
    if 1 <= n <= 20:
        return chr(0x2460 + n - 1)
    return str(n)

NUMBER_MODES = {
    "normal":   lambda n: str(n),
    "words":    num_to_words,
    "ordinal":  to_ordinal,
    "roman":    to_roman,
    "binary":   lambda n: format(int(n), "b"),
    "hex":      lambda n: format(int(n), "x"),
    "octal":    lambda n: format(int(n), "o"),
    "fullwidth":lambda n: _shift_digits(str(n), 0xFF10),
    "bold":     lambda n: _shift_digits(str(n), 0x1D7CE),
    "double":   lambda n: _shift_digits(str(n), 0x1D7D8),
    "sans":     lambda n: _shift_digits(str(n), 0x1D7E2),
    "boldsans": lambda n: _shift_digits(str(n), 0x1D7EC),
    "mono":     lambda n: _shift_digits(str(n), 0x1D7F6),
    "circled":  _circled,
    "sup":      lambda n: str(n).translate(_SUP),
    "sub":      lambda n: str(n).translate(_SUB),
    "keycap":   lambda n: "".join(c + "\ufe0f\u20e3" if c.isdigit() else c for c in str(n)),
    "spaced":   lambda n: " ".join(str(n)),
    "dotted":   lambda n: ".".join(str(n)),
    "reverse":  lambda n: str(n)[::-1],
}

def format_number(n, mode="normal"):
    mode = (mode or "normal").lower()
    if mode == "random":
        mode = random.choice([m for m in NUMBER_MODES if m != "normal"])
    fn = NUMBER_MODES.get(mode, NUMBER_MODES["normal"])
    try:
        return fn(n)
    except Exception:
        return str(n)

_CUSTOM_EMOJI_RE = re.compile(r"<a?:\w+:\d+>")
_ZWJ, _VS16, _KEYCAP = "\u200d", "\ufe0f", "\u20e3"

def split_emojis(text):
    """Split a string into individual emojis (unicode + custom). Space separated or stuck together."""
    out = []
    for token in text.split():
        i = 0
        while i < len(token):
            m = _CUSTOM_EMOJI_RE.match(token, i)
            if m:
                out.append(m.group(0))
                i = m.end()
                continue
            ch = token[i]
            j = i + 1
            # regional-indicator flag pairs
            if 0x1F1E6 <= ord(ch) <= 0x1F1FF and j < len(token) and 0x1F1E6 <= ord(token[j]) <= 0x1F1FF:
                j += 1
            while j < len(token):
                c = token[j]
                if c in (_VS16, _KEYCAP) or 0x1F3FB <= ord(c) <= 0x1F3FF:
                    j += 1
                elif c == _ZWJ and j + 1 < len(token):
                    j += 2
                else:
                    break
            out.append(token[i:j])
            i = j
    return out

def build_archive_html(title, rows, owner_note=""):
    """rows: list of dicts {id, author, author_id, avatar, time, content, attachments[(name,url,is_img)],
    embeds[str], reactions[str], reply_to, edited}"""
    esc = _html.escape
    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        f"<title>{esc(title)}</title>",
        "<style>body{background:#313338;color:#dbdee1;font-family:Segoe UI,Arial,sans-serif;margin:0;padding:20px}"
        "h1{font-size:20px}.meta{color:#949ba4;font-size:13px;margin-bottom:16px}"
        ".m{display:flex;gap:12px;padding:6px 8px;border-radius:4px}.m:hover{background:#2e3035}"
        ".av{width:40px;height:40px;border-radius:50%;background:#5865f2;flex:none}"
        ".n{font-weight:600;color:#fff}.t{color:#949ba4;font-size:12px;margin-left:6px}"
        ".c{white-space:pre-wrap;word-wrap:break-word}.rp{color:#949ba4;font-size:12px}"
        ".e{border-left:4px solid #5865f2;background:#2b2d31;padding:6px 10px;margin:4px 0;font-size:14px}"
        ".r{display:inline-block;background:#2b2d31;border-radius:6px;padding:1px 6px;margin:2px 4px 0 0;font-size:13px}"
        "img.a{max-width:400px;max-height:300px;border-radius:4px;display:block;margin-top:4px}a{color:#00a8fc}"
        "</style></head><body>",
        f"<h1>{esc(title)}</h1>",
        f"<div class='meta'>{len(rows)} messages &middot; archived {esc(_dt.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC'))} {esc(owner_note)}</div>",
    ]
    for r in rows:
        av = f"<img class='av' src='{esc(r['avatar'])}'>" if r.get("avatar") else "<div class='av'></div>"
        body = [f"<div class='m' id='m{r['id']}'>{av}<div>"]
        body.append(f"<div><span class='n'>{esc(r['author'])}</span><span class='t'>{esc(r['time'])}"
                    f"{' (edited)' if r.get('edited') else ''}</span></div>")
        if r.get("reply_to"):
            body.append(f"<div class='rp'>&#8618; {esc(r['reply_to'])}</div>")
        if r.get("content"):
            body.append(f"<div class='c'>{esc(r['content'])}</div>")
        for name, url, is_img in r.get("attachments", []):
            if is_img:
                body.append(f"<a href='{esc(url)}'><img class='a' src='{esc(url)}' alt='{esc(name)}'></a>")
            else:
                body.append(f"<div><a href='{esc(url)}'>&#128206; {esc(name)}</a></div>")
        for e in r.get("embeds", []):
            body.append(f"<div class='e'>{esc(e)}</div>")
        if r.get("reactions"):
            body.append("<div>" + "".join(f"<span class='r'>{esc(x)}</span>" for x in r["reactions"]) + "</div>")
        body.append("</div></div>")
        parts.append("".join(body))
    parts.append("</body></html>")
    return "\n".join(parts)

def tag_join(text, tag):
    """Put the tag where it still works with markdown: '# <@id> text', not '<@id> # text'."""
    if not tag:
        return text
    m = re.match(r"^(#{1,3} |>>> |> |- )", text)
    if m:
        return text[:m.end()] + tag + text[m.end():]
    return tag + text

# ---------- PURGE ----------
HELP_PURGE = """```
--- Purge ---

  ,purge <n>                 delete your last n messages here
  ,purge <n> <channel_id>    ...in another channel
  ,purgestop                 stop a running purge

Only your own messages are deleted.
```"""

_purge_stop = {"flag": False}

@bot.command(name="purge")
async def cmd_purge(ctx, amount: int = None, channel_id: int = None):
    if not amount or amount < 1:
        return await safe_send(ctx.channel, f"Usage: {PREFIX}purge <n> [channel_id]")
    ch = ctx.channel
    if channel_id:
        ch = bot.get_channel(channel_id)
        if ch is None:
            try: ch = await bot.fetch_channel(channel_id)
            except Exception: return await safe_send(ctx.channel, "Channel not found.")
    try: await ctx.message.delete()
    except Exception: pass
    _purge_stop["flag"] = False
    deleted, scanned = 0, 0
    SCAN_CAP = 10000
    try:
        async for m in ch.history(limit=None):
            if _purge_stop["flag"] or deleted >= amount or scanned >= SCAN_CAP:
                break
            scanned += 1
            if m.author.id != bot.user.id:
                continue
            if m.type not in (discord.MessageType.default, discord.MessageType.reply):
                continue
            try:
                await m.delete()
                deleted += 1
            except discord.HTTPException as e:
                if getattr(e, "status", 0) == 429:
                    await asyncio.sleep(3)
                continue
            await asyncio.sleep(random.uniform(0.35, 0.6))
    except Exception as e:
        print(f"[purge error] {e}")
    note = await safe_send(ch, f"🧹 Purged {deleted} message(s).")
    if note:
        await asyncio.sleep(3)
        try: await note.delete()
        except Exception: pass

@bot.command(name="purgestop")
async def cmd_purgestop(ctx):
    _purge_stop["flag"] = True
    await safe_send(ctx.channel, "Purge stopping.")

# ---------- ARCHIVE (owned chats only) ----------
HELP_ARCHIVE = """```
--- Archive to HTML ---

  ,archive                     archive this chat
  ,archive <channel_id>        archive another chat
  ,archive <id> <limit>        only the newest <limit> msgs
  ,archive <id> all send       also upload the file in chat

Works only in chats you own: group DMs you
created, or channels in servers you own.
Saved to the archives/ folder.
```"""

def _owns_chat(ch):
    if isinstance(ch, discord.GroupChannel):
        return getattr(ch, "owner_id", None) == bot.user.id
    g = getattr(ch, "guild", None)
    return bool(g) and g.owner_id == bot.user.id

@bot.command(name="archive", aliases=["arc"])
async def cmd_archive(ctx, *args):
    channel_id, limit, send_it = None, None, False
    for a in args:
        al = a.lower()
        if al == "send":
            send_it = True
        elif al == "all":
            limit = None
        elif a.isdigit():
            v = int(a)
            if channel_id is None and v > 10**8:
                channel_id = v
            else:
                limit = v
    ch = ctx.channel
    if channel_id:
        ch = bot.get_channel(channel_id)
        if ch is None:
            try: ch = await bot.fetch_channel(channel_id)
            except Exception: return await safe_send(ctx.channel, "Channel not found.")
    if not _owns_chat(ch):
        return await safe_send(ctx.channel, "❌ You can only archive chats you own (your group DMs / your servers).")
    status = await safe_send(ctx.channel, "📦 Archiving...")
    rows = []
    try:
        async for m in ch.history(limit=limit):
            ref = ""
            if m.reference and getattr(m.reference, "resolved", None) and hasattr(m.reference.resolved, "author"):
                rr = m.reference.resolved
                ref = f"{rr.author.display_name}: {(rr.content or '')[:80]}"
            atts = [(a.filename, a.url, (a.content_type or "").startswith("image/")) for a in m.attachments]
            embeds = []
            for e in m.embeds:
                bits = [x for x in (e.title, e.description, e.url) if x]
                if bits: embeds.append(" — ".join(str(b) for b in bits))
            rows.append({
                "id": m.id,
                "author": m.author.display_name,
                "author_id": m.author.id,
                "avatar": str(m.author.display_avatar.url) if getattr(m.author, "display_avatar", None) else "",
                "time": m.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
                "content": m.clean_content or "",
                "attachments": atts,
                "embeds": embeds,
                "reactions": [f"{r.emoji} {r.count}" for r in m.reactions],
                "reply_to": ref,
                "edited": m.edited_at is not None,
            })
            if len(rows) % 100 == 0:
                await asyncio.sleep(0.5)
    except Exception as e:
        return await safe_send(ctx.channel, f"❌ Archive failed: {e}")
    rows.reverse()  # oldest first
    name = getattr(ch, "name", None) or f"chat_{ch.id}"
    safe = "".join(c for c in name if c.isalnum() or c in "-_") or str(ch.id)
    os.makedirs("archives", exist_ok=True)
    path = os.path.join("archives", f"{safe}_{int(time.time())}.html")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(build_archive_html(f"Archive: {name}", rows))
    except Exception as e:
        return await safe_send(ctx.channel, f"❌ Could not save: {e}")
    msg = f"✅ Archived {len(rows)} messages → `{path}`"
    if send_it:
        try:
            await ch.send(msg, file=discord.File(path))
            msg = None
        except Exception as e:
            msg += f"\n(upload failed: {e})"
    if status:
        try: await status.delete()
        except Exception: pass
    if msg:
        await safe_send(ctx.channel, msg)

# ---------- MULTI-REACT ----------
DEFAULT_SETTINGS["mreact"] = {"enabled": True, "self": [], "targets": {}, "gap": 0.6}
if "mreact" not in settings:
    settings["mreact"] = json.loads(json.dumps(DEFAULT_SETTINGS["mreact"]))
else:
    for _k, _v in DEFAULT_SETTINGS["mreact"].items():
        settings["mreact"].setdefault(_k, _v)
save_settings()

HELP_MR = """```
--- Multi-React ---
(several emojis per message)

  ,mr self <emojis>         react to YOUR messages
  ,mr add @user <emojis>    react to that user
  ,mr add <id> <emojis>
  ,mr rm @user              remove a user
  ,mr selfoff               stop self reacts
  ,mr list                  show everything
  ,mr gap <seconds>         delay between emojis
  ,mr on / ,mr off          toggle all
  ,mr clear                 wipe all

Emojis can be spaced or stuck together:
  ,mr self 🔥😂💀   (max 20 each)
Custom emojis: paste them normally.
```"""

async def _react_many(message, emojis):
    gap = max(0.5, float(settings["mreact"].get("gap", 0.6)))
    for e in emojis:
        try:
            await message.add_reaction(discord.PartialEmoji.from_str(e))
        except discord.HTTPException as ex:
            if getattr(ex, "status", 0) == 429:
                await asyncio.sleep(2)
        except Exception as ex:
            print(f"[mreact] {e}: {ex}")
        await asyncio.sleep(gap)

async def mreact_try(message):
    cfg = settings.get("mreact", {})
    if not cfg.get("enabled", True):
        return
    if message.author.id == bot.user.id:
        emojis = cfg.get("self", [])
    else:
        emojis = cfg.get("targets", {}).get(str(message.author.id), [])
    if emojis:
        asyncio.create_task(_react_many(message, list(emojis)))

@bot.command(name="mr", aliases=["mreact", "multireact"])
async def cmd_mr(ctx, *, args: str = ""):
    cfg = settings["mreact"]
    parts = args.split(maxsplit=1)
    sub = parts[0].lower() if parts else ""
    rest = parts[1] if len(parts) > 1 else ""
    if not sub:
        return await safe_send(ctx.channel, HELP_MR)
    if sub == "self":
        em = split_emojis(rest)[:20]
        if not em: return await safe_send(ctx.channel, f"Usage: {PREFIX}mr self <emojis>")
        cfg["self"] = em; save_settings()
        return await safe_send(ctx.channel, f"Self reacts: {' '.join(em)}")
    if sub == "selfoff":
        cfg["self"] = []; save_settings()
        return await safe_send(ctx.channel, "Self reacts off.")
    if sub in ("add", "rm", "remove"):
        uid = ctx.message.mentions[0].id if ctx.message.mentions else None
        first, _, emoji_part = rest.partition(" ")
        if uid is None:
            try: uid = int(first.strip("<@!>"))
            except Exception: return await safe_send(ctx.channel, "Invalid user.")
        if sub == "add":
            if ctx.message.mentions:
                emoji_part = rest.split(maxsplit=1)[1] if len(rest.split(maxsplit=1)) > 1 else ""
            em = split_emojis(emoji_part)[:20]
            if not em: return await safe_send(ctx.channel, f"Usage: {PREFIX}mr add @user <emojis>")
            cfg["targets"][str(uid)] = em; save_settings()
            return await safe_send(ctx.channel, f"<@{uid}> → {' '.join(em)}")
        if cfg["targets"].pop(str(uid), None) is None:
            return await safe_send(ctx.channel, "Not in list.")
        save_settings()
        return await safe_send(ctx.channel, f"Removed <@{uid}>.")
    if sub == "list":
        lines = [f"Status: {'ON' if cfg['enabled'] else 'OFF'} | gap {cfg['gap']}s"]
        if cfg["self"]: lines.append("SELF → " + " ".join(cfg["self"]))
        for u, em in cfg["targets"].items():
            lines.append(f"<@{u}> → {' '.join(em)}")
        return await safe_send(ctx.channel, "\n".join(lines))
    if sub == "gap":
        try: cfg["gap"] = max(0.3, float(rest))
        except Exception: return await safe_send(ctx.channel, f"Usage: {PREFIX}mr gap <seconds>")
        save_settings()
        return await safe_send(ctx.channel, f"Gap: {cfg['gap']}s")
    if sub in ("on", "off"):
        cfg["enabled"] = sub == "on"; save_settings()
        return await safe_send(ctx.channel, f"Multi-react {'ON' if cfg['enabled'] else 'OFF'}.")
    if sub == "clear":
        cfg["self"] = []; cfg["targets"] = {}; save_settings()
        return await safe_send(ctx.channel, "Multi-react cleared.")
    await safe_send(ctx.channel, HELP_MR)

# ---------- AUTOCOUNT MODES ----------
HELP_ACMODES = """```
--- Autocount Modes ---

  ,acmode <mode>     set number style
  ,acmodes           list all styles
  ,acstep <n>        count by n (default 1)

Modes: normal words ordinal roman binary hex octal
fullwidth bold double sans boldsans mono circled
sup sub keycap spaced dotted reverse random
(random = a different style each number)
```"""

@bot.command(name="acmode")
async def cmd_acmode(ctx, mode: str = None):
    if not mode:
        return await safe_send(ctx.channel, f"Mode: {settings['autocount'].get('mode','normal')}")
    mode = mode.lower()
    if mode != "random" and mode not in NUMBER_MODES:
        return await safe_send(ctx.channel, f"Unknown mode. See {PREFIX}acmodes")
    settings["autocount"]["mode"] = mode
    save_settings()
    await safe_send(ctx.channel, f"Autocount mode: {mode} (e.g. {format_number(12, mode)})")

@bot.command(name="acmodes")
async def cmd_acmodes(ctx):
    lines = [f"{m}: {format_number(42, m)}" for m in NUMBER_MODES]
    await safe_send(ctx.channel, "Autocount modes (42):\n" + "\n".join(lines) + "\nrandom: mixed")

@bot.command(name="acstep")
async def cmd_acstep(ctx, n: int = None):
    if not n or n < 1:
        return await safe_send(ctx.channel, f"Step: {settings['autocount'].get('step',1)} (usage: {PREFIX}acstep <n>)")
    settings["autocount"]["step"] = n
    save_settings()
    await safe_send(ctx.channel, f"Autocount step: {n}")

# ---------- IMPORTFILE (fixed) ----------
import aiohttp

_pending_imports = {}   # {user_id: target_filename}

HELP_IMPORT = """```
--- Import Files ---

  ,importfile <name>          then upload a .txt
  ,importfile <name> + attach the .txt in the SAME message
  ,importfile <name> <url>    download from a link
  ,importfile list            list .txt files
  ,importfile cancel          cancel pending upload

Then use it, e.g. ,abfile <name>  ,alfile <name>
```"""

def _clean_txt_name(name):
    name = name.strip()
    if not name.lower().endswith(".txt"):
        name += ".txt"
    safe = "".join(c for c in name if c.isalnum() or c in "._-")
    return safe if safe and safe != ".txt" else None

async def _save_text(name, text):
    with open(name, "w", encoding="utf-8") as f:
        f.write(text)
    return len([l for l in text.splitlines() if l.strip()])

@bot.command(name="importfile", aliases=["import"])
async def cmd_importfile(ctx, name: str = None, url: str = None):
    atts = ctx.message.attachments
    if not name and atts:
        name = atts[0].filename
    if not name:
        return await safe_send(ctx.channel, HELP_IMPORT)
    if name.lower() == "list":
        txts = sorted(f for f in os.listdir(".") if f.endswith(".txt"))
        if not txts: return await safe_send(ctx.channel, "No .txt files found.")
        more = f"\n... +{len(txts)-40} more" if len(txts) > 40 else ""
        return await safe_send(ctx.channel, "Text files:\n" + "\n".join(f"- {t}" for t in txts[:40]) + more)
    if name.lower() == "cancel":
        removed = _pending_imports.pop(ctx.author.id, None)
        return await safe_send(ctx.channel, f"Cancelled `{removed}`." if removed else "No pending import.")
    safe = _clean_txt_name(name)
    if not safe:
        return await safe_send(ctx.channel, "Invalid filename.")

    if atts:
        if not atts[0].filename.lower().endswith(".txt"):
            return await safe_send(ctx.channel, "❌ Only .txt files can be imported.")
        try:
            text = (await atts[0].read()).decode("utf-8", errors="replace")
            n = await _save_text(safe, text)
        except Exception as e:
            return await safe_send(ctx.channel, f"❌ Import failed: {e}")
        return await safe_send(ctx.channel, f"✅ Saved `{safe}` ({n} lines). Use it with `{PREFIX}abfile {safe[:-4]}`.")

    if url:
        if not url.startswith(("http://", "https://")):
            return await safe_send(ctx.channel, "URL must start with http:// or https://")
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
                    if r.status != 200:
                        return await safe_send(ctx.channel, f"Download failed: HTTP {r.status}")
                    text = await r.text()
            n = await _save_text(safe, text)
        except Exception as e:
            return await safe_send(ctx.channel, f"❌ Download error: {e}")
        return await safe_send(ctx.channel, f"✅ Saved `{safe}` ({n} lines).")

    _pending_imports[ctx.author.id] = safe
    await safe_send(ctx.channel, f"📥 Upload the .txt now to save it as `{safe}`. `{PREFIX}importfile cancel` to abort.")

async def _try_import_attachment(message):
    """Called from the router. True if this message was a pending upload."""
    if message.author.id != bot.user.id or message.author.id not in _pending_imports:
        return False
    if not message.attachments:
        return False
    att = message.attachments[0]
    if not att.filename.lower().endswith(".txt"):
        await safe_send(message.channel, "❌ Only .txt files can be imported.")
        return True
    target = _pending_imports.pop(message.author.id)
    try:
        text = (await att.read()).decode("utf-8", errors="replace")
        n = await _save_text(target, text)
        await safe_send(message.channel, f"✅ Saved `{target}` ({n} lines). Use it with `{PREFIX}abfile {target[:-4]}`.")
    except Exception as e:
        await safe_send(message.channel, f"❌ Save failed: {e}")
    return True

# ---------- MENU ENTRIES ----------
EXTRA_HELP = {
    "purge": HELP_PURGE, "archive": HELP_ARCHIVE, "mr": HELP_MR,
    "acmode": HELP_ACMODES, "import": HELP_IMPORT,
}
EXTRA_HELP_ALIASES = {
    "purge": "purge", "archive": "archive", "arc": "archive",
    "mr": "mr", "mreact": "mr", "multireact": "mr",
    "acmode": "acmode", "acmodes": "acmode", "acm": "acmode",
    "import": "import", "importfile": "import", "imp": "import",
}

# =========================================================
# FEATURE PACK 2: reliability, snipe/watch, wordlist tools, scheduler,
# lookups, rpc, downloads, scanners, server tools, webhooks, profiles,
# restart persistence, panel, console, extra autocount styles
# =========================================================
import ast
import math
import sys
import shutil
import hmac
import threading
import urllib.parse as _up
from collections import deque, OrderedDict

os.makedirs("logs", exist_ok=True)
os.makedirs("data", exist_ok=True)

# ---------- pure helpers ----------

def parse_duration(text):
    """'90' -> 90s, '1h30m', '2d', '45s'. Returns seconds or None."""
    text = (text or "").strip().lower()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    total, found = 0, False
    for num, unit in re.findall(r"(\d+)\s*([smhdw])", text):
        total += int(num) * {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
        found = True
    rest = re.sub(r"(\d+)\s*([smhdw])", "", text).strip()
    return total if found and not rest else None

_CALC_FUNCS = {"sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
               "log": math.log, "log10": math.log10, "abs": abs, "round": round,
               "floor": math.floor, "ceil": math.ceil, "factorial": math.factorial}
_CALC_CONST = {"pi": math.pi, "e": math.e}

def safe_calc(expr):
    """Evaluate a math expression without eval()."""
    def ev(n):
        if isinstance(n, ast.Expression): return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)): return n.value
        if isinstance(n, ast.Name) and n.id in _CALC_CONST: return _CALC_CONST[n.id]
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd, ast.USub)):
            v = ev(n.operand); return v if isinstance(n.op, ast.UAdd) else -v
        if isinstance(n, ast.BinOp):
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Add): return a + b
            if isinstance(n.op, ast.Sub): return a - b
            if isinstance(n.op, ast.Mult): return a * b
            if isinstance(n.op, ast.Div): return a / b
            if isinstance(n.op, ast.FloorDiv): return a // b
            if isinstance(n.op, ast.Mod): return a % b
            if isinstance(n.op, ast.Pow):
                if abs(b) > 1000 or abs(a) > 10**9: raise ValueError("power too large")
                return a ** b
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _CALC_FUNCS and not n.keywords:
            args = [ev(x) for x in n.args]
            if n.func.id == "factorial" and (not args or args[0] > 500): raise ValueError("too large")
            return _CALC_FUNCS[n.func.id](*args)
        raise ValueError("unsupported expression")
    return ev(ast.parse(expr.replace("^", "**").strip(), mode="eval"))

_SECRET_PATTERNS = [
    (r"discord(?:app)?\.com/api/webhooks/\d+/[\w-]+", "Discord webhook URL (possible token exfiltration)", 3),
    (r"[\w-]{24,26}\.[\w-]{6}\.[\w-]{27,}", "Discord token-shaped string", 3),
    (r"leveldb|Local Storage|\\Discord\\|discordcanary|discordptb", "reads Discord/browser storage paths", 3),
    (r"CryptUnprotectData|win32crypt|browser_cookie3|Login Data|Cookies\.sqlite", "browser password/cookie access", 3),
    (r"base64\.b64decode|b64decode\(", "base64 decoding (hidden payloads)", 1),
    (r"\bexec\s*\(|\beval\s*\(|marshal\.loads|compile\s*\(", "dynamic code execution", 2),
    (r"os\.system|subprocess\.|Popen\(", "runs system commands", 1),
    (r"requests\.post|urllib\.request|httpx\.post|aiohttp.*\.post", "sends data over the network", 1),
    (r"pastebin\.com|hastebin|ngrok|grabify|iplogger|discord\.gift|bit\.ly|tinyurl", "suspicious host/shortener", 2),
    (r"getpass|keyboard\.|pynput|ImageGrab|pyautogui|screenshot", "keylogging / screen capture", 2),
]

def scan_text(text):
    """Heuristic scan. Returns (score, [findings])."""
    findings, score = [], 0
    for pat, label, w in _SECRET_PATTERNS:
        if re.search(pat, text, re.I):
            findings.append(label); score += w
    return score, findings

_SHORTENERS = {"bit.ly", "tinyurl.com", "t.co", "goo.gl", "is.gd", "cutt.ly", "rb.gy", "shorturl.at", "ow.ly", "grabify.link"}
_BAD_TLDS = {"zip", "mov", "xyz", "top", "click", "gq", "tk", "ml", "cf", "ga", "work", "rest", "country"}

def check_url(url):
    """Heuristic URL check. Returns (verdict, [reasons])."""
    reasons = []
    try:
        u = _up.urlparse(url if "://" in url else "http://" + url)
    except Exception:
        return "invalid", ["could not parse"]
    host = (u.hostname or "").lower()
    if not host:
        return "invalid", ["no host"]
    if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", host): reasons.append("raw IP address")
    if "xn--" in host: reasons.append("punycode (lookalike characters)")
    if host in _SHORTENERS: reasons.append("link shortener hides the destination")
    if host.count(".") >= 4: reasons.append("many subdomains")
    if "@" in (u.netloc or ""): reasons.append("'@' in address (credential trick)")
    if host.rsplit(".", 1)[-1] in _BAD_TLDS: reasons.append("commonly abused TLD")
    if u.scheme == "http": reasons.append("not HTTPS")
    if re.search(r"d[i1l]sc[o0]rd|nitro|steamcommun|gift", host) and not re.search(r"(^|\.)(discord\.com|discord\.gg|discordapp\.com|discord\.gift)$", host):
        reasons.append("looks like a Discord/Steam/Nitro imitation")
    verdict = "looks fine" if not reasons else ("suspicious" if len(reasons) < 3 else "high risk")
    return verdict, reasons

def wl_stats(lines):
    uniq = len(set(lines))
    return {"lines": len(lines), "unique": uniq, "duplicates": len(lines) - uniq,
            "avg_len": round(sum(map(len, lines)) / len(lines), 1) if lines else 0,
            "longest": max(map(len, lines)) if lines else 0}

def wl_dedupe(lines):
    seen, out = set(), []
    for l in lines:
        if l not in seen:
            seen.add(l); out.append(l)
    return out

def _wl_read(name):
    safe = _clean_txt_name(name)
    if not safe or not os.path.exists(safe):
        return None, safe
    with open(safe, "r", encoding="utf-8", errors="replace") as f:
        return [l.rstrip("\n").rstrip("\r") for l in f if l.strip()], safe

def _wl_write(safe, lines):
    with open(safe, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))

def chunk_text(text, size=1900):
    out, cur = [], ""
    for line in text.split("\n"):
        while len(line) > size:
            if cur: out.append(cur); cur = ""
            out.append(line[:size]); line = line[size:]
        if len(cur) + len(line) + 1 > size:
            out.append(cur); cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur: out.append(cur)
    return out

# ---------- extra autocount styles ----------
_G_ONES = ["", "α", "β", "γ", "δ", "ε", "ϛ", "ζ", "η", "θ"]
_G_TENS = ["", "ι", "κ", "λ", "μ", "ν", "ξ", "ο", "π", "ϙ"]
_G_HUNDS = ["", "ρ", "σ", "τ", "υ", "φ", "χ", "ψ", "ω", "ϡ"]

def to_greek_numeral(n):
    n = int(n)
    if not 1 <= n <= 999:
        return str(n)
    return _G_HUNDS[n // 100] + _G_TENS[(n // 10) % 10] + _G_ONES[n % 10] + "ʹ"

_POKEDEX = ("Bulbasaur Ivysaur Venusaur Charmander Charmeleon Charizard Squirtle Wartortle Blastoise Caterpie Metapod Butterfree "
 "Weedle Kakuna Beedrill Pidgey Pidgeotto Pidgeot Rattata Raticate Spearow Fearow Ekans Arbok Pikachu Raichu Sandshrew Sandslash "
 "Nidoran-F Nidorina Nidoqueen Nidoran-M Nidorino Nidoking Clefairy Clefable Vulpix Ninetales Jigglypuff Wigglytuff Zubat Golbat "
 "Oddish Gloom Vileplume Paras Parasect Venonat Venomoth Diglett Dugtrio Meowth Persian Psyduck Golduck Mankey Primeape Growlithe "
 "Arcanine Poliwag Poliwhirl Poliwrath Abra Kadabra Alakazam Machop Machoke Machamp Bellsprout Weepinbell Victreebel Tentacool "
 "Tentacruel Geodude Graveler Golem Ponyta Rapidash Slowpoke Slowbro Magnemite Magneton Farfetchd Doduo Dodrio Seel Dewgong Grimer "
 "Muk Shellder Cloyster Gastly Haunter Gengar Onix Drowzee Hypno Krabby Kingler Voltorb Electrode Exeggcute Exeggutor Cubone "
 "Marowak Hitmonlee Hitmonchan Lickitung Koffing Weezing Rhyhorn Rhydon Chansey Tangela Kangaskhan Horsea Seadra Goldeen Seaking "
 "Staryu Starmie Mr-Mime Scyther Jynx Electabuzz Magmar Pinsir Tauros Magikarp Gyarados Lapras Ditto Eevee Vaporeon Jolteon "
 "Flareon Porygon Omanyte Omastar Kabuto Kabutops Aerodactyl Snorlax Articuno Zapdos Moltres Dratini Dragonair Dragonite Mewtwo Mew").split()

_SPOOKY_DIGITS = {"0": "💀", "1": "👻", "2": "🎃", "3": "🦇", "4": "🕷️", "5": "🕸️", "6": "🧛", "7": "🧟", "8": "🔮", "9": "⚰️"}
_FONT_ROWS = [
    "𝐚 𝐛 𝐜 𝐝 𝐞 𝐟 𝐠 𝐡 𝐢 𝐣 𝐤 𝐥 𝐦 𝐧 𝐨 𝐩 𝐪 𝐫 𝐬 𝐭 𝐮 𝐯 𝐰 𝐱 𝐲 𝐳",
    "𝑎 𝑏 𝑐 𝑑 𝑒 𝑓 𝑔 ℎ 𝑖 𝑗 𝑘 𝑙 𝑚 𝑛 𝑜 𝑝 𝑞 𝑟 𝑠 𝑡 𝑢 𝑣 𝑤 𝑥 𝑦 𝑧",
    "𝒂 𝒃 𝒄 𝒅 𝒆 𝒇 𝒈 𝒉 𝒊 𝒋 𝒌 𝒍 𝒎 𝒏 𝒐 𝒑 𝒒 𝒓 𝒔 𝒕 𝒖 𝒗 𝒘 𝒙 𝒚 𝒛",
    "ａ ｂ ｃ ｄ ｅ ｆ ｇ ｈ ｉ ｊ ｋ ｌ ｍ ｎ ｏ ｐ ｑ ｒ ｓ ｔ ｕ ｖ ｗ ｘ ｙ ｚ",
    "𝚊 𝚋 𝚌 𝚍 𝚎 𝚏 𝚐 𝚑 𝚒 𝚓 𝚔 𝚕 𝚖 𝚗 𝚘 𝚙 𝚚 𝚛 𝚜 𝚝 𝚞 𝚟 𝚠 𝚡 𝚢 𝚣",
    "ᗩ ᗷ ᑕ ᗪ ᙓ ᖴ ᘜ ᖺ ᛁ ᒍ ᘔ ᒪ ᗰ ᑎ ᘎ ᑭ ᑫ ᖇ ᔕ ᗛ ᑌ ᐯ ᙡ ᙭ ᗌ ᘔ",
]
_FONTS = [r.split() for r in _FONT_ROWS if len(r.split()) == 26]

def _chaotic(n):
    out = []
    for ch in num_to_words(n):
        if "a" <= ch <= "z":
            out.append(random.choice(_FONTS)[ord(ch) - 97])
        else:
            out.append(ch)
    return "".join(out)

_CIPHER_KEY = random.sample("bcdfghjklmnpqrstvwxyz", 21)
def _cipher(n):
    c = _CIPHER_KEY
    v = "aeiou"[n % 5]
    return f"{c[n % 21]}{v}{c[(n * 3 + 7) % 21]}{c[(n * 7 + 13) % 21]}{n}"

_PSYMS = list("!@#$%^&*-+=~;:?,./|")
def _symbol(n):
    a = _PSYMS[n % len(_PSYMS)]
    b = _PSYMS[(n * 7 + 3) % len(_PSYMS)]
    if b == a:
        b = _PSYMS[(n + 11) % len(_PSYMS)]
    return f"{a}{b}{n}"

def _ts(n):
    h, m, s = n // 3600, (n % 3600) // 60, n % 60
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

_SENT_SUBJ = ["i", "she", "he", "they", "we", "bro", "u"]
_SENT_VO = [("ate", "apples"), ("scored", "points"), ("sent", "messages"), ("ran", "miles"), ("won", "rounds"),
            ("counted", "days"), ("sold", "units"), ("missed", "shots"), ("gained", "followers"), ("spent", "tokens")]
def _sentence(n):
    v, o = random.choice(_SENT_VO)
    return f"{random.choice(_SENT_SUBJ)} {v} **{num_to_words(n).upper()}** {o}"

def _digit_script(base):
    return lambda n: _shift_digits(str(n), base)

_KANJI = "〇一二三四五六七八九"
NUMBER_MODES.update({
    "greek": to_greek_numeral,
    "letter": lambda n: f"{chr(65 + (int(n) - 1) % 26)}{n}",
    "cipher": _cipher,
    "symbol": _symbol,
    "spooky": lambda n: "".join(_SPOOKY_DIGITS[d] for d in str(n)),
    "chaotic": _chaotic,
    "deg": lambda n: f"{int(n):02d}°",
    "ts": lambda n: _ts(int(n)),
    "rgb": lambda n: f"#{int(n) % 0x1000000:06X}",
    "dex": lambda n: f"{_POKEDEX[(int(n) - 1) % 151]} = {n}",
    "sent": _sentence,
    "arabic": _digit_script(0x0660),
    "persian": _digit_script(0x06F0),
    "devanagari": _digit_script(0x0966),
    "bengali": _digit_script(0x09E6),
    "thai": _digit_script(0x0E50),
    "kanji": lambda n: "".join(_KANJI[int(c)] if c.isdigit() else c for c in str(n)),
})

def _ac_sequence(start, end, step, skip):
    """Counting sequence. skip=True drops a few numbers between each hundred (hundreds always kept)."""
    if end < start:
        return list(range(start, end - 1, -step))
    if not skip:
        return list(range(start, end + 1, step))
    seq, n = [], start
    while n <= end:
        seq.append(n)
        if n % 100 == 0:
            n += 1
            continue
        room = ((n // 100) + 1) * 100 - n
        n += random.randint(1, min(12, room - 1)) if room > 12 else 1
    return seq

# ---------- reliability: rate-limit backoff, logging, errors ----------
STATS = {"messages_seen": 0, "commands_run": 0, "deleted_caught": 0, "edits_caught": 0, "ghost_pings": 0, "backoffs": 0}

def log_event(kind, text):
    try:
        with open(os.path.join("logs", "events.log"), "a", encoding="utf-8") as f:
            f.write(f"{_dt.datetime.utcnow():%Y-%m-%d %H:%M:%S} [{kind}] {text}\n")
    except Exception:
        pass

# ---------- pre-typer (typing bubble before sends, per channel) ----------
if "pretype" not in settings:
    settings["pretype"] = {"channels": {}}
settings["pretype"].setdefault("channels", {})

async def _pretype_if_needed(channel, content):
    sec = settings["pretype"]["channels"].get(str(getattr(channel, "id", "")))
    if sec is None:
        return
    sec = float(sec)
    if sec <= 0:                                   # auto: scale with message length
        sec = min(6.0, max(1.0, len(content or "") / 12))
    try:
        await channel.trigger_typing()
        await asyncio.sleep(min(sec, 8.0))
    except Exception:
        pass

async def safe_send(channel, content=None, **kwargs):
    try:
        await _pretype_if_needed(channel, content)
        return await channel.send(content, **kwargs)
    except Exception as e:
        print(f"[send fail] {e}")

async def safe_edit(channel, **kwargs):
    try: return await channel.edit(**kwargs)
    except Exception as e: print(f"[edit fail] {e}")

HELP_PRETYPE = """```
--- Pre-typer ---
(typing bubble before each message, per channel)

  ,pretype on [channel|here] [sec|auto]
  ,pretype off [channel|here|all]
  ,pretype list
  ,pretype send <channel_id> <sec> <text>
```"""

@bot.command(name="pretype")
async def cmd_pretype(ctx, action: str = None, *args):
    cfg = settings["pretype"]["channels"]
    a = (action or "list").lower()
    def chan(tok):
        if tok is None or tok.lower() == "here": return ctx.channel.id
        return int(tok) if tok.isdigit() else None
    if a == "on":
        toks = list(args)
        cid = chan(toks[0]) if toks and (toks[0].isdigit() and len(toks[0]) > 8 or toks[0].lower() == "here") else ctx.channel.id
        if toks and (toks[0].isdigit() and len(toks[0]) > 8 or toks[0].lower() == "here"): toks = toks[1:]
        sec = 0.0
        if toks and toks[0].lower() != "auto":
            try: sec = max(0.5, min(float(toks[0]), 8.0))
            except ValueError: return await safe_send(ctx.channel, "Seconds must be a number (or 'auto').")
        cfg[str(cid)] = sec; save_settings()
        return await safe_send(ctx.channel, f"⌨️ Pre-typer ON in <#{cid}> ({'auto' if sec == 0 else f'{sec}s'}).")
    if a == "off":
        tok = args[0] if args else None
        if tok and tok.lower() == "all":
            cfg.clear()
        else:
            cid = chan(tok)
            if cid is None: return await safe_send(ctx.channel, "Invalid channel.")
            cfg.pop(str(cid), None)
        save_settings()
        return await safe_send(ctx.channel, "Pre-typer off.")
    if a == "send":
        if len(args) < 3 or not args[0].isdigit():
            return await safe_send(ctx.channel, f"Usage: {PREFIX}pretype send <channel_id> <sec> <text>")
        ch = bot.get_channel(int(args[0]))
        if not ch: return await safe_send(ctx.channel, "Channel not found.")
        try: sec = max(0.5, min(float(args[1]), 30))
        except ValueError: return await safe_send(ctx.channel, "Seconds must be a number.")
        try: await ctx.message.delete()
        except Exception: pass
        await send_typing(ch, sec)
        return await safe_send(ch, " ".join(args[2:]))
    if a == "list":
        return await safe_send(ctx.channel, "Pre-typer: " + (", ".join(f"<#{c}> ({'auto' if s == 0 else str(s) + 's'})" for c, s in cfg.items()) or "off everywhere"))
    await safe_send(ctx.channel, HELP_PRETYPE)

async def _on_cmd_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    msg = f"{ctx.command}: {type(error).__name__}: {error}"
    print(f"[command error] {msg}")
    log_event("error", msg)
    try:
        with open(os.path.join("logs", "errors.log"), "a", encoding="utf-8") as f:
            f.write(f"{_dt.datetime.utcnow():%Y-%m-%d %H:%M:%S} {msg}\n")
    except Exception:
        pass
    if isinstance(error, (commands.BadArgument, commands.MissingRequiredArgument)):
        await safe_send(ctx.channel, f"⚠️ Bad arguments. Try `{PREFIX}help`.")
bot.add_listener(_on_cmd_error, "on_command_error")

async def _on_cmd(ctx):
    STATS["commands_run"] += 1
bot.add_listener(_on_cmd, "on_command")

# alert helper (console + file + optional log channel)
async def alert(text):
    print(f"[alert] {text}")
    log_event("alert", text)
    cid = settings.get("logchannel")
    if cid:
        ch = bot.get_channel(int(cid))
        if ch:
            await safe_send(ch, text[:1900])

# ---------- settings sections ----------
for _sec, _val in {
    "snipe": {"enabled": True, "off_channels": [], "autodelete": 30},
    "watch": {"enabled": False, "words": []},
    "pingreply": {"text": "", "cooldown": 60},
    "reminders": [],
    "todo": [],
    "notes": {},
    "webhooks": {},
    "restart": {"auto": False, "status": False, "stream_rotation": False},
}.items():
    if _sec not in settings:
        settings[_sec] = json.loads(json.dumps(_val))
    elif isinstance(_val, dict):
        for _k, _v in _val.items():
            settings[_sec].setdefault(_k, _v)
save_settings()

# ---------- message cache + snipe + ghost ping + keyword watcher + ping reply ----------
_CACHE = OrderedDict()           # message_id -> info
_DELETED = {}                    # channel_id -> deque
_EDITED = {}
_RXN = {}
_PING_LAST = {}

def _store(d, ch_id, item, n=20):
    d.setdefault(ch_id, deque(maxlen=n)).appendleft(item)

def _snipe_on(ch_id):
    s = settings["snipe"]
    return s["enabled"] and ch_id not in s["off_channels"]

async def _l_on_message(message):
    STATS["messages_seen"] += 1
    try:
        me = bot.user
        mentions_me = bool(me and (me in message.mentions or message.mention_everyone))
        _CACHE[message.id] = {
            "author": str(message.author), "author_id": message.author.id, "content": message.content or "",
            "channel_id": message.channel.id, "time": message.created_at.strftime("%H:%M:%S UTC"),
            "mentions_me": mentions_me, "atts": [a.url for a in message.attachments],
            "guild": getattr(getattr(message, "guild", None), "name", "DM"),
        }
        while len(_CACHE) > 6000:
            _CACHE.popitem(last=False)
        if me and message.author.id == me.id:
            return
        # keyword watcher
        w = settings["watch"]
        if w["enabled"] and w["words"] and message.content:
            low = message.content.lower()
            hit = [k for k in w["words"] if re.search(r"(?<!\w)" + re.escape(k.lower()) + r"(?!\w)", low)]
            if hit:
                await alert(f"🔎 keyword {hit} by {message.author} in #{getattr(message.channel, 'name', 'dm')}: {message.jump_url}")
        # auto respond to ping
        pr = settings["pingreply"]
        if pr["text"] and me and me in message.mentions:
            now = time.time()
            if now - _PING_LAST.get(message.channel.id, 0) >= max(10, int(pr["cooldown"])):
                _PING_LAST[message.channel.id] = now
                try:
                    await message.reply(pr["text"], mention_author=False)
                except Exception:
                    pass
    except Exception as e:
        print(f"[cache hook] {e}")
bot.add_listener(_l_on_message, "on_message")

async def _l_on_delete(payload):
    info = _CACHE.get(payload.message_id)
    if not info:
        return
    if bot.user and info["author_id"] == bot.user.id:
        return
    STATS["deleted_caught"] += 1
    if _snipe_on(info["channel_id"]):
        _store(_DELETED, info["channel_id"], info)
    if info["mentions_me"]:
        STATS["ghost_pings"] += 1
        await alert(f"👻 ghost ping from {info['author']} in {info['guild']}: \"{info['content'][:200]}\"")
bot.add_listener(_l_on_delete, "on_raw_message_delete")

async def _l_on_edit(payload):
    info = _CACHE.get(payload.message_id)
    new = (payload.data or {}).get("content")
    if not info or new is None or new == info["content"]:
        return
    if bot.user and info["author_id"] == bot.user.id:
        info["content"] = new
        return
    STATS["edits_caught"] += 1
    if _snipe_on(info["channel_id"]):
        _store(_EDITED, info["channel_id"], dict(info, new=new))
    info["content"] = new
bot.add_listener(_l_on_edit, "on_raw_message_edit")

async def _l_on_rxn_remove(payload):
    info = _CACHE.get(payload.message_id)
    if info and bot.user and payload.user_id != bot.user.id and _snipe_on(payload.channel_id):
        _store(_RXN, payload.channel_id, {"emoji": str(payload.emoji), "user_id": payload.user_id,
                                           "msg": info["content"][:80], "author": info["author"]})
bot.add_listener(_l_on_rxn_remove, "on_raw_reaction_remove")

HELP_TOOLS = """```
--- Snipe / Watch ---

  ,snipe [n]          last deleted msgs here
  ,esnipe [n]         last edited msgs (before/after)
  ,rsnipe             last removed reactions
  ,snipeon [here]     enable (all / this channel)
  ,snipeoff [here]    disable
  ,snipedel <sec|0>   auto-delete snipe replies
  ,logchannel <id|off>  where alerts are posted
  ,watch add|rm|list <word>  keyword watcher
  ,watch on|off
  ,pingreply <text|off>  auto-reply when pinged
  ,pingcool <sec>     reply cooldown per channel

Ghost pings (a ping deleted by the sender)
are alerted automatically.
```"""

async def _snipe_reply(ctx, text):
    msg = await safe_send(ctx.channel, text[:1950])
    d = int(settings["snipe"].get("autodelete", 30))
    if msg and d > 0:
        async def _later():
            await asyncio.sleep(d)
            try: await msg.delete()
            except Exception: pass
        asyncio.create_task(_later())

@bot.command(name="snipe")
async def cmd_snipe(ctx, n: int = 1):
    items = list(_DELETED.get(ctx.channel.id, []))[:max(1, min(n, 10))]
    try: await ctx.message.delete()
    except Exception: pass
    if not items:
        return await _snipe_reply(ctx, "Nothing sniped here.")
    out = "\n".join(f"🗑️ **{i['author']}** ({i['time']}): {i['content'] or '(no text)'}" + (f" 📎 {' '.join(i['atts'])}" if i['atts'] else "") for i in items)
    await _snipe_reply(ctx, out)

@bot.command(name="esnipe", aliases=["snipeedit"])
async def cmd_esnipe(ctx, n: int = 1):
    items = list(_EDITED.get(ctx.channel.id, []))[:max(1, min(n, 10))]
    try: await ctx.message.delete()
    except Exception: pass
    if not items:
        return await _snipe_reply(ctx, "No edits caught here.")
    await _snipe_reply(ctx, "\n".join(f"✏️ **{i['author']}**: {i['content']} → {i['new']}" for i in items))

@bot.command(name="rsnipe")
async def cmd_rsnipe(ctx):
    items = list(_RXN.get(ctx.channel.id, []))[:5]
    try: await ctx.message.delete()
    except Exception: pass
    if not items:
        return await _snipe_reply(ctx, "No removed reactions here.")
    await _snipe_reply(ctx, "\n".join(f"{i['emoji']} removed by <@{i['user_id']}> from {i['author']}: {i['msg']}" for i in items))

@bot.command(name="snipeon")
async def cmd_snipeon(ctx, scope: str = None):
    s = settings["snipe"]
    if scope == "here":
        if ctx.channel.id in s["off_channels"]: s["off_channels"].remove(ctx.channel.id)
    else:
        s["enabled"] = True
    save_settings()
    await safe_send(ctx.channel, f"Snipe ON ({'this channel' if scope == 'here' else 'global'}).")

@bot.command(name="snipeoff")
async def cmd_snipeoff(ctx, scope: str = None):
    s = settings["snipe"]
    if scope == "here":
        if ctx.channel.id not in s["off_channels"]: s["off_channels"].append(ctx.channel.id)
    else:
        s["enabled"] = False
    save_settings()
    await safe_send(ctx.channel, f"Snipe OFF ({'this channel' if scope == 'here' else 'global'}).")

@bot.command(name="snipedel")
async def cmd_snipedel(ctx, sec: int = None):
    if sec is None:
        return await safe_send(ctx.channel, f"Snipe auto-delete: {settings['snipe']['autodelete']}s (0 = keep)")
    settings["snipe"]["autodelete"] = max(0, sec); save_settings()
    await safe_send(ctx.channel, f"Snipe auto-delete: {max(0, sec)}s")

@bot.command(name="logchannel")
async def cmd_logchannel(ctx, cid: str = None):
    if cid is None:
        return await safe_send(ctx.channel, f"Log channel: {settings.get('logchannel') or 'none (console + logs/events.log)'}")
    if cid.lower() == "off":
        settings["logchannel"] = None
    elif cid.isdigit():
        settings["logchannel"] = int(cid)
    else:
        return await safe_send(ctx.channel, "Usage: ,logchannel <id|off>")
    save_settings()
    await safe_send(ctx.channel, "Log channel updated.")

@bot.command(name="watch")
async def cmd_watch(ctx, action: str = None, *, word: str = None):
    w = settings["watch"]
    a = (action or "").lower()
    if a == "add" and word:
        if word.lower() not in [x.lower() for x in w["words"]]: w["words"].append(word)
    elif a in ("rm", "remove") and word:
        w["words"] = [x for x in w["words"] if x.lower() != word.lower()]
    elif a in ("on", "off"):
        w["enabled"] = a == "on"
    elif a == "list":
        return await safe_send(ctx.channel, f"Watcher {'ON' if w['enabled'] else 'OFF'}: {', '.join(w['words']) or '(none)'}")
    else:
        return await safe_send(ctx.channel, HELP_TOOLS)
    save_settings()
    await safe_send(ctx.channel, f"Watcher {'ON' if w['enabled'] else 'OFF'}: {', '.join(w['words']) or '(none)'}")

@bot.command(name="pingreply")
async def cmd_pingreply(ctx, *, text: str = None):
    if not text:
        return await safe_send(ctx.channel, f"Ping reply: {settings['pingreply']['text'] or 'off'}")
    settings["pingreply"]["text"] = "" if text.lower() == "off" else text
    save_settings()
    await safe_send(ctx.channel, "Ping reply " + ("off." if not settings["pingreply"]["text"] else "set."))

@bot.command(name="pingcool")
async def cmd_pingcool(ctx, sec: int = None):
    if not sec:
        return await safe_send(ctx.channel, f"Cooldown: {settings['pingreply']['cooldown']}s")
    settings["pingreply"]["cooldown"] = max(10, sec); save_settings()
    await safe_send(ctx.channel, f"Cooldown: {max(10, sec)}s")

# ---------- wordlist tools ----------
HELP_WL = """```
--- Wordlist Tools ---

  ,wl stats <file>
  ,wl show <file> [n]
  ,wl dedupe <file>           (keeps .bak)
  ,wl shuffle <file>
  ,wl sort <file>
  ,wl add <file> <text>
  ,wl merge <out> <a> <b> ... (deduped)
  ,wl copy <src> <dst>
  ,wl export <file>           upload as .txt
  ,wl rm <file>               delete (keeps .bak)

Import new files with ,importfile <name>
```"""

@bot.command(name="wl", aliases=["wordlist"])
async def cmd_wl(ctx, action: str = None, *args):
    a = (action or "").lower()
    if not a or not args:
        return await safe_send(ctx.channel, HELP_WL)
    if a == "merge":
        if len(args) < 3:
            return await safe_send(ctx.channel, f"Usage: {PREFIX}wl merge <out> <a> <b> ...")
        out = _clean_txt_name(args[0]); merged = []
        for name in args[1:]:
            lines, _ = _wl_read(name)
            if lines is None: return await safe_send(ctx.channel, f"❌ `{name}` not found.")
            merged += lines
        merged = wl_dedupe(merged); _wl_write(out, merged)
        return await safe_send(ctx.channel, f"✅ `{out}`: {len(merged)} unique lines from {len(args) - 1} files.")
    if a == "add":
        if len(args) < 2: return await safe_send(ctx.channel, f"Usage: {PREFIX}wl add <file> <text>")
        safe = _clean_txt_name(args[0])
        with open(safe, "a", encoding="utf-8") as f: f.write(" ".join(args[1:]) + "\n")
        return await safe_send(ctx.channel, f"✅ Added to `{safe}`.")
    if a == "copy":
        if len(args) < 2: return await safe_send(ctx.channel, f"Usage: {PREFIX}wl copy <src> <dst>")
        lines, _ = _wl_read(args[0])
        if lines is None: return await safe_send(ctx.channel, "❌ Source not found.")
        dst = _clean_txt_name(args[1]); _wl_write(dst, lines)
        return await safe_send(ctx.channel, f"✅ Copied to `{dst}` ({len(lines)} lines).")
    lines, safe = _wl_read(args[0])
    if lines is None:
        return await safe_send(ctx.channel, f"❌ `{args[0]}` not found. See {PREFIX}files")
    if a == "stats":
        s = wl_stats(lines)
        return await safe_send(ctx.channel, f"`{safe}`: {s['lines']} lines, {s['unique']} unique, {s['duplicates']} dupes, avg {s['avg_len']} chars, longest {s['longest']}")
    if a == "show":
        n = int(args[1]) if len(args) > 1 and args[1].isdigit() else 10
        return await safe_send(ctx.channel, "```\n" + "\n".join(lines[:min(n, 25)])[:1850] + "\n```")
    if a in ("dedupe", "shuffle", "sort", "rm"):
        shutil.copyfile(safe, safe + ".bak")
        if a == "rm":
            os.remove(safe); return await safe_send(ctx.channel, f"🗑️ Deleted `{safe}` (backup: `{safe}.bak`).")
        new = wl_dedupe(lines) if a == "dedupe" else (random.sample(lines, len(lines)) if a == "shuffle" else sorted(lines, key=str.lower))
        _wl_write(safe, new)
        return await safe_send(ctx.channel, f"✅ {a}: {len(lines)} → {len(new)} lines (backup `{safe}.bak`).")
    if a == "export":
        try:
            return await ctx.channel.send(f"📄 `{safe}`", file=discord.File(safe))
        except Exception as e:
            return await safe_send(ctx.channel, f"❌ Upload failed: {e}")
    await safe_send(ctx.channel, HELP_WL)

# ---------- scheduler / reminders / todo / notes / say ----------
HELP_TIME = """```
--- Time & Notes ---

  ,remind <10m|2h|1d> <text>     reminder here
  ,schedule <time> <text>        send text here later
  ,announce <every> <times> <text>  recurring (min 5m)
  ,reminders / ,remindrm <id>
  ,da <sec> <text>      send, delete after n sec
  ,ghost <text>         send, delete after 1s
  ,pretype <sec> <text> typing, then send
  ,say <text>           send (deletes your cmd)
  ,todo add|done|rm|list|clear
  ,note add|show|rm|list <name> [text]
  ,date [timezone]  ,uptime
```"""

def _sched_add(kind, delay, channel_id, text, repeat=0, left=1):
    items = settings["reminders"]
    nid = (max([i["id"] for i in items]) + 1) if items else 1
    items.append({"id": nid, "due": time.time() + delay, "channel": channel_id, "text": text,
                  "kind": kind, "repeat": repeat, "left": left})
    save_settings()
    return nid

async def _sched_loop():
    while True:
        try:
            now = time.time()
            for it in list(settings["reminders"]):
                if it["due"] <= now:
                    ch = bot.get_channel(int(it["channel"]))
                    if ch:
                        await safe_send(ch, ("⏰ " if it["kind"] == "remind" else "") + it["text"])
                    it["left"] -= 1
                    if it["left"] > 0 and it["repeat"] > 0:
                        it["due"] = now + it["repeat"]
                    else:
                        settings["reminders"].remove(it)
                    save_settings()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[scheduler] {e}")
        await asyncio.sleep(5)

@bot.command(name="remind")
async def cmd_remind(ctx, when: str = None, *, text: str = None):
    d = parse_duration(when)
    if not d or not text:
        return await safe_send(ctx.channel, f"Usage: {PREFIX}remind <10m|2h|1d> <text>")
    nid = _sched_add("remind", d, ctx.channel.id, text)
    await safe_send(ctx.channel, f"⏰ Reminder #{nid} in {when}.")

@bot.command(name="schedule")
async def cmd_schedule(ctx, when: str = None, *, text: str = None):
    d = parse_duration(when)
    if not d or not text:
        return await safe_send(ctx.channel, f"Usage: {PREFIX}schedule <10m|2h|1d> <text>")
    try: await ctx.message.delete()
    except Exception: pass
    _sched_add("send", d, ctx.channel.id, text)

@bot.command(name="announce")
async def cmd_announce(ctx, every: str = None, times: int = None, *, text: str = None):
    d = parse_duration(every)
    if not d or not times or not text or d < 300 or not 1 <= times <= 50:
        return await safe_send(ctx.channel, f"Usage: {PREFIX}announce <every (min 5m)> <times 1-50> <text>")
    nid = _sched_add("send", d, ctx.channel.id, text, repeat=d, left=times)
    await safe_send(ctx.channel, f"📢 Announcement #{nid}: every {every}, {times} times.")

@bot.command(name="reminders")
async def cmd_reminders(ctx):
    items = settings["reminders"]
    if not items:
        return await safe_send(ctx.channel, "Nothing scheduled.")
    lines = [f"#{i['id']} [{i['kind']}] in {max(0, int(i['due'] - time.time()))}s ×{i['left']}: {i['text'][:60]}" for i in items[:15]]
    await safe_send(ctx.channel, "\n".join(lines))

@bot.command(name="remindrm")
async def cmd_remindrm(ctx, rid: int = None):
    before = len(settings["reminders"])
    settings["reminders"] = [i for i in settings["reminders"] if i["id"] != rid]
    save_settings()
    await safe_send(ctx.channel, "Removed." if len(settings["reminders"]) < before else "Not found.")

@bot.command(name="da")
async def cmd_da(ctx, sec: float = None, *, text: str = None):
    if sec is None or not text:
        return await safe_send(ctx.channel, f"Usage: {PREFIX}da <seconds> <text>")
    try: await ctx.message.delete()
    except Exception: pass
    m = await safe_send(ctx.channel, text)
    await asyncio.sleep(max(0.5, min(sec, 3600)))
    try: await m.delete()
    except Exception: pass

@bot.command(name="ghost")
async def cmd_ghost(ctx, *, text: str = None):
    if not text:
        return await safe_send(ctx.channel, f"Usage: {PREFIX}ghost <text>")
    try: await ctx.message.delete()
    except Exception: pass
    m = await safe_send(ctx.channel, text)
    await asyncio.sleep(1)
    try: await m.delete()
    except Exception: pass

@bot.command(name="say")
async def cmd_say(ctx, *, text: str = None):
    if not text:
        return await safe_send(ctx.channel, f"Usage: {PREFIX}say <text>")
    try: await ctx.message.delete()
    except Exception: pass
    await safe_send(ctx.channel, text)

@bot.command(name="todo")
async def cmd_todo(ctx, action: str = None, *, text: str = None):
    t = settings["todo"]
    a = (action or "list").lower()
    if a == "add" and text:
        t.append({"text": text, "done": False}); save_settings()
    elif a in ("done", "rm") and text and text.isdigit() and 1 <= int(text) <= len(t):
        if a == "done": t[int(text) - 1]["done"] = True
        else: t.pop(int(text) - 1)
        save_settings()
    elif a == "clear":
        t.clear(); save_settings()
    elif a != "list":
        return await safe_send(ctx.channel, f"Usage: {PREFIX}todo add <text> | done <n> | rm <n> | list | clear")
    if not t:
        return await safe_send(ctx.channel, "Todo list is empty.")
    await safe_send(ctx.channel, "\n".join(f"{i + 1}. {'✅' if x['done'] else '⬜'} {x['text']}" for i, x in enumerate(t))[:1900])

@bot.command(name="note")
async def cmd_note(ctx, action: str = None, name: str = None, *, text: str = None):
    n = settings["notes"]
    a = (action or "list").lower()
    if a == "add" and name and text:
        n[name.lower()] = text; save_settings(); return await safe_send(ctx.channel, f"Saved note `{name}`.")
    if a == "show" and name:
        return await safe_send(ctx.channel, n.get(name.lower(), "No such note.")[:1900])
    if a == "rm" and name:
        n.pop(name.lower(), None); save_settings(); return await safe_send(ctx.channel, "Removed.")
    if a == "list":
        return await safe_send(ctx.channel, "Notes: " + (", ".join(n) or "(none)"))
    await safe_send(ctx.channel, f"Usage: {PREFIX}note add <name> <text> | show <name> | rm <name> | list")

@bot.command(name="date", aliases=["time"])
async def cmd_date(ctx, tz: str = None):
    try:
        if tz:
            from zoneinfo import ZoneInfo
            now = _dt.datetime.now(ZoneInfo(tz)); label = tz
        else:
            now = _dt.datetime.now().astimezone(); label = "local"
    except Exception:
        return await safe_send(ctx.channel, "Unknown timezone (use e.g. Europe/London).")
    await safe_send(ctx.channel, f"🕒 {now:%A %Y-%m-%d %H:%M:%S} ({label}) — unix {int(now.timestamp())}")

def _fmt_dur(sec):
    sec = int(sec); d, r = divmod(sec, 86400); h, r = divmod(r, 3600); m, s = divmod(r, 60)
    return f"{d}d {h}h {m}m {s}s" if d else (f"{h}h {m}m {s}s" if h else f"{m}m {s}s")

@bot.command(name="uptime")
async def cmd_uptime(ctx):
    await safe_send(ctx.channel, f"⏱️ Up {_fmt_dur(time.monotonic() - START_TIME)}")

def _mem_cpu():
    mem = cpu = None
    try:
        import psutil
        p = psutil.Process()
        return round(p.memory_info().rss / 1048576, 1), p.cpu_percent(interval=0.2)
    except Exception:
        pass
    try:
        with open("/proc/self/status") as f:
            for l in f:
                if l.startswith("VmRSS"):
                    mem = round(int(l.split()[1]) / 1024, 1)
    except Exception:
        pass
    return mem, cpu

@bot.command(name="stats")
async def cmd_stats(ctx):
    mem, cpu = _mem_cpu()
    running = [k for k in tm.tasks if tm.is_running(k)]
    await safe_send(ctx.channel,
        f"```\nUptime: {_fmt_dur(time.monotonic() - START_TIME)}\nMessages seen: {STATS['messages_seen']}\n"
        f"Commands run: {STATS['commands_run']}\nDeleted caught: {STATS['deleted_caught']} | edits: {STATS['edits_caught']}\n"
        f"Ghost pings: {STATS['ghost_pings']} | rate-limit waits: {STATS['backoffs']}\n"
        f"Memory: {mem if mem is not None else 'n/a'} MB | CPU: {cpu if cpu is not None else 'n/a'}%\n"
        f"Running loops: {', '.join(running) or 'none'}\nPython {sys.version.split()[0]} | PID {os.getpid()}\n```")

@bot.command(name="logs")
async def cmd_logs(ctx, n: int = 10, which: str = "events"):
    path = os.path.join("logs", "errors.log" if which.startswith("err") else "events.log")
    if not os.path.exists(path):
        return await safe_send(ctx.channel, "Log is empty.")
    with open(path, encoding="utf-8", errors="replace") as f:
        tail = f.readlines()[-max(1, min(n, 30)):]
    await safe_send(ctx.channel, "```\n" + "".join(tail)[-1850:] + "\n```")

# ---------- lookups ----------
HELP_LOOK = """```
--- Lookups ---

  ,calc <expr>              ,weather <city>
  ,cur <amt> <FROM> <TO>    ,crypto <coin> [fiat]
  ,define <word>            ,wiki <topic>
  ,urban <term>             ,tr <lang> <text>

Examples: ,calc 2^10+sqrt(81)  ,cur 20 usd eur
          ,crypto bitcoin usd  ,tr es good morning
```"""

async def _http_json(url, params=None):
    async with aiohttp.ClientSession() as s:
        async with s.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15),
                         headers={"User-Agent": "Mozilla/5.0"}) as r:
            if r.status != 200:
                raise RuntimeError(f"HTTP {r.status}")
            return await r.json(content_type=None)

async def _http_text(url, params=None):
    async with aiohttp.ClientSession() as s:
        async with s.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15),
                         headers={"User-Agent": "curl/8"}) as r:
            if r.status != 200:
                raise RuntimeError(f"HTTP {r.status}")
            return await r.text()

@bot.command(name="calc")
async def cmd_calc(ctx, *, expr: str = None):
    if not expr: return await safe_send(ctx.channel, f"Usage: {PREFIX}calc <expression>")
    try: res = safe_calc(expr)
    except ZeroDivisionError: res = "division by zero"
    except Exception as e: res = f"error ({e})"
    await safe_send(ctx.channel, f"🧮 `{expr}` = **{res}**")

@bot.command(name="weather")
async def cmd_weather(ctx, *, city: str = None):
    if not city: return await safe_send(ctx.channel, f"Usage: {PREFIX}weather <city>")
    try: txt = await _http_text(f"https://wttr.in/{_up.quote(city)}", {"format": "3"})
    except Exception as e: return await safe_send(ctx.channel, f"❌ {e}")
    await safe_send(ctx.channel, txt.strip()[:500])

@bot.command(name="cur", aliases=["currency"])
async def cmd_cur(ctx, amount: float = None, src: str = None, dst: str = None):
    if amount is None or not src or not dst: return await safe_send(ctx.channel, f"Usage: {PREFIX}cur <amount> <FROM> <TO>")
    try:
        d = await _http_json("https://api.frankfurter.app/latest", {"amount": amount, "from": src.upper(), "to": dst.upper()})
        await safe_send(ctx.channel, f"💱 {amount} {src.upper()} = **{d['rates'][dst.upper()]} {dst.upper()}**")
    except Exception as e:
        await safe_send(ctx.channel, f"❌ {e}")

@bot.command(name="crypto")
async def cmd_crypto(ctx, coin: str = None, fiat: str = "usd"):
    if not coin: return await safe_send(ctx.channel, f"Usage: {PREFIX}crypto <coin id e.g. bitcoin> [fiat]")
    try:
        d = await _http_json("https://api.coingecko.com/api/v3/simple/price",
                             {"ids": coin.lower(), "vs_currencies": fiat.lower(), "include_24hr_change": "true"})
        v = d[coin.lower()]
        ch = v.get(f"{fiat.lower()}_24h_change")
        await safe_send(ctx.channel, f"🪙 {coin} = **{v[fiat.lower()]:,} {fiat.upper()}**" + (f" ({ch:+.2f}% 24h)" if ch is not None else ""))
    except Exception as e:
        await safe_send(ctx.channel, f"❌ Not found or API error ({e}). Use the CoinGecko id, e.g. `bitcoin`.")

@bot.command(name="define")
async def cmd_define(ctx, *, word: str = None):
    if not word: return await safe_send(ctx.channel, f"Usage: {PREFIX}define <word>")
    try:
        d = await _http_json(f"https://api.dictionaryapi.dev/api/v2/entries/en/{_up.quote(word)}")
        m = d[0]["meanings"][0]
        await safe_send(ctx.channel, f"📖 **{word}** ({m['partOfSpeech']}): {m['definitions'][0]['definition']}"[:1900])
    except Exception:
        await safe_send(ctx.channel, "No definition found.")

@bot.command(name="wiki")
async def cmd_wiki(ctx, *, topic: str = None):
    if not topic: return await safe_send(ctx.channel, f"Usage: {PREFIX}wiki <topic>")
    try:
        d = await _http_json("https://en.wikipedia.org/api/rest_v1/page/summary/" + _up.quote(topic.replace(" ", "_")))
        await safe_send(ctx.channel, f"📚 **{d['title']}**: {d['extract'][:1500]}\n<{d['content_urls']['desktop']['page']}>")
    except Exception:
        await safe_send(ctx.channel, "No article found.")

@bot.command(name="urban")
async def cmd_urban(ctx, *, term: str = None):
    if not term: return await safe_send(ctx.channel, f"Usage: {PREFIX}urban <term>")
    try:
        d = await _http_json("https://api.urbandictionary.com/v0/define", {"term": term})
        e = d["list"][0]
        clean = lambda s: s.replace("[", "").replace("]", "")
        await safe_send(ctx.channel, f"📕 **{term}**: {clean(e['definition'])[:1000]}\n*{clean(e.get('example', ''))[:500]}*")
    except Exception:
        await safe_send(ctx.channel, "No result.")

@bot.command(name="tr", aliases=["translate"])
async def cmd_tr(ctx, lang: str = None, *, text: str = None):
    if not lang or not text: return await safe_send(ctx.channel, f"Usage: {PREFIX}tr <lang code e.g. es> <text>")
    try:
        d = await _http_json("https://api.mymemory.translated.net/get", {"q": text[:500], "langpair": f"autodetect|{lang.lower()}"})
        await safe_send(ctx.channel, f"🌐 {d['responseData']['translatedText']}"[:1900])
    except Exception as e:
        await safe_send(ctx.channel, f"❌ Translate failed ({e}).")

# ---------- rich presence ----------
HELP_RPC = """```
--- Rich Presence ---

  ,rpc play <name>
  ,rpc watch <name>
  ,rpc listen <name>
  ,rpc comp <name>
  ,rpc stream <url> <name>
  ,rpc youtube <url> <name>
  ,rpc ps <game>
  ,rpc stack play:A | watch:B | listen:C
  ,rpc stop

Starting an rpc pauses the status/stream rotators.
```"""

def _make_rpc(kind, name, url=None):
    AT = discord.ActivityType
    if kind == "play": return discord.Game(name=name)
    if kind == "watch": return discord.Activity(type=AT.watching, name=name)
    if kind == "listen": return discord.Activity(type=AT.listening, name=name)
    if kind == "comp": return discord.Activity(type=AT.competing, name=name)
    if kind in ("stream", "youtube"):
        try:
            return discord.Streaming(name=name, url=url, **({"platform": "YouTube"} if kind == "youtube" else {}))
        except TypeError:
            return discord.Streaming(name=name, url=url)
    if kind == "ps":
        try: return discord.Game(name=name, platform="ps5")
        except TypeError: return discord.Game(name=name)
    return None

@bot.command(name="rpc")
async def cmd_rpc(ctx, kind: str = None, *, rest: str = ""):
    k = (kind or "").lower()
    if k == "stop":
        await bot.change_presence(activity=None)
        return await safe_send(ctx.channel, "RPC cleared.")
    if k not in ("play", "watch", "listen", "comp", "stream", "youtube", "ps", "stack"):
        return await safe_send(ctx.channel, HELP_RPC)
    tm.cancel("status"); tm.cancel("stream_rotation")
    try:
        if k == "stack":
            acts = []
            for part in rest.split("|"):
                kk, _, nn = part.strip().partition(":")
                a = _make_rpc(kk.strip().lower(), nn.strip())
                if a: acts.append(a)
            if not acts: return await safe_send(ctx.channel, "Usage: ,rpc stack play:A | watch:B")
            try: await bot.change_presence(activities=acts)
            except TypeError: await bot.change_presence(activity=acts[0])
        elif k in ("stream", "youtube"):
            url, _, name = rest.partition(" ")
            if not url.startswith("http") or not name.strip():
                return await safe_send(ctx.channel, f"Usage: {PREFIX}rpc {k} <url> <name>")
            await bot.change_presence(activity=_make_rpc(k, name.strip(), url))
        else:
            if not rest.strip(): return await safe_send(ctx.channel, f"Usage: {PREFIX}rpc {k} <name>")
            await bot.change_presence(activity=_make_rpc(k, rest.strip()))
        await safe_send(ctx.channel, f"✅ RPC set ({k}).")
    except Exception as e:
        await safe_send(ctx.channel, f"❌ RPC failed: {e}")

# ---------- downloader (yt-dlp) ----------
HELP_DL = """```
--- Downloader ---
(needs: pip install yt-dlp, ffmpeg for mp3)

  ,dl <url>            video (TikTok, Instagram,
                       YouTube, Twitter, ...)
  ,dla <url>           audio only
  ,dlpl <url>          playlist (max 5 items)
  ,dlurl <url>         print direct stream URL
  ,dlsubs <url>        subtitles as .srt

Files over ~24 MB are kept in downloads/ only.
```"""

async def _ytdlp(args, timeout=300):
    exe = shutil.which("yt-dlp")
    if not exe:
        return None, "yt-dlp is not installed. Run: python -m pip install yt-dlp"
    try:
        p = await asyncio.create_subprocess_exec(exe, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(p.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        return None, "Timed out."
    except Exception as e:
        return None, str(e)
    if p.returncode != 0:
        return None, (err.decode(errors="replace").strip().splitlines() or ["download failed"])[-1][:300]
    return out.decode(errors="replace"), None

async def _dl(ctx, url, mode):
    if not url or not url.startswith(("http://", "https://")):
        return await safe_send(ctx.channel, f"Usage: {PREFIX}dl <http(s) url>")
    os.makedirs("downloads", exist_ok=True)
    status = await safe_send(ctx.channel, "⬇️ Downloading...")
    base = ["--no-warnings", "-o", "downloads/%(title).70s.%(ext)s", "--print", "after_move:filepath", "--max-filesize", "200M"]
    if mode == "audio": base += ["-f", "bestaudio", "--no-playlist"]
    elif mode == "playlist": base += ["--yes-playlist", "--playlist-end", "5"]
    else: base += ["--no-playlist"]
    out, err = await _ytdlp(base + ["--", url])
    if status:
        try: await status.delete()
        except Exception: pass
    if err:
        return await safe_send(ctx.channel, f"❌ {err}")
    files = [l.strip() for l in out.splitlines() if l.strip() and os.path.exists(l.strip())][:5]
    if not files:
        return await safe_send(ctx.channel, "❌ Nothing downloaded.")
    for fpath in files:
        if os.path.getsize(fpath) <= 24 * 1024 * 1024:
            try:
                await ctx.channel.send(file=discord.File(fpath)); continue
            except Exception as e:
                await safe_send(ctx.channel, f"Upload failed for `{os.path.basename(fpath)}`: {e}")
        else:
            await safe_send(ctx.channel, f"📁 `{fpath}` is too big to upload; saved locally.")

@bot.command(name="dl", aliases=["tiktok", "instagram", "video", "ig", "mp4"])
async def cmd_dl(ctx, url: str = None): await _dl(ctx, url, "video")

@bot.command(name="dla", aliases=["audio"])
async def cmd_dla(ctx, url: str = None): await _dl(ctx, url, "audio")

@bot.command(name="dlpl", aliases=["playlist"])
async def cmd_dlpl(ctx, url: str = None): await _dl(ctx, url, "playlist")

@bot.command(name="dlurl", aliases=["stream_url"])
async def cmd_dlurl(ctx, url: str = None):
    if not url or not url.startswith(("http://", "https://")):
        return await safe_send(ctx.channel, f"Usage: {PREFIX}dlurl <url>")
    out, err = await _ytdlp(["--no-warnings", "--no-playlist", "-g", "--", url], 60)
    await safe_send(ctx.channel, f"❌ {err}" if err else "```\n" + out.strip()[:1800] + "\n```")

@bot.command(name="dlsubs")
async def cmd_dlsubs(ctx, url: str = None):
    if not url or not url.startswith(("http://", "https://")):
        return await safe_send(ctx.channel, f"Usage: {PREFIX}dlsubs <url>")
    os.makedirs("downloads", exist_ok=True)
    out, err = await _ytdlp(["--no-warnings", "--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", "en.*",
                             "--convert-subs", "srt", "-o", "downloads/%(title).70s", "--print", "title", "--", url], 120)
    if err: return await safe_send(ctx.channel, f"❌ {err}")
    title = out.strip().splitlines()[-1] if out.strip() else ""
    found = [f for f in os.listdir("downloads") if f.endswith(".srt") and f.startswith(title[:70])][:1]
    if not found: return await safe_send(ctx.channel, "No subtitles found.")
    await ctx.channel.send(file=discord.File(os.path.join("downloads", found[0])))

# ---------- scanners ----------
HELP_SCAN = """```
--- Safety Scanners ---
(heuristic, not antivirus)

  ,checkfile        attach a file, or reply to one
  ,checkurl <url>   look for phishing tricks

Flags webhook URLs, token strings, browser
password/cookie access, exec/eval, etc.
Never run files you can't read.
```"""

@bot.command(name="checkfile", aliases=["scanfile"])
async def cmd_checkfile(ctx):
    atts = list(ctx.message.attachments)
    if not atts and ctx.message.reference:
        try:
            ref = ctx.message.reference.resolved or await ctx.channel.fetch_message(ctx.message.reference.message_id)
            atts = list(ref.attachments)
        except Exception:
            pass
    if not atts:
        return await safe_send(ctx.channel, "Attach a file or reply to a message with one.")
    for a in atts[:3]:
        if a.size > 5 * 1024 * 1024:
            await safe_send(ctx.channel, f"`{a.filename}`: too large to scan (>5 MB)."); continue
        data = await a.read()
        if b"\x00" in data[:2048]:
            await safe_send(ctx.channel, f"`{a.filename}`: binary file — can't scan its contents. Treat as untrusted."); continue
        score, found = scan_text(data.decode("utf-8", errors="replace"))
        verdict = "✅ nothing obvious" if score == 0 else ("⚠️ suspicious" if score < 4 else "🚨 HIGH RISK")
        await safe_send(ctx.channel, f"`{a.filename}`: {verdict} (score {score})" + ("\n- " + "\n- ".join(found) if found else ""))

@bot.command(name="checkurl", aliases=["scanurl"])
async def cmd_checkurl(ctx, url: str = None):
    if not url: return await safe_send(ctx.channel, f"Usage: {PREFIX}checkurl <url>")
    verdict, reasons = check_url(url.strip("<>"))
    await safe_send(ctx.channel, f"🔗 {verdict}" + ("\n- " + "\n- ".join(reasons) if reasons else ""))

# ---------- server tools (needs the permissions in that server) ----------
HELP_SRV = """```
--- Server Tools ---
(work only where you have the permission)

  ,perms [@user]       permissions in this channel
  ,members             server info / member count
  ,invite [hours] [uses]   create an invite
  ,invites             list invites
  ,newchannel <name>   ,newcategory <name>
  ,roles               list roles
  ,role give|take @user @role
  ,rolenew <name> [hex]
  ,emojiadd <name>     attach/reply to an image
  ,servericon          attach an image
```"""

async def _need_guild(ctx):
    if not ctx.guild:
        await safe_send(ctx.channel, "Server-only command.")
        return False
    return True

async def _guild_call(ctx, coro, ok):
    try:
        await coro
        await safe_send(ctx.channel, ok)
    except discord.Forbidden:
        await safe_send(ctx.channel, "❌ Missing permission.")
    except Exception as e:
        await safe_send(ctx.channel, f"❌ {e}")

async def _image_bytes(ctx):
    atts = list(ctx.message.attachments)
    if not atts and ctx.message.reference:
        try:
            ref = ctx.message.reference.resolved or await ctx.channel.fetch_message(ctx.message.reference.message_id)
            atts = list(ref.attachments)
        except Exception:
            pass
    if not atts or not (atts[0].content_type or "").startswith("image/"):
        return None
    return await atts[0].read()

@bot.command(name="perms")
async def cmd_perms(ctx, member: discord.Member = None):
    if not await _need_guild(ctx): return
    m = member or ctx.guild.me or ctx.author
    perms = ctx.channel.permissions_for(m)
    on = sorted(n.replace("_", " ") for n, v in perms if v)
    await safe_send(ctx.channel, f"**{m}** here: " + (", ".join(on) or "none")[:1800])

@bot.command(name="members")
async def cmd_members(ctx):
    if not await _need_guild(ctx): return
    g = ctx.guild
    await safe_send(ctx.channel, f"**{g.name}** — {g.member_count} members, {len(g.channels)} channels, {len(g.roles)} roles, "
                                 f"boost level {g.premium_tier}, created {g.created_at:%Y-%m-%d}, owner <@{g.owner_id}>")

@bot.command(name="invite")
async def cmd_invite(ctx, hours: float = 24, uses: int = 0):
    if not await _need_guild(ctx): return
    try:
        inv = await ctx.channel.create_invite(max_age=int(hours * 3600), max_uses=max(0, uses))
        await safe_send(ctx.channel, f"🔗 {inv.url} (expires {'never' if hours == 0 else f'in {hours}h'}, uses {uses or '∞'})")
    except discord.Forbidden:
        await safe_send(ctx.channel, "❌ Missing permission.")
    except Exception as e:
        await safe_send(ctx.channel, f"❌ {e}")

@bot.command(name="invites")
async def cmd_invites(ctx):
    if not await _need_guild(ctx): return
    try:
        invs = await ctx.guild.invites()
    except discord.Forbidden:
        return await safe_send(ctx.channel, "❌ Missing permission.")
    if not invs: return await safe_send(ctx.channel, "No invites.")
    await safe_send(ctx.channel, "\n".join(f"{i.code} — {i.uses} uses by {i.inviter}" for i in invs[:20])[:1900])

@bot.command(name="newchannel")
async def cmd_newchannel(ctx, *, name: str = None):
    if not await _need_guild(ctx) or not name: return
    await _guild_call(ctx, ctx.guild.create_text_channel(name), f"✅ Created #{name}.")

@bot.command(name="newcategory")
async def cmd_newcategory(ctx, *, name: str = None):
    if not await _need_guild(ctx) or not name: return
    await _guild_call(ctx, ctx.guild.create_category(name), f"✅ Created category {name}.")

@bot.command(name="roles")
async def cmd_roles(ctx):
    if not await _need_guild(ctx): return
    await safe_send(ctx.channel, ", ".join(f"{r.name}" for r in reversed(ctx.guild.roles) if r.name != "@everyone")[:1900] or "No roles.")

@bot.command(name="role")
async def cmd_role(ctx, action: str = None, member: discord.Member = None, role: discord.Role = None):
    if not await _need_guild(ctx): return
    if action not in ("give", "take") or not member or not role:
        return await safe_send(ctx.channel, f"Usage: {PREFIX}role give|take @user @role")
    coro = member.add_roles(role) if action == "give" else member.remove_roles(role)
    await _guild_call(ctx, coro, f"✅ {action} {role.name} → {member}.")

@bot.command(name="rolenew")
async def cmd_rolenew(ctx, name: str = None, color: str = None):
    if not await _need_guild(ctx) or not name: return
    try: col = discord.Colour(int(color.strip("#"), 16)) if color else discord.Colour.default()
    except Exception: return await safe_send(ctx.channel, "Color must be hex, e.g. ff0000")
    await _guild_call(ctx, ctx.guild.create_role(name=name, colour=col), f"✅ Created role {name}.")

@bot.command(name="emojiadd")
async def cmd_emojiadd(ctx, name: str = None):
    if not await _need_guild(ctx) or not name: return await safe_send(ctx.channel, f"Usage: {PREFIX}emojiadd <name> (+ image)")
    img = await _image_bytes(ctx)
    if not img: return await safe_send(ctx.channel, "Attach (or reply to) an image under 256 KB.")
    await _guild_call(ctx, ctx.guild.create_custom_emoji(name=name, image=img), f"✅ Added emoji :{name}:")

@bot.command(name="servericon")
async def cmd_servericon(ctx):
    if not await _need_guild(ctx): return
    img = await _image_bytes(ctx)
    if not img: return await safe_send(ctx.channel, "Attach (or reply to) an image.")
    await _guild_call(ctx, ctx.guild.edit(icon=img), "✅ Server icon changed.")

# ---------- webhooks (yours) ----------
HELP_HOOK = """```
--- Webhooks ---

  ,hook add <name> <url>     (your msg is deleted)
  ,hook rm <name>   ,hook list
  ,hook send <name> <text>
  ,hook embed <name> <title> | <text> | [hex]

Embeds only work through webhooks;
user accounts can't send them directly.
```"""

async def _hook_post(url, payload):
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as r:
            return r.status

@bot.command(name="hook", aliases=["webhook"])
async def cmd_hook(ctx, action: str = None, name: str = None, *, rest: str = ""):
    a = (action or "").lower()
    hooks = settings["webhooks"]
    if a == "add":
        try: await ctx.message.delete()
        except Exception: pass
        url = rest.strip()
        if not name or not re.match(r"^https://(?:\w+\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+$", url):
            return await safe_send(ctx.channel, "Usage: ,hook add <name> <discord webhook url>")
        hooks[name.lower()] = url; save_settings()
        return await safe_send(ctx.channel, f"✅ Saved webhook `{name}`.")
    if a == "list":
        return await safe_send(ctx.channel, "Webhooks: " + (", ".join(hooks) or "(none)"))
    if a == "rm" and name:
        hooks.pop(name.lower(), None); save_settings()
        return await safe_send(ctx.channel, "Removed.")
    if a in ("send", "embed") and name and name.lower() in hooks and rest.strip():
        try: await ctx.message.delete()
        except Exception: pass
        if a == "send":
            payload = {"content": rest[:2000]}
        else:
            parts = [p.strip() for p in rest.split("|")]
            color = 0x5865F2
            if len(parts) > 2:
                try: color = int(parts[2].strip("#"), 16)
                except Exception: pass
            payload = {"embeds": [{"title": parts[0][:256], "description": (parts[1] if len(parts) > 1 else "")[:4000], "color": color}]}
        st = await _hook_post(hooks[name.lower()], payload)
        if st not in (200, 204):
            await safe_send(ctx.channel, f"❌ Webhook returned HTTP {st}.")
        return
    await safe_send(ctx.channel, HELP_HOOK)

# ---------- profiles ----------
HELP_PROF = """```
--- Profiles ---
(snapshots of settings.json)

  ,profile save <name>
  ,profile load <name>
  ,profile list
  ,profile del <name>

After loading, restart running loops
so they pick up the new settings.
```"""

@bot.command(name="profile")
async def cmd_profile(ctx, action: str = None, name: str = None):
    a = (action or "").lower()
    os.makedirs("profiles", exist_ok=True)
    safe = "".join(c for c in (name or "") if c.isalnum() or c in "-_")
    path = os.path.join("profiles", f"{safe}.json")
    if a == "list":
        return await safe_send(ctx.channel, "Profiles: " + (", ".join(sorted(f[:-5] for f in os.listdir("profiles") if f.endswith(".json"))) or "(none)"))
    if not safe:
        return await safe_send(ctx.channel, HELP_PROF)
    if a == "save":
        with open(path, "w", encoding="utf-8") as f: json.dump(settings, f, indent=2)
        return await safe_send(ctx.channel, f"✅ Saved profile `{safe}`.")
    if a == "load":
        if not os.path.exists(path): return await safe_send(ctx.channel, "No such profile.")
        with open(path, encoding="utf-8") as f: data = json.load(f)
        for k, v in data.items():                       # in place, so running loops keep valid references
            if isinstance(v, dict) and isinstance(settings.get(k), dict):
                settings[k].clear(); settings[k].update(v)
            else:
                settings[k] = v
        save_settings()
        return await safe_send(ctx.channel, f"✅ Loaded `{safe}`. Restart running loops to apply.")
    if a == "del":
        if os.path.exists(path): os.remove(path); return await safe_send(ctx.channel, "Deleted.")
        return await safe_send(ctx.channel, "No such profile.")
    await safe_send(ctx.channel, HELP_PROF)

# ---------- restart persistence ----------
HELP_RESTART = """```
--- Restart Persistence ---

  ,save             remember running status/stream
                    rotators and resume them on start
  ,restartstatus    show what is saved
  ,restartclear     forget it
  ,setchannel <id>  default channel (panel/alerts)
  ,savechannel      same as setchannel here

Counting/other loops are not auto-resumed.
```"""

@bot.command(name="save")
async def cmd_save(ctx):
    r = settings["restart"]
    r["status"] = tm.is_running("status")
    r["stream_rotation"] = tm.is_running("stream_rotation")
    r["auto"] = bool(r["status"] or r["stream_rotation"])
    save_settings()
    await safe_send(ctx.channel, f"💾 Saved: status={r['status']}, stream={r['stream_rotation']}.")

@bot.command(name="restartstatus")
async def cmd_restartstatus(ctx):
    r = settings["restart"]
    await safe_send(ctx.channel, f"Auto-resume: {r['auto']} | status={r['status']} | stream={r['stream_rotation']} | default channel={settings.get('default_channel')}")

@bot.command(name="restartclear")
async def cmd_restartclear(ctx):
    settings["restart"].update({"auto": False, "status": False, "stream_rotation": False}); save_settings()
    await safe_send(ctx.channel, "Restart state cleared.")

@bot.command(name="setchannel", aliases=["savechannel"])
async def cmd_setchannel(ctx, cid: int = None):
    settings["default_channel"] = cid or ctx.channel.id; save_settings()
    await safe_send(ctx.channel, f"Default channel: {settings['default_channel']}")

# ---------- extra autocount controls ----------
@bot.command(name="acskip")
async def cmd_acskip(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Skip: {'on' if settings['autocount'].get('skip') else 'off'}")
    settings["autocount"]["skip"] = mode.lower() == "on"; save_settings()
    await safe_send(ctx.channel, f"Autocount skip: {'ON' if settings['autocount']['skip'] else 'OFF'} (drops a few numbers between hundreds)")

# ---------- HTTP panel (aiohttp) + console ----------
HELP_PANEL = """```
--- Panel & Console ---

Set env PANEL_KEY (and optionally PANEL_PORT,
PANEL_HOST; default 127.0.0.1:8080) to enable.
Send the key as ?key=... or X-Key header.

  /status   /stop   /toggle?feature=<name>
  /setchannel?id=<id>
  /stream?text=<t>&url=<twitch/youtube url>

Console (type in Termux): help status stop quit
```"""

async def _start_panel():
    key = os.getenv("PANEL_KEY")
    if not key:
        return
    from aiohttp import web
    def authed(req):
        given = req.headers.get("X-Key") or req.query.get("key", "")
        return hmac.compare_digest(given.encode(), key.encode())
    async def status(req):
        if not authed(req): return web.json_response({"error": "unauthorized"}, status=401)
        return web.json_response({"user": str(bot.user), "uptime": int(time.monotonic() - START_TIME),
                                  "tasks": {k: tm.status(k) for k in tm.tasks}, "stats": STATS,
                                  "default_channel": settings.get("default_channel")})
    async def stop(req):
        if not authed(req): return web.json_response({"error": "unauthorized"}, status=401)
        cancel_all(); return web.json_response({"ok": True})
    async def toggle(req):
        if not authed(req): return web.json_response({"error": "unauthorized"}, status=401)
        f = req.query.get("feature", "")
        if tm.is_running(f): tm.pause(f); st = "paused"
        elif tm.is_paused(f): tm.resume(f); st = "running"
        else: return web.json_response({"error": "feature not running"}, status=404)
        return web.json_response({"feature": f, "state": st})
    async def setchannel(req):
        if not authed(req): return web.json_response({"error": "unauthorized"}, status=401)
        cid = req.query.get("id", "")
        if not cid.isdigit(): return web.json_response({"error": "bad id"}, status=400)
        settings["default_channel"] = int(cid); save_settings(); return web.json_response({"ok": True})
    async def stream(req):
        if not authed(req): return web.json_response({"error": "unauthorized"}, status=401)
        text, url = req.query.get("text", ""), req.query.get("url", "")
        if not text or not url.startswith("http"): return web.json_response({"error": "need text and url"}, status=400)
        tm.cancel("status"); tm.cancel("stream_rotation")
        await bot.change_presence(activity=discord.Streaming(name=text, url=url)); return web.json_response({"ok": True})
    app = web.Application()
    app.add_routes([web.get("/status", status), web.get("/stop", stop), web.get("/toggle", toggle),
                    web.get("/setchannel", setchannel), web.get("/stream", stream)])
    runner = web.AppRunner(app); await runner.setup()
    host, port = os.getenv("PANEL_HOST", "127.0.0.1"), int(os.getenv("PANEL_PORT", "8080"))
    await web.TCPSite(runner, host, port).start()
    print(f"[panel] listening on {host}:{port}")

def _console_cmd(c):
    if c == "help": print("help status stop quit")
    elif c == "status": print({k: tm.status(k) for k in tm.tasks}, STATS)
    elif c == "stop": cancel_all(); print("all loops stopped")
    elif c == "quit":
        cancel_all(); asyncio.ensure_future(bot.close())

def _console_thread(loop):
    try:
        for line in sys.stdin:
            loop.call_soon_threadsafe(_console_cmd, line.strip().lower())
    except Exception:
        pass

_STARTED = {"done": False}
async def _l_on_ready():
    if _STARTED["done"]:
        return
    _STARTED["done"] = True
    globals()["u_self_id"] = bot.user.id          # antigc reads this; its own hook never fired
    asyncio.create_task(_sched_loop())
    try: await _start_panel()
    except Exception as e: print(f"[panel] failed: {e}")
    try:
        if sys.stdin and sys.stdin.isatty():
            print("[console] type: help | status | stop | quit")
            threading.Thread(target=_console_thread, args=(asyncio.get_running_loop(),), daemon=True).start()
    except Exception:
        pass
    r = settings["restart"]
    if r.get("auto"):
        if r.get("status"):
            tm.register("status", asyncio.create_task(status_loop()))
        if r.get("stream_rotation"):
            tm.register("stream_rotation", asyncio.create_task(stream_rotation_loop()))
        print("[restart] resumed saved loops")
bot.add_listener(_l_on_ready, "on_ready")

# =========================================================
# FEATURE PACK 3: ai chat + lore, countdown, triggers, ackeyword,
# vanity watch (alert only), snapshot, typing, ss
# =========================================================
import copy

for _sec, _val in {
    "ai": {"provider": "groq", "keys": {}, "models": {}, "system": "You are a concise, helpful assistant.", "max_tokens": 400},
    "lore": {},
    "triggers": {},
    "ackeyword": None,
}.items():
    if _sec not in settings:
        settings[_sec] = json.loads(json.dumps(_val))
    elif isinstance(_val, dict):
        for _k, _v in _val.items():
            settings[_sec].setdefault(_k, _v)
save_settings()

# ---------- AI chat ----------
AI_PROVIDERS = {
    "groq": ("https://api.groq.com/openai/v1/chat/completions", "llama-3.3-70b-versatile", "GROQ_API_KEY"),
    "openai": ("https://api.openai.com/v1/chat/completions", "gpt-4o-mini", "OPENAI_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1/chat/completions", "openai/gpt-4o-mini", "OPENROUTER_API_KEY"),
    "deepseek": ("https://api.deepseek.com/chat/completions", "deepseek-chat", "DEEPSEEK_API_KEY"),
    "anthropic": ("https://api.anthropic.com/v1/messages", "claude-haiku-5-5", "ANTHROPIC_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", "gemini-2.0-flash", "GEMINI_API_KEY"),
}

HELP_AI = """```
--- AI Chat & Lore ---

  ,ai <prompt>                ask the AI
  ,aikey <provider> <key>     (your msg is deleted)
  ,aiset provider <name>      groq openai openrouter
                              deepseek anthropic gemini
  ,aiset model <name>         ,aiset system <text>
  ,aiset tokens <n>
  ,aistatus
  ,lore add key=value         facts the AI knows
  ,lore list|rm <key>|clear
  ,exportlore   ,importlore (attach file)

Keys are saved in settings.json (plain text).
They can also come from env vars like GROQ_API_KEY.
```"""

def _ai_key(provider):
    return settings["ai"]["keys"].get(provider) or os.getenv(AI_PROVIDERS[provider][2])

async def ai_complete(prompt):
    cfg = settings["ai"]
    prov = cfg["provider"]
    if prov not in AI_PROVIDERS:
        raise RuntimeError("unknown provider")
    key = _ai_key(prov)
    if not key:
        raise RuntimeError(f"no key for {prov} — use {PREFIX}aikey {prov} <key>")
    url, default_model, _ = AI_PROVIDERS[prov]
    model = cfg["models"].get(prov) or default_model
    system = cfg.get("system", "")
    if settings["lore"]:
        system += "\nBackground facts:\n" + "\n".join(f"- {k}: {v}" for k, v in list(settings["lore"].items())[:60])
    mt = int(cfg.get("max_tokens", 400))
    if prov == "anthropic":
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        body = {"model": model, "max_tokens": mt, "system": system, "messages": [{"role": "user", "content": prompt}]}
    elif prov == "gemini":
        url = url.format(model=model)
        headers = {"x-goog-api-key": key, "content-type": "application/json"}
        body = {"contents": [{"parts": [{"text": prompt}]}], "systemInstruction": {"parts": [{"text": system}]},
                "generationConfig": {"maxOutputTokens": mt}}
    else:
        headers = {"Authorization": f"Bearer {key}", "content-type": "application/json"}
        body = {"model": model, "max_tokens": mt, "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as r:
            data = await r.json(content_type=None)
            if r.status != 200:
                err = data.get("error", data) if isinstance(data, dict) else data
                raise RuntimeError(f"HTTP {r.status}: {str(err)[:200]}")
    if prov == "anthropic":
        return "".join(b.get("text", "") for b in data.get("content", []))
    if prov == "gemini":
        return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
    return data["choices"][0]["message"]["content"]

@bot.command(name="ai")
async def cmd_ai(ctx, *, prompt: str = None):
    if not prompt:
        return await safe_send(ctx.channel, f"Usage: {PREFIX}ai <prompt>")
    try:
        async with ctx.channel.typing():
            out = await ai_complete(prompt)
    except Exception as e:
        return await safe_send(ctx.channel, f"❌ {e}")
    for part in chunk_text(out.strip() or "(empty reply)", 1900)[:3]:
        await safe_send(ctx.channel, part)

@bot.command(name="aikey")
async def cmd_aikey(ctx, provider: str = None, key: str = None):
    try: await ctx.message.delete()
    except Exception: pass
    if not provider or provider.lower() not in AI_PROVIDERS or not key:
        return await safe_send(ctx.channel, f"Usage: {PREFIX}aikey <{'|'.join(AI_PROVIDERS)}> <key>")
    settings["ai"]["keys"][provider.lower()] = key; save_settings()
    await safe_send(ctx.channel, f"✅ Key saved for {provider.lower()}.")

@bot.command(name="aiset")
async def cmd_aiset(ctx, what: str = None, *, value: str = None):
    cfg = settings["ai"]; w = (what or "").lower()
    if w == "provider" and value and value.lower() in AI_PROVIDERS:
        cfg["provider"] = value.lower()
    elif w == "model" and value:
        cfg["models"][cfg["provider"]] = value.strip()
    elif w == "system" and value:
        cfg["system"] = value
    elif w == "tokens" and value and value.isdigit():
        cfg["max_tokens"] = max(50, min(int(value), 2000))
    else:
        return await safe_send(ctx.channel, HELP_AI)
    save_settings()
    await safe_send(ctx.channel, "✅ Updated.")

@bot.command(name="aistatus")
async def cmd_aistatus(ctx):
    c = settings["ai"]; p = c["provider"]
    await safe_send(ctx.channel, f"AI: {p} / {c['models'].get(p) or AI_PROVIDERS[p][1]} | key: {'set' if _ai_key(p) else 'MISSING'} | tokens {c['max_tokens']} | lore {len(settings['lore'])} facts")

@bot.command(name="lore")
async def cmd_lore(ctx, action: str = None, *, rest: str = None):
    lore = settings["lore"]; a = (action or "list").lower()
    if a == "add" and rest and "=" in rest:
        k, _, v = rest.partition("=")
        if k.strip() and v.strip():
            lore[k.strip()] = v.strip(); save_settings()
            return await safe_send(ctx.channel, f"✅ Lore: {k.strip()}")
    elif a == "rm" and rest:
        lore.pop(rest.strip(), None); save_settings(); return await safe_send(ctx.channel, "Removed.")
    elif a == "clear":
        lore.clear(); save_settings(); return await safe_send(ctx.channel, "Lore cleared.")
    elif a == "list":
        return await safe_send(ctx.channel, ("\n".join(f"{k}: {v}" for k, v in lore.items())[:1900]) or "Lore is empty.")
    await safe_send(ctx.channel, f"Usage: {PREFIX}lore add key=value | rm <key> | list | clear")

@bot.command(name="exportlore")
async def cmd_exportlore(ctx):
    if not settings["lore"]:
        return await safe_send(ctx.channel, "Lore is empty.")
    os.makedirs("data", exist_ok=True)
    path = os.path.join("data", "lore_export.json")
    with open(path, "w", encoding="utf-8") as f: json.dump(settings["lore"], f, indent=2, ensure_ascii=False)
    try: await ctx.channel.send(file=discord.File(path))
    except Exception as e: await safe_send(ctx.channel, f"❌ {e}")

@bot.command(name="importlore")
async def cmd_importlore(ctx):
    if not ctx.message.attachments:
        return await safe_send(ctx.channel, "Attach a lore_export file (.json/.txt) with this command.")
    try:
        data = json.loads((await ctx.message.attachments[0].read()).decode("utf-8", errors="replace"))
        if not isinstance(data, dict): raise ValueError("expected a JSON object")
        settings["lore"].update({str(k): str(v) for k, v in data.items()}); save_settings()
        await safe_send(ctx.channel, f"✅ Imported {len(data)} facts.")
    except Exception as e:
        await safe_send(ctx.channel, f"❌ Import failed: {e}")

# ---------- macros: countdown, triggers, ackeyword, vanity watch, snapshot ----------
HELP_MACRO = """```
--- Macros & Utilities ---

  ,countdown <sec> [text]     live countdown (max 120)
  ,trigger <word>, <command>  typing the word alone
                              runs the command (yours only)
  ,trigger list | remove <word>
  ,ackeyword <word|off>       stop autocount when the
                              word is said in its channel
  ,vanitycheck <code>         is a vanity invite free?
  ,vanitywatch <code> [sec]   alert when free (min 60s)
  ,vanitystop
  ,snapshot                   save settings to profiles/
  ,typing <sec>   ,ss (stop rpc/stream)
```"""

@bot.command(name="countdown")
async def cmd_countdown(ctx, secs: int = None, *, text: str = "Go!"):
    if not secs or not 1 <= secs <= 120:
        return await safe_send(ctx.channel, f"Usage: {PREFIX}countdown <1-120> [text]")
    try: await ctx.message.delete()
    except Exception: pass
    msg = await safe_send(ctx.channel, f"⏳ **{secs}**")
    if not msg: return
    left = secs
    while left > 0:
        step = 2 if left > 2 else 1
        await asyncio.sleep(step)
        left -= step
        try: await msg.edit(content=f"⏳ **{max(left, 0)}**" if left > 0 else f"✅ **{text}**")
        except Exception: break

@bot.command(name="trigger")
async def cmd_trigger(ctx, *, args: str = None):
    t = settings["triggers"]
    if not args:
        return await safe_send(ctx.channel, HELP_MACRO)
    low = args.strip().lower()
    if low == "list":
        return await safe_send(ctx.channel, ("\n".join(f"'{k}' → {v}" for k, v in t.items())[:1900]) or "No triggers.")
    if low.startswith("remove "):
        t.pop(low[7:].strip(), None); save_settings(); return await safe_send(ctx.channel, "Removed.")
    if "," in args:
        word, _, cmd = args.partition(",")
        word, cmd = word.strip().lower(), cmd.strip().lstrip(PREFIX)
        if word and cmd and " " not in word and ctx.bot.get_command(cmd.split()[0]):
            t[word] = cmd; save_settings()
            return await safe_send(ctx.channel, f"✅ '{word}' → {PREFIX}{cmd}")
    await safe_send(ctx.channel, f"Usage: {PREFIX}trigger <word>, <command>")

def _ac_channel_id():
    try:
        coro = tm.tasks.get("autocount").get_coro()
        return coro.cr_frame.f_locals["channel"].id
    except Exception:
        return None

async def _l_pack3_message(message):
    me = bot.user
    if not me:
        return
    # owner-only trigger macros
    if message.author.id == me.id and message.content and not message.content.startswith(PREFIX):
        cmd = settings["triggers"].get(message.content.strip().lower())
        if cmd:
            try:
                m2 = copy.copy(message)
                m2.content = f"{PREFIX}{cmd}"
                try: await message.delete()
                except Exception: pass
                await bot.process_commands(m2)
            except Exception as e:
                print(f"[trigger] {e}")
        return
    # autocount stop keyword
    kw = settings.get("ackeyword")
    if kw and message.content and kw.lower() in message.content.lower() and tm.is_running("autocount"):
        cid = _ac_channel_id()
        if cid is None or cid == message.channel.id:
            tm.cancel("autocount")
            await alert(f"⏹️ autocount stopped by keyword '{kw}' from {message.author}")
bot.add_listener(_l_pack3_message, "on_message")

@bot.command(name="ackeyword")
async def cmd_ackeyword(ctx, *, word: str = None):
    if not word:
        return await safe_send(ctx.channel, f"AC stop keyword: {settings.get('ackeyword') or 'none'}")
    settings["ackeyword"] = None if word.lower() == "off" else word.strip().lower(); save_settings()
    await safe_send(ctx.channel, "AC keyword " + ("removed." if not settings["ackeyword"] else f"set: {settings['ackeyword']}"))

async def _vanity_free(code):
    async with aiohttp.ClientSession() as s:
        async with s.get(f"https://discord.com/api/v10/invites/{_up.quote(code)}", timeout=aiohttp.ClientTimeout(total=15),
                         headers={"User-Agent": "Mozilla/5.0"}) as r:
            if r.status == 404: return True
            if r.status == 200: return False
            if r.status == 429:
                ra = r.headers.get("Retry-After", "10")
                raise RuntimeError(f"rate limited, retry in {ra}s")
            raise RuntimeError(f"HTTP {r.status}")

@bot.command(name="vanitycheck")
async def cmd_vanitycheck(ctx, code: str = None):
    code = (code or "").strip().lower().replace("discord.gg/", "")
    if not re.fullmatch(r"[a-z0-9-]{2,32}", code):
        return await safe_send(ctx.channel, f"Usage: {PREFIX}vanitycheck <code>")
    try:
        free = await _vanity_free(code)
    except Exception as e:
        return await safe_send(ctx.channel, f"❌ {e}")
    await safe_send(ctx.channel, f"discord.gg/{code}: " + ("**appears free** (may still be reserved/blocked)" if free else "taken"))

async def _vanity_loop(code, interval):
    while True:
        try:
            if await _vanity_free(code):
                await alert(f"🔔 discord.gg/{code} appears FREE")
                return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[vanitywatch] {e}")
            await asyncio.sleep(60)
        await asyncio.sleep(interval)

@bot.command(name="vanitywatch")
async def cmd_vanitywatch(ctx, code: str = None, interval: int = 120):
    code = (code or "").strip().lower().replace("discord.gg/", "")
    if not re.fullmatch(r"[a-z0-9-]{2,32}", code):
        return await safe_send(ctx.channel, f"Usage: {PREFIX}vanitywatch <code> [seconds, min 60]")
    tm.cancel("vanitywatch")
    tm.register("vanitywatch", asyncio.create_task(_vanity_loop(code, max(60, interval))))
    await safe_send(ctx.channel, f"👀 Watching discord.gg/{code} every {max(60, interval)}s (alert only, nothing is claimed).")

@bot.command(name="vanitystop")
async def cmd_vanitystop(ctx):
    tm.cancel("vanitywatch"); await safe_send(ctx.channel, "Vanity watch stopped.")

@bot.command(name="snapshot")
async def cmd_snapshot(ctx):
    os.makedirs("profiles", exist_ok=True)
    path = os.path.join("profiles", f"snapshot_{int(time.time())}.json")
    with open(path, "w", encoding="utf-8") as f: json.dump(settings, f, indent=2)
    await safe_send(ctx.channel, f"📸 Saved `{path}`")

@bot.command(name="typing")
async def cmd_typing(ctx, sec: float = 5):
    try: await ctx.message.delete()
    except Exception: pass
    await send_typing(ctx.channel, max(1, min(sec, 60)))

@bot.command(name="ss")
async def cmd_ss(ctx):
    await bot.change_presence(activity=None)
    await safe_send(ctx.channel, "Presence/stream cleared.")

EXTRA_HELP.update({"ai": HELP_AI, "macro": HELP_MACRO})
EXTRA_HELP_ALIASES.update({"ai": "ai", "lore": "ai", "macro": "macro", "trigger": "macro", "countdown": "macro",
                           "vanity": "macro", "ackeyword": "macro", "snapshot": "macro"})


# ---------- menu ----------
EXTRA_HELP.update({
    "tools": HELP_TOOLS, "wl": HELP_WL, "time": HELP_TIME, "look": HELP_LOOK, "rpc": HELP_RPC,
    "dl": HELP_DL, "scan": HELP_SCAN, "srv": HELP_SRV, "hook": HELP_HOOK, "prof": HELP_PROF,
    "restart": HELP_RESTART, "panel": HELP_PANEL,
})
EXTRA_HELP_ALIASES.update({
    "tools": "tools", "snipe": "tools", "watch": "tools", "ghostping": "tools",
    "wl": "wl", "wordlist": "wl", "wordlists": "wl",
    "time": "time", "remind": "time", "notes": "time", "todo": "time", "schedule": "time",
    "look": "look", "lookup": "look", "calc": "look", "weather": "look",
    "rpc": "rpc", "presence": "rpc",
    "dl": "dl", "download": "dl", "downloader": "dl",
    "scan": "scan", "checkfile": "scan", "checkurl": "scan",
    "srv": "srv", "server": "srv", "role": "srv",
    "hook": "hook", "webhook": "hook", "webhooks": "hook",
    "prof": "prof", "profile": "prof", "profiles": "prof",
    "restart": "restart", "save": "restart",
    "panel": "panel", "console": "panel",
})
EXTRA_HELP["acmode"] = """```
--- Autocount Modes ---

  ,acmode <mode>   ,acmodes   ,acstep <n>
  ,acskip on|off   drop a few numbers between hundreds

Modes: normal words ordinal roman binary hex octal
greek letter cipher symbol spooky chaotic deg ts
rgb dex sent fullwidth bold double sans boldsans
mono circled sup sub keycap spaced dotted reverse
arabic persian devanagari bengali thai kanji random
(random = different style each number)
```"""

# =========================================================
# PRESS ENGINE (sequential wordlist with (mention) placeholder)
# =========================================================
DEFAULT_SETTINGS["press"] = {
    "file": "press.txt",
    "delay": 2.0,
    "case": "original",
    "format": "off",
    "format_chance": 30,
    "shuffle": False,
    "stagger": False,
    "target_uid": None,
    "sets": {},
    "loop": True,
}
if "press" not in settings:
    settings["press"] = json.loads(json.dumps(DEFAULT_SETTINGS["press"]))
else:
    for k, v in DEFAULT_SETTINGS["press"].items():
        if k not in settings["press"]:
            settings["press"][k] = v
save_settings()

HELP_PRESS = """```
--- Press ---

Control:
  ,press [cid] [uid]     start (uses press.txt)
  ,stoppress             stop
  ,presspause / ,pressresume
  ,pressinfo             show current settings

File:
  ,pressfile <name>      change wordlist
  ,presswall             attach a .txt to replace press.txt

Text:
  ,presscase <original|u|l>
  ,pressdelay <seconds>
  ,pressshuffle on/off
  ,pressstagger on/off   random typing between lines
  ,pressformat on/off [pct]   # - ** * at random

Target:
  ,presstarget <uid|off>  (mention) in lines → <@uid>

Sets:
  ,pressset <name> <m1|m2|m3>   create set
  ,presssetlist
  ,presssetdel <name>
  ,execute <name> [cid] [delay]
  ,stopexecute <name|all>
```"""

async def press_loop(channel, key="press"):
    p = settings["press"]
    if not load_lines(p["file"]):
        await safe_send(channel, f"[press] {p['file']} is empty or missing.")
        return
    while True:
        try:
            if tm.is_paused(key):
                await asyncio.sleep(0.5); continue
            lines = load_lines(p["file"])
            if not lines:
                await asyncio.sleep(2); continue
            if p.get("shuffle"):
                random.shuffle(lines)
            for raw in lines:
                while tm.is_paused(key):
                    await asyncio.sleep(0.3)
                text = raw
                if "(mention)" in text.lower():
                    uid = p.get("target_uid")
                    repl = f"<@{uid}>" if uid else ""
                    text = re.sub(r"\(mention\)", repl, text, flags=re.IGNORECASE)
                    text = " ".join(text.split())
                c = p.get("case", "original")
                if c == "u": text = text.upper()
                elif c == "l": text = text.lower()
                if p.get("format") == "on" and random.randint(1, 100) <= p.get("format_chance", 30):
                    r = random.random()
                    if r < 0.3: text = f"# {text}"
                    elif r < 0.6: text = f"- {text}"
                    elif r < 0.8: text = f"**{text}**"
                    else: text = f"*{text}*"
                if p.get("stagger") and random.random() < 0.35:
                    await send_typing(channel, random.uniform(1.0, 3.0))
                await safe_send(channel, text)
                await asyncio.sleep(p.get("delay", 2.0))
            if not p.get("loop", True):
                break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[press error] {e}")
            await asyncio.sleep(3)

@bot.command(name="press")
async def cmd_press(ctx, channel_id: int = None, uid: int = None):
    ch = bot.get_channel(channel_id) if channel_id else ctx.channel
    if not ch:
        return await safe_send(ctx.channel, "Channel not found.")
    if uid:
        settings["press"]["target_uid"] = uid; save_settings()
    tm.cancel("press")
    task = asyncio.create_task(press_loop(ch))
    tm.register("press", task)
    await safe_send(ctx.channel, f"Press started in {getattr(ch,'name',ch.id)} using {settings['press']['file']}.")

@bot.command(name="stoppress")
async def cmd_stoppress(ctx):
    tm.cancel("press")
    await safe_send(ctx.channel, "Press stopped.")

@bot.command(name="presspause")
async def cmd_presspause(ctx):
    await safe_send(ctx.channel, "Press paused." if tm.pause("press") else "Press not running.")

@bot.command(name="pressresume")
async def cmd_pressresume(ctx):
    await safe_send(ctx.channel, "Press resumed." if tm.resume("press") else "Press not paused.")

@bot.command(name="pressinfo")
async def cmd_pressinfo(ctx):
    p = settings["press"]
    await safe_send(ctx.channel,
        f"```\n--- Press ---\nStatus: {tm.status('press')}\nFile: {p['file']}\nDelay: {p['delay']}s\n"
        f"Case: {p['case']}\nFormat: {p['format']} ({p['format_chance']}%)\n"
        f"Shuffle: {p['shuffle']}\nStagger: {p['stagger']}\nTarget: {p.get('target_uid')}\nLoop: {p['loop']}\n```")

@bot.command(name="pressfile")
async def cmd_pressfile(ctx, filename: str = None):
    if not filename:
        return await safe_send(ctx.channel, f"Current: {settings['press']['file']}")
    fname = normalize_filename(filename, "press.txt")
    if not os.path.exists(fname):
        return await safe_send(ctx.channel, f"{fname} not found.")
    settings["press"]["file"] = fname; save_settings()
    await safe_send(ctx.channel, f"Press file: {fname}")

@bot.command(name="presswall")
async def cmd_presswall(ctx):
    if not ctx.message.attachments:
        return await safe_send(ctx.channel, "Attach a .txt file to replace press.txt")
    att = ctx.message.attachments[0]
    if not att.filename.lower().endswith(".txt"):
        return await safe_send(ctx.channel, "Only .txt files.")
    try:
        text = (await att.read()).decode("utf-8", errors="replace")
        with open("press.txt", "w", encoding="utf-8") as f:
            f.write(text)
        n = len([l for l in text.splitlines() if l.strip()])
        await safe_send(ctx.channel, f"✅ press.txt saved ({n} lines).")
    except Exception as e:
        await safe_send(ctx.channel, f"❌ {e}")

@bot.command(name="pressdelay")
async def cmd_pressdelay(ctx, seconds: float = None):
    if seconds is None:
        return await safe_send(ctx.channel, f"Current: {settings['press']['delay']}s")
    if seconds < 0.3:
        return await safe_send(ctx.channel, "Min 0.3s.")
    settings["press"]["delay"] = seconds; save_settings()
    await safe_send(ctx.channel, f"Press delay: {seconds}s")

@bot.command(name="presscase")
async def cmd_presscase(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Current: {settings['press']['case']}")
    m = mode.lower()
    if m in ("u", "upper"): m = "u"
    elif m in ("l", "lower"): m = "l"
    elif m in ("o", "original"): m = "original"
    else: return await safe_send(ctx.channel, "Use original/u/l.")
    settings["press"]["case"] = m; save_settings()
    await safe_send(ctx.channel, f"Press case: {m}")

@bot.command(name="pressshuffle")
async def cmd_pressshuffle(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Shuffle: {settings['press']['shuffle']}")
    settings["press"]["shuffle"] = mode.lower() == "on"; save_settings()
    await safe_send(ctx.channel, f"Shuffle: {'ON' if settings['press']['shuffle'] else 'OFF'}")

@bot.command(name="pressstagger")
async def cmd_pressstagger(ctx, mode: str = None):
    if mode is None:
        return await safe_send(ctx.channel, f"Stagger: {settings['press']['stagger']}")
    settings["press"]["stagger"] = mode.lower() == "on"; save_settings()
    await safe_send(ctx.channel, f"Stagger: {'ON' if settings['press']['stagger'] else 'OFF'}")

@bot.command(name="pressformat")
async def cmd_pressformat(ctx, mode: str = None, pct: int = None):
    p = settings["press"]
    if mode is None:
        return await safe_send(ctx.channel, f"Format: {p['format']} ({p['format_chance']}%)")
    if mode.lower() == "off":
        p["format"] = "off"
    elif mode.lower() == "on":
        p["format"] = "on"
        if pct is not None and 0 <= pct <= 100: p["format_chance"] = pct
    else:
        return await safe_send(ctx.channel, "on/off [pct]")
    save_settings()
    await safe_send(ctx.channel, f"Format: {p['format']} ({p['format_chance']}%)")

@bot.command(name="presstarget")
async def cmd_presstarget(ctx, uid: str = None):
    if not uid:
        return await safe_send(ctx.channel, f"Target: {settings['press'].get('target_uid')}")
    if uid.lower() == "off":
        settings["press"]["target_uid"] = None; save_settings()
        return await safe_send(ctx.channel, "Target cleared.")
    try: v = int(uid.strip("<@!>"))
    except: return await safe_send(ctx.channel, "Invalid uid.")
    settings["press"]["target_uid"] = v; save_settings()
    await safe_send(ctx.channel, f"Target: <@{v}> — (mention) → <@{v}>")

@bot.command(name="pressset")
async def cmd_pressset(ctx, name: str = None, *, msgs: str = None):
    if not name or not msgs:
        return await safe_send(ctx.channel, "Usage: ,pressset <name> <m1|m2|m3>")
    lst = [m.strip() for m in msgs.split("|") if m.strip()]
    settings["press"]["sets"][name] = lst
    save_settings()
    await safe_send(ctx.channel, f"Set '{name}': {len(lst)} messages.")

@bot.command(name="presssetlist")
async def cmd_presssetlist(ctx):
    sets = settings["press"]["sets"]
    if not sets: return await safe_send(ctx.channel, "No sets.")
    await safe_send(ctx.channel, "\n".join(f"- {n} ({len(m)})" for n, m in sets.items()))

@bot.command(name="presssetdel")
async def cmd_presssetdel(ctx, name: str = None):
    if name in settings["press"]["sets"]:
        del settings["press"]["sets"][name]; save_settings()
        await safe_send(ctx.channel, f"Deleted '{name}'.")
    else:
        await safe_send(ctx.channel, "Not found.")

async def _execute_set(name, ch, delay, key):
    while tm.is_running(key) or tm.is_paused(key):
        try:
            if tm.is_paused(key):
                await asyncio.sleep(0.5); continue
            msgs = settings["press"]["sets"].get(name)
            if not msgs: return
            for m in list(msgs):
                while tm.is_paused(key):
                    await asyncio.sleep(0.3)
                await safe_send(ch, m)
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[execute {name}] {e}")
            await asyncio.sleep(3)

@bot.command(name="execute")
async def cmd_execute(ctx, name: str = None, channel_id: int = None, delay: float = 2.0):
    if not name or name not in settings["press"]["sets"]:
        return await safe_send(ctx.channel, "Usage: ,execute <set> [channel_id] [delay]")
    ch = bot.get_channel(channel_id) if channel_id else ctx.channel
    if not ch: return await safe_send(ctx.channel, "Channel not found.")
    key = f"exec_{name}"
    tm.cancel(key)
    task = asyncio.create_task(_execute_set(name, ch, max(0.5, delay), key))
    tm.register(key, task)
    await safe_send(ctx.channel, f"Running '{name}' in {getattr(ch,'name',ch.id)}.")

@bot.command(name="stopexecute")
async def cmd_stopexecute(ctx, name: str = None):
    if not name: return await safe_send(ctx.channel, "Usage: ,stopexecute <name|all>")
    if name == "all":
        keys = [k for k in list(tm.tasks) if k.startswith("exec_")]
        for k in keys: tm.cancel(k)
        await safe_send(ctx.channel, f"Stopped {len(keys)} set(s).")
    else:
        tm.cancel(f"exec_{name}")
        await safe_send(ctx.channel, f"Stopped '{name}'.")

# =========================================================
# RECITE (quote a user's message + insult)
# =========================================================
DEFAULT_SETTINGS["recite"] = {
    "file": "recite.txt",
    "target_uid": None,
    "channel_id": None,
    "rate": 50,        # % chance to reply per target message
    "wpm": 300,
    "max_quote": 100,
}
if "recite" not in settings:
    settings["recite"] = json.loads(json.dumps(DEFAULT_SETTINGS["recite"]))
else:
    for k, v in DEFAULT_SETTINGS["recite"].items():
        if k not in settings["recite"]:
            settings["recite"][k] = v
save_settings()

HELP_RECITE = """```
--- Recite ---

  ,recite <uid> <channel_id>   set target
  ,recite start                begin
  ,recite off                  stop
  ,recite rate <1-100>         % chance per message
  ,recite wpm <30-1000>        typing speed
  ,reciteinfo                  show current
  ,recitefile <name>           insult wordlist
```"""

async def recite_loop(key="recite"):
    cfg = settings["recite"]
    uid, cid = cfg.get("target_uid"), cfg.get("channel_id")
    if not uid or not cid:
        return
    ch = bot.get_channel(int(cid))
    if not ch:
        return
    last_id = None
    while tm.is_running(key) or tm.is_paused(key):
        try:
            if tm.is_paused(key):
                await asyncio.sleep(1); continue
            lines = load_lines(cfg["file"])
            if not lines:
                await asyncio.sleep(5); continue
            history = ch.history(limit=15, after=discord.Object(id=last_id)) if last_id else ch.history(limit=15)
            async for m in history:
                if not last_id or m.id > last_id:
                    last_id = m.id
                if m.author.id != uid: continue
                if random.randint(1, 100) > cfg.get("rate", 50): continue
                quote = (m.content or "").strip()
                if not quote: continue
                if len(quote) > cfg.get("max_quote", 100):
                    quote = quote[:cfg["max_quote"]].rsplit(" ", 1)[0] + "..."
                out = f'"{quote}" {random.choice(lines)}'
                await send_typing(ch, max(0.3, len(out) / 5 / cfg.get("wpm", 300) * 60))
                await safe_send(ch, out)
            await asyncio.sleep(2)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[recite] {e}")
            await asyncio.sleep(5)

@bot.command(name="recite")
async def cmd_recite(ctx, *args):
    cfg = settings["recite"]
    if not args:
        return await safe_send(ctx.channel, HELP_RECITE)
    a = args[0].lower()
    if a == "start":
        if not cfg.get("target_uid") or not cfg.get("channel_id"):
            return await safe_send(ctx.channel, "Set target first: ,recite <uid> <channel_id>")
        tm.cancel("recite")
        task = asyncio.create_task(recite_loop("recite"))
        tm.register("recite", task)
        return await safe_send(ctx.channel, "Recite started.")
    if a == "off":
        tm.cancel("recite")
        return await safe_send(ctx.channel, "Recite stopped.")
    if a == "rate" and len(args) > 1:
        try: cfg["rate"] = max(1, min(100, int(args[1])))
        except: return await safe_send(ctx.channel, "1-100")
        save_settings(); return await safe_send(ctx.channel, f"Rate: {cfg['rate']}%")
    if a == "wpm" and len(args) > 1:
        try: cfg["wpm"] = max(30, min(1000, int(args[1])))
        except: return await safe_send(ctx.channel, "30-1000")
        save_settings(); return await safe_send(ctx.channel, f"WPM: {cfg['wpm']}")
    if len(args) >= 2 and args[0].lstrip("<@!>").rstrip(">").isdigit() and args[1].isdigit():
        cfg["target_uid"] = int(args[0].strip("<@!>"))
        cfg["channel_id"] = int(args[1])
        save_settings()
        return await safe_send(ctx.channel, f"Target: <@{cfg['target_uid']}> in {cfg['channel_id']}")
    await safe_send(ctx.channel, HELP_RECITE)

@bot.command(name="reciteinfo")
async def cmd_reciteinfo(ctx):
    c = settings["recite"]
    await safe_send(ctx.channel,
        f"```\nStatus: {tm.status('recite')}\nTarget: {c.get('target_uid')}\nChannel: {c.get('channel_id')}\n"
        f"Rate: {c['rate']}% | WPM: {c['wpm']} | File: {c['file']}\n```")

@bot.command(name="recitefile")
async def cmd_recitefile(ctx, filename: str = None):
    if not filename:
        return await safe_send(ctx.channel, f"File: {settings['recite']['file']}")
    fname = normalize_filename(filename, "recite.txt")
    if not os.path.exists(fname):
        return await safe_send(ctx.channel, f"{fname} not found.")
    settings["recite"]["file"] = fname; save_settings()
    await safe_send(ctx.channel, f"Recite file: {fname}")

# =========================================================
# NAMED AUTOPASTE SESSIONS
# =========================================================
if "autopaste_sessions" not in settings:
    settings["autopaste_sessions"] = {}
save_settings()

HELP_APSESS = """```
--- Named Autopaste Sessions ---

  ,apmessage <name> <text>       queue msg
  ,aplist [name]                 show queues
  ,pasteremove <name> <id>       remove by position
  ,apstart <name> [cid] [delay]  run a session
  ,apsstop <name|all>            stop session(s)

(Named sessions use .apsstop to avoid clashing
 with the basic .apstop command.)
```"""

async def _ap_session_loop(name, key):
    sess = settings["autopaste_sessions"].get(name)
    if not sess: return
    ch = bot.get_channel(int(sess["channel_id"])) if sess.get("channel_id") else None
    if not ch: return
    while tm.is_running(key) or tm.is_paused(key):
        try:
            if tm.is_paused(key):
                await asyncio.sleep(0.5); continue
            msgs = sess.get("messages", [])
            if not msgs:
                await asyncio.sleep(2); continue
            for m in list(msgs):
                if not (tm.is_running(key) or tm.is_paused(key)): return
                while tm.is_paused(key): await asyncio.sleep(0.3)
                await safe_send(ch, m)
                await asyncio.sleep(sess.get("delay", 3.0))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[apsess {name}] {e}")
            await asyncio.sleep(3)

@bot.command(name="apmessage")
async def cmd_apmessage(ctx, name: str = None, *, text: str = None):
    if not name or not text:
        return await safe_send(ctx.channel, f"Usage: {PREFIX}apmessage <name> <text>")
    sess = settings["autopaste_sessions"].setdefault(name, {"messages": [], "channel_id": None, "delay": 3.0})
    sess["messages"].append(text)
    save_settings()
    await safe_send(ctx.channel, f"[{name}] added #{len(sess['messages'])}: {text[:60]}")

@bot.command(name="aplist")
async def cmd_aplist(ctx, name: str = None):
    if name:
        sess = settings["autopaste_sessions"].get(name)
        if not sess: return await safe_send(ctx.channel, "No such session.")
        if not sess["messages"]: return await safe_send(ctx.channel, "Empty.")
        lines = [f"{i+1}. {m[:60]}" for i, m in enumerate(sess["messages"])]
        return await safe_send(ctx.channel,
            f"[{name}] ch:{sess.get('channel_id')} delay:{sess.get('delay')}s\n" + "\n".join(lines))
    if not settings["autopaste_sessions"]:
        return await safe_send(ctx.channel, "No sessions.")
    await safe_send(ctx.channel, "\n".join(
        f"{n}: {len(s['messages'])} msgs | ch {s.get('channel_id')} | {s.get('delay')}s"
        for n, s in settings["autopaste_sessions"].items()))

@bot.command(name="pasteremove")
async def cmd_pasteremove(ctx, name: str = None, idx: int = None):
    if not name or idx is None:
        return await safe_send(ctx.channel, f"Usage: {PREFIX}pasteremove <name> <n>")
    sess = settings["autopaste_sessions"].get(name)
    if not sess: return await safe_send(ctx.channel, "No such session.")
    if 1 <= idx <= len(sess["messages"]):
        removed = sess["messages"].pop(idx - 1); save_settings()
        return await safe_send(ctx.channel, f"Removed #{idx}: {removed[:60]}")
    await safe_send(ctx.channel, "Invalid index.")

@bot.command(name="apstart")
async def cmd_apstart(ctx, name: str = None, cid: int = None, delay: float = 3.0):
    if not name:
        return await safe_send(ctx.channel, f"Usage: {PREFIX}apstart <name> [cid] [delay]")
    sess = settings["autopaste_sessions"].setdefault(name, {"messages": [], "channel_id": None, "delay": 3.0})
    if cid: sess["channel_id"] = cid
    sess["delay"] = max(0.5, delay)
    if not sess.get("channel_id"):
        sess["channel_id"] = ctx.channel.id
    save_settings()
    key = f"apsess_{name}"
    tm.cancel(key)
    task = asyncio.create_task(_ap_session_loop(name, key))
    tm.register(key, task)
    await safe_send(ctx.channel, f"[{name}] running in ch {sess['channel_id']} every {sess['delay']}s")

@bot.command(name="apsstop")
async def cmd_apsstop(ctx, name: str = None):
    if not name:
        return await safe_send(ctx.channel, f"Usage: {PREFIX}apsstop <name|all>")
    if name == "all":
        keys = [k for k in list(tm.tasks) if k.startswith("apsess_")]
        for k in keys: tm.cancel(k)
        return await safe_send(ctx.channel, f"Stopped {len(keys)} session(s).")
    tm.cancel(f"apsess_{name}")
    await safe_send(ctx.channel, f"[{name}] stopped.")

# =========================================================
# ONE-SHOT SENDERS (burst / ladder / pastewl / paste)
# =========================================================
if "pastewl_file" not in settings:
    settings["pastewl_file"] = "pastewl.txt"
save_settings()

HELP_ONESHOT = """```
--- One-Shot Senders ---

  ,burst [cid] <m1,m2,m3>     fire messages back-to-back
  ,ladder [cid] <words>       one word per msg, typing between
  ,pastewl                    attach a .txt for one-shot
  ,paste [cid] [delay]        send wl once (no loop)
  ,pastewlshow                peek current paste wl
```"""

@bot.command(name="burst")
async def cmd_burst(ctx, *args):
    if not args:
        return await safe_send(ctx.channel, f"Usage: {PREFIX}burst [cid] <m1,m2,m3>")
    cid, rest = None, list(args)
    if rest and rest[0].isdigit() and len(rest[0]) > 8:
        cid = int(rest.pop(0))
    ch = bot.get_channel(cid) if cid else ctx.channel
    if not ch: return await safe_send(ctx.channel, "Channel not found.")
    text = " ".join(rest)
    msgs = [m.strip() for m in text.split(",") if m.strip()]
    if not msgs: return await safe_send(ctx.channel, "No messages.")
    if len(msgs) > 25: return await safe_send(ctx.channel, "Max 25 per burst.")
    for i, m in enumerate(msgs):
        await safe_send(ch, m)
        if i < len(msgs) - 1:
            await asyncio.sleep(0.1)
    await safe_send(ctx.channel, f"Burst: {len(msgs)} message(s) sent.")

@bot.command(name="ladder")
async def cmd_ladder(ctx, *args):
    if not args:
        return await safe_send(ctx.channel, f"Usage: {PREFIX}ladder [cid] <words>")
    cid, rest = None, list(args)
    if rest and rest[0].isdigit() and len(rest[0]) > 8:
        cid = int(rest.pop(0))
    ch = bot.get_channel(cid) if cid else ctx.channel
    if not ch: return await safe_send(ctx.channel, "Channel not found.")
    words = " ".join(rest).split()
    if not words: return await safe_send(ctx.channel, "No words.")
    if len(words) > 40: return await safe_send(ctx.channel, "Max 40 words.")
    for i, w in enumerate(words):
        await send_typing(ch, random.uniform(0.4, 1.0))
        await safe_send(ch, w)
        if i < len(words) - 1:
            await asyncio.sleep(random.uniform(0.6, 1.4))
    await safe_send(ctx.channel, f"Ladder: {len(words)} word(s) sent.")

@bot.command(name="pastewl")
async def cmd_pastewl(ctx):
    if not ctx.message.attachments:
        return await safe_send(ctx.channel, f"Attach a .txt to set pastewl ({settings['pastewl_file']}).")
    att = ctx.message.attachments[0]
    if not att.filename.lower().endswith(".txt"):
        return await safe_send(ctx.channel, "Only .txt.")
    try:
        text = (await att.read()).decode("utf-8", errors="replace")
        with open(settings["pastewl_file"], "w", encoding="utf-8") as f: f.write(text)
        n = len([l for l in text.splitlines() if l.strip()])
        await safe_send(ctx.channel, f"✅ Paste wl saved ({n} lines).")
    except Exception as e:
        await safe_send(ctx.channel, f"❌ {e}")

@bot.command(name="pastewlshow")
async def cmd_pastewlshow(ctx):
    lines = load_lines(settings["pastewl_file"])
    if not lines: return await safe_send(ctx.channel, "Paste wl empty.")
    await safe_send(ctx.channel, f"{len(lines)} lines. First 10:\n" + "\n".join(f"- {l[:60]}" for l in lines[:10]))

@bot.command(name="paste")
async def cmd_paste(ctx, cid: int = None, delay: float = 1.5):
    if not cid: return await safe_send(ctx.channel, f"Usage: {PREFIX}paste <cid> [delay]")
    ch = bot.get_channel(cid)
    if not ch: return await safe_send(ctx.channel, "Channel not found.")
    lines = load_lines(settings["pastewl_file"])
    if not lines: return await safe_send(ctx.channel, "Paste wl empty.")
    delay = max(0.5, min(delay, 30))
    for i, l in enumerate(lines):
        await safe_send(ch, l)
        if i < len(lines) - 1:
            await asyncio.sleep(delay)
    await safe_send(ctx.channel, f"Sent {len(lines)} line(s) to {getattr(ch,'name',cid)}.")

# =========================================================
# GC EXTRAS (lock / mode / agct / one-shot rename)
# =========================================================
if "gc_extra" not in settings:
    settings["gc_extra"] = {"locks": {}, "modes": {}}
else:
    settings["gc_extra"].setdefault("locks", {})
    settings["gc_extra"].setdefault("modes", {})
save_settings()

HELP_GCEXTRA = """```
--- GC Extras ---

  ,gclock <gid> <name>[,<name2>,...]   lock name (snap back on change)
  ,gcunlock <gid>                      release lock
  ,gclocks                             list locks
  ,gcmode <gid> <spin|bold|strip|rcase|reverse|off>
  ,agct <gid> <name>[,<name2>]         max-speed rename cycle
  ,agctstop <gid>                      stop agct
  ,renamegc <gid> <name>               one-shot rename
```"""

def _gc_transform(name, mode):
    if mode == "spin":    return name.upper() if name == name.lower() else name.lower()
    if mode == "bold":    return f"**{name.strip('*')}**"
    if mode == "strip":   return re.sub(r"[^A-Za-z0-9 ]", "", name)
    if mode == "rcase":   return "".join(random.choice([c.upper(), c.lower()]) for c in name)
    if mode == "reverse": return name[::-1]
    return name

async def _gclock_loop(gid, names, key):
    idx = 0
    cur = names[0]
    ch = bot.get_channel(gid)
    if not ch: return
    try:
        await ch.edit(name=cur)
    except Exception: pass
    while tm.is_running(key) or tm.is_paused(key):
        try:
            if tm.is_paused(key):
                await asyncio.sleep(1); continue
            cur_ch = bot.get_channel(gid)
            if cur_ch and cur_ch.name != cur:
                idx = (idx + 1) % len(names)
                cur = names[idx]
                try: await cur_ch.edit(name=cur)
                except Exception: pass
            await asyncio.sleep(0.5)
        except asyncio.CancelledError: raise
        except Exception as e:
            print(f"[gclock] {e}")
            await asyncio.sleep(1)

async def _agct_loop(gid, names, key):
    ch = bot.get_channel(gid)
    if not ch: return
    counter = 1
    idx = 0
    burst = 0
    while tm.is_running(key) or tm.is_paused(key):
        try:
            if tm.is_paused(key):
                await asyncio.sleep(0.5); continue
            base = names[idx % len(names)]
            new = f"{base} {counter}"[:100]
            try:
                await ch.edit(name=new)
                counter += 1; idx += 1; burst += 1
            except discord.HTTPException as e:
                if getattr(e, "status", 0) == 429:
                    await asyncio.sleep(2)
            if burst >= 5:
                burst = 0; await asyncio.sleep(0.5)
            else:
                await asyncio.sleep(0)
        except asyncio.CancelledError: raise
        except Exception:
            await asyncio.sleep(0.3)

@bot.command(name="gclock")
async def cmd_gclock(ctx, gid: int = None, *, names: str = None):
    if not gid or not names:
        return await safe_send(ctx.channel, f"Usage: {PREFIX}gclock <gid> <name>[,<name2>,...]")
    lst = [n.strip() for n in names.split(",") if n.strip()]
    if not lst: return await safe_send(ctx.channel, "Need at least one name.")
    settings["gc_extra"]["locks"][str(gid)] = lst; save_settings()
    key = f"gclock_{gid}"
    tm.cancel(key)
    task = asyncio.create_task(_gclock_loop(gid, lst, key))
    tm.register(key, task)
    await safe_send(ctx.channel, f"Locked {gid} → {lst}")

@bot.command(name="gcunlock")
async def cmd_gcunlock(ctx, gid: int = None):
    if not gid: return await safe_send(ctx.channel, f"Usage: {PREFIX}gcunlock <gid>")
    tm.cancel(f"gclock_{gid}")
    settings["gc_extra"]["locks"].pop(str(gid), None); save_settings()
    await safe_send(ctx.channel, f"Unlocked {gid}.")

@bot.command(name="gclocks")
async def cmd_gclocks(ctx):
    l = settings["gc_extra"]["locks"]
    if not l: return await safe_send(ctx.channel, "No GC locks.")
    await safe_send(ctx.channel, "\n".join(f"{gid} → {', '.join(n)}" for gid, n in l.items()))

@bot.command(name="gcmode")
async def cmd_gcmode(ctx, gid: int = None, mode: str = None):
    if not gid or not mode:
        return await safe_send(ctx.channel, f"Usage: {PREFIX}gcmode <gid> <spin|bold|strip|rcase|reverse|off>")
    m = mode.lower()
    if m not in ("spin", "bold", "strip", "rcase", "reverse", "off"):
        return await safe_send(ctx.channel, "Invalid mode.")
    if m == "off":
        settings["gc_extra"]["modes"].pop(str(gid), None)
    else:
        settings["gc_extra"]["modes"][str(gid)] = m
    save_settings()
    await safe_send(ctx.channel, f"GC mode {m} for {gid}.")

async def _gc_mode_hook(payload):
    cid = payload.channel_id
    mode = settings["gc_extra"]["modes"].get(str(cid))
    if not mode: return
    ch = bot.get_channel(cid)
    if not ch: return
    want = _gc_transform(ch.name or "", mode)
    if ch.name != want:
        try: await ch.edit(name=want[:100])
        except Exception: pass
bot.add_listener(_gc_mode_hook, "on_guild_channel_update")

@bot.command(name="agct")
async def cmd_agct(ctx, gid: int = None, *, names: str = None):
    if not gid or not names:
        return await safe_send(ctx.channel, f"Usage: {PREFIX}agct <gid> <name>[,<name2>]")
    lst = [n.strip() for n in names.split(",") if n.strip()]
    if not lst: return await safe_send(ctx.channel, "Need at least one name.")
    key = f"agct_{gid}"
    tm.cancel(key)
    task = asyncio.create_task(_agct_loop(gid, lst, key))
    tm.register(key, task)
    await safe_send(ctx.channel, f"AGCT started on {gid} cycling {lst}")

@bot.command(name="agctstop")
async def cmd_agctstop(ctx, gid: int = None):
    if not gid: return await safe_send(ctx.channel, f"Usage: {PREFIX}agctstop <gid>")
    tm.cancel(f"agct_{gid}")
    await safe_send(ctx.channel, f"AGCT stopped on {gid}.")

@bot.command(name="renamegc")
async def cmd_renamegc(ctx, gid: int = None, *, name: str = None):
    if not gid or not name:
        return await safe_send(ctx.channel, f"Usage: {PREFIX}renamegc <gid> <name>")
    ch = bot.get_channel(gid)
    if not ch: return await safe_send(ctx.channel, "Channel not found.")
    try:
        await ch.edit(name=name[:100])
        await safe_send(ctx.channel, "Renamed.")
    except Exception as e:
        await safe_send(ctx.channel, f"❌ {e}")

# =========================================================
# SPY / SHUSH / KILL (scoped to chats you can already read)
# =========================================================
if "spy" not in settings:
    settings["spy"] = {}          # { uid_str: cid }
if "shush" not in settings:
    settings["shush"] = []        # [uid, ...]
save_settings()

HELP_SPY = """```
--- Spy / Shush / Kill ---
(scoped to chats you can already read)

  ,spy <cid> <uid>      forward that user's msgs to cid
  ,spylist              show active spies
  ,spystop <uid|all>    stop spying

  ,shush <uid> [uid…]   auto-delete those users' msgs
  ,shush off            stop shushing
  ,shush list           show current list

  ,kill <gc_id> <uid>   remove user from a GC
                        (works only if you own the GC)
```"""

async def _spy_hook(message):
    try:
        if not settings["spy"]: return
        uid = str(message.author.id)
        cid = settings["spy"].get(uid)
        if not cid: return
        dst = bot.get_channel(int(cid))
        if not dst: return
        guild = getattr(getattr(message, "guild", None), "name", "DM/GC")
        ch_name = getattr(message.channel, "name", str(message.channel.id))
        body = (message.content or "")[:500]
        atts = " ".join(a.url for a in message.attachments[:3])
        await safe_send(dst, f"**[spy]** {message.author} in {guild}/#{ch_name}:\n{body} {atts}".strip())
    except Exception: pass
bot.add_listener(_spy_hook, "on_message")

async def _shush_hook(message):
    try:
        if message.author.id in settings["shush"]:
            await message.delete()
    except Exception: pass
bot.add_listener(_shush_hook, "on_message")

@bot.command(name="spy")
async def cmd_spy(ctx, cid: int = None, uid: str = None):
    if not cid or not uid:
        return await safe_send(ctx.channel, f"Usage: {PREFIX}spy <cid> <uid>")
    try: v = int(uid.strip("<@!>"))
    except: return await safe_send(ctx.channel, "Invalid uid.")
    settings["spy"][str(v)] = cid; save_settings()
    await safe_send(ctx.channel, f"Spying on <@{v}> → forwarding to {cid}.")

@bot.command(name="spylist")
async def cmd_spylist(ctx):
    if not settings["spy"]:
        return await safe_send(ctx.channel, "No spies.")
    await safe_send(ctx.channel, "\n".join(f"<@{u}> → ch {c}" for u, c in settings["spy"].items()))

@bot.command(name="spystop")
async def cmd_spystop(ctx, uid: str = None):
    if not uid:
        return await safe_send(ctx.channel, f"Usage: {PREFIX}spystop <uid|all>")
    if uid.lower() == "all":
        settings["spy"] = {}; save_settings()
        return await safe_send(ctx.channel, "All spies stopped.")
    try: v = str(int(uid.strip("<@!>")))
    except: return await safe_send(ctx.channel, "Invalid uid.")
    settings["spy"].pop(v, None); save_settings()
    await safe_send(ctx.channel, "Stopped.")

@bot.command(name="shush")
async def cmd_shush(ctx, *args):
    if not args or args[0].lower() == "list":
        if not settings["shush"]:
            return await safe_send(ctx.channel, "Nobody shushed.")
        return await safe_send(ctx.channel, "\n".join(f"<@{u}>" for u in settings["shush"]))
    if args[0].lower() == "off":
        settings["shush"] = []; save_settings()
        return await safe_send(ctx.channel, "Shush off.")
    added = 0
    for a in args:
        try:
            v = int(a.strip("<@!>"))
            if v not in settings["shush"]:
                settings["shush"].append(v); added += 1
        except Exception: pass
    save_settings()
    await safe_send(ctx.channel, f"Shushing {added} user(s). Their msgs will be deleted where I have perms.")

@bot.command(name="kill")
async def cmd_kill(ctx, gid: int = None, uid: str = None):
    if not gid or not uid:
        return await safe_send(ctx.channel, f"Usage: {PREFIX}kill <gc_id> <uid>")
    ch = bot.get_channel(gid)
    if not isinstance(ch, discord.GroupChannel):
        return await safe_send(ctx.channel, "Not a group channel.")
    if ch.owner_id != bot.user.id:
        return await safe_send(ctx.channel, "You don't own that GC.")
    try: v = int(uid.strip("<@!>"))
    except: return await safe_send(ctx.channel, "Invalid uid.")
    try:
        target = await bot.fetch_user(v)
        await ch.remove_recipients(target)
        await safe_send(ctx.channel, "Removed.")
    except Exception as e:
        await safe_send(ctx.channel, f"❌ {e}")

# =========================================================
# MULTI-SLOT WORDLISTS (1-5 per engine)
# =========================================================
HELP_WSLOTS = """```
--- Wordlist Slots ---

  ,abwl <1-5>            switch AB to slot N
  ,abwlset <1-5>         attach a .txt to fill slot N
  ,abwlshow <1-5>        preview slot N
  ,alwl / ,alwlset / ,alwlshow  (same for autoladder)
  ,akwl / ,akwlset / ,akwlshow  (same for autokill)

Slots are stored as wl_<engine>_<n>.txt.
The active slot's contents get copied into the
engine's main file.
```"""

def _slot_path(engine, n):
    return f"wl_{engine}_{n}.txt"

def _slot_current(engine):
    return settings.get(engine, {}).get("slot", 1)

def _slot_apply(engine):
    """Copy active slot into the engine's file."""
    n = _slot_current(engine)
    src = _slot_path(engine, n)
    if not os.path.exists(src):
        return False
    dst = settings[engine]["file"]
    try:
        shutil.copyfile(src, dst)
        return True
    except Exception:
        return False

async def _slot_switch(ctx, engine, n):
    if not 1 <= n <= 5:
        return await safe_send(ctx.channel, "Slot must be 1-5.")
    settings[engine]["slot"] = n
    save_settings()
    if _slot_apply(engine):
        return await safe_send(ctx.channel, f"{engine}: slot {n} active.")
    await safe_send(ctx.channel,
        f"{engine}: slot {n} selected (no file yet — upload with ,{engine[:2]}wlset {n}).")

async def _slot_set(ctx, engine, n):
    if not 1 <= n <= 5:
        return await safe_send(ctx.channel, "Slot must be 1-5.")
    if not ctx.message.attachments:
        return await safe_send(ctx.channel, f"Attach a .txt for slot {n}.")
    att = ctx.message.attachments[0]
    if not att.filename.lower().endswith(".txt"):
        return await safe_send(ctx.channel, "Only .txt.")
    try:
        text = (await att.read()).decode("utf-8", errors="replace")
        path = _slot_path(engine, n)
        with open(path, "w", encoding="utf-8") as f: f.write(text)
        settings[engine]["slot"] = n
        save_settings()
        _slot_apply(engine)
        count = len([l for l in text.splitlines() if l.strip()])
        await safe_send(ctx.channel, f"{engine}: slot {n} filled ({count} lines) and activated.")
    except Exception as e:
        await safe_send(ctx.channel, f"❌ {e}")

async def _slot_show(ctx, engine, n):
    if not 1 <= n <= 5:
        return await safe_send(ctx.channel, "Slot must be 1-5.")
    path = _slot_path(engine, n)
    if not os.path.exists(path):
        return await safe_send(ctx.channel, "Empty slot.")
    lines = load_lines(path)
    await safe_send(ctx.channel,
        f"{engine} slot {n} ({len(lines)} lines):\n" + "\n".join(f"- {l[:70]}" for l in lines[:10]))

# --- AB ---
@bot.command(name="abwl")
async def cmd_abwl(ctx, n: int = None):
    if not n: return await safe_send(ctx.channel, f"Current AB slot: {_slot_current('autobeef')}")
    await _slot_switch(ctx, "autobeef", n)

@bot.command(name="abwlset")
async def cmd_abwlset(ctx, n: int = None):
    if not n: return await safe_send(ctx.channel, f"Usage: {PREFIX}abwlset <1-5> (attach .txt)")
    await _slot_set(ctx, "autobeef", n)

@bot.command(name="abwlshow")
async def cmd_abwlshow(ctx, n: int = None):
    if not n: return await safe_send(ctx.channel, f"Usage: {PREFIX}abwlshow <1-5>")
    await _slot_show(ctx, "autobeef", n)

# --- AL ---
@bot.command(name="alwl")
async def cmd_alwl(ctx, n: int = None):
    if not n: return await safe_send(ctx.channel, f"Current AL slot: {_slot_current('autoladder')}")
    await _slot_switch(ctx, "autoladder", n)

@bot.command(name="alwlset")
async def cmd_alwlset(ctx, n: int = None):
    if not n: return await safe_send(ctx.channel, f"Usage: {PREFIX}alwlset <1-5> (attach .txt)")
    await _slot_set(ctx, "autoladder", n)

@bot.command(name="alwlshow")
async def cmd_alwlshow(ctx, n: int = None):
    if not n: return await safe_send(ctx.channel, f"Usage: {PREFIX}alwlshow <1-5>")
    await _slot_show(ctx, "autoladder", n)

# --- AK ---
@bot.command(name="akwl")
async def cmd_akwl(ctx, n: int = None):
    if not n: return await safe_send(ctx.channel, f"Current AK slot: {_slot_current('autokill')}")
    await _slot_switch(ctx, "autokill", n)

@bot.command(name="akwlset")
async def cmd_akwlset(ctx, n: int = None):
    if not n: return await safe_send(ctx.channel, f"Usage: {PREFIX}akwlset <1-5> (attach .txt)")
    await _slot_set(ctx, "autokill", n)

@bot.command(name="akwlshow")
async def cmd_akwlshow(ctx, n: int = None):
    if not n: return await safe_send(ctx.channel, f"Usage: {PREFIX}akwlshow <1-5>")
    await _slot_show(ctx, "autokill", n)

EXTRA_HELP["pretype"] = HELP_PRETYPE
EXTRA_HELP_ALIASES.update({"pretype": "pretype", "pretyper": "pretype"})

EXTRA_HELP["press"] = HELP_PRESS
EXTRA_HELP_ALIASES.update({"press": "press", "pressset": "press"})

EXTRA_HELP["recite"] = HELP_RECITE
EXTRA_HELP_ALIASES.update({"recite": "recite"})

EXTRA_HELP["apsess"] = HELP_APSESS
EXTRA_HELP_ALIASES.update({"apsess": "apsess", "apsession": "apsess", "apmessage": "apsess"})

EXTRA_HELP["oneshot"] = HELP_ONESHOT
EXTRA_HELP_ALIASES.update({
    "oneshot": "oneshot", "burst": "oneshot", "ladder": "oneshot", "pastewl": "oneshot",
})

EXTRA_HELP["gcx"] = HELP_GCEXTRA
EXTRA_HELP_ALIASES.update({
    "gcx": "gcx", "gclock": "gcx", "gcmode": "gcx", "agct": "gcx", "renamegc": "gcx",
})

EXTRA_HELP["spy"] = HELP_SPY
EXTRA_HELP_ALIASES.update({"spy": "spy", "shush": "spy", "kill": "spy"})

EXTRA_HELP["wslots"] = HELP_WSLOTS
EXTRA_HELP_ALIASES.update({
    "wslots": "wslots", "abwl": "wslots", "alwl": "wslots", "akwl": "wslots",
})

# =========================================================
# RUN
# =========================================================
if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except KeyboardInterrupt:
        cancel_all()
        print("\nShutting down.")