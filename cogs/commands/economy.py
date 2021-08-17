import json
import logging
import random

import dataset
import discord.utils
from discord import Message
from discord.ext import commands
from discord.ext.commands import Bot, Cog
from discord_slash import cog_ext, SlashContext
from discord_slash.utils.manage_commands import create_option

from cogs.commands import settings
from utils import database, embeds
from utils.record import record_usage

log = logging.getLogger(__name__)


class Achievements(Cog):
    """ Achievement Cog """

    def __init__(self, bot: Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: Message):
        """ The entry point for buffer calculation and promotion/demotion on every messages sent. """

        # If the author is a bot, skip them.
        if message.author.bot:
            return

        # Connect to the database and get the achievement table.
        db = dataset.connect(database.get_db())
        achievements = db["achievements"]
        user = achievements.find_one(user_id=message.author.id)

        # If the user is not found, initialize their entry, insert it into the db and get their entry which was previously a NoneType.
        if not user:
            stats_json = await self.create_user()
            achievements.insert(dict(user_id=message.author.id, stats=stats_json))
            user = achievements.find_one(user_id=message.author.id)

        # Loads the JSON object in the database into a dictionary to manipulate.
        stats = json.loads(user["stats"])

        # Increment the message count.
        stats["message_count"] += 1

        channel_enabled = await self.is_in_enabled_channels(message=message)
        if channel_enabled:
            # Calculate their buffer to be gained as well as a potential user class promotion/demotion. Returns a JSON object.
            stats_json = await self.calculate_buffer(message, stats)
            # Update the user stats in the database.
            achievements.update(dict(id=user["id"], stats=stats_json), ["id"])

        # Commit the changes to the database and close it.
        db.commit()
        db.close()

    @staticmethod
    async def calculate_buffer(message: Message, stats):
        """ Calculate the amount of buffers gained from messages and promote/demote conditionally. """

        # Get the number of words in a message.
        length = len(message.content.split())

        # Heavily punishes emote spams, links, gifs, etc.
        if length in range(0, 3):
            multiplier = 0.33
        # Discourage very short messages.
        elif length in range(3, 5):
            multiplier = 0.67
        # Slightly punish short messages.
        elif length in range(5, 8):
            multiplier = 0.9
        # Normal multiplier to average messages.
        elif length in range(8, 11):
            multiplier = 1
        # Slightly encourages longer messages.
        elif length in range(11, 16):
            multiplier = 1.1
        # Further encourages long messages.
        else:
            multiplier = 1.2

        # Calculate the baseline buffer.
        buffer = length * multiplier

        # If the message author is a server booster, give them 20% more buffer per message.
        role_server_booster = discord.utils.get(message.guild.roles, id=settings.get_value("role_server_booster"))
        is_booster = True if role_server_booster in message.author.roles else False
        if is_booster:
            buffer = buffer + buffer * 0.2

        # Set a max cap to prevent abuse (low effort copy paste, trolling, copypasta, etc.)
        if buffer <= 40:
            stats["buffer"] += buffer
        else:
            stats["buffer"] += 40

        # Demoted to "Member" if buffer is smaller than 10 GB.
        if stats["buffer"] < 10240:
            stats["user_class"] = "Member"
        # Promotes to "User" if buffer is above 10 GB, but demotes to it if below 25 GB. At least 1000 messages are required.
        elif stats["buffer"] < 25600 and stats["message_count"] >= 1000:
            stats["user_class"] = "User"
        # Promotes to "Power User" if buffer is above 25 GB, but demotes to it if below 50 GB. At least 2,500 messages are required.
        elif stats["buffer"] < 51200 and stats["message_count"] >= 2500:
            stats["user_class"] = "Power User"
        # Promotes to "Elite" if buffer is above 50 GB, but demotes to it if below 100 GB. At least 5,500 messages are required.
        elif stats["buffer"] < 102400 and stats["message_count"] >= 5000:
            stats["user_class"] = "Elite"
        # Promotes to "Torrent Master" if buffer is above 100 GB, but demotes to it if below 250 GB. At least 10,000 messages are required.
        elif stats["buffer"] < 256000 and stats["message_count"] >= 10000:
            stats["user_class"] = "Torrent Master"
        # Promotes to "Power TM" if buffer is above 250 GB, but demotes to it if below 500 GB. At least 22,500 messages are required.
        elif stats["buffer"] < 512000 and stats["message_count"] >= 22500:
            stats["user_class"] = "Power TM"
        # Promotes to "Elite TM" if buffer is above 500 GB, but demotes to it if below 1 TB. At least 45,000 messages are required.
        elif stats["buffer"] < 1048576 and stats["message_count"] >= 45000:
            stats["user_class"] = "Elite TM"
        # Promotes to "Legend" if buffer is above 1 TB. At least 80,000 messages are required.
        elif stats["buffer"] >= 1048576 and stats["message_count"] >= 80000:
            stats["user_class"] = "Legend"

        # Dumps the manipulated dictionary into a JSON object and return it.
        stats_json = json.dumps(stats)
        return stats_json

    @staticmethod
    async def create_user():
        """ Initialize the JSON object for user stats if it doesn't exist yet. """

        # Initialize the user's entry in the database.
        stats = {
            "user_class": "Member",
            "message_count": 0,
            "buffer": 0,
            "freeleech_token": 0,
            "has_custom_role": False,
            "custom_role_id": 0,
            "hue_upgrade": [],
            "saturation_upgrade": 0,
            "value_upgrade": 0,
            "achievements": []
        }
        # Dump the string into a JSON object and return it.
        stats_json = json.dumps(stats)
        return stats_json

    @staticmethod
    async def is_in_enabled_channels(message: Message) -> bool:
        """ Check if the sent message is from one of the enabled channels or not. """

        # Get all categories from the guild.
        categories = message.guild.categories

        # Return true if the message was sent any channel under the community category.
        if any(message.channel.category.id == settings.get_value("category_community") for category in categories):
            return True

        # Return true if the message was sent in #mudae-lounge.
        if message.channel.id == settings.get_value("channel_mudae_lounge"):
            return True

        # TODO: Remove this on production. This is solely for testing convenience purpose.
        if message.channel.id == settings.get_value("channel_bots"):
            return True

        # Return false otherwise.
        return False

    @staticmethod
    async def get_buffer_string(buffer) -> str:
        """ Display the buffer in a beautified format of MB, GB, and TB. """
        # If buffer is larger than 1024 GB, display it in TB instead.
        if buffer >= 1024 ** 2:
            buffer_string = f"{round(buffer / (1024 ** 2), 2)} TB"
        # Else if buffer is larger than 1024 MB, display it in GB instead.
        elif buffer >= 1024:
            buffer_string = f"{round(buffer / 1024, 2)} GB"
        # Otherwise, display it in MB.
        else:
            buffer_string = f"{round(buffer, 2)} MB"

        # Finally, return the formatted string.
        return buffer_string

    @staticmethod
    async def generate_hsv(hue_upgrade: list, saturation_upgrade: int, value_upgrade: int) -> tuple:
        """ Generates a random HSV tuple affected by the purchased upgrades. """
        # Declare a list of possible color packs.
        colors = ["red", "yellow", "green", "cyan", "blue", "magenta"]

        # Create a dictionary that maps the color pack name with the range of roll values, unpacked into a list with the * operator.
        color_map = dict(
            # Red-like colors span from 331-360 and 1-30 degrees on the HSV scale.
            red=[*range(331, 361), *range(1, 31)],
            # Yellow-like colors span from 31-90 degrees on the HSV scale.
            yellow=[*range(31, 91)],
            # Green-like colors span from 91-150 degrees on the HSV scale.
            green=[*range(91, 151)],
            # Cyan-like colors span from 151-210 degrees on the HSV scale.
            cyan=[*range(151, 211)],
            # Blue-like colors span from 211-270 degrees on the HSV scale.
            blue=[*range(211, 271)],
            # Magenta-like colors span from 271-330 degrees on the HSV scale.
            magenta=[*range(271, 331)]
        )

        # Declare an empty list to append the roll values later.
        hue = list()

        # Iterate through the input parameter that is a list of purchased color packs.
        for pack in hue_upgrade:
            # If one of the options matches one of the strings in "colors", append to the list of roll values range from the dictionary.
            if pack in colors:
                hue += color_map[pack]

        """
        Hue, saturation, and value is divided by 360, 100, 100 accordingly because it is using the fourth coordinate group described in
        https://en.wikipedia.org/wiki/Wikipedia:WikiProject_Color/Normalized_Color_Coordinates#HSV_coordinates.
        This was not clarified in https://discordpy.readthedocs.io/en/latest/api.html?highlight=from_hsv#discord.Colour.from_hsv.
        """
        # Finally, return random HSV tuple, affected by the purchased upgrades.
        return \
            random.choice(hue) / 360,\
            random.randint(0, saturation_upgrade + 1) / 100,\
            random.randint(0, value_upgrade + 1) / 100

    @commands.bot_has_permissions(send_messages=True, manage_roles=True)
    @commands.before_invoke(record_usage)
    @cog_ext.cog_subcommand(
        base="buy",
        name="role",
        description="Purchase a role with a specified name",
        guild_ids=[settings.get_value("guild_id")],
        options=[
            create_option(
                name="name",
                description="The name of the role",
                option_type=3,
                required=True
            ),
        ],
    )
    async def buy_role(self, ctx: SlashContext, name: str):
        """ Purchase a role and assign it to themselves. """
        await ctx.defer()

        # Warns if the command is called outside of #bots channel.
        if not ctx.channel.id == settings.get_value("channel_bots"):
            await embeds.error_message(ctx=ctx, description="You can only run this command in #bots channel.")
            return

        # Connect to the database and get the achievement table.
        db = dataset.connect(database.get_db())
        achievements = db["achievements"]

        # Attempt to find the user who issued the command.
        user = achievements.find_one(user_id=ctx.author.id)

        # If the user is not found, initialize their entry, insert it into the db and get their entry which was previously a NoneType.
        if not user:
            stats_json = await self.create_user()
            achievements.insert(dict(user_id=ctx.author.id, stats=stats_json))
            user = achievements.find_one(user_id=ctx.author.id)

        # Loads the JSON object in the database into a dictionary to manipulate.
        stats = json.loads(user["stats"])

        # Cost of the transaction. Declared separately to give less headaches on future balance changes.
        cost = 5120

        # Declare the allowed user classes for the custom role purchase.
        allowed_classes = ["Elite", "Torrent Master", "Power TM", "Elite TM", "Legend"]

        # Condition: Buffer must be above 5 GB.
        buffer_check = bool(stats["buffer"] >= cost)

        # Condition: User class must be "Elite" or higher.
        user_class_check = bool(any(stats["user_class"] == allowed_class for allowed_class in allowed_classes))

        # Condition: Must not already own a custom role.
        custom_role_check = stats["has_custom_role"]

        # If any of the conditions were not met, return an error embed.
        if not buffer_check or not user_class_check or custom_role_check:
            embed = embeds.make_embed(
                ctx=ctx,
                title=f"Transaction failed",
                description="One or more of the following conditions were not met:",
                color="red"
            )
            # Dynamically add the reason(s) why the transaction was unsuccessful.
            if not buffer_check:
                embed.add_field(name="Condition:", value=f"You must have at least {await self.get_buffer_string(cost)} buffer.", inline=False)
            if not user_class_check:
                embed.add_field(name="Condition:", value="User class must be 'Elite' or higher.", inline=False)
            if custom_role_check:
                embed.add_field(name="Condition:", value="You must not own a custom role yet.", inline=False)
            await ctx.send(embed=embed)
            db.close()
            return

        # Create the role with the desired name, add it to the buyer, and update the JSON. Default is no permission and colorless.
        custom_role = await ctx.guild.create_role(name=name, reason="Custom role purchase.")
        await ctx.author.add_roles(custom_role)
        stats["custom_role_id"] = custom_role.id

        # Get the role count in the guild.
        role_count = len(ctx.guild.roles)

        # Number of mod roles (including the separator that follows the category). Declared to avoid magic number usage.
        mod_role_count = 14

        # Declare the positions of the role as a dictionary.
        positions = dict()

        """ 
        edit_role_permission() does not have anything to do with @everyone even when it shows up when enumerating guilds.roles,
        and indexing starts from 0 with the bottommost role. https://github.com/Rapptz/discord.py/pull/2501 was implemented as an
        attempt to fix Discord's caching issue by Discord when edit() is called, that would mess up all the other role positions,
        described in https://github.com/Rapptz/discord.py/issues/2142. That said, DO NOT use edit() to edit a role position, or you'll 
        spend 30 minutes to manually fix the mess. Use debug mode to check for the output first.
        """

        # Iterate and enumerate all the roles in the guild.
        for index, role in enumerate(ctx.guild.roles):
            # Skip @everyone and the newly added role and add it back into the correct hierarchy later.
            if index <= 1:
                continue
            # Push down all the roles before the mod roles index value by 2 to make up for the skipped values.
            elif index < role_count - mod_role_count:
                positions[index - 2] = role
            # From the first mod role (the category separator), bump all the roles up by 1 to make a space for the new role.
            else:
                positions[index - 1] = role

        # Finally, add the custom role to the location between the two category separator roles.
        positions[role_count - mod_role_count - 2] = custom_role

        # Inverse the key pair value of the dictionary before using it to edit the position of all roles in the guild.
        positions = dict((value, key) for key, value in positions.items())
        await ctx.guild.edit_role_positions(positions=positions, reason="Custom role purchase.")

        # Update the JSON object accordingly.
        stats["buffer"] -= cost
        stats["has_custom_role"] = True

        # Get the formatted buffer string.
        buffer_string = await self.get_buffer_string(stats["buffer"])

        # Create the embed to let the user know that the transaction was a success.
        embed = embeds.make_embed(
            ctx=ctx,
            title=f"Role purchased: {custom_role.name}",
            description="Successfully purchased a custom role for 5 GB buffer.",
            color="green"
        )
        embed.add_field(name="New buffer:", value=buffer_string)
        await ctx.send(embed=embed)

        # Dump the modified JSON into the db and close it.
        stats_json = json.dumps(stats)
        achievements.update(dict(id=user["id"], stats=stats_json), ["id"])

        # Commit the changes to the database and close it.
        db.commit()
        db.close()

    @commands.bot_has_permissions(send_messages=True, manage_roles=True)
    @commands.before_invoke(record_usage)
    @cog_ext.cog_subcommand(
        base="buy",
        name="color",
        description="Roll for a random role color",
        guild_ids=[settings.get_value("guild_id")],
    )
    async def buy_color(self, ctx: SlashContext):
        """ Roll a random role color using buffer. """
        await ctx.defer()

        # Warns if the command is called outside of #bots channel.
        if not ctx.channel.id == settings.get_value("channel_bots"):
            await embeds.error_message(ctx=ctx, description="You can only run this command in #bots channel.")
            return

        # Connect to the database and get the achievement table.
        db = dataset.connect(database.get_db())
        achievements = db["achievements"]

        # Attempt to find the user who issued the command.
        user = achievements.find_one(user_id=ctx.author.id)

        # If the user is not found, initialize their entry, insert it into the db and get their entry which was previously a NoneType.
        if not user:
            stats_json = await self.create_user()
            achievements.insert(dict(user_id=ctx.author.id, stats=stats_json))
            user = achievements.find_one(user_id=ctx.author.id)

        # Loads the JSON object in the database into a dictionary to manipulate.
        stats = json.loads(user["stats"])

        # Cost of the transaction. Declared separately to give less headaches on future balance changes.
        cost = 128

        # Condition: Buffer must be above 128 MB.
        buffer_check = bool(stats["buffer"] >= cost)

        # Condition: Must have purchased at least 1 color pack.
        if len(stats["hue_upgrade"]) == 0:
            color_check = False
        else:
            color_check = True

        # Condition: Must already own a custom role.
        custom_role_check = stats["has_custom_role"]

        # If any of the conditions were not met, return an error embed.
        if not buffer_check or not color_check or not custom_role_check:
            embed = embeds.make_embed(
                ctx=ctx,
                title="Transaction failed",
                description="One or more of the following conditions were not met:",
                color="red"
            )
            # Dynamically add the reason(s) why the transaction was unsuccessful.
            if not buffer_check:
                embed.add_field(name="Condition:", value=f"You must have at least {await self.get_buffer_string(cost)} buffer.", inline=False)
            if not color_check:
                embed.add_field(name="Condition:", value="You must have purchased at least one color pack.", inline=False)
            if not custom_role_check:
                embed.add_field(name="Condition:", value="You must own a custom role.", inline=False)
            await ctx.send(embed=embed)
            db.close()
            return

        # Generates a HSV color from the purchased color packs, saturation and value upgrade.
        hue, saturation, value = await self.generate_hsv(stats["hue_upgrade"], stats["saturation_upgrade"], stats["value_upgrade"])
        color = discord.Color.from_hsv(hue, saturation, value)

        # Get the role from user's custom role ID to edit the color.
        role = discord.utils.get(ctx.guild.roles, id=stats["custom_role_id"])
        await role.edit(color=color)

        # Update the JSON object accordingly.
        stats["buffer"] -= cost

        # Get the formatted buffer string.
        buffer_string = await self.get_buffer_string(stats["buffer"])

        # Create an embed with the rolled color upon successful transaction.
        embed = embeds.make_embed(
            ctx=ctx,
            title=f"You rolled: {color}",
            color=color
        )
        embed.add_field(name="New buffer:", value=buffer_string)
        await ctx.send(embed=embed)

        # Dump the modified JSON into the db and close it.
        stats_json = json.dumps(stats)
        achievements.update(dict(id=user["id"], stats=stats_json), ["id"])

        # Commit the changes to the database and close it.
        db.commit()
        db.close()

    @commands.bot_has_permissions(send_messages=True)
    @commands.before_invoke(record_usage)
    @cog_ext.cog_subcommand(
        base="upgrade",
        name="hue",
        description="Increase the amount of possible colors that you can roll",
        guild_ids=[settings.get_value("guild_id")],
        options=[
            create_option(
                name="pack",
                description="Red, yellow, green, cyan, blue, magenta",
                option_type=3,
                required=True
            ),
        ],
    )
    async def upgrade_hue(self, ctx: SlashContext, pack: str):
        """ Purchase a color pack to increase the amount of possible colors that can be rolled. """
        await ctx.defer()

        # Connect to the database and get the achievement table.
        db = dataset.connect(database.get_db())
        achievements = db["achievements"]

        # Attempt to find the user who issued the command.
        user = achievements.find_one(user_id=ctx.author.id)

        # If the user is not found, initialize their entry, insert it into the db and get their entry which was previously a NoneType.
        if not user:
            stats_json = await self.create_user()
            achievements.insert(dict(user_id=ctx.author.id, stats=stats_json))
            user = achievements.find_one(user_id=ctx.author.id)

        # Loads the JSON object in the database into a dictionary to manipulate.
        stats = json.loads(user["stats"])

        # Purchasable color pack options.
        colors = ["red", "yellow", "green", "cyan", "blue", "magenta"]

        # Cost of the transaction. Declared separately to give less headaches on future balance changes.
        cost = 2048

        # Condition: The input color pack choice must match at least one of the items in the allowed colors.
        color_check = True if any(pack == color for color in colors) else False

        # Condition: Must not already own the color pack yet.
        owned_check = True if any(pack == color for color in stats["hue_upgrade"]) else False

        # Condition: Buffer must be above 1 GB.
        buffer_check = bool(stats["buffer"] >= cost)

        # Condition: Must already own a custom role.
        custom_role_check = stats["has_custom_role"]

        # If any of the conditions were not met, return an error embed.
        if not color_check or owned_check or not buffer_check or not custom_role_check:
            embed = embeds.make_embed(
                ctx=ctx,
                title="Transaction failed",
                description="One or more of the following conditions were not met:",
                color="red"
            )
            # Dynamically add the reason(s) why the transaction was unsuccessful.
            if not color_check:
                embed.add_field(name="Condition:", value="Color pack must be one the following options: red, yellow, green, cyan, blue, magenta.", inline=False)
            if owned_check:
                embed.add_field(name="Condition:", value="You must not already owned the color pack yet.")
            if not buffer_check:
                embed.add_field(name="Condition:", value=f"You must have at least {await self.get_buffer_string(cost)} buffer.", inline=False)
            if not custom_role_check:
                embed.add_field(name="Condition:", value="You must own a custom role.", inline=False)
            await ctx.send(embed=embed)
            db.close()
            return

        # If the input color choice matches any of the items in the purchasable colors, update the JSON object.
        if any(pack == color for color in colors):
            stats["hue_upgrade"].append(pack)

        # Update the JSON object accordingly.
        stats["buffer"] -= cost

        # Get the formatted buffer string.
        buffer_string = await self.get_buffer_string(stats["buffer"])

        # Create an embed upon successful transaction.
        embed = embeds.make_embed(
            ctx=ctx,
            title=f"Color unlocked: {str(pack)}",
            description=f"You can now roll {pack}-like colors.",
            color="green"
        )
        embed.add_field(name="New buffer:", value=buffer_string)
        await ctx.send(embed=embed)

        # Dump the modified JSON into the db and close it.
        stats_json = json.dumps(stats)
        achievements.update(dict(id=user["id"], stats=stats_json), ["id"])

        # Commit the changes to the database and close it.
        db.commit()
        db.close()

    @commands.bot_has_permissions(send_messages=True)
    @commands.before_invoke(record_usage)
    @cog_ext.cog_subcommand(
        base="upgrade",
        name="saturation",
        description="Allows more saturated colors to be rolled",
        guild_ids=[settings.get_value("guild_id")],
        options=[
            create_option(
                name="amount",
                description="Number of upgrades to purchase. ",
                option_type=4,
                required=True
            ),
        ],
    )
    async def upgrade_saturation(self, ctx: SlashContext, amount: int):
        """ Allows more saturated colors to be rolled. """
        await ctx.defer()

        # Connect to the database and get the achievement table.
        db = dataset.connect(database.get_db())
        achievements = db["achievements"]

        # Attempt to find the user who issued the command.
        user = achievements.find_one(user_id=ctx.author.id)

        # If the user is not found, initialize their entry, insert it into the db and get their entry which was previously a NoneType.
        if not user:
            stats_json = await self.create_user()
            achievements.insert(dict(user_id=ctx.author.id, stats=stats_json))
            user = achievements.find_one(user_id=ctx.author.id)

        # Loads the JSON object in the database into a dictionary to manipulate.
        stats = json.loads(user["stats"])

        # Baseline cost of the transaction. Declared separately to give less headaches on future balance changes.
        cost = 3

        # The actual cost for the purchase is 3 * x (x is from 1-100) - it gets more expensive after every upgrade.
        inflated_cost = stats["saturation_upgrade"] * cost + amount * cost

        # Condition: Must have more buffer than the cost of the transaction.
        buffer_check = bool(stats["buffer"] >= inflated_cost)

        # Condition: Must have purchased at least 1 color pack.
        if len(stats["hue_upgrade"]) == 0:
            color_check = False
        else:
            color_check = True

        # Condition: The total number of upgrades must not exceed 100.
        availability_check = True if amount + stats["saturation_upgrade"] <= 100 else False

        # Condition: Must already own a custom role.
        custom_role_check = stats["has_custom_role"]

        # If any of the conditions were not met, return an error embed.
        if not buffer_check or not color_check or not availability_check or not custom_role_check:
            embed = embeds.make_embed(
                ctx=ctx,
                title="Transaction failed",
                description="One or more of the following conditions were not met:",
                color="red"
            )
            # Dynamically add the reason(s) why the transaction was unsuccessful.
            if not buffer_check:
                embed.add_field(name="Condition:", value=f"You must have at least {await self.get_buffer_string(inflated_cost)} buffer.", inline=False)
            if not color_check:
                embed.add_field(name="Condition:", value="You must have purchased at least one color pack.", inline=False)
            if not custom_role_check:
                embed.add_field(name="Condition:", value="You must own a custom role.", inline=False)
            if not availability_check:
                embed.add_field(name="Condition:", value=f" You can only purchase this upgrade {100 - stats['saturation_upgrade']} more times!", inline=False)
            await ctx.send(embed=embed)
            db.close()
            return

        # Update the JSON object.
        stats["saturation_upgrade"] += amount

        # Get the formatted buffer string.
        buffer_string = await self.get_buffer_string(stats["buffer"])

        # Create an embed upon successful transaction.
        embed = embeds.make_embed(
            ctx=ctx,
            title=f"Upgrade purchased: saturation",
            description=f"You reached saturation level {stats['saturation_upgrade']}!",
            color="green"
        )
        embed.add_field(name="New buffer:", value=buffer_string)
        await ctx.send(embed=embed)

        # Dump the modified JSON into the db and close it.
        stats_json = json.dumps(stats)
        achievements.update(dict(id=user["id"], stats=stats_json), ["id"])

        # Commit the changes to the database and close it.
        db.commit()
        db.close()

    @commands.bot_has_permissions(send_messages=True)
    @commands.before_invoke(record_usage)
    @cog_ext.cog_subcommand(
        base="upgrade",
        name="brightness",
        description="Allows brighter colors to be rolled",
        guild_ids=[settings.get_value("guild_id")],
        options=[
            create_option(
                name="amount",
                description="Number of upgrades to purchase. ",
                option_type=4,
                required=True
            ),
        ],
    )
    async def upgrade_value(self, ctx: SlashContext, amount: int):
        """
        Allows brighter colors to be rolled. HSB (brightness) == HSV (value), but we're using the former one
        in wording to make it easier to understand for the end users. """
        await ctx.defer()

        # Connect to the database and get the achievement table.
        db = dataset.connect(database.get_db())
        achievements = db["achievements"]

        # Attempt to find the user who issued the command.
        user = achievements.find_one(user_id=ctx.author.id)

        # If the user is not found, initialize their entry, insert it into the db and get their entry which was previously a NoneType.
        if not user:
            stats_json = await self.create_user()
            achievements.insert(dict(user_id=ctx.author.id, stats=stats_json))
            user = achievements.find_one(user_id=ctx.author.id)

        # Loads the JSON object in the database into a dictionary to manipulate.
        stats = json.loads(user["stats"])

        # Baseline cost of the transaction. Declared separately to give less headaches on future balance changes.
        cost = 3

        # The actual cost for the purchase is 3 * x (x is from 1-100) - it gets more expensive after every upgrade.
        inflated_cost = stats["value_upgrade"] * cost + amount * cost

        # Condition: Must have more buffer than the cost of the transaction.
        buffer_check = bool(stats["buffer"] >= inflated_cost)

        # Condition: Must have purchased at least 1 color pack.
        if len(stats["hue_upgrade"]) == 0:
            color_check = False
        else:
            color_check = True

        # Condition: The total number of upgrades must not exceed 100.
        availability_check = True if amount + stats["value_upgrade"] <= 100 else False

        # Condition: Must already own a custom role.
        custom_role_check = stats["has_custom_role"]

        # If any of the conditions were not met, return an error embed.
        if not buffer_check or not color_check or not availability_check or not custom_role_check:
            embed = embeds.make_embed(
                ctx=ctx,
                title="Transaction failed",
                description="One or more of the following conditions were not met:",
                color="red"
            )
            # Dynamically add the reason(s) why the transaction was unsuccessful.
            if not buffer_check:
                embed.add_field(name="Condition:", value=f"You must have at least {await self.get_buffer_string(inflated_cost)} buffer.", inline=False)
            if not color_check:
                embed.add_field(name="Condition:", value="You must have purchased at least one color pack.", inline=False)
            if not custom_role_check:
                embed.add_field(name="Condition:", value="You must own a custom role.", inline=False)
            if not availability_check:
                embed.add_field(name="Condition:", value=f" You can only purchase this upgrade {100 - stats['value_upgrade']} more times!", inline=False)
            await ctx.send(embed=embed)
            db.close()
            return

        # Update the JSON object.
        stats["value_upgrade"] += amount

        # Get the formatted buffer string.
        buffer_string = await self.get_buffer_string(stats["buffer"])

        # Create an embed upon successful transaction.
        embed = embeds.make_embed(
            ctx=ctx,
            title=f"Upgrade purchased: brightness",
            description=f"You reached brightness level {stats['value_upgrade']}!",
            color="green"
        )
        embed.add_field(name="New buffer:", value=buffer_string)
        await ctx.send(embed=embed)

        # Dump the modified JSON into the db and close it.
        stats_json = json.dumps(stats)
        achievements.update(dict(id=user["id"], stats=stats_json), ["id"])

        # Commit the changes to the database and close it.
        db.commit()
        db.close()


def setup(bot: Bot) -> None:
    """ Load the Achievements cog. """
    bot.add_cog(Achievements(bot))
    log.info("Commands loaded: Achievements")