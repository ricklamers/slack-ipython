import os
import re
import atexit
import subprocess
import sys
import json
import base64
import pathlib

from uuid import uuid4

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from dotenv import load_dotenv

import slack_ipython.kernel_manager as kernel_manager
import slack_ipython.utils as utils
import slack_ipython.config as config

load_dotenv()
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_APP_TOKEN = os.environ["SLACK_APP_TOKEN"]

# Globals
logger = config.get_logger()
messaging = None
channels = set()
# Note, only one kernel_manager_process can be active
kernel_manager_process = None


def broadcast_to_slack_clients(message, message_type="message", app=None):
    # Broadcast to all channels that have sent a Slack message
    if app is None:
        raise Exception("No app passed")

    # PNG preprocessing step
    file_path = None
    if message_type == "image/png":
        image_dir = "image_cache"
        os.makedirs(image_dir, exist_ok=True)
        tmp_path = os.path.join(image_dir, str(uuid4()) + ".png")

        with open(tmp_path, "wb") as fh:
            fh.write(base64.decodebytes(message.encode("utf-8")))
        file_path = tmp_path

    for channel in channels:
        try:
            logger.debug("Sending Slack message %s[truncated]]" % message[:10])
            if message_type == "message":
                app.client.chat_postMessage(text=message, channel=channel)
            elif message_type == "message_raw":
                app.client.chat_postMessage(
                    text=message,
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"```{message}```",
                            },
                        }
                    ],
                    channel=channel,
                )
            elif message_type == "image/png" and file_path is not None:
                try:
                    logger.debug("Uploading PNG to Slack")
                    app.client.files_upload(file=file_path, channels=channel)
                except Exception as e:
                    logger.debug(f"{e}[{type(e)}]")
        except Exception as e:
            logger.debug(f"{type(e)}[{e}]")

    # File cleanup
    if file_path is not None:
        try:
            os.remove(file_path)
        except Exception:
            pass


def start_snakemq(app):
    global messaging

    messaging, link = utils.init_snakemq(config.IDENT_MAIN)

    def on_recv(conn, ident, message):
        message = json.loads(message.data.decode("utf-8"))

        if message["type"] == "status":
            if message["value"] == "ready":
                broadcast_to_slack_clients("Kernel is ready.", app=app)
        elif message["type"] in ["message", "message_raw", "image/png"]:
            # TODO: 1:1 kernel <> channel mapping
            broadcast_to_slack_clients(
                message["value"], message_type=message["type"], app=app
            )

    messaging.on_message_recv.add(on_recv)
    logger.info("Starting snakemq loop")
    link.loop()


def start_kernel_manager():
    global kernel_manager_process
    kernel_manager_script_path = os.path.join(
        pathlib.Path(__file__).parent.resolve(), "kernel_manager.py"
    )
    kernel_manager_process = subprocess.Popen(
        [sys.executable, kernel_manager_script_path]
    )


def stop_kernel_manager():
    if kernel_manager_process:
        kernel_manager_process.terminate()
        kernel_manager.cleanup_kernels()
    else:
        raise Exception("No active kernel_manager_process!")


def generate_help():
    return "The following commands are available: \n" + "".join(
        map(lambda x: "\n .kernel " + x, ["help", "restart", "version"])
    )


def start_bot():
    global channels

    app = App(token=SLACK_BOT_TOKEN, name="Jarvis")

    @app.event({
        "type": "message",
        "subtype": "message_changed"
    })
    def handler_changed(message, _):
        # Register handler to satisfy Bolt
        logger.debug(message)
        pass

    @app.event({
        "type": "message",
        "subtype": "message_deleted"
    })
    def handler_delete(message, _):
        # Register handler to satisfy Bolt
        logger.debug(message)
        pass

    @app.message(re.compile(".*"))
    def parse(message, say):
        message_text = message["text"]
        dm_channel = message["channel"]

        channels.add(dm_channel)

        if message_text.startswith(".kernel"):
            if message_text == ".kernel restart":
                say(text="Restarting kernel...", channel=dm_channel)
                stop_kernel_manager()
                start_kernel_manager()
            elif message_text == ".kernel help":
                say(text=generate_help(), channel=dm_channel)
            elif message_text == ".kernel version":
                utils.send_json(
                    messaging,
                    {"type": "execute", "value": "!python --version"},
                    config.IDENT_KERNEL_MANAGER,
                )
            else:
                say(text=generate_help(), channel=dm_channel)
        else:
            logger.debug("Rcvd Slack msg: %s" % message_text)

            # Filter tags from incoming messages (for auto URL conversion)
            filtered_message_text = re.sub('<(.*?)>', '\\1', message_text)
            
            logger.debug("Fltrd Slack msg: %s" % filtered_message_text)

            utils.send_json(
                messaging,
                {"type": "execute", "value": filtered_message_text},
                config.IDENT_KERNEL_MANAGER,
            )

    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.connect()
    return app


def main():
    app = start_bot()
    start_kernel_manager()
    atexit.register(stop_kernel_manager)
    start_snakemq(app)
