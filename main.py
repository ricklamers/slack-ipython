# Idea: spawn Jupyter kernel
# Connect programmatically
# Send input
# Capture (rich output)

import os
import sys
import subprocess
import atexit
import queue

from time import sleep

from jupyter_client import BlockingKernelClient

kernel_connection_file = os.path.join(os.getcwd(), "test.json")

if os.path.isfile(kernel_connection_file):
  os.remove(kernel_connection_file)
if os.path.isdir(kernel_connection_file):
  os.rmdir(kernel_connection_file)

kernel_process = subprocess.Popen([sys.executable, "launch_kernel.py", "--IPKernelApp.connection_file", kernel_connection_file, "--matplotlib=inline"])

def cleanup():
  kernel_process.kill()
  print("Cleaned up")

atexit.register(cleanup)

# Wait for kernel connection file to be written
while True:
  if not os.path.isfile(kernel_connection_file):
    sleep(.1)
  else:
    break

# Client
kc = BlockingKernelClient(connection_file=kernel_connection_file)
kc.load_connection_file()
kc.start_channels()
kc.wait_for_ready()

def print_all_results(kc):
  hit_empty = 0
  while True:
    try:
      msg = kc.get_iopub_msg(timeout=.1)
      #print(msg['msg_type'])
      #print(msg)
      if msg['msg_type'] == 'execute_result':
        if 'text/plain' in msg['content']['data']:
          print(msg['content']['data']['text/plain'])
      if msg['msg_type'] == 'display_data':
        if 'text/plain' in msg['content']['data']:
          print(msg['content']['data']['text/plain'])
        if 'image/png' in msg['content']['data']:
          # Convert to Slack upload
          print("<img src='data:image/png;base64,%s'>" % msg['content']['data']['image/png'])
      elif msg['msg_type'] == 'stream':
        print(msg['content']['text'], end='')
      elif msg['msg_type'] == 'error':
        print('\n'.join(msg['content']['traceback']))
      # if msg['msg_type'] == 'status' and msg['content']['execution_state'] == 'idle':
      #   print("Execution: idle")
    except queue.Empty:
      hit_empty += 1
      if hit_empty == 10:
        break
    except Exception as e:
      print(e, type(e))

while True:
  try:
    print_all_results(kc)
  except Exception as e:
    print(e)
    pass

  interactive = input()
  if interactive == "exit":
    break
  else:
    kc.execute(interactive)


