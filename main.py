# Importa bibliotecas essenciais do sistema e do Python
import os
import asyncio
from threading import Thread
import discord
from discord.ext import commands
from dotenv import load_dotenv
from flask import Flask
import yt_dlp
import subprocess

# Atualiza o yt-dlp para a versão mais recente diretamente na inicialização do Render
try:
    print("Verificando atualizações do yt-dlp...")
    subprocess.check_call(
        ["pip", "install", "--upgrade", "--no-cache-dir", "yt-dlp"]
    )
    print("yt-dlp atualizado com sucesso para a última versão!")
except Exception as e:
    print(f"Não foi possível atualizar o yt-dlp automaticamente: {e}")



# --- SERVIDOR WEB PARA O RENDER (Mantém a porta 8080 aberta) ---
app = Flask("")


@app.route("/")
def home():
    return "Bot DJ Bolado Online!"


def run():
    app.run(host="0.0.0.0", port=8080)


def keep_alive():
    t = Thread(target=run, daemon=True)
    t.start()


# Inicia o servidor web em segundo plano
keep_alive()
# -------------------------------------------------------------

# Carrega o token secreto do arquivo .env
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# Configura as permissões (intents) do bot
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

# Inicializa o bot com o prefixo '!' e desativa o help padrão
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# Dicionários globais de gerenciamento por servidor (Guild ID)
queues = {}
volumes = {}
loop_modes = {}
filters = {}
disconnect_tasks = {}  # Gerencia tarefas de inatividade para evitar bugs
import os

# Caminho absoluto para garantir que o Render encontre o cookies.txt
base_dir = os.path.dirname(os.path.abspath(__file__))
cookies_path = os.path.join(base_dir, "cookies.txt")

ytdl_format_options = {
    'format': 'ba*[ext=m4a]/b/best',
    'noplaylist': True,
    'extractaudio': True,
    'audioformat': 'mp3',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch',
    'source_address': '0.0.0.0',
    'cookiefile': 'cookies.txt',  # <-- Certifique-se de que este arquivo existe e está no diretório correto do deploy
    'extractor_args': {
        'youtube': {'player_client': ['default', 'web_embedded']}
    },
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)


# Função auxiliar de verificação: Checa se o usuário está no mesmo canal de voz do bot
async def check_voice_channel(ctx):
    if ctx.author.voice is None:
        await ctx.send(
            embed=discord.Embed(
                description="⚠️ Você precisa estar em um canal de voz.",
                color=discord.Color.red(),
            )
        )
        return False
    if ctx.voice_client is None:
        await ctx.send(
            embed=discord.Embed(
                description="⚠️ O bot não está conectado a nenhum canal de voz.",
                color=discord.Color.red(),
            )
        )
        return False
    if ctx.author.voice.channel != ctx.voice_client.channel:
        await ctx.send(
            embed=discord.Embed(
                description=(
                    "⚠️ Você precisa estar no **mesmo canal de voz** que o bot"
                    " para usar este comando."
                ),
                color=discord.Color.red(),
            )
        )
        return False
    return True


# Função auxiliar de permissão: Verifica se o usuário é Admin ou possui o cargo "DJ"
def has_dj_permissions(ctx):
    if ctx.author.guild_permissions.administrator:
        return True
    dj_role = discord.utils.get(ctx.author.roles, name="DJ")
    if dj_role is not None:
        return True
    return False


# Classe interativa de botões (View) com checagem de canal de voz
class MusicControlView(discord.ui.View):

    def __init__(self, bot_instance):
        super().__init__(timeout=None)
        self.bot = bot_instance

    @discord.ui.button(
        label="Pausar/Retomar", style=discord.ButtonStyle.blurple, emoji="⏯️"
    )
    async def pause_resume(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if (
            interaction.user.voice is None
            or interaction.user.voice.channel
            != interaction.guild.voice_client.channel
        ):
            return await interaction.response.send_message(
                "⚠️ Você precisa estar no mesmo canal de voz que o bot.",
                ephemeral=True,
            )

        if interaction.guild.voice_client:
            if interaction.guild.voice_client.is_playing():
                interaction.guild.voice_client.pause()
                await interaction.response.send_message(
                    "⏸️ Música pausada!", ephemeral=True
                )
            elif interaction.guild.voice_client.is_paused():
                interaction.guild.voice_client.resume()
                await interaction.response.send_message(
                    "▶️ Música retomada!", ephemeral=True
                )

    @discord.ui.button(label="Pular", style=discord.ButtonStyle.green, emoji="⏭️")
    async def skip_song(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if (
            interaction.user.voice is None
            or interaction.user.voice.channel
            != interaction.guild.voice_client.channel
        ):
            return await interaction.response.send_message(
                "⚠️ Você precisa estar no mesmo canal de voz que o bot.",
                ephemeral=True,
            )

        if (
            interaction.guild.voice_client
            and interaction.guild.voice_client.is_playing()
        ):
            interaction.guild.voice_client.stop()
            await interaction.response.send_message(
                "⏭️ Música pulada!", ephemeral=True
            )

    @discord.ui.button(label="Parar", style=discord.ButtonStyle.red, emoji="⏹️")
    async def stop_music(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        member = interaction.guild.get_member(interaction.user.id)
        is_admin = member.guild_permissions.administrator
        has_dj = discord.utils.get(member.roles, name="DJ") is not None

        if not (is_admin or has_dj):
            return await interaction.response.send_message(
                (
                    "⚠️ Apenas administradores ou quem possui o cargo **DJ** podem"
                    " parar o player."
                ),
                ephemeral=True,
            )

        guild_id = interaction.guild_id
        if guild_id in queues:
            queues[guild_id].clear()
        if interaction.guild.voice_client:
            interaction.guild.voice_client.stop()
            await interaction.response.send_message(
                "⏹️ Música parada e fila limpa!", ephemeral=True
            )


# Classe auxiliar de áudio e filtros
class YTDLSource(discord.PCMVolumeTransformer):

    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get("title")
        self.url = data.get("url")
        self.webpage_url = data.get("webpage_url") or data.get("url")

    @classmethod
    async def from_url(
        cls,
        query,
        *,
        loop=None,
        stream=False,
        volume=0.5,
        audio_filter="normal",
    ):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(
            None, lambda: ytdl.extract_info(query, download=not stream)
        )

        if "entries" in data:
            data = data["entries"][0]

        filename = data["url"] if stream else ytdl.prepare_filename(data)

        ff_options = {"options": "-vn"}
        if audio_filter == "bassboost":
            ff_options = {
                "options": '-vn -af "equalizer=f=100:width_type=o:width=2:g=15"'
            }
        elif audio_filter == "nightcore":
            ff_options = {
                "options": (
                    '-vn -af "asetrate=44100*1.25,aresample=44100,atempo=1.05"'
                )
            }

        audio_source = discord.FFmpegPCMAudio(filename, **ff_options)
        return cls(audio_source, data=data, volume=volume)


# Função recursiva de reprodução segura
async def play_next(ctx):
    guild_id = ctx.guild.id

    # Cancela qualquer tarefa de inatividade pendente para este servidor
    if guild_id in disconnect_tasks:
        disconnect_tasks[guild_id].cancel()
        del disconnect_tasks[guild_id]

    current_loop = loop_modes.get(guild_id, "off")

    if guild_id in queues and len(queues[guild_id]) > 0:
        if (
            current_loop == "song"
            and hasattr(ctx, "_last_player")
            and ctx._last_player
        ):
            player = ctx._last_player
        else:
            player = queues[guild_id].pop(0)
            ctx._last_player = player
            if current_loop == "queue":
                queues[guild_id].append(player)

        def after_playing(error):
            if error:
                print(f"Erro no player: {error}")
            coro = play_next(ctx)
            fut = asyncio.run_coroutine_threadsafe(coro, bot.loop)
            try:
                fut.result()
            except Exception as e:
                print(f"Erro ao avançar a fila: {e}")

        current_vol = volumes.get(guild_id, 0.5)
        player.volume = current_vol

        if ctx.voice_client and not ctx.voice_client.is_playing():
            ctx.voice_client.play(player, after=after_playing)

            embed = discord.Embed(
                title="🎶 Tocando Agora",
                description=f"**[{player.title}]({player.webpage_url})**",
                color=discord.Color.blurple(),
            )
            embed.set_footer(text=f"Loop: {current_loop.upper()} • DJ Bolado")
            view = MusicControlView(bot)
            await ctx.send(embed=embed, view=view)
    else:
        # Cria uma tarefa segura de inatividade (5 minutos)
        async def inactivity_timer():
            await asyncio.sleep(300)
            if (
                ctx.voice_client
                and not ctx.voice_client.is_playing()
                and not ctx.voice_client.is_paused()
            ):
                if guild_id not in queues or len(queues[guild_id]) == 0:
                    await ctx.voice_client.disconnect()
                    await ctx.send(
                        embed=discord.Embed(
                            description="💤 Desconectado por inatividade.",
                            color=discord.Color.dark_gray(),
                        )
                    )

        disconnect_tasks[guild_id] = bot.loop.create_task(inactivity_timer())


@bot.event
async def on_ready():
    print(f"Conectado como {bot.user} (ID: {bot.user.id})")
    try:
        with open("logo.png", "rb") as image:
            await bot.user.edit(avatar=image.read())
            print("Avatar atualizado automaticamente.")
    except Exception:
        print("Nenhuma 'logo.png' encontrada.")
    print("DJ Bolado online com segurança máxima ativa!")


# Tratamento de Erros e Cooldown (Anti-Flood)
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        return await ctx.send(
            embed=discord.Embed(
                description=(
                    "⏳ Calma aí! Aguarde"
                    f" **{error.retry_after:.1f}s** para usar este comando"
                    " novamente (Anti-Flood)."
                ),
                color=discord.Color.orange(),
            )
        )
    if isinstance(error, commands.CommandNotFound):
        return  # Ignora comandos inexistentes para não poluir o chat
    if isinstance(error, commands.MissingRequiredArgument):
        cmd = ctx.command.name if ctx.command else "comando"
        return await ctx.send(
            embed=discord.Embed(
                title="⚠️ Argumento Faltando",
                description=f"Verifique como usar o comando `!{cmd}`.",
                color=discord.Color.orange(),
            )
        )
    print(f"Erro capturado: {error}")


@bot.command(name="ajuda")
async def ajuda(ctx):
    embed = discord.Embed(
        title="🤖 Ajuda - DJ Bolado (Blindado)",
        description="Comandos seguros:",
        color=discord.Color.blue(),
    )
    embed.add_field(
        name="Música",
        value=(
            "`!tocar [termo]`\n`!pausar`\n`!retomar`\n`!pular`\n`!parar` (Requer"
            " cargo DJ ou Admin)"
        ),
        inline=False,
    )
    embed.add_field(
        name="Fila & Config",
        value=(
            "`!fila`\n`!remover [pos]`\n`!volume [0-100]`\n`!loop"
            " [off/song/queue]`\n`!filtro [bassboost/nightcore/normal]`"
        ),
        inline=False,
    )
    try:
        await ctx.author.send(embed=embed)
        await ctx.message.add_reaction("✅")
    except discord.Forbidden:
        await ctx.send("⚠️ Abra suas DMs para receber a ajuda.")


@bot.command(name="ping")
async def ping(ctx):
    await ctx.send(
        embed=discord.Embed(title="Pong! 🏓", color=discord.Color.green())
    )


@bot.command(name="entrar")
async def entrar(ctx):
    if ctx.author.voice is None:
        return await ctx.send(
            embed=discord.Embed(
                description="⚠️ Entre em um canal de voz.", color=discord.Color.red()
            )
        )
    canal = ctx.author.voice.channel
    if ctx.voice_client:
        await ctx.voice_client.move_to(canal)
    else:
        await canal.connect()
    await ctx.send(
        embed=discord.Embed(
            description=f"🔊 Conectado a **{canal.name}**",
            color=discord.Color.blue(),
        )
    )


@bot.command(name="sair")
async def sair(ctx):
    if not await check_voice_channel(ctx):
        return
    guild_id = ctx.guild.id
    if guild_id in queues:
        queues[guild_id].clear()
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
    await ctx.send(
        embed=discord.Embed(
            description="👋 Desconectado e fila limpa.",
            color=discord.Color.dark_gray(),
        )
    )


# Comando unificado de tocar (Com Cooldown Anti-Flood de 2 segundos)
@commands.cooldown(1, 2, commands.BucketType.user)
@bot.command(name="tocar", help="Toca uma música")
async def tocar(ctx, *, query: str):
    if ctx.author.voice is None:
        return await ctx.send(
            embed=discord.Embed(
                description="⚠️ Entre em um canal de voz.", color=discord.Color.red()
            )
        )

    canal_voz = ctx.author.voice.channel
    if ctx.voice_client is None:
        await canal_voz.connect()
    elif ctx.voice_client.channel != canal_voz:
        return await ctx.send(
            embed=discord.Embed(
                description="⚠️ Você precisa estar no mesmo canal que o bot.",
                color=discord.Color.red(),
            )
        )

    async with ctx.typing():
        try:
            guild_id = ctx.guild.id

            if guild_id in queues and len(queues[guild_id]) >= 50:
                return await ctx.send(
                    embed=discord.Embed(
                        description=(
                            "⚠️ A fila atingiu o limite máximo de **50 músicas**."
                            " Aguarde algumas tocarem."
                        ),
                        color=discord.Color.orange(),
                    )
                )

            current_vol = volumes.get(guild_id, 0.5)
            active_filter = filters.get(guild_id, "normal")

            player = await YTDLSource.from_url(
                query,
                loop=bot.loop,
                stream=True,
                volume=current_vol,
                audio_filter=active_filter,
            )

            if guild_id not in queues:
                queues[guild_id] = []

            queues[guild_id].append(player)

            if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
                embed = discord.Embed(
                    title="➕ Adicionado à Fila",
                    description=f"**[{player.title}]({player.webpage_url})**",
                    color=discord.Color.purple(),
                )
                embed.add_field(name="Posição na Fila", value=str(len(queues[guild_id])))
                await ctx.send(embed=embed)
            else:
                # Remove o primeiro item que acabou de ser adicionado para o play_next rodar corretamente
                queues[guild_id].pop(0)
                ctx._last_player = player
                
                current_vol = volumes.get(guild_id, 0.5)
                player.volume = current_vol

                def after_playing(error):
                    if error:
                        print(f"Erro no player: {error}")
                    coro = play_next(ctx)
                    fut = asyncio.run_coroutine_threadsafe(coro, bot.loop)
                    try:
                        fut.result()
                    except Exception as e:
                        print(f"Erro ao avançar a fila: {e}")

                ctx.voice_client.play(player, after=after_playing)

                embed = discord.Embed(
                    title="🎶 Tocando Agora",
                    description=f"**[{player.title}]({player.webpage_url})**",
                    color=discord.Color.blurple(),
                )
                embed.set_footer(text=f"Loop: {loop_modes.get(guild_id, 'off').upper()} • DJ Bolado")
                view = MusicControlView(bot)
                await ctx.send(embed=embed, view=view)

        except Exception as e:
            print(f"Erro seguro de processamento: {e}")
            await ctx.send(
                embed=discord.Embed(
                    description="❌ Não foi possível processar este link/termo.",
                    color=discord.Color.red(),
                )
            )


@bot.command(name="fila")
async def mostrar_fila(ctx):
    guild_id = ctx.guild.id
    if guild_id in queues and len(queues[guild_id]) > 0:
        descricao = ""
        for i, player in enumerate(queues[guild_id][:10], 1):
            descricao += f"`{i}.` [{player.title}]({player.webpage_url})\n"
        embed = discord.Embed(
            title="📜 Fila de Reprodução",
            description=descricao,
            color=discord.Color.gold(),
        )
        embed.set_footer(text=f"Total na fila: {len(queues[guild_id])}")
        await ctx.send(embed=embed)
    else:
        await ctx.send(
            embed=discord.Embed(
                description="🏁 A fila está vazia.", color=discord.Color.orange()
            )
        )


@bot.command(name="remover")
async def remover(ctx, posicao: int):
    guild_id = ctx.guild.id
    if guild_id in queues and len(queues[guild_id]) > 0:
        if 1 <= posicao <= len(queues[guild_id]):
            removida = queues[guild_id].pop(posicao - 1)
            await ctx.send(
                embed=discord.Embed(
                    description=f"🗑️ Removido: **{removida.title}**",
                    color=discord.Color.red(),
                )
            )
        else:
            await ctx.send(
                embed=discord.Embed(
                    description="⚠️ Posição inválida.", color=discord.Color.orange()
                )
            )
    else:
        await ctx.send(
            embed=discord.Embed(
                description="⚠️ A fila está vazia.", color=discord.Color.orange()
            )
        )


@bot.command(name="loop")
async def loop(ctx, modo: str):
    modo = modo.lower()
    if modo in ["off", "song", "queue"]:
        loop_modes[ctx.guild.id] = modo
        await ctx.send(
            embed=discord.Embed(
                description=f"🔁 Loop alterado para: **{modo.upper()}**",
                color=discord.Color.green(),
            )
        )
    else:
        await ctx.send(
            embed=discord.Embed(
                description="⚠️ Use: `off`, `song` ou `queue`.",
                color=discord.Color.red(),
            )
        )


@bot.command(name="filtro")
async def filtro(ctx, tipo: str):
    tipo = tipo.lower()
    if tipo in ["normal", "bassboost", "nightcore"]:
        filters[ctx.guild.id] = tipo
        await ctx.send(
            embed=discord.Embed(
                description=f"🎛️ Filtro definido para: **{tipo.upper()}**",
                color=discord.Color.green(),
            )
        )
    else:
        await ctx.send(
            embed=discord.Embed(
                description="⚠️ Use: `normal`, `bassboost` ou `nightcore`.",
                color=discord.Color.red(),
            )
        )


@bot.command(name="volume")
async def volume(ctx, vol: int):
    if not await check_voice_channel(ctx):
        return
    if 0 <= vol <= 100:
        guild_id = ctx.guild.id
        decimal_vol = vol / 100.0
        volumes[guild_id] = decimal_vol
        if ctx.voice_client.source:
            ctx.voice_client.source.volume = decimal_vol
        await ctx.send(
            embed=discord.Embed(
                description=f"🔊 Volume em **{vol}%**",
                color=discord.Color.green(),
            )
        )
    else:
        await ctx.send(
            embed=discord.Embed(
                description="⚠️ Insira entre 0 e 100.", color=discord.Color.red()
            )
        )


@bot.command(name="pular")
async def pula(ctx):
    if not await check_voice_channel(ctx):
        return
    if ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send(
            embed=discord.Embed(
                description="⏭️ Música pulada!", color=discord.Color.blue()
            )
        )
    else:
        await ctx.send(
            embed=discord.Embed(
                description="⚠️ Nada tocando.", color=discord.Color.orange()
            )
        )


@bot.command(name="pausar")
async def pausar(ctx):
    if not await check_voice_channel(ctx):
        return
    if ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send(
            embed=discord.Embed(
                description="⏸️ Pausado.", color=discord.Color.yellow()
            )
        )
    else:
        await ctx.send(
            embed=discord.Embed(
                description="⚠️ Nada tocando.", color=discord.Color.orange()
            )
        )


@bot.command(name="retomar")
async def retomar(ctx):
    if not await check_voice_channel(ctx):
        return
    if ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send(
            embed=discord.Embed(
                description="▶️ Retomado!", color=discord.Color.green()
            )
        )
    else:
        await ctx.send(
            embed=discord.Embed(
                description="⚠️ O player não está pausado.",
                color=discord.Color.orange(),
            )
        )


@bot.command(name="parar")
async def parar(ctx):
    if not await check_voice_channel(ctx):
        return

    if not has_dj_permissions(ctx):
        return await ctx.send(
            embed=discord.Embed(
                description=(
                    "⚠️ Apenas administradores ou usuários com o cargo **DJ**"
                    " podem parar o bot."
                ),
                color=discord.Color.red(),
            )
        )

    guild_id = ctx.guild.id
    if guild_id in queues:
        queues[guild_id].clear()
    if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
        ctx.voice_client.stop()
        await ctx.send(
            embed=discord.Embed(
                description="⏹️ Parado e fila limpa com segurança.",
                color=discord.Color.dark_red(),
            )
        )
    else:
        await ctx.send(
            embed=discord.Embed(
                description="⚠️ Nada em reprodução.", color=discord.Color.orange()
            )
        )


bot.run(TOKEN)