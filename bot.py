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
            if not os.path.isfile(king):
                print("Error: king binary not found.")
                return

            if not os.access(king, os.X_OK):
                print("Error: king is not executable. Check file permissions.")
                return

            process = subprocess.Popen(
                [king],
                cwd=current_directory
            )

            try:
                subprocess.run(
                    [sys.executable, bot],
                    cwd=current_directory,
                    check=True
                )
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()

        except subprocess.CalledProcessError as e:
            print(f"Bot crashed: {e}. Restarting in 5s...")
            time.sleep(5)

        except Exception as e:
            print(f"Error: {e}. Restarting in 5s...")
            time.sleep(5)

if __name__ == "__main__":
    restart_bot()
