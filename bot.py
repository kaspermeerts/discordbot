import asyncio
import cairo
from datetime import datetime, timedelta, time, timezone
import dotenv
import discord
import enum
from discord.ext import commands, tasks
import io
import json
import logging
import logging.handlers
import math
import os
import random
import re
import typing

dotenv.load_dotenv()

ALPHABET = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"
NOT_CYRILLIC_RE = re.compile(r"[^" + ALPHABET + "]")

with open("twentythousandwords.txt") as file:
    common_words = file.read().splitlines()
common_words.sort(key=lambda s: [ALPHABET.index(c) for c in s])

with open("wiktionary_ru.txt") as file:
    all_words = file.read().splitlines()
all_words += common_words
all_words = list(dict.fromkeys(all_words))
all_words.sort(key=lambda s: [ALPHABET.index(c) for c in s])
print(f"Loaded {len(all_words)} words")

with open("soft_masculine_nouns.txt") as file:
    masculine_nouns = file.read().splitlines()

with open("soft_feminine_nouns.txt") as file:
    feminine_nouns = file.read().splitlines()

print(f"Loaded {len(masculine_nouns)} soft masculine nouns")
print(f"Loaded {len(feminine_nouns)} soft feminine nouns")
soft_nouns = list(set(masculine_nouns + feminine_nouns).intersection(common_words))

with open("removed_words.txt") as file:
    removed_words = file.read().splitlines()
for removed_word in removed_words:
    try:
        common_words.remove(removed_word)
    except ValueError:
        pass

common_words_by_length = [[word for word in common_words if len(word) == i] for i in range(24)]

@enum.unique
class GameState(enum.IntEnum):
    CONTINUE = 0
    FINISHED = 1
    RESTARTING = 2

class GameManager():
    def __init__(self, game_type):
        self.channels = []
        self.game_type = game_type
        self.games = {}

    def add_channel(self, channel_id : int):
        self.channels.append(channel_id)
        try:
            with open(f"{channel_id}.json") as f:
                try:
                    resume = json.load(f)
                    self.games[channel_id] = self.game_type(resume)
                except ValueError:
                    logging.getLogger('discord').error(f"Malformed JSON file: {channel_id}.json")
                    os.remove(f"{channel_id}.json")
                    self.games[channel_id] = None
        except FileNotFoundError:
            self.games[channel_id] = None

    def remove_channel(self, channel_id : int):
        try:
            self.channels.remove(channel_id)
            self.games.pop(channel_id)
        except (KeyError, ValueError):
            pass

        try:
            os.remove(f"{channel_id}.json")
        except Exception:
            pass

    def add_game(self, channel_id : int, game):
        self.games[channel_id] = game
        self.update_game(channel_id, game)

    def get_game(self, channel_id : int):
        try:
            return self.games[channel_id]
        except KeyError:
            return None

    def update_game(self, channel_id, game):
        with open(f"{channel_id}.json", "w") as f:
            json.dump(game.__dict__, f)

COLOR_ABSENT  = (0x78/0xff, 0x7c/0xff, 0x7e/0xff)
COLOR_PRESENT = (0xc9/0xff, 0xb4/0xff, 0x58/0xff)
COLOR_CORRECT = (0x6a/0xff, 0xaa/0xff, 0x64/0xff)
COLOR_KEY_BG  = (0x87/0xff, 0x8a/0xff, 0x8c/0xff)

def rounded_rect(context, x, y, w, h, r):
    context.new_path()

    context.arc(x+r  , y+r  , r, 2*math.pi * 1/2, 2*math.pi * 3/4)
    context.arc(x+w-r, y+r  , r, 2*math.pi * 3/4, 2*math.pi * 0  )
    context.arc(x+w-r, y+h-r, r, 2*math.pi * 0  , 2*math.pi * 1/4)
    context.arc(x+r  , y+h-r, r, 2*math.pi * 1/4, 2*math.pi * 1/2)

    context.close_path()

def draw_word(word, colors):

    length = 8 if len(word) <= 8 else len(word)
    surface = cairo.ImageSurface(cairo.Format.ARGB32, 10 + length * (80+10), 100)
    cr = cairo.Context(surface)
    cr.select_font_face("Sans")
    options = cr.get_font_options()
    options.set_antialias(cairo.Antialias.GRAY)
    cr.set_font_options(options)
    cr.set_font_size(60)
    cr.set_source_rgba(1, 1, 1, 0)
    cr.paint()

    for i, letter in enumerate(word):
        rounded_rect(cr, 10 + i * (80+10), 10, 80, 80, 5)

        cr.set_source_rgb(colors[i][0], colors[i][1], colors[i][2])
        cr.fill_preserve()

        cr.set_line_width(2)
        cr.set_source_rgb(0, 0, 0)
        cr.stroke()

        cr.set_source_rgb(1, 1, 1)
        extents = cr.text_extents(letter.lower())

        cr.move_to(10 + i*(80+10)+40- extents.x_bearing - extents.width / 2,10+40 - (extents.y_bearing*0) + extents.height*0/2 + 16)
        cr.show_text(letter.lower())

    file_obj = io.BytesIO()
    surface.write_to_png(file_obj)
    file_obj.seek(0)

    return file_obj

class WordleGame:
    def __init__(self, resume=None):
        if resume:
            self.__dict__ = resume
            return

        self.state = GameState.CONTINUE
        self.guesses = 0

        LENGTH_WEIGHTS = [0, 0, 0, 0, 3, 10, 5, 4, 3]
        self.solution = None
        while not self.solution:
            (length,) = random.choices(range(len(LENGTH_WEIGHTS)), weights=LENGTH_WEIGHTS)
            try:
                self.solution = random.choice(common_words_by_length[length])
            except IndexError:
                continue

    def guess(self, guess):

        if len(guess) != len(self.solution):
            return "🔢", None, None

        if guess not in all_words:
            return "❓", None, None

        colors = [COLOR_ABSENT] * len(self.solution)

        tally = {letter:self.solution.count(letter) for letter in self.solution}
        for i, letter in enumerate(guess):
            if letter == self.solution[i]:
                colors[i] = COLOR_CORRECT
                tally[letter] -= 1

        for i, letter in enumerate(guess):
            if letter in tally and tally[letter] > 0 and letter != self.solution[i]:
                colors[i] = COLOR_PRESENT
                tally[letter] -= 1

        file_obj = draw_word(guess, colors)

        self.guesses += 1
        if guess != self.solution:
            return None, None, file_obj

        if self.guesses <= len(self.solution) * 0.75:
            emoji = "😲"
        elif self.guesses <= len(self.solution) * 2.0:
            emoji = "🍬"
        else:
            emoji = None
        self.state = GameState.FINISHED
        msg = ""
        msg += random.choice(["Wow! ", "Good job! ", "Congratulations! ", "Amazing! "])
        msg += random.choice(["You got it! ", "That's it! ", "That's the word I was thinking of! "])
        msg += random.choice(["😊", "🤗", "😃"])
        msg += f"\nhttps://en.wiktionary.org/wiki/{self.solution}\nhttps://ru.wiktionary.org/wiki/{self.solution}"

        return emoji, msg, file_obj

class Wordle(commands.Cog):
    CHANNEL_NAME = "wordle"
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('discord').getChild(self.__class__.__name__)
        self.game_manager = GameManager(WordleGame)

        for guild in self.bot.guilds:
            self.guild_setup(guild)

    async def cog_load(self):
        for channel in self.game_manager.channels:
            game = self.game_manager.get_game(channel)
            if not game or game.state != GameState.CONTINUE:
                await self.start_game(channel)

    def guild_setup(self, guild):
        channel = discord.utils.get(guild.channels, name=self.CHANNEL_NAME)
        if not channel:
            return None
        self.logger.info(f"Found #{self.CHANNEL_NAME} channel in {guild}: {channel.id}")
        self.game_manager.add_channel(channel.id)
        return channel.id

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        if channel_id := self.guild_setup(guild):
            await self.start_game(channel_id)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        for channel in guild.channels:
            if channel.id in self.game_manager.channels:
                self.game_manager.remove_channel(channel.id)

    @commands.command(hidden=True)
    @commands.check_any(commands.is_owner(), commands.has_permissions(administrator=True))
    async def stopW(self, ctx):
        """Concede defeat on this word and get a new one"""
        game = self.game_manager.get_game(ctx.channel.id)
        if not game or game.state != GameState.CONTINUE:
            return

        game.state = GameState.FINISHED
        self.game_manager.update_game(ctx.channel.id, game)
        await ctx.send(f"Oops! The word was **{game.solution}**. Was it too hard?")
        await self.stop_game(ctx.channel.id)

    @commands.command(hidden=True)
    async def length(self, ctx):
        """Get the length of the current target word"""
        game = self.game_manager.get_game(ctx.channel.id)
        if not game or game.state != GameState.CONTINUE:
            return

        await ctx.send(f"The word I'm thinking of is **{len(game.solution)}** letters long")

    async def stop_game(self, channel_id):
        game = self.game_manager.get_game(channel_id)
        if not game: #or game.state != GameState.CONTINUE:
            return

        if game.state == GameState.RESTARTING:
            return

        game.state = GameState.RESTARTING
        self.game_manager.update_game(channel_id, game)

        async with self.bot.get_channel(channel_id).typing():
            await asyncio.sleep(3)

        await self.start_game(channel_id)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.channel.id not in self.game_manager.channels:
            return
        if message.author == self.bot.user:
            return
        ctx = await self.bot.get_context(message)
        if ctx.command:
            return

        game = self.game_manager.get_game(message.channel.id)
        if not game or game.state != GameState.CONTINUE:
            return

        if len(message.content.split()) > 1:
            return
        guess = message.content.strip().lower()

        if not guess or NOT_CYRILLIC_RE.search(guess):
            return

        self.logger.info(f"{message.author.display_name} trying {guess}")

        emoji, msg, file_obj = game.guess(guess)
        if emoji:
            await message.add_reaction(emoji)

        if file_obj:
            await message.channel.send(msg, file=discord.File(fp=file_obj, filename="guess.png"), suppress_embeds=True)
        elif msg:
            await message.channel.send(msg, suppress_embeds=True)

        self.game_manager.update_game(message.channel.id, game)

        if game.state == GameState.FINISHED:
            await self.stop_game(message.channel.id)

    async def start_game(self, channel_id):
        if channel_id not in self.game_manager.channels:
            return

        game = WordleGame()
        self.logger.info(f"Starting new game in {self.bot.get_channel(channel_id).guild}: {game.solution}")
        self.game_manager.add_game(channel_id, game)
        await self.bot.get_channel(channel_id).send(f"I picked a word of {len(game.solution)} letters. Good luck!\n\n⬜ Grey square: The letter is not in the word\n🟨 Yellow square: The letter is in the word, but not in that place\n🟩 Green square: The letter is in the word, and it is in the correct place!")

class PersistentGenderView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)

        self.cog = cog

    async def guess(self, interaction, guess_masculine):
        word = interaction.message.content
        if word in feminine_nouns:
            is_masculine = False
        elif word in masculine_nouns:
            is_masculine = True
        else:
            await interaction.message.delete()
            self.cog.logger.error(f"{word} is not a soft noun")
            return

        self.cog.logger.info(f"{interaction.user.display_name} " + ("in" if (guess_masculine ^ is_masculine) else "") + f"correctly thinks {word} is " + ("masculine" if guess_masculine else "feminine"))

        if guess_masculine ^ is_masculine:
            await interaction.response.send_message("❌ Неправильно! Попробуй ещё раз.", ephemeral=True, delete_after=3)
            return

        await interaction.response.send_message(f"✅ Правильно! **{word}** " + ("мужского" if is_masculine else "женского") + " рода.", ephemeral=True, delete_after=3)

        await self.cog.pose_question(interaction.channel.id, source=word)

    @discord.ui.button(label="Мужской", emoji="♂️", custom_id='persistent:masculine')
    async def masculine(self, interaction, _button):
        await self.guess(interaction, guess_masculine=True)

    @discord.ui.button(label="Женский", emoji="♀️", custom_id='persistent:feminine')
    async def feminine(self, interaction, _button):
        await self.guess(interaction, guess_masculine=False)

class SoftSign(commands.Cog):
    CHANNEL_NAME = "its_okay_to_be_soft"
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('discord').getChild(self.__class__.__name__)

        self.persistent_view = PersistentGenderView(self)
        self.bot.add_view(self.persistent_view)

        self.channels = []
        self.last_word = {}
        for guild in self.bot.guilds:
            self.guild_setup(guild)

    def guild_setup(self, guild):
        channel = discord.utils.get(guild.channels, name=self.CHANNEL_NAME)
        if not channel:
            return
        self.logger.info(f"Found #{self.CHANNEL_NAME} channel in {guild}: {channel.id}")
        self.channels.append(channel.id)
        self.last_word[channel.id] = None

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        self.guild_setup(guild)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        for channel in guild.channels:
            if channel.id in self.channels:
                self.channels.remove(channel.id)
                try:
                    self.last_word.pop(channel.id)
                except (KeyError, ValueError):
                    pass

    @commands.command(hidden=True)
    @commands.check_any(commands.is_owner(), commands.has_permissions(administrator=True))
    async def softsign(self, ctx):
        """Pose a question about the gender of a word ending with a soft sign"""
        if ctx.channel.id not in self.channels:
            return

        await self.pose_question(ctx.channel.id, override=True)

    async def pose_question(self, channel_id, source=None, override=False):
        if not override:
            if source and self.last_word[channel_id] and source != self.last_word[channel_id]:
                return

        word = random.choice(soft_nouns)

        self.last_word[channel_id] = word
        self.logger.info(f"Spawning new word: {word}")
        await asyncio.sleep(5)
        await self.bot.get_channel(channel_id).send(word, view=self.persistent_view)

MIN_LENGTH = 3
MAX_LENGTH = 7

class SpellingGame():
    def __init__(self, resume=None):
        if resume:
            self.__dict__ = resume
            return

        self.state = GameState.CONTINUE
        self.deadline = int((datetime.now().replace(microsecond=0,second=0,minute=0) + timedelta(hours=24)).timestamp())

        self.root_word = random.choice(common_words_by_length[10])
        letters = list(self.root_word)

        solution_words = []
        acceptable_words = []
        for word in all_words:
            if not MIN_LENGTH <= len(word) <= MAX_LENGTH:
                continue

            if all(letter in letters for letter in word):
                if any(letters.count(letter) < list(word).count(letter) for letter in letters):
                    continue
                if word in common_words:
                    solution_words.append(word)
                else:
                    acceptable_words.append(word)

        self.words = dict.fromkeys(solution_words)
        self.other_words = dict.fromkeys(acceptable_words)

    def guess(self, guess, author):

        if guess in self.words:
            if self.words[guess] is not None:
                return "🔁"
            else:
                self.words[guess] = author.id
                if all(self.words.values()):
                    self.state = GameState.FINISHED
                return "✅"
        elif guess in self.other_words:
            if self.other_words[guess] is not None:
                return "🔁"
            else:
                self.other_words[guess] = author.id
                return "📚"
        elif guess in all_words:
            return "❎"

        return "❓"

    def winners_and_losers(self, guild):
        NATIVE_NAMES = ["Сладкая Пилюля", "ЯЖПОГРОМИСТ", "Fish-Teacher", "Trogdor the destroyer", "He🇧🇧🇧🇧🇧🇧🇧", "Ксения"]
        LEARNER_NAMES = ["Kwinten", "Liisa", "Leeto", "Zalamلظلام", "ХАРА́М"]

        learners_score = 0
        learners = set()
        natives_score = 0
        natives = set()

        words = self.words | self.other_words

        for user_id in words.values():
            if not user_id:
                continue

            if 1 <= user_id < 20:
                natives.add(NATIVE_NAMES[user_id - 1])
                natives_score += 1
                continue
            elif 101 <= user_id < 120:
                learners.add(LEARNER_NAMES[user_id - 101])
                learners_score += 1
                continue

            member = guild.get_member(user_id)
            if not member:
                continue

            if any(role.name == "Native" for role in member.roles):
                natives.add(member.display_name)
                natives_score += 1
            else:
                learners.add(member.display_name)
                learners_score += 1

        if natives_score == 0 and learners_score == 0:
            return None, None

        natives_team = Team(name="natives", score=natives_score, members=list(natives))
        learners_team = Team(name="learners", score=learners_score, members=list(learners))
        if learners_score >= natives_score:
            winners, losers = learners_team, natives_team
        else:
            winners, losers = natives_team, learners_team

        return winners, losers

    def progress_embed(self):
        game_ended = self.state != GameState.CONTINUE
        COLUMNS_BY_LENGTH = [0, 0, 0, 4, 3, 3, 2, 2, 2]
        solutions_by_length = [[word for word in self.words if len(word) == i] for i in range(MAX_LENGTH + 1)]
        line = ""
        lines = []
        for length in range(MIN_LENGTH, MAX_LENGTH + 1):
            if not solutions_by_length[length]:
                continue

            for count, word in enumerate(solutions_by_length[length]):
                if self.words[word]:
                    if game_ended:
                        line += "~~" + word + "~~"
                    else:
                        line += word
                else:
                    if game_ended:
                        line += "**" + word + "**"
                    else:
                        line += "\\*" * length
                if (count + 1) % COLUMNS_BY_LENGTH[length] == 0:
                    lines.append(line)
                    line = ""
                else:
                    line += " "

            lines.append(line)
            if line != "":
                lines.append("")
            line = ""

        if line:
            lines.append(line)

        msgl = '\n'.join(lines[:len(lines)//2])
        msgr = '\n'.join(lines[len(lines)//2:])

        found_words = sum(1 for word, user_id in self.words.items() if user_id)
        total_words = len(self.words)
        embed = discord.Embed(title=f"Progress: {found_words} out of {total_words} found", description="The game has ended" if game_ended else f"The game ends <t:{self.deadline}:R>")
        embed.add_field(name="\u200b", value=msgl, inline=True)
        embed.add_field(name="\u200b", value=msgr, inline=True)

        return embed

class Team(typing.NamedTuple):
    name : str
    score : int
    members : list[str]

CHECK_DEADLINE_TIMES = [time(hour=i) for i in range(24)]

class Spelling(commands.Cog):
    CHANNEL_NAME = "spelling_bee"
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('discord').getChild(self.__class__.__name__)
        self.game_manager = GameManager(SpellingGame)

        for guild in self.bot.guilds:
            self.guild_setup(guild)

        self.check_deadline.start()

    async def cog_load(self):
        for channel in self.game_manager.channels:
            game = self.game_manager.get_game(channel)
            if not game or game.state != GameState.CONTINUE:
                await self.start_game(channel)

    def guild_setup(self, guild):
        channel = discord.utils.get(guild.channels, name=self.CHANNEL_NAME)
        if not channel:
            return None
        self.logger.info(f"Found #{self.CHANNEL_NAME} channel in {guild}: {channel.id}")
        self.game_manager.add_channel(channel.id)
        return channel.id

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        channel_id = self.guild_setup(guild)
        if channel_id:
            await self.start_game(channel_id)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        for channel in guild.channels:
            if channel.id in self.game_manager.channels:
                self.game_manager.remove_channel(channel.id)

    @commands.command()
    async def letters(self, ctx):
        """Show the letters of the current word in a random order"""
        game = self.game_manager.get_game(ctx.channel.id)
        if not game:
            return

        word = game.root_word
        msg = f"The word is **{word}**\n"
        msg += "For inspiration, here are the letters in a random order: "
        msg += "**" + "** **".join(random.sample(word, k=len(word))) + "**"
        await ctx.send(msg)

    @commands.command(hidden=True)
    @commands.check_any(commands.is_owner(), commands.has_permissions(administrator=True))
    async def shuffle(self, ctx):
        game = self.game_manager.get_game(ctx.channel.id)
        if not game:
            return

        for word in game.words:
            if random.random() < 0.5:
                game.words[word] = 1 + random.randrange(5) + random.choice([0,100])

        self.game_manager.update_game(ctx.channel.id, game)

    @commands.command()
    async def progress(self, ctx):
        """Give an overview of how many words have already been found"""
        game = self.game_manager.get_game(ctx.channel.id)
        if not game:
            return

        embed = game.progress_embed()
        await ctx.send(embed=embed)


    def teams_message(self, winners, losers, game_ended=False):

        if winners.score == losers.score:
            msg = f"It's a tie! Both teams have found **{winners.score}** words. "
            if game_ended:
                msg += "Everyone is a winner! 🤗\n"
            members = winners.members + losers.members
        else:
            wnoun = "word" if winners.score == 1 else "words"
            lnoun = "word" if losers.score == 1 else "words"

            msg = f"Team {winners.name} has found **{winners.score}** {wnoun}. They are ahead of team {losers.name}, which has found **{losers.score}** {lnoun}.\n"
            members = winners.members

        if not game_ended:
            return msg

        members = ["**" + name + "**" for name in members]
        if len(members) == 1:
            (member_names,) = members
        else:
            member_names = ', '.join(members[:-1]) + " and " + members[-1]

        if winners.score == losers.score:
            msg +=  "Congratulations to all participants: "
        else:
            msg += f"Congratulations to team {winners.name}: "
        msg += member_names + ". "
        CANDIES = "🍬🍪🧁🍩🍭🍫🍦"
        msg += random.choice(["This is for you: ", "Take this: ", "Enjoy: "]) + ''.join(random.choices(CANDIES, k=len(members))) + "\n"

        return msg

    @commands.command()
    async def teams(self, ctx):
        """Which team is winning?"""
        game = self.game_manager.get_game(ctx.channel.id)
        if not game or game.state != GameState.CONTINUE:
            return

        winners, losers = game.winners_and_losers(ctx.guild)

        if not winners or not losers:
            await ctx.send("Nobody here has found a word yet. Why don't you give it a shot?")
        else:
            msg = self.teams_message(winners, losers, game_ended=False)
            await ctx.send(msg)

    @commands.command()
    async def previousgame(self, ctx):
        """The outcome of the previous game"""
        channel_id = ctx.channel.id
        try:
            with open(f"{channel_id}-previousgame.json") as f:
                winnersdict, losersdict, timestamp, gamedict = json.load(f)
            winners = Team(**winnersdict)
            losers = Team(**losersdict)
            game = SpellingGame(gamedict)
        except FileNotFoundError:
            return
        except (ValueError, TypeError) as e:
            self.logger.error(f"Malformed JSON file: {channel_id}-previousgame.json: " + str(e))
            os.remove(f"{channel_id}-previousgame.json")
            return

        embed = game.progress_embed()

        msg = f"The **previous** game ended at <t:{timestamp}:f>. The word was **{game.root_word}**.\n"
        if winners.score == losers.score:
            msg += f"It was a tie, with both teams having found **{winners.score}** words. "
            members = winners.members + losers.members
        else:
            wnoun = "word" if winners.score == 1 else "words"
            lnoun = "word" if losers.score == 1 else "words"

            msg += f"Team {winners.name} won, having found **{winners.score}** {wnoun}. They got the better of team {losers.name}, which found **{losers.score}** {lnoun}.\n"
            members = winners.members

        members = ["**" + name + "**" for name in members]
        if len(members) == 1:
            (member_names,) = members
        else:
            member_names = ', '.join(members[:-1]) + " and " + members[-1]

        if winners.score == losers.score:
            msg +=  "Congratulations to all participants: "
        else:
            msg += f"Congratulations to team {winners.name}: "
        msg += member_names + ". "

        await ctx.send(msg, embed=embed)

    @commands.command(hidden=True)
    @commands.check_any(commands.is_owner(), commands.has_permissions(administrator=True))
    async def stopSB(self, ctx):
        await self.stop_game(ctx.channel.id)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.channel.id not in self.game_manager.channels:
            return
        if message.author == self.bot.user:
            return
        ctx = await self.bot.get_context(message)
        if ctx.command:
            return

        game = self.game_manager.get_game(message.channel.id)
        if not game or game.state != GameState.CONTINUE:
            return

        if len(message.content.split()) > 1:
            return

        guess = message.content.strip().lower()

        if NOT_CYRILLIC_RE.search(guess):
            return

        self.logger.info(f"{message.author.display_name} guesses {guess}")

        emoji = game.guess(guess, message.author)
        if emoji:
            await message.add_reaction(emoji)

        self.game_manager.update_game(message.channel.id, game)

        if game.state == GameState.FINISHED:
            await self.stop_game(message.channel.id)

    async def stop_game(self, channel_id):
        game = self.game_manager.get_game(channel_id)
        if not game: #or game.state != GameState.CONTINUE:
            return

        if game.state == GameState.RESTARTING:
            return

        game.state = GameState.RESTARTING
        self.game_manager.update_game(channel_id, game)

        guild = self.bot.get_channel(channel_id).guild

        msg = "Alright, the game is over! "
        embed = None
        winners, losers = game.winners_and_losers(guild)
        if winners and losers:
            with open(f"{channel_id}-previousgame.json", "w") as f:
                json.dump((winners._asdict(), losers._asdict(), int(datetime.now().timestamp()), game.__dict__), f)

            teams_msg = self.teams_message(winners, losers, game_ended=True)
            msg += random.choice(["Good job!", "Congratulations everyone!", "Excellent work!"]) + " "
            msg += random.choice(["😊", "🤗", "😃"]) + "\n"
            msg += teams_msg

            embed = game.progress_embed()

        await self.bot.get_channel(channel_id).send(msg, embed=embed)

        async with self.bot.get_channel(channel_id).typing():
            await asyncio.sleep(3)

        await self.start_game(channel_id)

    async def start_game(self, channel_id):
        if channel_id not in self.game_manager.channels:
            return

        game = SpellingGame()
        self.game_manager.add_game(channel_id, game)
        self.logger.info(f"Starting new spelling game in {self.bot.get_channel(channel_id).guild} with word {game.root_word}")

        msg = "Let's play a game! I will give you a word and you have to **make smaller words (3-7 letters long) out of it using each letter at most once.**\n"
        msg += f"The game will go on until all these words have been found, or until <t:{game.deadline}:t> (this time is automatically adjusted to your timezone). There are **{len(game.words)}** words to be found.\n\n"
        msg += f"Your word is **{game.root_word}**"

        await self.bot.get_channel(channel_id).send(msg)

    @tasks.loop(time=CHECK_DEADLINE_TIMES)
    async def check_deadline(self):
        now = datetime.now().timestamp()

        for channel_id in self.game_manager.channels:
            game = self.game_manager.get_game(channel_id)
            if not game or game.state != GameState.CONTINUE:
                continue

            # Give 1 second of leeway to avoid accidentally skipping a whole hour
            if now + 1 >= game.deadline:
                if now < game.deadline:
                    self.logger.warning(f"Task triggered at {now}, which is before the deadline {game.deadline}!")
                await self.stop_game(channel_id)

    @check_deadline.before_loop
    async def before_check_deadline(self):
        await self.bot.wait_until_ready()
        await self.check_deadline()


class BumpReminder(commands.Cog):
    CHANNEL_NAME = "bumps"
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger('discord').getChild(self.__class__.__name__)
        self.channels = []
        self.reminders_disboard = {}
        self.reminders_server_monitoring = {}

        for guild in self.bot.guilds:
            self.guild_setup(guild)

        self.check_reminders.start()

    def guild_setup(self, guild):
        channel = discord.utils.get(guild.channels, name=self.CHANNEL_NAME)
        if not channel:
            return None
        self.logger.info(f"Found #{self.CHANNEL_NAME} channel in {guild}: {channel.id}")
        self.channels.append(channel.id)
        return channel.id

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        channel_id = self.guild_setup(guild)
        if channel_id:
            self.update_bump_time(channel_id)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        for channel in guild.channels:
            if channel.id in self.channels:
                self.channels.remove(channel.id)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.channel.id not in self.channels:
            return
        if message.author == bot.user:
            return
        if self.update_bump_time(message):
            await message.add_reaction(random.choice(["🙏", "🤗", "😻", "😌", "❤"]))

    async def cog_load(self):
        for channel in self.channels:
            async for msg in self.bot.get_channel(channel).history(limit=150):
                self.update_bump_time(msg)

    def update_bump_time(self, msg):
        channel_id = msg.channel.id
        if msg.author.name == "DISBOARD":
            if not msg.embeds or "Bump done!" not in msg.embeds[0].description:
                return False
            delta = timedelta(hours=2)
            reminders = self.reminders_disboard
        elif msg.author.name == "Server Monitoring":
            if not msg.embeds:
                return False

            if "Server bumped by" in msg.embeds[0].description:
                delta = timedelta(hours=4)
            elif "The next Bump for this server will be available in" in msg.embeds[0].description:
                try:
                    delta_regex = re.compile('(?P<hours>\d+):(?P<minutes>\d+):(?P<seconds>\d+)')
                    delta_match = delta_regex.search(msg.embeds[0].description)
                    delta = timedelta(hours=int(delta_match['hours']), minutes=int(delta_match['minutes']), seconds=int(delta_match['seconds']) + 1) # XXX
                except Exception as e: # XXX
                    self.logger.error(f"Could not parse message as timedelta! " + str(e))
                    return False
            else:
                return False

            reminders = self.reminders_server_monitoring
        else:
            return False

        reminder = msg.created_at + delta
        if channel_id in reminders and reminder <= reminders[channel_id]:
            return False
        if reminder < datetime.now(timezone.utc):
            # if a reminder is scheduled for the past, we either already made it, or we've been out so
            # so long there's no telling what's going on
            return False

        reminders[channel_id] = reminder
        self.logger.info(f"Successful {msg.author.name} bump. Reminding in " + str(delta) + ", which is at " + str(reminder))

        return True

    @tasks.loop(minutes=5)
    async def check_reminders(self):
        now = datetime.now(timezone.utc)

        for channel_id in self.channels:
            if self.reminders_disboard.get(channel_id, now) < now:
                msg = "Time to bump Disboard!"
                self.reminders_disboard.pop(channel_id)
            elif self.reminders_server_monitoring.get(channel_id, now) < now:
                msg = "Time to bump Server Monitoring!"
                self.reminders_server_monitoring.pop(channel_id)
            else:
                continue

            await self.bot.get_channel(channel_id).send(msg)

    @check_reminders.before_loop
    async def before_check_reminders(self):
        await self.bot.wait_until_ready()


intents = discord.Intents(guilds=True, members=True, messages=True, message_content=True)
bot = commands.Bot(command_prefix='!', help_command=None, intents=intents)

@bot.event
async def on_ready():
    await bot.add_cog(Wordle(bot))
    await bot.add_cog(SoftSign(bot))
    await bot.add_cog(Spelling(bot))
    await bot.add_cog(BumpReminder(bot))

@bot.listen('on_message')
async def message_listener(msg):
    if msg.author == bot.user:
        return
    if not isinstance(msg.channel, discord.DMChannel):
        return

    await msg.channel.send(random.choice(["meow", "мяу", "miauw", "مياو"]))

    if random.random() < 0.05:
        logging.getLogger('discord').info(f"{msg.author.display_name} is getting a selfie")

        try:
            random_img = os.path.join("selfies", random.choice(os.listdir("selfies")))
            async with msg.channel.typing():
                await msg.channel.send(file=discord.File(random_img))
        except OSError:
            pass

@bot.command(hidden=True)
@commands.check_any(commands.is_owner(), commands.has_permissions(administrator=True))
async def remove(ctx, word=None):
    if not word:
        await ctx.reply("You need to tell me which word to remove")
        return

    try:
        common_words.remove(word)
        common_words_by_length[len(word)].remove(word)
        with open("removed_words.txt", "a") as f:
            f.write(word + '\n')
        logging.getLogger('discord').warning(f"{ctx.author.display_name} removed {word}")
        await ctx.reply(f"Removing {word} from my list of target words")
    except ValueError:
        await ctx.reply(f"{word} does not seem to occur in my list. Try checking the spelling.")

if __name__ == "__main__":
    handler = logging.handlers.RotatingFileHandler("discord.log",
        maxBytes = 1 << 20,
        backupCount = 5
    )
    handler.setFormatter(
        logging.Formatter(
            '{asctime} {levelname:<8} {name:<16}: {message}',
            style='{'
        )
    )

    logging.getLogger('discord').addHandler(handler)

    bot.run(os.getenv("TOKEN"))
