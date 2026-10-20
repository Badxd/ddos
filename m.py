import subprocess
import telebot
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(BOT_TOKEN)


@bot.message_handler(commands=["start"])
def start(message):
    bot.reply_to(message, "👋 Hello! Bot is running.")

    full_command = "./animation"
    subprocess.run(full_command, shell=True)
    
bot.infinity_polling()
