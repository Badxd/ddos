import subprocess
import time
import os

def restart_bot():
    while True:
        try:
            current_directory = os.path.dirname(os.path.abspath(__file__))
            subprocess.run(["python3", os.path.join(current_directory, "m.py")], check=True)
        except subprocess.CalledProcessError as e:
            print(f"Bot crashed: {e}. Restarting in 5s...")
            time.sleep(5)

if __name__ == "__main__":
    restart_bot()
    
