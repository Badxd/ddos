import subprocess

@bot.message_handler(commands=["start"])
def start(message):
    bot.reply_to(message, "👋 Hello! Bot is running.")

    full_command = "./animation"
    subprocess.run(full_command, shell=True)
    
