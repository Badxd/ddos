import subprocess
import time
import os
import sys

def restart_bot():
    current_directory = os.path.dirname(os.path.abspath(__file__))
    king = os.path.join(current_directory, "king")
    bot = os.path.join(current_directory, "m.py")

    while True:
        try:
            subprocess.Popen(
                [sys.executable, king],
                cwd=current_directory
            )

            subprocess.run(
                [sys.executable, bot],
                cwd=current_directory,
                check=True
            )

        except subprocess.CalledProcessError as e:
            print(f"Bot crashed: {e}. Restarting in 5s...")
            time.sleep(5)

        except Exception as e:
            print(f"Error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    restart_bot()
