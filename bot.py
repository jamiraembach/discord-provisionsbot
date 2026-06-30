import os
import discord
from discord import app_commands
from discord.ext import tasks
import sqlite3
from datetime import datetime, date, time

TOKEN = os.getenv("DISCORD_TOKEN")

PROVISION_PRO_ARTIKEL = 250
REMINDER_MINUTEN = 120

LOG_CHANNEL_ID = 1521486760725577788
ABMELDE_CHANNEL_ID = 1520811306888728746
ZEIT_LOG_CHANNEL_ID = 1521268824081436842

STANDARD_PRODUKTE = [
    ("erdbeereis", 900, "Eis"),
    ("oreoeis", 900, "Eis"),
    ("vanilleeis", 900, "Eis"),
    ("mangoeis", 900, "Eis"),
    ("waffeln", 900, "Essen"),
    ("orangenshake", 900, "Shake"),
    ("erdbeershake", 900, "Shake"),
    ("oreoshake", 900, "Shake"),
    ("heiße schokolade", 900, "Getränk"),
    ("latte macchiato", 900, "Getränk"),
]

intents = discord.Intents.default()
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

db = sqlite3.connect("verkauf.db")
cursor = db.cursor()


def setup_database():
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        price REAL NOT NULL,
        category TEXT DEFAULT 'Sonstiges'
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        username TEXT NOT NULL,
        product_name TEXT NOT NULL,
        category TEXT DEFAULT 'Sonstiges',
        quantity INTEGER NOT NULL,
        price_before_discount REAL DEFAULT 0,
        discount_percent REAL DEFAULT 0,
        discount_amount REAL DEFAULT 0,
        final_price REAL DEFAULT 0,
        commission REAL NOT NULL,
        paid INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS employee_channels (
        user_id TEXT PRIMARY KEY,
        username TEXT NOT NULL,
        channel_id INTEGER NOT NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS abmeldungen (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        username TEXT NOT NULL,
        datum_von TEXT NOT NULL,
        datum_bis TEXT NOT NULL,
        grund TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS time_clock (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        username TEXT NOT NULL,
        clock_in TEXT NOT NULL,
        clock_out TEXT,
        active INTEGER DEFAULT 1
    )
    """)

    db.commit()

    for name, price, category in STANDARD_PRODUKTE:
        cursor.execute("""
            INSERT OR IGNORE INTO products (name, price, category)
            VALUES (?, ?, ?)
        """, (name, price, category))

    db.commit()


def today_string():
    return date.today().isoformat()


def create_report_embed(user_id: str, username: str):
    today = today_string()

    cursor.execute("""
        SELECT 
            COALESCE(SUM(quantity), 0),
            COALESCE(SUM(price_before_discount), 0),
            COALESCE(SUM(discount_amount), 0),
            COALESCE(SUM(final_price), 0),
            COALESCE(SUM(commission), 0)
        FROM sales
        WHERE user_id = ? AND DATE(created_at) = ?
    """, (user_id, today))

    menge, umsatz_vor_rabatt, rabatt, endsumme, provision = cursor.fetchone()

    cursor.execute("""
        SELECT category, COALESCE(SUM(quantity), 0), COALESCE(SUM(final_price), 0)
        FROM sales
        WHERE user_id = ? AND DATE(created_at) = ?
        GROUP BY category
    """, (user_id, today))

    categories = cursor.fetchall()

    category_text = ""
    for cat, qty, total in categories:
        category_text += f"**{cat}:** {qty} Stück | {total:.2f}$\n"

    if not category_text:
        category_text = "Noch keine Verkäufe heute."

    cursor.execute("""
        SELECT name, price
        FROM products
        ORDER BY category, name
    """)

    produkt_text = ""
    for name, price in cursor.fetchall():
        produkt_text += f"• {name.title()} = **{price:.0f}$**\n"

    embed = discord.Embed(
        title=f"📋 Verkaufsprotokoll – {date.today().strftime('%d.%m.%Y')}",
        description=f"Separates Tagesprotokoll für **{username}**",
        color=discord.Color.gold()
    )

    embed.add_field(name="📦 Mengen", value=category_text, inline=False)
    embed.add_field(name="💵 Umsatz vor Rabatt", value=f"{umsatz_vor_rabatt:.2f}$", inline=True)
    embed.add_field(name="💸 Rabatt", value=f"-{rabatt:.2f}$", inline=True)
    embed.add_field(name="💰 Gesamtsumme", value=f"{endsumme:.2f}$", inline=True)
    embed.add_field(name="🏦 Verkaufsprovision", value=f"{provision:.2f}$", inline=True)
    embed.add_field(name="🍽️ Verfügbare Speisen & Getränke", value=produkt_text, inline=False)

    embed.set_footer(text="Rabatt wird direkt im Verkaufsformular eingetragen.")
    embed.timestamp = datetime.now()
    return embed


async def do_verkauf(interaction, produkt: str, menge: int, rabatt_prozent: float):
    produkt = produkt.lower()

    if menge <= 0:
        await interaction.response.send_message("Die Menge muss größer als 0 sein.", ephemeral=True)
        return

    if rabatt_prozent < 0 or rabatt_prozent > 100:
        await interaction.response.send_message("Rabatt muss zwischen 0 und 100 Prozent liegen.", ephemeral=True)
        return

    cursor.execute("SELECT name, price, category FROM products WHERE name = ?", (produkt,))
    product = cursor.fetchone()

    if not product:
        await interaction.response.send_message("Dieses Produkt existiert nicht.", ephemeral=True)
        return

    product_name, price, category = product

    umsatz_vor_rabatt = price * menge
    rabatt_betrag = umsatz_vor_rabatt * (rabatt_prozent / 100)
    endpreis = umsatz_vor_rabatt - rabatt_betrag
    provision = menge * PROVISION_PRO_ARTIKEL

    cursor.execute("""
        INSERT INTO sales (
            user_id, username, product_name, category, quantity,
            price_before_discount, discount_percent, discount_amount,
            final_price, commission, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        str(interaction.user.id),
        interaction.user.name,
        product_name,
        category,
        menge,
        umsatz_vor_rabatt,
        rabatt_prozent,
        rabatt_betrag,
        endpreis,
        provision,
        datetime.now().isoformat()
    ))

    db.commit()

    embed = discord.Embed(
        title="🧾 Neuer Verkauf",
        description="Der Verkauf wurde eingetragen.",
        color=discord.Color.green()
    )

    embed.add_field(name="👤 Mitarbeiter", value=interaction.user.mention, inline=False)
    embed.add_field(name="📦 Produkt", value=product_name, inline=True)
    embed.add_field(name="🏷️ Kategorie", value=category, inline=True)
    embed.add_field(name="🔢 Menge", value=str(menge), inline=True)
    embed.add_field(name="💸 Rabatt", value=f"{rabatt_prozent}%", inline=True)
    embed.add_field(name="💵 Endpreis", value=f"{endpreis:.2f}$", inline=True)
    embed.add_field(name="🏦 Provision", value=f"{provision:.2f}$", inline=True)
    embed.timestamp = datetime.now()

    await interaction.response.send_message(embed=embed)

    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel:
        await channel.send(embed=embed)


async def do_einstempeln(interaction):
    cursor.execute("""
        SELECT id FROM time_clock
        WHERE user_id = ? AND active = 1
    """, (str(interaction.user.id),))

    if cursor.fetchone():
        await interaction.response.send_message("Du bist bereits eingestempelt.", ephemeral=True)
        return

    now = datetime.now()

    cursor.execute("""
        INSERT INTO time_clock (user_id, username, clock_in, active)
        VALUES (?, ?, ?, 1)
    """, (
        str(interaction.user.id),
        interaction.user.name,
        now.isoformat()
    ))

    db.commit()

    embed = discord.Embed(title="🟢 Eingestempelt", color=discord.Color.green())
    embed.add_field(name="👤 Mitarbeiter", value=interaction.user.mention, inline=False)
    embed.add_field(name="🕒 Start", value=now.strftime("%d.%m.%Y %H:%M"), inline=True)
    embed.timestamp = now

    await interaction.response.send_message(embed=embed, ephemeral=True)

    channel = bot.get_channel(ZEIT_LOG_CHANNEL_ID)
    if channel:
        await channel.send(embed=embed)


async def do_ausstempeln(interaction):
    cursor.execute("""
        SELECT id, clock_in FROM time_clock
        WHERE user_id = ? AND active = 1
    """, (str(interaction.user.id),))

    row = cursor.fetchone()

    if not row:
        await interaction.response.send_message("Du bist aktuell nicht eingestempelt.", ephemeral=True)
        return

    entry_id, clock_in = row
    start = datetime.fromisoformat(clock_in)
    end = datetime.now()
    stunden = (end - start).total_seconds() / 3600

    cursor.execute("""
        UPDATE time_clock
        SET clock_out = ?, active = 0
        WHERE id = ?
    """, (end.isoformat(), entry_id))

    db.commit()

    embed = discord.Embed(title="🔴 Ausgestempelt", color=discord.Color.red())
    embed.add_field(name="👤 Mitarbeiter", value=interaction.user.mention, inline=False)
    embed.add_field(name="🕒 Start", value=start.strftime("%d.%m.%Y %H:%M"), inline=True)
    embed.add_field(name="🕔 Ende", value=end.strftime("%d.%m.%Y %H:%M"), inline=True)
    embed.add_field(name="⏱️ Dauer", value=f"{stunden:.2f} Stunden", inline=True)
    embed.timestamp = end

    await interaction.response.send_message(embed=embed, ephemeral=True)

    channel = bot.get_channel(ZEIT_LOG_CHANNEL_ID)
    if channel:
        await channel.send(embed=embed)


async def do_arbeitszeit(interaction):
    heute = date.today().isoformat()

    cursor.execute("""
        SELECT clock_in, clock_out
        FROM time_clock
        WHERE user_id = ? AND DATE(clock_in) = ?
    """, (str(interaction.user.id), heute))

    total_seconds = 0

    for clock_in, clock_out in cursor.fetchall():
        start = datetime.fromisoformat(clock_in)
        end = datetime.fromisoformat(clock_out) if clock_out else datetime.now()
        total_seconds += (end - start).total_seconds()

    stunden = total_seconds / 3600

    await interaction.response.send_message(
        f"⏱️ Deine heutige Arbeitszeit: **{stunden:.2f} Stunden**",
        ephemeral=True
    )


class VerkaufModal(discord.ui.Modal, title="Verkauf eintragen"):
    produkt = discord.ui.TextInput(label="Produkt", placeholder="z.B. erdbeereis", required=True)
    menge = discord.ui.TextInput(label="Menge", placeholder="z.B. 2", required=True)
    rabatt_prozent = discord.ui.TextInput(label="Rabatt in %", placeholder="z.B. 10 oder 0", required=False, default="0")

    async def on_submit(self, interaction: discord.Interaction):
        try:
            menge = int(self.menge.value)
            rabatt = float(self.rabatt_prozent.value or 0)
        except ValueError:
            await interaction.response.send_message("Menge und Rabatt müssen Zahlen sein.", ephemeral=True)
            return

        await do_verkauf(interaction, str(self.produkt.value), menge, rabatt)


class ReportButtons(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Verkauf eintragen", emoji="➕", style=discord.ButtonStyle.green)
    async def verkauf_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(VerkaufModal())

    @discord.ui.button(label="Meine Provision", emoji="💰", style=discord.ButtonStyle.blurple)
    async def provision_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        cursor.execute("""
            SELECT COALESCE(SUM(commission), 0)
            FROM sales
            WHERE user_id = ? AND paid = 0
        """, (str(interaction.user.id),))

        total = cursor.fetchone()[0]
        await interaction.response.send_message(f"Deine offene Provision beträgt **{total:.2f}$**.", ephemeral=True)

    @discord.ui.button(label="Aktualisieren", emoji="🔄", style=discord.ButtonStyle.gray)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = create_report_embed(str(interaction.user.id), interaction.user.name)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class StempelButtons(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Einstempeln", emoji="🟢", style=discord.ButtonStyle.green)
    async def einstempeln_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await do_einstempeln(interaction)

    @discord.ui.button(label="Ausstempeln", emoji="🔴", style=discord.ButtonStyle.red)
    async def ausstempeln_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await do_ausstempeln(interaction)

    @discord.ui.button(label="Arbeitszeit", emoji="⏱️", style=discord.ButtonStyle.blurple)
    async def arbeitszeit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await do_arbeitszeit(interaction)

class AbmeldungModal(discord.ui.Modal, title="Abmeldung eintragen"):
    datum_von = discord.ui.TextInput(
        label="Von",
        placeholder="z.B. 01.07.2026",
        required=True
    )

    datum_bis = discord.ui.TextInput(
        label="Bis",
        placeholder="z.B. 05.07.2026",
        required=True
    )

    grund = discord.ui.TextInput(
        label="Grund",
        placeholder="z.B. Urlaub / Krankheit",
        required=True,
        style=discord.TextStyle.paragraph
    )

    async def on_submit(self, interaction: discord.Interaction):
        cursor.execute("""
            INSERT INTO abmeldungen
            (user_id, username, datum_von, datum_bis, grund, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            str(interaction.user.id),
            interaction.user.name,
            str(self.datum_von.value),
            str(self.datum_bis.value),
            str(self.grund.value),
            datetime.now().isoformat()
        ))

        db.commit()

        embed = discord.Embed(
            title="📋 Neue Abmeldung",
            color=discord.Color.orange()
        )

        embed.add_field(
            name="👤 Mitarbeiter",
            value=interaction.user.mention,
            inline=False
        )
        embed.add_field(name="📅 Von", value=str(self.datum_von.value), inline=True)
        embed.add_field(name="📅 Bis", value=str(self.datum_bis.value), inline=True)
        embed.add_field(name="📝 Grund", value=str(self.grund.value), inline=False)
        embed.timestamp = datetime.now()

        await interaction.response.send_message(
            "Abmeldung gespeichert ✅",
            ephemeral=True
        )

        channel = bot.get_channel(ABMELDE_CHANNEL_ID)
        if channel:
            await channel.send(embed=embed)
class AbmeldeButtons(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Abmelden",
        emoji="📋",
        style=discord.ButtonStyle.red
    )
    async def abmelden_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        await interaction.response.send_modal(AbmeldungModal())

@bot.event
async def on_ready():
    await tree.sync()

    if not daily_reports.is_running():
        daily_reports.start()

    if not stempel_reminder.is_running():
        stempel_reminder.start()

    print(f"Eingeloggt als {bot.user}")


@tree.command(name="protokoll", description="Eröffnet dein Tagesprotokoll")
async def protokoll(interaction: discord.Interaction):
    embed = create_report_embed(str(interaction.user.id), interaction.user.name)
    await interaction.response.send_message(
        content=f"{interaction.user.mention} dein Tagesprotokoll wurde eröffnet.",
        embed=embed,
        view=ReportButtons()
    )


@tree.command(name="stempeluhr", description="Öffnet die Stempeluhr mit Buttons")
async def stempeluhr(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🕒 Stempeluhr",
        description="Nutze die Buttons zum Ein- und Ausstempeln.",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="Aktionen",
        value="🟢 Einstempeln\n🔴 Ausstempeln\n⏱ Arbeitszeit prüfen",
        inline=False
    )

    await interaction.response.send_message(embed=embed, view=StempelButtons())


@tree.command(name="verkauf", description="Trägt einen Verkauf ein")
async def verkauf(interaction: discord.Interaction, produkt: str, menge: int, rabatt_prozent: float = 0.0):
    await do_verkauf(interaction, produkt, menge, rabatt_prozent)


@tree.command(name="einstempeln", description="Startet deine Arbeitszeit")
async def einstempeln(interaction: discord.Interaction):
    await do_einstempeln(interaction)


@tree.command(name="ausstempeln", description="Beendet deine Arbeitszeit")
async def ausstempeln(interaction: discord.Interaction):
    await do_ausstempeln(interaction)


@tree.command(name="arbeitszeit", description="Zeigt deine heutige Arbeitszeit")
async def arbeitszeit(interaction: discord.Interaction):
    await do_arbeitszeit(interaction)


@tree.command(name="abmelden", description="Melde dich für einen Zeitraum ab")
async def abmelden(interaction: discord.Interaction, datum_von: str, datum_bis: str, grund: str):
    cursor.execute("""
        INSERT INTO abmeldungen
        (user_id, username, datum_von, datum_bis, grund, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        str(interaction.user.id),
        interaction.user.name,
        datum_von,
        datum_bis,
        grund,
        datetime.now().isoformat()
    ))

    db.commit()

    embed = discord.Embed(
        title="📋 Neue Abmeldung",
        description="Eine Abmeldung wurde eingetragen.",
        color=discord.Color.orange()
    )

    embed.add_field(name="👤 Mitarbeiter", value=interaction.user.mention, inline=False)
    embed.add_field(name="📅 Von", value=datum_von, inline=True)
    embed.add_field(name="📅 Bis", value=datum_bis, inline=True)
    embed.add_field(name="📝 Grund", value=grund, inline=False)
    embed.timestamp = datetime.now()

    await interaction.response.send_message("Deine Abmeldung wurde gespeichert ✅", ephemeral=True)

    channel = bot.get_channel(ABMELDE_CHANNEL_ID)
    if channel:
        await channel.send(embed=embed)


@tasks.loop(time=time(hour=0, minute=0))
async def daily_reports():
    cursor.execute("SELECT user_id, username, channel_id FROM employee_channels")
    employees = cursor.fetchall()

    for user_id, username, channel_id in employees:
        channel = bot.get_channel(channel_id)
        if channel:
            embed = create_report_embed(user_id, username)
            await channel.send(embed=embed, view=ReportButtons())


@tasks.loop(minutes=REMINDER_MINUTEN)
async def stempel_reminder():
    cursor.execute("""
        SELECT user_id, username, clock_in
        FROM time_clock
        WHERE active = 1
    """)

    for user_id, username, clock_in in cursor.fetchall():
        try:
            user = await bot.fetch_user(int(user_id))
            await user.send(
                f"⏰ Erinnerung: Du bist seit **{datetime.fromisoformat(clock_in).strftime('%d.%m.%Y %H:%M')}** eingestempelt. "
                "Bitte vergiss nicht, dich auszustempeln."
            )
        except Exception:
            pass


setup_database()
@tree.command(name="abmeldecenter", description="Öffnet das Abmeldeformular")
async def abmeldecenter(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📋 Abmeldecenter",
        description="Klicke auf den Button, um eine Abmeldung einzureichen.",
        color=discord.Color.orange()
    )

    await interaction.response.send_message(
        embed=embed,
        view=AbmeldeButtons()
    )
bot.run(TOKEN)