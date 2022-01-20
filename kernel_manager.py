import sys
import subprocess
import os
import queue
import json
import re


from time import sleep

from jupyter_client import BlockingKernelClient

import snakemq.link
import snakemq.packeter
import snakemq.messaging
import snakemq.message

from config import IDENT_KERNEL_MANAGER, IDENT_MAIN, KERNEL_PID_FILENAME, SNAKEMQ_PORT, get_logger

# Set up global hook
my_messaging = None
kernel_process = None

logger = get_logger()


def escape_ansi(line):
    ansi_escape = re.compile(r"(?:\x1B[@-_]|[\x80-\x9F])[0-?]*[ -/]*[@-~]")
    return ansi_escape.sub("", line)


def clean_exit():
    logger.info("Cleaned up kernel_process")
    kernel_process.kill()
    sys.exit()


def start_snakemq(kc):
    global my_messaging

    my_link = snakemq.link.Link()
    my_packeter = snakemq.packeter.Packeter(my_link)
    my_messaging = snakemq.messaging.Messaging(IDENT_KERNEL_MANAGER, "", my_packeter)
    my_link.add_connector(("localhost", SNAKEMQ_PORT))

    def on_recv(conn, ident, message):

        logger.debug(ident, message)

        if ident == IDENT_MAIN:
            message = json.loads(message.data.decode("utf-8"))

            if message["type"] == "execute":
                logger.debug("Executing command: %s" % message["value"])
                kc.execute(message["value"])
                flush_kernel_msgs(kc)
            if message["type"] == "flush":
                flush_kernel_msgs(kc, tries=1)
            if message["type"] == "exit":
                logger.debug("Abc")
                clean_exit()

    my_messaging.on_message_recv.add(on_recv)
    logger.info("Starting snakemq loop")
    my_link.loop()


def send_message(message, type="message"):
    message = snakemq.message.Message(
        json.dumps({"type": type, "value": message}).encode("utf-8"), ttl=600
    )
    my_messaging.send_message(IDENT_MAIN, message)


def flush_kernel_msgs(kc, tries=10):
    try:
        hit_empty = 0

        while True:
            try:
                msg = kc.get_iopub_msg(timeout=0.1)
                if msg["msg_type"] == "execute_result":
                    if "text/plain" in msg["content"]["data"]:
                        send_message(
                            msg["content"]["data"]["text/plain"], "message_raw"
                        )
                if msg["msg_type"] == "display_data":
                    if "text/plain" in msg["content"]["data"]:
                        send_message(msg["content"]["data"]["text/plain"])
                    if "image/png" in msg["content"]["data"]:
                        # Convert to Slack upload
                        send_message(
                            "<img src='data:image/png;base64,%s'>"
                            % msg["content"]["data"]["image/png"]
                        )
                elif msg["msg_type"] == "stream":
                    logger.debug("Received stream output %s" % msg["content"]["text"])
                    send_message(msg["content"]["text"])
                elif msg["msg_type"] == "error":
                    send_message(
                        escape_ansi("\n".join(msg["content"]["traceback"])),
                        "message_raw",
                    )
            except queue.Empty:
                hit_empty += 1
                if hit_empty == tries:
                    # Empty queue for one second, give back control
                    break
            except Exception as e:
                logger.debug(e)
    except Exception as e:
        logger.debug(e)


def start_kernel():
    global kernel_process
    kernel_connection_file = os.path.join(os.getcwd(), "test.json")

    if os.path.isfile(kernel_connection_file):
        os.remove(kernel_connection_file)
    if os.path.isdir(kernel_connection_file):
        os.rmdir(kernel_connection_file)

    kernel_process = subprocess.Popen(
        [
            sys.executable,
            "launch_kernel.py",
            "--IPKernelApp.connection_file",
            kernel_connection_file,
            "--matplotlib=inline",
        ]
    )
    # Write PID for caller to kill
    with open(KERNEL_PID_FILENAME, 'w') as p:
        p.write(str(kernel_process.pid))

    # Wait for kernel connection file to be written
    while True:
        if not os.path.isfile(kernel_connection_file):
            sleep(0.1)
        else:
            break

    # Client
    kc = BlockingKernelClient(connection_file=kernel_connection_file)
    kc.load_connection_file()
    kc.start_channels()
    kc.wait_for_ready()
    return kc


if __name__ == "__main__":
    kc = start_kernel()
    start_snakemq(kc)
