import os
import re
import atexit
import signal
import subprocess
import sys
import json

from config import IDENT_MAIN, IDENT_KERNEL_MANAGER, KERNEL_PID_FILENAME, SNAKEMQ_PORT, get_logger

import snakemq.link
import snakemq.packeter
import snakemq.messaging
import snakemq.message
import threading
import time

from threading import Semaphore
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from dotenv import load_dotenv

load_dotenv()

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_APP_TOKEN = os.environ["SLACK_APP_TOKEN"]

logger = get_logger()

# Globals mutable
my_messaging = None
channels = set()
app = None


class FlushMessenger(threading.Thread):
    def __init__(self, my_messaging, kill_sema):
        threading.Thread.__init__(self)
        self.kill_sema = kill_sema
        self.my_messaging = my_messaging

    def run(self):
        logger.info("Running message flusher...")
        while True:

            if self.kill_sema.acquire(blocking=False):
                logger.info("Sema was released to kill thread")
                sys.exit()

            time.sleep(1)
            message = snakemq.message.Message(
                json.dumps({"type": "flush"}).encode("utf-8"), ttl=600
            )
            self.my_messaging.send_message(IDENT_KERNEL_MANAGER, message)


def start_snakemq(app):
    global my_messaging

    my_link = snakemq.link.Link()
    my_packeter = snakemq.packeter.Packeter(my_link)
    my_messaging = snakemq.messaging.Messaging(IDENT_MAIN, "", my_packeter)
    my_link.add_listener(("localhost", SNAKEMQ_PORT))

    def on_recv(conn, ident, message):
        if ident == IDENT_KERNEL_MANAGER:
            message = json.loads(message.data.decode("utf-8"))

            if message["type"] == "message" or message["type"] == "message_raw":
                # Broadcast to all channels that have sent a Slack message
                # TODO: 1:1 kernel <> channel mapping
                for channel in channels:
                    try:
                        if message["type"] == "message":
                            app.client.chat_postMessage(
                                text=message["value"], channel=channel
                            )
                        elif message["type"] == "message_raw":
                            app.client.chat_postMessage(
                                text=message["value"],
                                blocks=[
                                    {
                                        "type": "section",
                                        "text": {
                                            "type": "mrkdwn",
                                            "text": f"```{message['value']}```",
                                        },
                                    }
                                ],
                                channel=channel,
                            )
                    except Exception as e:
                        logger.debug(f"{type(e)}[{e}]")

    # Start FlushMessenger
    kill_sema = Semaphore()
    t = FlushMessenger(my_messaging, kill_sema)
    t.start()

    def end_thread():
        kill_sema.release()

    atexit.register(end_thread)

    my_messaging.on_message_recv.add(on_recv)
    logger.info("Starting snakemq loop")
    my_link.loop()


def start_kernel_manager():
    kernel_manager_process = subprocess.Popen([sys.executable, "kernel_manager.py"])
    
    def cleanup():
        kernel_manager_process.kill()

        # Find kernel PID to kill
        with open(KERNEL_PID_FILENAME, 'r') as f:
            os.kill(int(f.read()), signal.SIGKILL)

    atexit.register(cleanup)


def start_bot():
    global channels

    app = App(token=SLACK_BOT_TOKEN, name="Jarvis")

    @app.message(re.compile(".*"))
    def parse(message, say):
        message_text = message["text"]
        dm_channel = message["channel"]

        channels.add(dm_channel)

        if message_text.startswith("/command"):
            if message_text == "/command restart":
                # restart kernel
                pass
            elif message_text == "/command test":
                say(text="I'm alive.", channel=dm_channel)
        else:
            message_obj = snakemq.message.Message(
                json.dumps({"type": "execute", "value": message_text}).encode("utf-8"),
                ttl=600,
            )
            my_messaging.send_message("kernel_manager", message_obj)

    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.connect()
    return app


def exit_kernel_manager():
    message = snakemq.message.Message(
        json.dumps({"type": "exit"}).encode("utf-8"), ttl=600
    )
    my_messaging.send_message(IDENT_KERNEL_MANAGER, message)
    logger.debug("End send...")

if __name__ == "__main__":
    app = start_bot()
    start_kernel_manager()
    start_snakemq(app)