import sys
import subprocess
import os
import queue
import json
import signal
import threading
import time
import atexit

from time import sleep
from jupyter_client import BlockingKernelClient

import config
import utils

# Set up globals
messaging = None
logger = config.get_logger()

class FlushingThread(threading.Thread):
    def __init__(self, kc, kill_sema):
        threading.Thread.__init__(self)
        self.kill_sema = kill_sema
        self.kc = kc

    def run(self):
        logger.info("Running message flusher...")
        while True:

            if self.kill_sema.acquire(blocking=False):
                logger.info("Sema was released to kill thread")
                sys.exit()

            flush_kernel_msgs(self.kc)
            time.sleep(1)

def cleanup_kernels():
    for filename in os.listdir(config.KERNEL_PID_DIR):
        fp = os.path.join(config.KERNEL_PID_DIR, filename)
        if os.path.isfile(fp):
            try:
                pid = int(filename.split(".pid")[0])
                logger.debug("Killing PID %s" % pid)
                os.kill(pid, signal.SIGKILL)
                os.remove(fp)
            except Exception as e:
                logger.debug(e)


def start_snakemq(kc):
    global messaging

    messaging, link = utils.init_snakemq(config.IDENT_KERNEL_MANAGER, "connect")

    def on_recv(conn, ident, message):
        if ident == config.IDENT_MAIN:
            message = json.loads(message.data.decode("utf-8"))

            if message["type"] == "execute":
                logger.debug("Executing command: %s" % message["value"])
                kc.execute(message["value"])
                # Try direct flush with default wait (0.2)
                flush_kernel_msgs(kc)

    messaging.on_message_recv.add(on_recv)

    start_flusher(kc)
    
    # Send alive
    utils.send_json(messaging, {"type": "status", "value": "ready"}, config.IDENT_MAIN)

    logger.info("Starting snakemq loop")
    link.loop()


def start_flusher(kc):
    # Start FlushMessenger
    kill_sema = threading.Semaphore()
    kill_sema.acquire()
    t = FlushingThread(kc, kill_sema)
    t.start()

    def end_thread():
        kill_sema.release()

    atexit.register(end_thread)


def send_message(message, message_type="message"):
    utils.send_json(messaging, {"type": message_type, "value": message}, config.IDENT_MAIN)


def flush_kernel_msgs(kc, tries=1, timeout=0.2):
    try:
        hit_empty = 0

        while True:
            try:
                msg = kc.get_iopub_msg(timeout=timeout)
                if msg["msg_type"] == "execute_result":
                    if "text/plain" in msg["content"]["data"]:
                        send_message(
                            msg["content"]["data"]["text/plain"], "message_raw"
                        )
                if msg["msg_type"] == "display_data":
                    if "image/png" in msg["content"]["data"]:
                        # Convert to Slack upload
                        send_message(
                            msg["content"]["data"]["image/png"],
                            message_type="image/png"
                        )
                    elif "text/plain" in msg["content"]["data"]:
                        send_message(msg["content"]["data"]["text/plain"])
                    
                elif msg["msg_type"] == "stream":
                    logger.debug("Received stream output %s" % msg["content"]["text"])
                    send_message(msg["content"]["text"])
                elif msg["msg_type"] == "error":
                    send_message(
                        utils.escape_ansi("\n".join(msg["content"]["traceback"])),
                        "message_raw",
                    )
            except queue.Empty:
                hit_empty += 1
                if hit_empty == tries:
                    # Empty queue for one second, give back control
                    break
            except Exception as e:
                logger.debug(f"{e} [{type(e)}")
                break
    except Exception as e:
        logger.debug(f"{e} [{type(e)}")


def start_kernel():
    kernel_connection_file = os.path.join(os.getcwd(), "kernel_connection_file.json")

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
    str_kernel_pid = str(kernel_process.pid)
    os.makedirs(config.KERNEL_PID_DIR, exist_ok=True)
    with open(os.path.join(config.KERNEL_PID_DIR, str_kernel_pid + ".pid"), 'w') as p:
        p.write("kernel")

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
