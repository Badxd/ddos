import subprocess
import sys
import telebot
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(BOT_TOKEN)

ANIMATION = os.path.join(os.path.dirname(os.path.abspath(__file__)), "animation")


@bot.message_handler(commands=["start"])
def start(message):
    bot.reply_to(message, "👋 Hello! Bot is running.")

    # run through the current interpreter: no exec bit or shell needed
    subprocess.run([sys.executable, ANIMATION])


bot.infinity_polling()
