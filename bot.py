"""
Author MikkMakk88, morgaesis et al.

(c)2021
"""

# pylint: disable=import-error


import os
import re
import logging
import asyncio
import queue

import discord
import jokeapi
import pafy
from youtubesearchpython import VideosSearch


class MusicBot(discord.Client):
    """
    The main bot functionality
    """

    # pylint: disable=no-self-use

    COMMAND_PREFIX = "!"
    _discord_helper = discord.Client()

    def __init__(self):
        self.handlers = {}
        self.voice_client = None
        self.play_ctx_queue = queue.Queue()

        # Boolean to control whether the after callback is called
        self.after_callback_blocked = False
        self.url_regex = re.compile(
            r"http[s]?://"
            r"(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*(),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+"
        )

        self.register_command("play", handler=self.play)
        self.register_command("stop", handler=self.stop)
        self.register_command("pause", handler=self.pause)
        self.register_command("resume", handler=self.resume)
        self.register_command("skip", handler=self.skip)
        self.register_command("queue", handler=self.queue)

        self.register_command("hello", handler=self.hello)
        self.register_command("countdown", handler=self.countdown)
        self.register_command("dinkster", handler=self.dinkster)
        self.register_command("joke", handler=self.joke)

        super().__init__()

    def register_command(self, command_name, handler=None):
        """Register a command with the name 'command_name'.

        Arguments:
          command_name: String. The name of the command to register. This is
            what users should use to run the command.
          handler: A function. This function should accept two arguments:
            discord.Message and a string. The messageris the message being
            processed by the handler and command_content is the string
            contents of the command passed by the user.
        """
        assert handler
        assert command_name not in self.handlers
        self.handlers[command_name] = handler

    def get_command_handler(self, message_content):
        """Tries to parse message_content as a command and fetch the corresponding
        handler for the command.

        Arguments
        ---------
        message_content : discord.message
            Contents of the message to parse. Assumed to start with COMMAND_PREFIX.

        Returns
        -------
        handler : function
            Function to handle the command.

        command_content : discord.message
            The message contents with the prefix and command named stripped.

        error_msg : str
            String or None. If not None it signals that the command was unknown. The
            value will be an error message displayable to the user which says the
            command was not recognized.
        """
        if message_content[0] != self.COMMAND_PREFIX:
            raise ValueError(
                f"Message '{message_content}' does not begin with"
                f" '{MusicBot.COMMAND_PREFIX}'"
            )

        content_split = message_content[1:].split(" ", 1)
        command_name = content_split[0]
        command_content = ""
        if len(content_split) == 2:
            command_content = content_split[1]

        if command_name not in self.handlers:
            return None, None, f"Command {command_name} not recognized."

        return self.handlers[command_name], command_content, None

    @_discord_helper.event
    async def on_ready(self):
        """Login and loading handling"""
        logging.info("we have logged in as %s", self.user)

    @_discord_helper.event
    async def on_message(self, message):
        """Handler for receiving messages"""
        if message.author == self.user:
            return

        if not message.content:
            return

        if message.content[0] != MusicBot.COMMAND_PREFIX:
            # Message not attempting to be a command.
            return

        handler, command_content, error_msg = self.get_command_handler(message.content)

        # The command was not recognized
        if error_msg:
            return await message.channel.send(error_msg)

        # Execute the command.
        await handler(message, command_content)

    def after_callback(self, _):
        """
        Plays the next item in queue if after_callback_blocked == False, otherwise stops the music.
        Used as a callback for play().
        """
        if not self.after_callback_blocked:
            # we could self.loop.create_task here if next_in_queue needs to be async
            self.next_in_queue()
        else:
            self.after_callback_blocked = False

    def _stop(self):
        """
        A helper function that stops playing music
        """
        self.after_callback_blocked = True
        self.voice_client.stop()

    def next_in_queue(self):
        """Switch to next song in queue"""
        if not self.play_ctx_queue.empty():
            cmd_ctx = self.play_ctx_queue.get()
            media = cmd_ctx[0]
            message = cmd_ctx[1]
            audio_url = media.getbestaudio().url
            audio_source = discord.FFmpegPCMAudio(audio_url)

            loop = self.loop

            if self.voice_client.is_playing():
                self._stop()

            self.voice_client.play(audio_source, after=self.after_callback)
            loop.create_task(
                message.channel.send(f"Now Playing: \n```\n{media.title}\n```")
            )

    def get_voice_channel(self, message):
        """
        Get voice channel for message author. Complain if author is not in a channel
        """
        if not isinstance(message, discord.Message):
            logging.error("message is not of type discord.Message!")
            return None
        if message.author.voice is None:
            self.loop.create_task(
                message.channel.send("You are not connected to a voice channel!")
            )
            return None
        voice_channel = message.author.voice.channel
        return voice_channel

    async def get_voice_client(self, message):
        """
        Get voice client for message author. Complain if author is not in a channel,
        connect to author voice client if not yet connected
        """
        if not isinstance(message, discord.Message):
            logging.error("message is not of type discord.Message!")
            return None
        voice_channel = self.get_voice_channel(message)
        if voice_channel is None:
            return None
        for voice_client in self.voice_clients:
            if voice_client.guild == message.guild:
                logging.info("Self in voice channel")
                return voice_client
        return await self.connect_deaf(voice_channel)

    async def connect_deaf(self, channel):
        """Connect to channel self-deafened, the connected voice client"""
        logging.info("Connecting to voice channel")

        if self.voice_client is not None:
            return self.voice_client

        voice_client = await channel.connect()
        await voice_client.guild.change_voice_state(
            channel=channel,
            self_deaf=True,
        )
        voice_states = voice_client.channel.voice_states
        await asyncio.sleep(1)  # Without sleep deafening isn't registered in logs
        logging.info(
            "Bot is deafened: %s", voice_states[voice_client.user.id].self_deaf
        )
        return voice_client

    async def play(self, message, command_content):
        """
        Play URL or first search term from command_content in the author's voice channel
        """
        voice_channel = self.get_voice_channel(message)
        if voice_channel is None:
            # Exit early if user is not connected
            return

        media = None
        if self.url_regex.match(command_content):
            media = pafy.new(command_content)
        else:
            search_result = VideosSearch(command_content).result()
            media = pafy.new(search_result["result"][0]["id"])

        logging.info("Media found:\n%s", media)

        # We queue up a pair of the media metadata and the message context, so we can
        # continue to message the channel that this command was instanciated from as the
        # queue is unrolled.
        self.play_ctx_queue.put((media, message))

        voice_client = await self.connect_deaf(voice_channel)
        self.voice_client = voice_client

        if voice_client.is_playing():
            await message.channel.send(f"Added to Queue: \n```\n{media.title}\n```")
        else:
            self.next_in_queue()

    async def stop(self, message, _command_content):
        """Stop currently playing song"""
        voice_client = await self.get_voice_client(message)
        if voice_client:
            voice_client.stop()

    async def pause(self, message, _command_content):
        """Pause currently playing song"""
        voice_client = await self.get_voice_client(message)
        if voice_client:
            voice_client.pause()

    async def resume(self, message, _command_content):
        """Resume playing current song"""
        voice_client = await self.get_voice_client(message)
        if voice_client:
            voice_client.resume()

    async def skip(self, _message, _command_content):
        """Skip to next song in queue"""
        if self.voice_client:
            if self.play_ctx_queue.empty():
                self._stop()
            else:
                self.next_in_queue()

    async def queue(self, message, _command_content):
        """Displays media that has been queued"""
        items = list(
            self.play_ctx_queue.queue
        )  # This relize on the internal data structure of the queue, which isn't ideal.

        if len(items) == 0:
            await message.channel.send("No audio in queue.")
        else:
            lines = [f"{i+1}: {item[0].title}" for i, item in enumerate(items)]
            await message.channel.send("\n".join(lines))

    async def hello(self, message, _command_content):
        """Greet the author with a nice message"""
        await message.channel.send("Hello!")

    async def countdown(self, message, command_content):
        """Count down from 10 and explode"""
        try:
            seconds = int(command_content)
            while seconds > 0:
                await message.channel.send(seconds)
                await asyncio.sleep(1)
                seconds -= 1
            await message.channel.send("BOOOM!!!")
        except ValueError:
            await message.channel.send(f"{command_content} is not an integer.")

    async def dinkster(self, message, _command_content):
        """Ring the dinkster in all voice channels"""
        for channel in await message.guild.fetch_channels():
            if isinstance(channel, discord.VoiceChannel):
                voice_client = await channel.connect()
                audio_source = await discord.FFmpegOpusAudio.from_probe("Dinkster.ogg")
                voice_client.play(audio_source)
                await asyncio.sleep(10)
                await voice_client.disconnect()

    async def joke(self, message, command_content, joke_pause=3):
        """
        Reply to the author with joke from Sv443's Joke API

        If the joke is two-parter wait `joke_pause` seconds between setup and
        delivery.
        """
        logging.info("Making jokes: %s", message.content)
        argv = command_content.split()

        valid_categories = set(
            [
                "any",
                "misc",
                "programming",
                "dark",
                "pun",
                "spooky",
                "christmas",
            ]
        )
        # Setup complete

        # User asks for help
        if "help" in argv or "-h" in argv or "--help" in argv:
            await message.channel.send("I see you asked for help!")
            await message.channel.send("You can ask for the following categories:")
            await message.channel.send(f"{', '.join(valid_categories)}")
            return

        # User asks for categories
        categories = set(cat.lower() for cat in argv)
        invalid_categories = categories - valid_categories
        logging.info("Invalid categories: %s", invalid_categories)
        category_plurality = "categories" if len(invalid_categories) > 1 else "category"
        if len(invalid_categories) > 0:
            await message.channel.send(
                f"Invalid joke {category_plurality} "
                f"'{', '.join(invalid_categories)}'"
            )
            logging.info(
                "User %s requested invalid joke category %s",
                message.author,
                invalid_categories,
            )
            return

        # Get the joke
        jokes = jokeapi.Jokes()
        joke = jokes.get_joke(lang="en", category=categories)
        logging.info(
            "User %s requested joke of category %s", message.author, categories
        )
        logging.info("The joke is: %s", joke)

        # Joke can be one-liner or has setup
        if joke["type"] == "single":
            await message.channel.send(joke["joke"])
        else:
            await message.channel.send(joke["setup"])
            await asyncio.sleep(joke_pause)
            await message.channel.send(joke["delivery"])


if __name__ == "__main__":
    print("Starting Discord bot")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # pylint: disable=invalid-name
    token = os.getenv("DISCORD_TOKEN")
    if token is None:
        with open(".env", "r", encoding="utf-8") as env_file:
            for line in env_file.readlines():
                match = re.search(r"^DISCORD_TOKEN=(.*)", line)
                if match is not None:
                    logging.info("Found token in file '.env'")
                    token = match.group(1).strip()
    assert token is not None

    logging.info("Starting bot")
    MusicBot().run(token)
